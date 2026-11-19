"""Build a native portable distribution for the OS running this script."""
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = "InspectionWorkbench"
TARGETS = {
    ("Windows", "amd64"): "windows-x64",
    ("Windows", "x86_64"): "windows-x64",
    ("Darwin", "arm64"): "macos-arm64",
    ("Darwin", "x86_64"): "macos-intel",
}


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
            "app/server.py",
        ],
        cwd=ROOT, check=True,
    )
    bundle = ROOT / "dist" / NAME
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
