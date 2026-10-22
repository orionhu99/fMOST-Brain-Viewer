"""GitHub release transport, isolated from viewer state and saving."""
from pathlib import Path
from contextlib import contextmanager
import ctypes
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from PySide6 import QtCore, QtWidgets
from version import __version__

GITHUB_RELEASES_API = "https://api.github.com/repos/orionhu99/fMOST-Brain-Viewer/releases"

class AtlasSetupCancelled(Exception):
    pass

def version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    return tuple(map(int, match.groups())) if match else None


def newer_stable_releases(payload, current_version: str) -> list[dict]:
    """Return newer, published stable releases in descending version order."""
    current = version_tuple(current_version)
    if current is None:
        raise ValueError(f"Invalid current version: {current_version}")
    releases = []
    for release in payload:
        version = version_tuple(str(release.get("tag_name", "")))
        if (
            version is not None
            and version > current
            and not release.get("draft", False)
            and not release.get("prerelease", False)
        ):
            releases.append((version, release))
    releases.sort(key=lambda entry: entry[0], reverse=True)
    return [release for _version, release in releases]


def release_installer_asset(release: dict) -> dict | None:
    version = version_tuple(str(release.get("tag_name", "")))
    if version is None:
        return None
    expected = f"fMOST-Brain-Viewer-Setup-{'.'.join(map(str, version))}-win64.exe"
    return next(
        (asset for asset in release.get("assets", []) if asset.get("name") == expected),
        None,
    )


def fetch_github_releases() -> list[dict]:
    request = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"fMOST-Brain-Viewer/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


@contextmanager
def _download_target_lock(destination: Path):
    """OS locks release on process exit; keep the lock file to avoid rename races."""
    with destination.with_name(destination.name + ".lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if not lock.tell():
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another update download is already using this file.") from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another update download is already using this file.") from exc
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def download_release_asset(asset: dict, destination: Path, progress_callback=None, cancelled=None) -> Path:
    """Reuse verified installers or resume downloads, then replace atomically.

    Called by DownloadWorker: reads and short retry waits stay off the UI thread.
    Cancellation and transient failures retain a digest-bound partial download.
    """
    expected = str(asset.get("digest", "")).strip()
    expected_match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", expected)
    if expected_match is None:
        raise ValueError("Installer SHA-256 metadata is missing or invalid.")

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise AtlasSetupCancelled("Update download cancelled.")

    def sha256(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                check_cancelled()
                block = stream.read(1024 * 1024)
                if not block:
                    return digest.hexdigest()
                digest.update(block)

    check_cancelled()
    url = str(asset["browser_download_url"])
    if not url.startswith("https://github.com/orionhu99/fMOST-Brain-Viewer/releases/download/"):
        raise ValueError("Installer URL must belong to this project's GitHub releases.")
    expected_digest = expected_match.group(1).casefold()
    declared_size = int(asset.get("size", 0))
    if declared_size < 0:
        raise ValueError("Installer size is invalid.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    metadata = destination.with_name(destination.name + ".part.json")
    identity = {"sha256": expected_digest, "url": url, "size": declared_size}

    def discard_partial():
        temporary.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)

    def finish():
        check_cancelled()
        if sha256(temporary) != expected_digest:
            discard_partial()
            raise ValueError("Downloaded installer SHA-256 does not match GitHub metadata.")
        check_cancelled()
        temporary.replace(destination)
        metadata.unlink(missing_ok=True)
        return destination

    with _download_target_lock(destination):
        if destination.is_file() and sha256(destination) == expected_digest:
            discard_partial()
            if progress_callback is not None:
                size = destination.stat().st_size
                progress_callback(size, size)
            return destination
        try:
            matching_partial = metadata.stat().st_size < 4096 and json.loads(
                metadata.read_text(encoding="utf-8")
            ) == identity
        except (FileNotFoundError, ValueError, UnicodeError):
            matching_partial = False
        if not matching_partial or (temporary.exists() and declared_size and temporary.stat().st_size > declared_size):
            discard_partial()
        metadata.write_text(json.dumps(identity), encoding="utf-8")
        if temporary.is_file() and (not declared_size or temporary.stat().st_size == declared_size):
            if sha256(temporary) == expected_digest:
                return finish()
            discard_partial()
            metadata.write_text(json.dumps(identity), encoding="utf-8")

        for attempt in range(3):
            check_cancelled()
            offset = temporary.stat().st_size if temporary.exists() else 0
            headers = {"User-Agent": f"fMOST-Brain-Viewer/{__version__}", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    status = getattr(response, "status", 200)
                    length_header = response.headers.get("Content-Length")
                    length = int(length_header) if length_header is not None else None
                    if length is not None and length < 0:
                        raise ValueError("Installer response has an invalid Content-Length.")
                    if status == 206:
                        content_range = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                        )
                        if content_range is None:
                            raise ValueError("Installer response has an invalid Content-Range.")
                        start, end, total = map(int, content_range.groups())
                        if start != offset or not start <= end < total or (length is not None and length != end - start + 1):
                            raise ValueError("Installer response Content-Range does not match the requested bytes.")
                        response_end = end + 1
                    elif status == 200:
                        # Some servers ignore Range. Never append a full response.
                        offset = 0
                        total = length if length is not None else declared_size
                        response_end = total
                    else:
                        raise ValueError(f"Unexpected installer HTTP status: {status}.")
                    if declared_size and total != declared_size:
                        raise ValueError("Installer response size does not match GitHub metadata.")
                    downloaded = offset
                    with temporary.open("ab" if offset else "wb") as stream:
                        while True:
                            check_cancelled()
                            block = response.read1(256 * 1024) if hasattr(response, "read1") else response.read(256 * 1024)
                            if not block:
                                break
                            if response_end and downloaded + len(block) > response_end:
                                raise ValueError("Installer exceeds its declared size.")
                            stream.write(block)
                            downloaded += len(block)
                            if progress_callback is not None:
                                progress_callback(downloaded, total)
                    check_cancelled()
                    if total and downloaded != total:
                        raise urllib.error.URLError("Installer download ended before all bytes arrived.")
                return finish()
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                    raise
                if attempt == 2:
                    raise
                # At most 0.75 seconds total backoff, interruptible in 50 ms steps.
                for _ in range(5 * (attempt + 1)):
                    check_cancelled()
                    time.sleep(0.05)


def launch_update_installer(installer: Path, elevate: bool = False, *,
                            install_directory: Path | None = None,
                            resume_path: Path | None = None,
                            wait_pid: int | None = None) -> bool:
    """Launch normally; elevation is an explicit fallback for protected installs."""
    if sys.platform == "win32":
        arguments = "/CLOSEAPPLICATIONS /NOFORCECLOSEAPPLICATIONS /NORESTART"
        if any(value is not None for value in (install_directory, resume_path, wait_pid)):
            if install_directory is None or resume_path is None or type(wait_pid) is not int or wait_pid <= 0:
                raise ValueError("A complete update restart handoff is required.")
            paths = (str(install_directory.resolve()), str(resume_path.resolve()))
            if any(any(c in value for c in '\x00\r\n"') for value in paths):
                raise ValueError("Invalid update installation or recovery path.")
            if not resume_path.is_file():
                raise ValueError("The update recovery file is missing.")
            # Do not relaunch a user's scientific viewer as a different administrator.
            if elevate:
                raise ValueError("Automatic session restoration requires installation as the current user.")
            arguments += (f' /SILENT /SP- /DIR="{paths[0]}"'
                          f' /UPDATESESSION="{paths[1]}" /UPDATEPID={wait_pid}')
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas" if elevate else "open", str(installer),
            arguments, None, 1,
        )
        return int(result) > 32
    launched = QtCore.QProcess.startDetached(str(installer), [])
    return bool(launched[0] if isinstance(launched, tuple) else launched)


class DownloadWorker(QtCore.QThread):
    progress = QtCore.Signal(int, int)

    def __init__(self, asset, destination, parent=None, downloader=download_release_asset):
        super().__init__(parent)
        self.asset, self.destination = asset, destination
        self.downloader = downloader
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.downloader(
                self.asset, self.destination, self.progress.emit,
                self.isInterruptionRequested,
            )
        except Exception as exc:
            self.error = exc


class DownloadDialog(QtWidgets.QDialog):
    def __init__(self, asset, destination, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download update")
        self.setMinimumWidth(440)
        layout = QtWidgets.QVBoxLayout(self)
        self.label = QtWidgets.QLabel("Connecting to GitHub…")
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 0)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(self.reject)
        self.worker = DownloadWorker(asset, destination, self)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    @QtCore.Slot(int, int)
    def _progress(self, done, total):
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(done)
        if not self.worker.isInterruptionRequested():
            self.label.setText(f"Downloaded {done / 1024**2:.1f} / {total / 1024**2:.1f} MiB")

    def reject(self):
        if self.worker.isRunning():
            self.worker.requestInterruption()
            self.label.setText("Cancelling… waiting for the current network read to stop.")
            self.cancel_button.setEnabled(False)
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            event.accept()

    @QtCore.Slot()
    def _finished(self):
        if self.worker.isInterruptionRequested() or self.worker.error is not None:
            super().reject()
        else:
            self.accept()
