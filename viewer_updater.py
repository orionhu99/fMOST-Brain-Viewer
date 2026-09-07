"""GitHub release transport, isolated from viewer state and saving."""
from pathlib import Path
import ctypes
import hashlib
import json
import re
import sys
import urllib.request
import uuid
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


def download_release_asset(asset: dict, destination: Path, progress_callback=None, cancelled=None) -> Path:
    """Download one GitHub release asset atomically and verify its digest."""
    expected = str(asset.get("digest", "")).strip()
    expected_match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", expected)
    if expected_match is None:
        raise ValueError("Installer SHA-256 metadata is missing or invalid.")
    if cancelled is not None and cancelled():
        raise AtlasSetupCancelled("Update download cancelled.")
    url = str(asset["browser_download_url"])
    if not url.startswith("https://github.com/orionhu99/fMOST-Brain-Viewer/releases/download/"):
        raise ValueError("Installer URL must belong to this project's GitHub releases.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
    request = urllib.request.Request(
        str(asset["browser_download_url"]),
        headers={"User-Agent": f"fMOST-Brain-Viewer/{__version__}"},
    )
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=5) as response, temporary.open("wb") as stream:
            total = int(response.headers.get("Content-Length", asset.get("size", 0)))
            downloaded = 0
            while True:
                if cancelled is not None and cancelled():
                    raise AtlasSetupCancelled("Update download cancelled.")
                block = response.read1(256 * 1024) if hasattr(response, "read1") else response.read(256 * 1024)
                if not block:
                    break
                if downloaded + len(block) > int(asset.get("size", 0)) > 0:
                    raise ValueError("Installer exceeds its declared size.")
                stream.write(block)
                digest.update(block)
                downloaded += len(block)
                if progress_callback is not None:
                    progress_callback(downloaded, total)
        if cancelled is not None and cancelled():
            raise AtlasSetupCancelled("Update download cancelled.")
        if digest.hexdigest() != expected_match.group(1).casefold():
            raise ValueError("Downloaded installer SHA-256 does not match GitHub metadata.")
        temporary.replace(destination)
        return destination
    finally:
        if temporary.exists():
            temporary.unlink()


def launch_update_installer(installer: Path, elevate: bool = False) -> bool:
    """Launch normally; elevation is an explicit fallback for protected installs."""
    if sys.platform == "win32":
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas" if elevate else "open", str(installer),
            "/CLOSEAPPLICATIONS /NOFORCECLOSEAPPLICATIONS /NORESTART", None, 1,
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
