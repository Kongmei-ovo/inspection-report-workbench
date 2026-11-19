"""Install the matching, verified GitHub Release into a separate local directory."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile
import certifi

REPO = "Kongmei-ovo/inspection-report-workbench"
RELEASES = f"https://github.com/{REPO}/releases"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
NAME = "InspectionWorkbench"
TARGETS = {
    ("Windows", "amd64"): "windows-x64",
    ("Windows", "x86_64"): "windows-x64",
    ("Darwin", "arm64"): "macos-arm64",
    ("Darwin", "x86_64"): "macos-intel",
}
FROZEN = bool(getattr(sys, "frozen", False))
VERSION_FILE = Path(__file__).with_name("version.txt")
CURRENT = VERSION_FILE.read_text(encoding="ascii").strip() if FROZEN else "开发版"
MAX_DOWNLOAD = 150 * 1024 * 1024
MAX_EXPANDED = 500 * 1024 * 1024
INSTALL_LOCK = Lock()


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    digest: str


def target():
    return TARGETS.get((platform.system(), platform.machine().lower()))


def version_key(version):
    match = re.fullmatch(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})", version)
    return tuple(map(int, match.groups())) if match else None


def get_url(url):
    request = Request(url, headers={"User-Agent": f"{NAME}/{CURRENT if FROZEN else 'dev'}"})
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return urlopen(request, timeout=30, context=context)


def select_release(items, platform_name, current):
    asset_name = f"{NAME}-{platform_name}.zip"
    current_key = version_key(current) or (0, 0, 0)
    found = []
    for item in items:
        if item.get("draft"):
            continue
        tag = item.get("tag_name", "")
        key = version_key(tag)
        if key is None or key <= current_key or not tag.startswith("v"):
            continue
        expected = f"{RELEASES}/download/{tag}/{asset_name}"
        for asset in item.get("assets", []):
            digest = asset.get("digest") or ""
            size = asset.get("size")
            if (
                asset.get("name") == asset_name and asset.get("browser_download_url") == expected
                and isinstance(size, int) and 0 < size <= MAX_DOWNLOAD
                and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            ):
                found.append((key, Release(tag, expected, size, digest[7:])))
    return max(found, key=lambda item: item[0])[1] if found else None


def check():
    platform_name = target()
    if not platform_name:
        return None
    try:
        with get_url(API) as response:
            items = json.loads(response.read(2 * 1024 * 1024 + 1))
    except (URLError, OSError, ValueError) as error:
        raise UpdateError("无法连接 GitHub 检查更新，请稍后再试。") from error
    return select_release(items, platform_name, CURRENT)


def download(release, path):
    digest = hashlib.sha256()
    size = 0
    try:
        with get_url(release.url) as response, path.open("wb") as output:
            while chunk := response.read(256 * 1024):
                size += len(chunk)
                if size > release.size:
                    raise UpdateError("下载大小与发布记录不符，更新已停止。")
                output.write(chunk)
                digest.update(chunk)
    except (URLError, OSError) as error:
        raise UpdateError("下载更新失败，请检查网络或磁盘空间后重试。") from error
    if size != release.size or digest.hexdigest() != release.digest:
        raise UpdateError("下载文件校验失败，更新已停止。")


def versions_dir():
    if platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        root = Path.home() / "Library" / "Application Support"
    return root / NAME / "versions" / (target() or "unsupported")


def extract_bundle(archive_path, folder):
    with ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > 10_000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
            raise UpdateError("更新包超过预期大小。")
        for entry in entries:
            relative = PurePosixPath(entry.filename)
            mode = (entry.external_attr >> 16) & 0xFFFF
            if (
                not relative.parts or relative.parts[0] != NAME or ".." in relative.parts
                or "\\" in entry.filename or ":" in entry.filename or stat.S_ISLNK(mode)
            ):
                raise UpdateError("更新包包含无效路径。")
            path = folder.joinpath(*relative.parts)
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output)
            if platform.system() == "Darwin" and mode & 0o111:
                path.chmod(0o755)


def executable(bundle):
    return bundle / (NAME + (".exe" if platform.system() == "Windows" else ""))


def valid_bundle(bundle, version):
    marker = bundle / "_internal" / "version.txt"
    launcher = bundle / "启动工作台.command"
    try:
        return (
            version_key(version) is not None and executable(bundle).is_file()
            and (platform.system() != "Darwin" or launcher.is_file())
            and version_key(marker.read_text(encoding="ascii").strip()) == version_key(version)
        )
    except (OSError, ValueError):
        return False


def install(release):
    if not FROZEN or version_key(CURRENT) is None:
        raise UpdateError("请从发布页下载正式试用包，开发构建无法自动安装更新。")
    root = versions_dir()
    final = root / release.version
    if not INSTALL_LOCK.acquire(blocking=False):
        raise UpdateError("正在安装更新，请稍候。")
    try:
        if valid_bundle(final, release.version):
            return final
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=root, prefix=".update-") as tmp:
            work = Path(tmp)
            archive = work / "update.zip"
            download(release, archive)
            extract_bundle(archive, work)
            staged = work / NAME
            if not valid_bundle(staged, release.version):
                raise UpdateError("更新包的程序文件或版本标记不正确。")
            if final.exists():
                raise UpdateError("本机已有同名版本目录，请打开发布页手动更新。")
            try:
                staged.rename(final)
            except OSError:
                if not valid_bundle(final, release.version):
                    raise
        return final
    except (OSError, BadZipFile, RuntimeError) as error:
        raise UpdateError("无法解压或安装新版，请检查磁盘空间或从发布页手动更新。") from error
    finally:
        INSTALL_LOCK.release()


def newer_installed():
    current = version_key(CURRENT)
    if not FROZEN or current is None:
        return None
    try:
        versions = [
            (key, bundle) for bundle in versions_dir().iterdir()
            if (key := version_key(bundle.name)) is not None and key > current
            and valid_bundle(bundle, bundle.name)
        ]
        return max(versions, key=lambda item: item[0])[1] if versions else None
    except OSError:
        return None


def launch(bundle):
    try:
        if platform.system() == "Darwin":
            subprocess.run(["/usr/bin/open", "-a", "Terminal", str(bundle / "启动工作台.command")],
                           check=True, timeout=10, capture_output=True)
        else:
            # Reset the bootloader state when starting a different frozen executable.
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
            subprocess.Popen([str(executable(bundle))], cwd=bundle, env=env,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError(f"新版已下载，自动启动失败。请从以下目录手动启动：{bundle}") from error
