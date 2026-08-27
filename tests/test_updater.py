from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fmost_brain_viewer as viewer


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class UpdaterTests(unittest.TestCase):
    def test_background_worker_reports_success_and_original_error(self) -> None:
        successful = []
        worker = viewer.UpdateCheckWorker()
        worker.completed.connect(lambda releases, error: successful.append((releases, error)))
        payload = [{"tag_name": "v2.4.0", "draft": False, "prerelease": False}]
        with mock.patch.object(viewer, "fetch_github_releases", return_value=payload):
            worker.run()
        self.assertEqual(successful[0][0][0]["tag_name"], "v2.4.0")
        self.assertIsNone(successful[0][1])

        failed = []
        worker = viewer.UpdateCheckWorker()
        worker.completed.connect(lambda releases, error: failed.append((releases, error)))
        with mock.patch.object(
            viewer, "fetch_github_releases", side_effect=TimeoutError("slow network")
        ):
            worker.run()
        self.assertEqual(failed[0][0], [])
        self.assertIsInstance(failed[0][1], TimeoutError)

    def test_versions_and_release_filtering(self) -> None:
        payload = [
            {"tag_name": "v2.4.0", "draft": False, "prerelease": False},
            {"tag_name": "v2.3.3", "draft": False, "prerelease": True},
            {"tag_name": "v2.3.2", "draft": False, "prerelease": False},
            {"tag_name": "invalid", "draft": False, "prerelease": False},
        ]
        releases = viewer.newer_stable_releases(payload, "2.3.1")
        self.assertEqual([release["tag_name"] for release in releases], ["v2.4.0", "v2.3.2"])
        self.assertEqual(viewer.version_tuple("v2.3.2"), (2, 3, 2))

    def test_installer_asset_requires_exact_release_version(self) -> None:
        release = {
            "tag_name": "v2.3.2",
            "assets": [
                {"name": "fMOST-Brain-Viewer-Portable-2.3.2-win64.zip"},
                {"name": "fMOST-Brain-Viewer-Setup-2.3.2-win64.exe"},
            ],
        }
        self.assertEqual(
            viewer.release_installer_asset(release)["name"],
            "fMOST-Brain-Viewer-Setup-2.3.2-win64.exe",
        )

    def test_windows_update_requests_elevation_and_closes_running_app(self) -> None:
        shell_execute = mock.Mock(return_value=42)
        fake_windll = mock.Mock()
        fake_windll.shell32.ShellExecuteW = shell_execute
        with (
            mock.patch.object(viewer.sys, "platform", "win32"),
            mock.patch.object(viewer.ctypes, "windll", fake_windll, create=True),
        ):
            self.assertTrue(viewer.launch_update_installer(Path("update.exe")))
        self.assertEqual(shell_execute.call_args.args[1], "runas")
        self.assertIn("/FORCECLOSEAPPLICATIONS", shell_execute.call_args.args[3])

    def test_atomic_download_verifies_sha256_and_cleans_part(self) -> None:
        payload = b"synthetic installer"
        asset = {
            "browser_download_url": "https://example.invalid/setup.exe",
            "size": len(payload),
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "setup.exe"
            with mock.patch.object(
                viewer.urllib.request, "urlopen", return_value=FakeResponse(payload)
            ):
                viewer.download_release_asset(asset, destination)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(destination.with_suffix(".exe.part").exists())

            asset["digest"] = "sha256:" + "0" * 64
            with mock.patch.object(
                viewer.urllib.request, "urlopen", return_value=FakeResponse(payload)
            ):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    viewer.download_release_asset(asset, destination)
            self.assertFalse(destination.with_suffix(".exe.part").exists())


if __name__ == "__main__":
    unittest.main()
