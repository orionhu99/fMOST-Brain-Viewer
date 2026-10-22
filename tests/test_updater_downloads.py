from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import viewer_updater as updater


class DownloadResponse(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None, chunk_size=None, fail_after=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}
        self.chunk_size = chunk_size
        self.fail_after = fail_after

    def read1(self, size):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise ConnectionResetError("synthetic interrupted connection")
        return self.read(min(size, self.chunk_size or size))


class UpdaterDownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.destination = Path(self.directory.name) / "setup.exe"
        self.partial = self.destination.with_name("setup.exe.part")
        self.metadata = self.destination.with_name("setup.exe.part.json")
        self.payload = b"synthetic installer contents"
        self.asset = {
            "browser_download_url": "https://github.com/orionhu99/fMOST-Brain-Viewer/releases/download/v9.0.0/setup.exe",
            "size": len(self.payload),
            "digest": "sha256:" + hashlib.sha256(self.payload).hexdigest(),
        }
        self.sleep = mock.patch.object(updater.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def seed_partial(self, body, asset=None):
        asset = asset or self.asset
        self.partial.write_bytes(body)
        self.metadata.write_text(json.dumps({
            "sha256": asset["digest"].split(":", 1)[1],
            "url": asset["browser_download_url"],
            "size": asset["size"],
        }), encoding="utf-8")

    def range_response(self, start):
        return DownloadResponse(self.payload[start:], status=206, headers={
            "Content-Range": f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}",
        })

    def test_verified_cached_destination_is_reused_without_network(self):
        self.destination.write_bytes(self.payload)
        self.seed_partial(b"stale")
        progress = mock.Mock()
        with mock.patch.object(updater.urllib.request, "urlopen") as request:
            self.assertEqual(updater.download_release_asset(self.asset, self.destination, progress), self.destination)
        request.assert_not_called()
        progress.assert_called_once_with(len(self.payload), len(self.payload))
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.metadata.exists())

    def test_completed_partial_is_verified_and_reused(self):
        self.seed_partial(self.payload)
        with mock.patch.object(updater.urllib.request, "urlopen") as request:
            updater.download_release_asset(self.asset, self.destination)
        request.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertFalse(self.partial.exists())

    def test_range_resume_requests_existing_offset_and_checks_full_digest(self):
        self.seed_partial(self.payload[:8])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=self.range_response(8)) as request:
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=8-")
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertFalse(self.metadata.exists())

    def test_range_ignored_rewrites_instead_of_appending(self):
        self.seed_partial(self.payload[:8])
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=DownloadResponse(self.payload)):
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_invalid_content_range_preserves_existing_prefix(self):
        for content_range in ("", "bytes 0-18/27", "bytes 8-30/27", "bytes 8-26/*"):
            with self.subTest(content_range=content_range):
                self.seed_partial(self.payload[:8])
                response = DownloadResponse(self.payload[8:], status=206, headers={"Content-Range": content_range})
                with (
                    mock.patch.object(updater.urllib.request, "urlopen", return_value=response),
                    self.assertRaisesRegex(ValueError, "Content-Range"),
                ):
                    updater.download_release_asset(self.asset, self.destination)
                self.assertEqual(self.partial.read_bytes(), self.payload[:8])
                self.assertFalse(self.destination.exists())

    def test_range_response_length_must_match_range(self):
        self.seed_partial(self.payload[:8])
        response = self.range_response(8)
        response.headers["Content-Length"] = "2"
        with (
            mock.patch.object(updater.urllib.request, "urlopen", return_value=response),
            self.assertRaisesRegex(ValueError, "Content-Range"),
        ):
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(self.partial.read_bytes(), self.payload[:8])

    def test_digest_change_discards_previous_partial(self):
        old_asset = {**self.asset, "digest": "sha256:" + "0" * 64}
        self.seed_partial(b"old bytes", old_asset)
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=DownloadResponse(self.payload)) as request:
            updater.download_release_asset(self.asset, self.destination)
        self.assertIsNone(request.call_args.args[0].get_header("Range"))
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_unidentified_partial_is_discarded(self):
        self.partial.write_bytes(b"untrusted bytes")
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=DownloadResponse(self.payload)) as request:
            updater.download_release_asset(self.asset, self.destination)
        self.assertIsNone(request.call_args.args[0].get_header("Range"))

    def test_cancelled_download_retains_partial_and_can_resume(self):
        stop = [False]

        def progress(_done, _total):
            stop[0] = True

        with (
            mock.patch.object(updater.urllib.request, "urlopen", return_value=DownloadResponse(self.payload, chunk_size=8)),
            self.assertRaises(updater.AtlasSetupCancelled),
        ):
            updater.download_release_asset(self.asset, self.destination, progress, lambda: stop[0])
        self.assertEqual(self.partial.read_bytes(), self.payload[:8])
        self.assertTrue(self.metadata.exists())
        self.assertFalse(self.destination.exists())
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=self.range_response(8)):
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_transient_interruption_retries_from_written_offset(self):
        responses = [DownloadResponse(self.payload, chunk_size=8, fail_after=8), self.range_response(8)]
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=responses) as request:
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[0].get_header("Range"), "bytes=8-")
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_short_response_retries_instead_of_discarding_partial(self):
        first = DownloadResponse(self.payload[:8], headers={"Content-Length": str(len(self.payload))})
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=[first, self.range_response(8)]):
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_retries_are_bounded_and_leave_partial_for_later(self):
        failures = [
            DownloadResponse(self.payload, chunk_size=8, fail_after=8),
            urllib.error.URLError("synthetic offline"),
            TimeoutError("synthetic timeout"),
        ]
        with (
            mock.patch.object(updater.urllib.request, "urlopen", side_effect=failures) as request,
            self.assertRaises(TimeoutError),
        ):
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(self.partial.read_bytes(), self.payload[:8])
        self.assertTrue(self.metadata.exists())

    def test_permanent_http_error_does_not_retry(self):
        failure = urllib.error.HTTPError(self.asset["browser_download_url"], 404, "not found", {}, None)
        with (
            mock.patch.object(updater.urllib.request, "urlopen", side_effect=failure) as request,
            self.assertRaises(urllib.error.HTTPError),
        ):
            updater.download_release_asset(self.asset, self.destination)
        self.assertEqual(request.call_count, 1)

    def test_retry_backoff_is_cancellable(self):
        stop = [False]

        def interrupt(_seconds):
            stop[0] = True

        with (
            mock.patch.object(updater.urllib.request, "urlopen", side_effect=TimeoutError("offline")) as request,
            mock.patch.object(updater.time, "sleep", side_effect=interrupt),
            self.assertRaises(updater.AtlasSetupCancelled),
        ):
            updater.download_release_asset(self.asset, self.destination, cancelled=lambda: stop[0])
        self.assertEqual(request.call_count, 1)

    def test_hash_failure_removes_partial_and_does_not_replace_existing_file(self):
        previous = b"previously cached installer"
        self.destination.write_bytes(previous)
        corrupt = bytes([self.payload[0] ^ 1]) + self.payload[1:]
        with (
            mock.patch.object(updater.urllib.request, "urlopen", return_value=DownloadResponse(corrupt)),
            self.assertRaisesRegex(ValueError, "SHA-256"),
        ):
            updater.download_release_asset(self.asset, self.destination)
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.metadata.exists())
        self.assertEqual(self.destination.read_bytes(), previous)

    def test_resume_verifies_existing_prefix_as_well_as_new_bytes(self):
        self.seed_partial(b"corrupt!")
        with (
            mock.patch.object(updater.urllib.request, "urlopen", return_value=self.range_response(8)),
            self.assertRaisesRegex(ValueError, "SHA-256"),
        ):
            updater.download_release_asset(self.asset, self.destination)
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.metadata.exists())
        self.assertFalse(self.destination.exists())

    def test_complete_cancelled_download_is_reused_on_next_attempt(self):
        stop = [False]

        def progress(_done, _total):
            stop[0] = True

        with (
            mock.patch.object(updater.urllib.request, "urlopen", return_value=DownloadResponse(self.payload)),
            self.assertRaises(updater.AtlasSetupCancelled),
        ):
            updater.download_release_asset(self.asset, self.destination, progress, lambda: stop[0])
        self.assertEqual(self.partial.read_bytes(), self.payload)
        with mock.patch.object(updater.urllib.request, "urlopen") as request:
            updater.download_release_asset(self.asset, self.destination)
        request.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_active_destination_lock_prevents_partial_corruption(self):
        self.seed_partial(self.payload[:8])
        with (
            updater._download_target_lock(self.destination),
            mock.patch.object(updater.urllib.request, "urlopen") as request,
            self.assertRaisesRegex(RuntimeError, "already using"),
        ):
            updater.download_release_asset(self.asset, self.destination)
        request.assert_not_called()
        self.assertEqual(self.partial.read_bytes(), self.payload[:8])
        # A failed competing call must not leave the target locked.
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=self.range_response(8)):
            updater.download_release_asset(self.asset, self.destination)


if __name__ == "__main__":
    unittest.main()
