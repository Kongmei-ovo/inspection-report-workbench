"""Exercise the update transaction without downloading production packages."""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import updater


class UpdaterTest(TestCase):
    def release(self, tag, payload):
        asset = "InspectionWorkbench-macos-arm64.zip"
        return {
            "tag_name": tag,
            "assets": [{
                "name": asset,
                "browser_download_url": f"{updater.RELEASES}/download/{tag}/{asset}",
                "digest": "sha256:" + sha256(payload).hexdigest(),
                "size": len(payload),
            }],
        }

    def test_selects_newest_matching_version_and_rejects_unverified_urls(self):
        older = self.release("v0.2.1", b"zip")
        newest = self.release("v0.10.0", b"zip")
        draft = self.release("v1.0.0", b"draft")
        draft["draft"] = True
        invalid = self.release("v2.0.0", b"foreign")
        invalid["assets"][0]["browser_download_url"] = "https://example.com/update.zip"
        items = [older, invalid, draft, newest]
        self.assertEqual(updater.select_release(items, "macos-arm64", "v0.2.0").version, "v0.10.0")
        self.assertIsNone(updater.select_release(items, "windows-x64", "v0.2.0"))
        self.assertIsNone(updater.select_release(items, "macos-arm64", "v0.10.0"))

    def make_bundle(self, version, unsafe=False):
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            for name in ("InspectionWorkbench", "启动工作台.command"):
                entry = ZipInfo(f"{updater.NAME}/{name}")
                entry.external_attr = 0o100755 << 16
                archive.writestr(entry, b"test executable")
            archive.writestr(f"{updater.NAME}/_internal/version.txt", version)
            if unsafe:
                archive.writestr(f"{updater.NAME}/../escaped", b"bad")
        return payload.getvalue()

    def test_verified_install_keeps_old_copy_and_never_promotes_bad_download(self):
        version = "v0.2.1"
        payload = self.make_bundle(version)
        item = self.release(version, payload)
        release = updater.select_release([item], "macos-arm64", "v0.2.0")
        with TemporaryDirectory() as tmp, patch.object(updater, "FROZEN", True), \
                patch.object(updater, "CURRENT", "v0.2.0"), \
                patch.object(updater, "versions_dir", return_value=Path(tmp)), \
                patch.object(updater.platform, "system", return_value="Darwin"):
            old = Path(tmp) / "v0.2.0"
            old.mkdir()
            with patch.object(updater, "get_url", return_value=BytesIO(b"x" * len(payload))):
                with self.assertRaisesRegex(updater.UpdateError, "校验失败"):
                    updater.install(release)
                self.assertFalse((Path(tmp) / version).exists())
            with patch.object(updater, "get_url", return_value=BytesIO(payload)):
                new = updater.install(release)
            self.assertTrue(old.exists())
            self.assertEqual(updater.newer_installed(), new)
            if os.name != "nt":
                self.assertEqual(updater.executable(new).stat().st_mode & 0o777, 0o755)
            with patch.object(updater, "get_url", side_effect=AssertionError("already installed")):
                self.assertEqual(updater.install(release), new)

    def test_rejects_archive_traversal(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            zipped = directory / "unsafe.zip"
            zipped.write_bytes(self.make_bundle("v0.2.1", unsafe=True))
            with self.assertRaisesRegex(updater.UpdateError, "无效路径"):
                updater.extract_bundle(zipped, directory)
            self.assertFalse((directory / "escaped").exists())


if __name__ == "__main__":
    main()
