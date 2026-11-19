"""Build a native portable distribution for the OS running this script."""
from importlib.metadata import distribution
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
NAME = "InspectionWorkbench"
TARGETS = {
    ("Windows", "amd64"): "windows-x64",
    ("Windows", "x86_64"): "windows-x64",
    ("Darwin", "arm64"): "macos-arm64",
    ("Darwin", "x86_64"): "macos-intel",
}


def copy_notices(bundle):
    notices = bundle / "third-party-notices"
    pending = ["Flask", "pdfplumber", "Pillow", "python-docx", "openpyxl", "PyInstaller"]
    handled = set()
    while pending:
        name = canonicalize_name(pending.pop())
        if name in handled:
            continue
        handled.add(name)
        package = distribution(name)
        for relative in package.files or ():
            if any(part.endswith(".dist-info") for part in relative.parts) and (
                "licenses" in relative.parts or relative.name.lower().startswith(("license", "copying", "notice"))
            ):
                target = notices / name / relative.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(package.locate_file(relative), target)
        for entry in package.requires or ():
            requirement = Requirement(entry)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    for folder in [Path(sys.base_prefix), Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"]:
        license_file = folder / "LICENSE.txt"
        if license_file.is_file():
            shutil.copyfile(license_file, notices / "Python-LICENSE.txt")
            break


def main():
    target = TARGETS.get((platform.system(), platform.machine().lower()))
    if not target:
        sys.exit(f"Unsupported build target: {platform.system()} {platform.machine()}")
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onedir", "--name", NAME,
            "--add-data", "app/templates:templates",
            "--add-data", "app/static:static",
            "--collect-data", "pdfminer",
            "app/server.py",
        ],
        cwd=ROOT, check=True,
    )
    bundle = ROOT / "dist" / NAME
    copy_notices(bundle)
    shutil.copyfile(ROOT / "docs" / "操作指南.md", bundle / "操作指南.txt")
    if target.startswith("macos"):
        launcher = bundle / "启动工作台.command"
        launcher.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\nexec ./InspectionWorkbench\n')
        launcher.chmod(0o755)
    output = ROOT / "packages"
    output.mkdir(exist_ok=True)
    archive = shutil.make_archive(str(output / f"{NAME}-{target}"), "zip", bundle.parent, bundle.name)
    print(archive)


if __name__ == "__main__":
    main()
