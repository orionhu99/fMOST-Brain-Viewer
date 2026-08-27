"""Interactive fMOST SWC and Allen CCF atlas viewer."""

from __future__ import annotations

import colorsys
import ctypes
import csv
import gzip
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import shutil
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import nrrd
import numpy as np
import pyvista as pv
from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets
from pyvistaqt import QtInteractor
from scipy import ndimage

from version import __version__

DATA_ROOT = Path.home()
ATLAS_ROOT = Path()
TEMPLATE_25 = ATLAS_ROOT / "average_template_25.nrrd"
ANNOTATION_10 = ATLAS_ROOT / "annotation_10.nrrd"
CACHE_ROOT = Path()
ATLAS_ANNOTATION_SOURCE = ANNOTATION_10
ATLAS_SIGNATURE = ""
ATLAS_LABEL_MAX = 1500
UNASSIGNED_COLOR = "#888888"
ACTIVE_PROJECT: Path | None = None
REGION_CACHE_VERSION = 3
DEFAULT_BRAIN_OPACITY = 0.20
AXON_TUBE_RADIUS_PER_SIZE_UM = 4.0
AXON_TUBE_SIDES = 8
MAX_HIDDEN_AXON_ACTORS = 64
MAX_HIDDEN_AXON_POINTS = 2_000_000
ATLAS_TEMPLATE_NAME = "average_template_25.nrrd"
ATLAS_ANNOTATION_NAME = "annotation_10.nrrd"
NRRD_DTYPE_CODES = {
    "uint8": "u1", "uchar": "u1", "uint16": "u2", "ushort": "u2",
    "unsigned short": "u2", "int16": "i2", "short": "i2",
    "uint32": "u4", "unsigned int": "u4", "int32": "i4",
    "float": "f4", "double": "f8",
}
ASSET_ROOT = next(
    (
        path for path in (
            Path(__file__).resolve().parent / "assets",
            Path(sys.prefix) / "assets",
        )
        if path.exists()
    ),
    Path(__file__).resolve().parent / "assets",
)
RESOURCE_ROOT = next(
    (
        path for path in (
            Path(__file__).resolve().parent / "resources",
            Path(sys.prefix) / "resources",
        )
        if path.exists()
    ),
    Path(__file__).resolve().parent / "resources",
)
APP_ICON = ASSET_ROOT / "fmost_brain_viewer.ico"
APP_LOGO = ASSET_ROOT / "fmost_brain_logo.png"
ATLAS_MANIFEST_PATH = RESOURCE_ROOT / "atlas_manifest.json"
BUNDLED_ONTOLOGY_PATH = RESOURCE_ROOT / "allen_structure_graph_1.json"
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
CACHE_BASE = LOCAL_APP_DATA / "fMOST Brain Viewer" / "cache" / "atlas"
CACHE_ROOT = CACHE_BASE
LOG_DIR = LOCAL_APP_DATA / "fMOST Brain Viewer" / "logs"
LOG_PATH = LOG_DIR / "fmost-brain-viewer.log"
LOGGER = logging.getLogger("fmost_brain_viewer")
GITHUB_RELEASES_API = (
    "https://api.github.com/repos/orionhu99/fMOST-Brain-Viewer/releases"
)
EXPORT_CONTENT_LABELS = {
    "brain": "3D brain atlas",
    "slice": "Coronal annotation slice",
    "somas": "Soma locations",
    "axons": "Selected neurons / axons",
    "regions": "Highlighted brain regions",
    "grid": "Coordinate grid and bounds",
    "axes": "Orientation axes",
    "legend": "Brain-region legend",
}

class AtlasSetupCancelled(Exception):
    pass


def derived_cache_root(signature: str) -> Path:
    """Return the per-user cache namespace for one exact atlas identity."""
    return CACHE_BASE / f"derived_{signature[:20]}"


def valid_camera_position(camera) -> bool:
    """Accept only finite, non-degenerate PyVista camera triplets."""
    try:
        vectors = np.asarray(camera, dtype=float)
    except (TypeError, ValueError):
        return False
    if vectors.shape != (3, 3) or not np.isfinite(vectors).all():
        return False
    direction = vectors[1] - vectors[0]
    view_up = vectors[2]
    return (
        np.linalg.norm(direction) > 1e-9
        and np.linalg.norm(view_up) > 1e-9
        and np.linalg.norm(np.cross(direction, view_up)) > 1e-9
    )


def region_search_matches(ontology: dict[int, dict], query: str) -> list[int]:
    """Rank atlas matches predictably without depending on dictionary order."""
    text = query.strip().casefold()
    if not text:
        return []

    def rank(entry):
        region_id, info = entry
        acronym = str(info.get("acronym", "")).casefold()
        name = str(info.get("name", "")).casefold()
        words = re.findall(r"[\w]+", name)
        if text.isdigit() and int(text) == region_id:
            category = 0
        elif text == acronym:
            category = 1
        elif acronym.startswith(text):
            category = 2
        elif any(word.startswith(text) for word in words):
            category = 3
        elif text in acronym or text in name:
            category = 4
        else:
            return None
        return category, acronym, name, region_id

    ranked = []
    for entry in ontology.items():
        key = rank(entry)
        if key is not None:
            ranked.append((key, entry[0]))
    ranked.sort()
    return [region_id for _key, region_id in ranked]


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


def download_release_asset(asset: dict, destination: Path, progress_callback=None) -> Path:
    """Download one GitHub release asset atomically and verify its digest."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        str(asset["browser_download_url"]),
        headers={"User-Agent": f"fMOST-Brain-Viewer/{__version__}"},
    )
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
            total = int(response.headers.get("Content-Length", asset.get("size", 0)))
            downloaded = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                stream.write(block)
                digest.update(block)
                downloaded += len(block)
                if progress_callback is not None:
                    progress_callback(downloaded, total)
        expected = str(asset.get("digest", ""))
        if expected.startswith("sha256:") and digest.hexdigest() != expected[7:].casefold():
            raise ValueError("Downloaded installer SHA-256 does not match GitHub metadata.")
        temporary.replace(destination)
        return destination
    finally:
        if temporary.exists():
            temporary.unlink()


def launch_update_installer(installer: Path) -> bool:
    """Start an update with the permissions required to replace an old install."""
    if sys.platform == "win32":
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(installer),
            "/CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS",
            None,
            1,
        )
        return int(result) > 32
    launched = QtCore.QProcess.startDetached(str(installer), [])
    return bool(launched[0] if isinstance(launched, tuple) else launched)


def configure_logging() -> Path:
    """Configure a bounded per-user log file and return its location."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not LOGGER.handlers:
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
    LOGGER.info("Starting fMOST Brain Viewer %s", __version__)
    return LOG_PATH


def _global_exception_hook(exception_type, exception, exception_traceback) -> None:
    LOGGER.critical(
        "Unhandled exception",
        exc_info=(exception_type, exception, exception_traceback),
    )
    app = QtWidgets.QApplication.instance()
    if app is not None:
        QtWidgets.QMessageBox.critical(
            None,
            "Unexpected error",
            "An unexpected error occurred. Details were written to:\n"
            f"{LOG_PATH}\n\n{exception}",
        )


def load_atlas_manifest() -> dict:
    try:
        manifest = json.loads(ATLAS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"The bundled atlas manifest is missing or invalid: {ATLAS_MANIFEST_PATH}"
        ) from exc
    files = manifest.get("files", {})
    if not all(name in files for name in (ATLAS_TEMPLATE_NAME, ATLAS_ANNOTATION_NAME)):
        raise RuntimeError("The bundled atlas manifest does not describe both CCF files.")
    ontology = manifest.get("ontology", {})
    if not all(key in ontology for key in ("file", "size", "sha256")):
        raise RuntimeError("The bundled atlas manifest does not describe its ontology.")
    return manifest


def _sha256_file(path: Path, progress=None) -> str:
    digest = hashlib.sha256()
    size = max(path.stat().st_size, 1)
    read_bytes = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            read_bytes += len(chunk)
            if progress is not None:
                progress.setValue(min(round(read_bytes / size * 1000), 999))
                progress.setLabelText(f"Verifying {path.name}...")
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    raise AtlasSetupCancelled()
    return digest.hexdigest()


def _manifest_file_is_valid(path: Path, specification: dict, progress=None) -> bool:
    if not path.is_file() or path.stat().st_size != int(specification["size"]):
        return False
    return _sha256_file(path, progress).casefold() == str(
        specification["sha256"]
    ).casefold()


def _file_change_token(path: Path, stat_result=None) -> str:
    """Return the filesystem change token, including Windows FILE_BASIC_INFO."""
    stat_result = stat_result or path.stat()
    if os.name != "nt":
        return str(stat_result.st_ctime_ns)
    try:
        import ctypes
        from ctypes import wintypes

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_longlong),
                ("LastAccessTime", ctypes.c_longlong),
                ("LastWriteTime", ctypes.c_longlong),
                ("ChangeTime", ctypes.c_longlong),
                ("FileAttributes", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path),
            0x80,  # FILE_READ_ATTRIBUTES
            0x1 | 0x2 | 0x4,  # share read, write, and delete
            None,
            3,  # OPEN_EXISTING
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed")
        try:
            information = FileBasicInfo()
            get_information = kernel32.GetFileInformationByHandleEx
            get_information.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.LPVOID,
                wintypes.DWORD,
            ]
            get_information.restype = wintypes.BOOL
            if not get_information(
                handle, 0, ctypes.byref(information), ctypes.sizeof(information)
            ):
                raise OSError(
                    ctypes.get_last_error(), "GetFileInformationByHandleEx failed"
                )
            return str(information.ChangeTime)
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return f"stat:{stat_result.st_ctime_ns}"


def _quick_file_fingerprint(path: Path) -> dict:
    """Cheaply validate a cached full hash using stat data and spaced samples."""
    before = path.stat()
    before_change_token = _file_change_token(path, before)
    size = before.st_size
    chunk_size = 512 * 1024
    maximum_offset = max(0, size - chunk_size)
    offsets = sorted({
        0,
        min(maximum_offset, max(0, size // 4 - chunk_size // 2)),
        min(maximum_offset, max(0, size // 2 - chunk_size // 2)),
        min(maximum_offset, max(0, size * 3 // 4 - chunk_size // 2)),
        maximum_offset,
    })
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as stream:
        for offset in offsets:
            stream.seek(offset)
            digest.update(offset.to_bytes(8, "little", signed=False))
            digest.update(stream.read(chunk_size))
    after = path.stat()
    after_change_token = _file_change_token(path, after)
    if (
        (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        or before_change_token != after_change_token
    ):
        raise OSError(f"Atlas file changed while its identity was being read: {path.name}")
    return {
        "size": size,
        "mtime_ns": after.st_mtime_ns,
        "change_token": after_change_token,
        "sample_sha256": digest.hexdigest(),
    }


def _atlas_file_identities(folder: Path) -> dict[str, dict]:
    cache_root = folder / "viewer_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / "atlas_file_identities_v1.json"
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_records = payload.get("files", {}) if payload.get("version") == 1 else {}
    except (OSError, json.JSONDecodeError, AttributeError):
        cached_records = {}

    records: dict[str, dict] = {}
    identities: dict[str, dict] = {}
    changed = False
    for name in (ATLAS_TEMPLATE_NAME, ATLAS_ANNOTATION_NAME):
        path = folder / name
        quick = _quick_file_fingerprint(path)
        cached = cached_records.get(name, {})
        cached_sha = str(cached.get("sha256", "")).casefold()
        if cached.get("fingerprint") == quick and re.fullmatch(r"[0-9a-f]{64}", cached_sha):
            sha256 = cached_sha
        else:
            sha256 = _sha256_file(path)
            quick_after = _quick_file_fingerprint(path)
            if quick_after != quick:
                raise OSError(f"Atlas file changed during SHA256 calculation: {name}")
            quick = quick_after
            changed = True
        records[name] = {"fingerprint": quick, "sha256": sha256}
        identities[name] = {"size": quick["size"], "sha256": sha256}

    if changed or records != cached_records:
        temporary = cache_path.with_suffix(cache_path.suffix + ".part")
        temporary.write_text(
            json.dumps({"version": 1, "files": records}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(cache_path)
    return identities


def _atlas_signature_from_identities(manifest: dict, files: dict[str, dict]) -> str:
    identity = {
        "atlas": manifest.get("atlas", "Allen Mouse Brain CCF"),
        "release": manifest.get("release", "CCFv3 2017"),
        "files": files,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def atlas_signature(folder: Path) -> str:
    return _atlas_signature_from_identities(
        load_atlas_manifest(), _atlas_file_identities(folder)
    )


def _raw_nrrd_is_complete(path: Path, header: dict) -> bool:
    if str(header.get("encoding", "raw")).lower() != "raw":
        return True
    dtype_name = str(header.get("type", "")).lower()
    if dtype_name not in NRRD_DTYPE_CODES:
        return False
    with path.open("rb") as stream:
        prefix = stream.read(1024 * 1024)
    match = re.search(br"\r?\n\r?\n", prefix)
    if not match:
        return False
    payload_bytes = int(np.prod(header.get("sizes", ()))) * np.dtype(
        NRRD_DTYPE_CODES[dtype_name]
    ).itemsize
    return path.stat().st_size == match.end() + payload_bytes


def validate_atlas_directory(
    folder: Path, require_manifest_hash: bool = False, progress=None
) -> tuple[Path, Path]:
    """Validate the two Allen CCF volumes without loading voxel data."""
    manifest = load_atlas_manifest()
    template = folder / ATLAS_TEMPLATE_NAME
    annotation = folder / ATLAS_ANNOTATION_NAME
    missing = [path.name for path in (template, annotation) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing atlas file(s): " + ", ".join(missing) + f"\n\nFolder: {folder}"
        )
    try:
        template_header = nrrd.read_header(str(template))
        annotation_header = nrrd.read_header(str(annotation))
    except Exception as exc:
        raise ValueError(f"Cannot read the atlas NRRD header:\n{exc}") from exc

    template_sizes = tuple(map(int, template_header.get("sizes", ())))
    annotation_sizes = tuple(map(int, annotation_header.get("sizes", ())))
    template_expected = tuple(
        map(int, manifest["files"][ATLAS_TEMPLATE_NAME]["dimensions"])
    )
    annotation_expected = tuple(
        map(int, manifest["files"][ATLAS_ANNOTATION_NAME]["dimensions"])
    )
    if template_sizes not in (template_expected, template_expected[::-1]):
        raise ValueError(
            f"{ATLAS_TEMPLATE_NAME} has dimensions {template_sizes}; "
            "expected the 25 um Allen CCF template dimensions."
        )
    if annotation_sizes not in (annotation_expected, annotation_expected[::-1]):
        raise ValueError(
            f"{ATLAS_ANNOTATION_NAME} has dimensions {annotation_sizes}; "
            "expected the 10 um Allen CCF annotation dimensions."
        )
    template_type = str(template_header.get("type", "")).lower()
    if template_type not in ("uint16", "ushort", "unsigned short"):
        raise ValueError(
            f"{ATLAS_TEMPLATE_NAME} uses unsupported voxel type: {template_type}"
        )
    annotation_type = str(annotation_header.get("type", "")).lower()
    if annotation_type not in (
        "uint16", "ushort", "unsigned short", "uint32", "unsigned int"
    ):
        raise ValueError(
            f"{ATLAS_ANNOTATION_NAME} uses unsupported voxel type: {annotation_type}"
        )
    for path, header in ((template, template_header), (annotation, annotation_header)):
        if not _raw_nrrd_is_complete(path, header):
            raise ValueError(f"{path.name} is incomplete or has an invalid raw payload size.")
    for header, expected, label in (
        (template_header, 25.0, ATLAS_TEMPLATE_NAME),
        (annotation_header, 10.0, ATLAS_ANNOTATION_NAME),
    ):
        directions = np.asarray(header.get("space directions", ()), dtype=float)
        expected_directions = np.eye(3, dtype=float) * expected
        if directions.shape != (3, 3) or not np.allclose(
            directions, expected_directions, atol=0.01
        ):
            raise ValueError(
                f"{label} has unsupported space directions {directions.tolist()}; "
                f"expected positive axis-aligned {expected} um directions."
            )
        origin = np.asarray(header.get("space origin", (0.0, 0.0, 0.0)), dtype=float)
        if origin.shape != (3,) or not np.allclose(origin, 0.0, atol=0.01):
            raise ValueError(
                f"{label} has unsupported space origin {origin.tolist()}; expected (0, 0, 0)."
            )
    if require_manifest_hash:
        for path in (template, annotation):
            if not _manifest_file_is_valid(
                path, manifest["files"][path.name], progress
            ):
                raise ValueError(f"{path.name} failed the official file SHA256 check.")
    return template, annotation


def _progress_dialog(title: str, parent=None) -> QtWidgets.QProgressDialog:
    progress = QtWidgets.QProgressDialog("Starting...", "Cancel", 0, 1000, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.show()
    QtWidgets.QApplication.processEvents()
    return progress


def _download_file(
    url: str, destination: Path, progress, label: str, specification: dict
) -> None:
    part = destination.with_suffix(destination.suffix + ".part")
    expected_size = int(specification["size"])
    resumed = part.stat().st_size if part.exists() else 0
    if resumed == expected_size:
        if _manifest_file_is_valid(part, specification, progress):
            part.replace(destination)
            progress.setValue(1000)
            return
        part.unlink()
        resumed = 0
    if resumed > expected_size:
        part.unlink()
        resumed = 0
    headers = {"User-Agent": f"fMOST-Brain-Viewer/{__version__}"}
    if resumed:
        headers["Range"] = f"bytes={resumed}-"
    request = urllib.request.Request(
        url, headers=headers
    )
    started = time.monotonic()
    downloaded = resumed
    with urllib.request.urlopen(request, timeout=60) as response:
        partial_response = getattr(response, "status", None) == 206
        if resumed and not partial_response:
            resumed = 0
            downloaded = 0
        mode = "ab" if resumed and partial_response else "wb"
        with part.open(mode) as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                elapsed = max(time.monotonic() - started, 0.01)
                percent = downloaded / expected_size
                progress.setValue(min(round(percent * 1000), 999))
                progress.setLabelText(
                    f"Downloading {label}\n"
                    f"{downloaded / 1024**2:.1f} / {expected_size / 1024**2:.1f} MB   "
                    f"{downloaded / elapsed / 1024**2:.1f} MB/s"
                )
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    raise AtlasSetupCancelled()
    if downloaded != expected_size:
        raise IOError(
            f"Incomplete download: received {downloaded} of {expected_size} bytes"
        )
    progress.setValue(0)
    if not _manifest_file_is_valid(part, specification, progress):
        raise IOError(
            f"{label} does not match the CCFv3 2017 identity pinned by this "
            "application release. The Allen 'current-release' target may have "
            "changed. Keep the .part file, check for a newer fMOST Brain Viewer "
            "release, or choose an existing verified atlas folder."
        )
    part.replace(destination)
    progress.setValue(1000)


def _atlas_download_conflicts(folder: Path, manifest: dict | None = None) -> list[Path]:
    """Return original files that an official download must never overwrite."""
    manifest = manifest or load_atlas_manifest()
    return [
        path
        for name, specification in manifest["files"].items()
        if (path := folder / name).exists()
        and not _manifest_file_is_valid(path, specification)
    ]


def download_official_atlas(folder: Path, parent=None) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / ".fmost-write-test"
    try:
        probe.write_bytes(b"")
    except OSError as exc:
        raise OSError(f"The atlas folder is not writable: {folder}") from exc
    finally:
        probe.unlink(missing_ok=True)
    manifest = load_atlas_manifest()
    conflicts = _atlas_download_conflicts(folder, manifest)
    if conflicts:
        names = ", ".join(path.name for path in conflicts)
        raise FileExistsError(
            "The selected folder already contains atlas file(s) that do not match "
            f"this release's pinned official identity: {names}. To protect existing "
            "NRRD data, the viewer will not overwrite them. Choose a new or empty "
            "download folder."
        )
    required = sum(
        max(
            0,
            int(specification["size"])
            - (folder / f"{name}.part").stat().st_size
            if (folder / f"{name}.part").is_file()
            else int(specification["size"]),
        )
        for name, specification in manifest["files"].items()
        if not _manifest_file_is_valid(folder / name, specification)
    )
    annotation_specification = manifest["files"][ATLAS_ANNOTATION_NAME]
    raw_annotation_bytes = int(
        np.prod(annotation_specification["dimensions"])
    ) * np.dtype("u4").itemsize
    prepared_annotation = folder / "viewer_cache" / "annotation_10_raw.nrrd"
    if not prepared_annotation.is_file() or prepared_annotation.stat().st_size < raw_annotation_bytes:
        required += raw_annotation_bytes
    if shutil.disk_usage(folder).free < required + 512 * 1024**2:
        raise OSError(
            "Not enough free disk space for the Allen CCF download and annotation "
            f"preparation. Required free space: {(required + 512 * 1024**2) / 1024**3:.1f} GB."
        )
    progress = _progress_dialog("Download Allen CCF", parent)
    try:
        for label in (ATLAS_TEMPLATE_NAME, ATLAS_ANNOTATION_NAME):
            destination = folder / label
            specification = manifest["files"][label]
            url = str(specification["url"])
            if _manifest_file_is_valid(destination, specification):
                continue
            progress.setValue(0)
            for attempt in range(1, 4):
                try:
                    _download_file(url, destination, progress, label, specification)
                    break
                except AtlasSetupCancelled:
                    raise
                except Exception:
                    LOGGER.exception("Atlas download attempt %s failed for %s", attempt, label)
                    if attempt == 3:
                        raise
                    progress.setLabelText(
                        f"Download interrupted. Retrying {label} ({attempt + 1}/3)..."
                    )
                    QtWidgets.QApplication.processEvents()
                    time.sleep(1)
        return validate_atlas_directory(folder, require_manifest_hash=True, progress=progress)
    finally:
        progress.close()


def prepare_annotation_for_memmap(
    annotation: Path,
    atlas_root: Path,
    parent=None,
    source_sha256: str | None = None,
) -> Path:
    """Stream-decompress an official gzip NRRD into a raw cache for random access."""
    header = nrrd.read_header(str(annotation))
    encoding = str(header.get("encoding", "raw")).lower()
    if encoding in ("raw", "txt", "text", "ascii"):
        if encoding != "raw":
            raise ValueError("The annotation NRRD must use raw or gzip encoding.")
        return annotation
    if encoding not in ("gzip", "gz"):
        raise ValueError(f"Unsupported annotation NRRD encoding: {encoding}")

    dtype_name = str(header.get("type", "")).lower()
    if dtype_name not in NRRD_DTYPE_CODES:
        raise ValueError(f"Unsupported annotation NRRD type: {dtype_name}")
    expected_bytes = int(np.prod(header["sizes"])) * np.dtype(
        NRRD_DTYPE_CODES[dtype_name]
    ).itemsize

    cache_root = atlas_root / "viewer_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    raw_path = cache_root / "annotation_10_raw.nrrd"
    identity_path = cache_root / "annotation_10_raw.identity.json"
    source_identity = {
        "source_size": annotation.stat().st_size,
        "source_sha256": source_sha256 or _sha256_file(annotation),
    }
    cached_identity = None
    if identity_path.is_file():
        try:
            cached_identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_identity = None
    if raw_path.is_file() and cached_identity == source_identity:
        raw_header = nrrd.read_header(str(raw_path))
        if tuple(map(int, raw_header.get("sizes", ()))) in (
            (1140, 800, 1320), (1320, 800, 1140)
        ) and _raw_nrrd_is_complete(raw_path, raw_header):
            return raw_path
    free_bytes = shutil.disk_usage(cache_root).free
    if free_bytes < expected_bytes + 512 * 1024**2:
        raise OSError(
            f"Not enough free disk space to prepare the 10 um annotation.\n"
            f"Required: {(expected_bytes + 512 * 1024**2) / 1024**3:.1f} GB\n"
            f"Available: {free_bytes / 1024**3:.1f} GB"
        )

    progress = _progress_dialog("Prepare Allen CCF annotation", parent)
    part = raw_path.with_suffix(raw_path.suffix + ".part")
    identity_part = identity_path.with_suffix(identity_path.suffix + ".part")
    if part.exists():
        part.unlink()
    if identity_part.exists():
        identity_part.unlink()
    started = time.monotonic()
    written = 0
    try:
        with annotation.open("rb") as source:
            prefix = source.read(1024 * 1024)
            match = re.search(br"\r?\n\r?\n", prefix)
            if not match:
                raise ValueError("Cannot locate the annotation NRRD header terminator.")
            header_bytes = prefix[:match.start()]
            header_bytes = re.sub(
                br"(?im)^encoding:\s*(gzip|gz)\s*$", b"encoding: raw", header_bytes
            )
            source.seek(match.end())
            with part.open("wb") as output:
                output.write(header_bytes + b"\n\n")
                with gzip.GzipFile(fileobj=source, mode="rb") as payload:
                    while True:
                        chunk = payload.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        written += len(chunk)
                        elapsed = max(time.monotonic() - started, 0.01)
                        progress.setValue(min(round(written / expected_bytes * 1000), 999))
                        progress.setLabelText(
                            "Preparing annotation for fast coronal slicing\n"
                            f"{written / 1024**3:.2f} / {expected_bytes / 1024**3:.2f} GB   "
                            f"{written / elapsed / 1024**2:.1f} MB/s"
                        )
                        QtWidgets.QApplication.processEvents()
                        if progress.wasCanceled():
                            raise AtlasSetupCancelled()
        if written != expected_bytes:
            raise IOError(
                f"Decompressed annotation size mismatch: {written} != {expected_bytes}"
            )
        part.replace(raw_path)
        identity_part.write_text(
            json.dumps(source_identity, sort_keys=True, indent=2), encoding="utf-8"
        )
        identity_part.replace(identity_path)
        progress.setValue(1000)
        return raw_path
    except Exception:
        if part.exists():
            part.unlink()
        if identity_part.exists():
            identity_part.unlink()
        raise
    finally:
        progress.close()


def activate_atlas(folder: Path, parent=None) -> None:
    global ATLAS_ROOT, TEMPLATE_25, ANNOTATION_10, ATLAS_ANNOTATION_SOURCE
    global ATLAS_SIGNATURE, CACHE_ROOT
    template, annotation = validate_atlas_directory(folder)
    file_identities = _atlas_file_identities(folder)
    signature = _atlas_signature_from_identities(load_atlas_manifest(), file_identities)
    annotation_sha256 = file_identities[ATLAS_ANNOTATION_NAME]["sha256"]
    prepared_annotation = prepare_annotation_for_memmap(
        annotation, folder, parent, source_sha256=str(annotation_sha256).casefold()
    )
    ATLAS_ROOT = folder
    TEMPLATE_25 = template
    ANNOTATION_10 = prepared_annotation
    ATLAS_ANNOTATION_SOURCE = annotation
    ATLAS_SIGNATURE = signature
    # All derived surfaces/libraries live in an atlas-identity namespace. This
    # prevents a replaced annotation from reusing an older VTP merely because
    # its size and timestamp happen to match.
    CACHE_ROOT = derived_cache_root(signature)
    settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
    settings.setValue("atlas_directory", str(folder))
    settings.setValue("atlas_signature", signature)
    settings.sync()
    LOGGER.info("Activated Allen CCF atlas at %s (signature %s)", folder, signature)


def brain_paths(brain_id: str) -> tuple[Path, Path, Path]:
    project = ACTIVE_PROJECT or DATA_ROOT
    return (
        project / f"{brain_id}_reg_800",
        project / brain_id / "soma location" / f"{brain_id}_root_reg.swc",
        project / "viewer_config.json",
    )


@dataclass
class BrainDataset:
    brain_id: str
    project: Path
    key: str
    axon_dir: Path
    soma_path: Path
    soma_color: str
    enabled: bool = True
    soma_ids: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))
    soma_points: np.ndarray = field(default_factory=lambda: np.empty((0, 3), dtype=float))
    soma_point_by_id: dict[int, np.ndarray] = field(default_factory=dict)
    soma_regions: dict[int, int | None] = field(default_factory=dict)
    outside_soma_count: int = 0
    manual_region_path: Path | None = None
    manual_region_applied: int = 0
    manual_region_ignored: int = 0
    matched_axon_count: int = 0
    unmatched_axon_count: int = 0
    duplicate_axon_count: int = 0
    soma_mesh: object | None = None
    soma_actor: object | None = None


def project_paths(brain_id: str, project: Path) -> tuple[Path, Path, Path]:
    return (
        project / f"{brain_id}_reg_800",
        project / brain_id / "soma location" / f"{brain_id}_root_reg.swc",
        project / "viewer_config.json",
    )


def dataset_key(brain_id: str, project: Path) -> str:
    return f"{brain_id}|{str(project.resolve()).casefold()}"


def configure_dataset(selected_file: Path) -> str:
    """Infer a dataset ID and project root from any file inside the project."""
    resolved = selected_file.resolve()
    for folder in [resolved.parent, *resolved.parents[:5]]:
        for brain_id, project in discover_brain_projects(folder):
            axon_dir, soma_path, _ = project_paths(brain_id, project)
            try:
                inside_axon = resolved.is_relative_to(axon_dir.resolve())
            except ValueError:
                inside_axon = False
            if resolved == soma_path.resolve() or inside_axon:
                activate_project(project, brain_id)
                return brain_id
    raise FileNotFoundError(
        "Cannot locate a complete dataset project containing the selected file."
    )


def discover_brain_projects(selected_folder: Path) -> list[tuple[str, Path]]:
    """Find complete dataset projects from a project or any nearby folder."""
    folders: set[Path] = set()
    current = selected_folder.resolve()
    folders.add(current)
    for parent in list(current.parents)[:4]:
        folders.add(parent)
    try:
        for child in current.iterdir():
            if child.is_dir() and any(child.glob("*_reg_800")):
                folders.add(child)
    except OSError:
        pass

    found: dict[tuple[str, str], tuple[str, Path]] = {}
    for folder in folders:
        try:
            axon_dirs = [path for path in folder.glob("*_reg_800") if path.is_dir()]
        except OSError:
            continue
        for axon_dir in axon_dirs:
            match = re.fullmatch(r"(.+)_reg_800", axon_dir.name, flags=re.IGNORECASE)
            if match is None:
                continue
            brain_id = match.group(1).strip()
            if not brain_id:
                continue
            soma = folder / brain_id / "soma location" / f"{brain_id}_root_reg.swc"
            if soma.is_file() and any(axon_dir.glob("*.swc")):
                found[(brain_id, str(folder).casefold())] = (brain_id, folder)
    return sorted(found.values(), key=lambda item: (item[0], str(item[1])))


def activate_project(project: Path, brain_id: str) -> None:
    """Configure the selected brain project independently of the shared atlas."""
    global DATA_ROOT, ACTIVE_PROJECT
    ACTIVE_PROJECT = project
    DATA_ROOT = project.parent


def read_swc_with_ids(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    parsed_rows = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            text = line.replace("\x00", "").strip()
            if not text or text.startswith("#"):
                continue
            fields = text.split()
            if len(fields) < 7:
                continue
            try:
                parsed_rows.append([float(value) for value in fields[:7]])
            except ValueError as exc:
                raise ValueError(f"Invalid SWC row in {path}, line {line_number}") from exc
    if not parsed_rows:
        raise ValueError(f"No valid seven-column SWC rows found: {path}")
    rows = np.asarray(parsed_rows, dtype=float)
    if not np.isfinite(rows).all():
        raise ValueError(f"SWC contains a non-finite value: {path}")
    if not np.equal(rows[:, 0], np.rint(rows[:, 0])).all():
        raise ValueError(f"SWC node IDs must be integers: {path}")
    if not np.equal(rows[:, 6], np.rint(rows[:, 6])).all():
        raise ValueError(f"SWC parent IDs must be integers: {path}")
    ids = np.rint(rows[:, 0]).astype(np.int64)
    if len(np.unique(ids)) != len(ids):
        duplicates = sorted(
            int(node_id) for node_id, count in Counter(map(int, ids)).items() if count > 1
        )
        preview = ", ".join(map(str, duplicates[:8]))
        suffix = "..." if len(duplicates) > 8 else ""
        raise ValueError(f"SWC contains duplicate node ID(s) {preview}{suffix}: {path}")
    points = rows[:, 2:5].astype(np.float32)
    parents = np.rint(rows[:, 6]).astype(np.int64)
    index_by_id = {node_id: index for index, node_id in enumerate(ids)}
    edges = np.asarray(
        [
            (index_by_id[parent], index)
            for index, parent in enumerate(parents)
            if parent in index_by_id
        ],
        dtype=np.int64,
    )
    return ids, points, edges


def read_swc(path: Path) -> tuple[np.ndarray, np.ndarray]:
    _, points, edges = read_swc_with_ids(path)
    return points, edges


def line_mesh(points: np.ndarray, edges: np.ndarray) -> pv.PolyData:
    # Construct the topology explicitly. ``pv.PolyData(points)`` also creates
    # one vertex cell per SWC node, which renders as fixed-size square markers.
    mesh = pv.PolyData()
    mesh.points = points
    if len(edges):
        adjacency = [[] for _ in range(len(points))]
        for first, second in edges:
            adjacency[int(first)].append(int(second))
            adjacency[int(second)].append(int(first))
        unused = {tuple(sorted(map(int, edge))) for edge in edges}
        paths = []
        starts = [index for index, neighbors in enumerate(adjacency) if len(neighbors) != 2]
        for start in starts:
            for neighbor in adjacency[start]:
                edge = tuple(sorted((start, neighbor)))
                if edge not in unused:
                    continue
                path = [start, neighbor]
                unused.remove(edge)
                previous, current = start, neighbor
                while len(adjacency[current]) == 2:
                    following = next(node for node in adjacency[current] if node != previous)
                    edge = tuple(sorted((current, following)))
                    if edge not in unused:
                        break
                    path.append(following)
                    unused.remove(edge)
                    previous, current = current, following
                paths.append(path)
        for first, second in list(unused):
            paths.append([first, second])
        mesh.lines = np.concatenate(
            [np.asarray([len(path), *path], dtype=np.int64) for path in paths]
        )
    return mesh


def axon_tube_mesh(points: np.ndarray, edges: np.ndarray, size: float) -> pv.PolyData:
    centerlines = line_mesh(points, edges)
    if size <= 0:
        return centerlines
    return centerlines.tube(
        radius=float(size) * AXON_TUBE_RADIUS_PER_SIZE_UM,
        n_sides=AXON_TUBE_SIDES,
        capping=True,
    )


def load_volume(path: Path) -> pv.ImageData:
    data, header = nrrd.read(str(path), index_order="F")
    directions = np.asarray(header["space directions"], dtype=float)
    spacing = np.linalg.norm(directions, axis=1)
    if data.shape == (528, 320, 456):
        data = data.transpose(2, 1, 0)
        spacing = spacing[::-1]
    origin = np.asarray(header.get("space origin", (0, 0, 0)), dtype=float)
    grid = pv.ImageData(dimensions=data.shape, spacing=spacing, origin=origin)
    grid.point_data["intensity"] = data.ravel(order="F")
    return grid


class RawNrrdMemmap:
    """Memory-map an inline, raw, 3-D NRRD without loading the full file."""

    TYPE_MAP = NRRD_DTYPE_CODES

    def __init__(self, path: Path):
        self.path = path
        with path.open("rb") as stream:
            prefix = stream.read(65536)
        match = re.search(br"\r?\n\r?\n", prefix)
        if not match:
            raise ValueError(f"Cannot find NRRD header terminator: {path}")
        fields: dict[str, str] = {}
        for line in prefix[: match.start()].decode("ascii").splitlines():
            if ":" in line and not line.startswith("#"):
                key, value = line.split(":", 1)
                fields[key.strip().lower()] = value.strip()
        if fields.get("encoding", "raw").lower() != "raw":
            raise ValueError("The annotation NRRD must use raw encoding.")
        self.storage_shape = tuple(int(value) for value in fields["sizes"].split())
        self.allen_axis_order = self.storage_shape == (1320, 800, 1140)
        self.shape = (
            (self.storage_shape[2], self.storage_shape[1], self.storage_shape[0])
            if self.allen_axis_order else self.storage_shape
        )
        dtype_code = self.TYPE_MAP[fields["type"].lower()]
        byte_order = ">" if fields.get("endian", sys.byteorder).lower() == "big" else "<"
        self.dtype = np.dtype(byte_order + dtype_code)
        self.direct_atlas_ids = self.dtype.itemsize >= 4
        directions = re.findall(r"\(([^)]+)\)", fields["space directions"])
        storage_spacing = tuple(
            float(np.linalg.norm([float(v) for v in direction.split(",")]))
            for direction in directions
        )
        self.spacing = (
            (storage_spacing[2], storage_spacing[1], storage_spacing[0])
            if self.allen_axis_order else storage_spacing
        )
        self.data = np.memmap(
            path, mode="r", dtype=self.dtype, offset=match.end(),
            shape=self.storage_shape, order="F",
        )

    def coronal(self, index: int) -> np.ndarray:
        if self.allen_axis_order:
            source = np.asarray(self.data[index, :, :]).T
        else:
            source = np.asarray(self.data[:, :, index])
        return np.array(source, dtype=self.dtype.newbyteorder("="), copy=True)

    def value_at(self, world_index: np.ndarray) -> int:
        x, y, z = map(int, world_index)
        storage_index = (z, y, x) if self.allen_axis_order else (x, y, z)
        return int(self.data[storage_index])

    def world_crop(self, low: np.ndarray, high: np.ndarray) -> np.ndarray:
        if self.allen_axis_order:
            return np.asarray(
                self.data[low[2]:high[2], low[1]:high[1], low[0]:high[0]]
            ).transpose(2, 1, 0)
        return np.asarray(
            self.data[low[0]:high[0], low[1]:high[1], low[2]:high[2]]
        )

    def world_labels(self, step: int) -> np.ndarray:
        labels = np.asarray(self.data[::step, ::step, ::step])
        return labels.transpose(2, 1, 0) if self.allen_axis_order else labels

    def atlas_id(self, stored_value: int) -> int:
        return stored_value if self.direct_atlas_ids else encoded_to_atlas_id(stored_value)

    def stored_value(self, atlas_id: int) -> int:
        return atlas_id if self.direct_atlas_ids else atlas_id_to_encoded(atlas_id)

    def is_background(self, stored_value: int) -> bool:
        return stored_value == 0 or (not self.direct_atlas_ids and stored_value == 65535)


def encoded_to_atlas_id(value: int) -> int:
    return round(value * ATLAS_LABEL_MAX / 65535)


def atlas_id_to_encoded(atlas_id: int) -> int:
    return round(atlas_id * 65535 / ATLAS_LABEL_MAX)


def classify_somas(
    annotation: RawNrrdMemmap,
    soma_ids: np.ndarray,
    soma_points: np.ndarray,
) -> tuple[dict[int, int | None], int]:
    result: dict[int, int | None] = {}
    outside_count = 0
    shape = np.asarray(annotation.shape)
    spacing = np.asarray(annotation.spacing)
    for soma_id, point in zip(soma_ids, soma_points):
        index = np.floor(point / spacing).astype(int)
        if np.any(index < 0) or np.any(index >= shape):
            result[int(soma_id)] = None
            outside_count += 1
            continue
        raw = annotation.value_at(index)
        if annotation.is_background(raw):
            low = np.maximum(index - 2, 0)
            high = np.minimum(index + 3, shape)
            values = annotation.world_crop(low, high).ravel()
            if annotation.direct_atlas_ids:
                valid = values[values != 0]
            else:
                valid = values[(values != 0) & (values != 65535)]
            raw = Counter(map(int, valid)).most_common(1)[0][0] if len(valid) else 0
        result[int(soma_id)] = annotation.atlas_id(raw) if raw else None
    return result, outside_count


def manual_soma_region_csv(brain_id: str, project: Path) -> Path | None:
    folder = project / brain_id / "soma location"
    expected_name = f"soma location_{brain_id}.csv".casefold()
    try:
        return next(
            (path for path in folder.glob("*.csv") if path.name.casefold() == expected_name),
            None,
        )
    except OSError:
        return None


def load_manual_soma_regions(
    path: Path,
    ontology: dict[int, dict],
    valid_soma_ids: set[int],
) -> tuple[dict[int, int | None], int]:
    """Read soma ID-to-region overrides; unknown or malformed rows are ignored."""
    acronym_ids = {
        str(info.get("acronym", "")).strip().casefold(): region_id
        for region_id, info in ontology.items()
    }
    unassigned_labels = {
        "background", "out", "outside", "unassigned", "none", "na", "n/a",
    }
    overrides: dict[int, int | None] = {}
    ignored = 0
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) < 2:
                if any(field.strip() for field in row):
                    ignored += 1
                continue
            try:
                soma_id = int(row[0].strip())
            except ValueError:
                # Permit a conventional header row without reporting it as invalid.
                if row[0].strip().casefold() not in {"id", "soma id", "soma_id"}:
                    ignored += 1
                continue
            if soma_id not in valid_soma_ids:
                ignored += 1
                continue
            label = row[1].strip()
            normalized = re.sub(
                r"(?:[_-](?:l|r|left|right))$", "", label, flags=re.IGNORECASE
            ).strip().casefold()
            if normalized in unassigned_labels:
                overrides[soma_id] = None
                continue
            if normalized.isdigit() and int(normalized) in ontology:
                overrides[soma_id] = int(normalized)
                continue
            region_id = acronym_ids.get(normalized)
            if region_id is None:
                ignored += 1
                continue
            overrides[soma_id] = region_id
    return overrides, ignored


def _flatten_ontology(nodes: list[dict], output: dict[int, dict]) -> None:
    for node in nodes:
        children = node.get("children", [])
        color = str(node.get("color_hex_triplet", "")).strip().lstrip("#")
        output[int(node["id"])] = {
            "acronym": node.get("acronym", str(node["id"])),
            "name": node.get("name", "Unknown structure"),
            "children": [int(child["id"]) for child in children],
            "color_hex_triplet": color.upper()
            if re.fullmatch(r"[0-9a-fA-F]{6}", color)
            else None,
        }
        _flatten_ontology(children, output)


def load_ontology() -> dict[int, dict]:
    ontology_specification = load_atlas_manifest().get("ontology", {})
    if not _manifest_file_is_valid(BUNDLED_ONTOLOGY_PATH, ontology_specification):
        raise RuntimeError(
            "The bundled Allen structure graph failed its size or SHA256 check. "
            "Please reinstall fMOST Brain Viewer."
        )
    try:
        payload = json.loads(BUNDLED_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "The bundled Allen structure graph is missing or invalid. "
            "Please reinstall fMOST Brain Viewer."
        ) from exc
    output: dict[int, dict] = {}
    _flatten_ontology(payload.get("msg", []), output)
    if len(output) < 1000:
        raise RuntimeError("The bundled Allen structure graph is incomplete.")
    return output


def structure_family_ids(ontology: dict[int, dict], region_id: int) -> list[int]:
    family: list[int] = []
    pending = [region_id]
    while pending:
        current = pending.pop()
        if current in family:
            continue
        family.append(current)
        pending.extend(ontology.get(current, {}).get("children", []))
    return [atlas_id for atlas_id in family if atlas_id > 0]


def deterministic_color(key: int | str) -> str:
    if isinstance(key, str):
        hashed = int.from_bytes(
            hashlib.sha256(key.encode("utf-8")).digest()[:8], "big"
        )
        hue = hashed / 2**64
    else:
        hue = (key * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.96)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def region_rgba(region_id: int, ontology: dict[int, dict]) -> tuple[int, int, int, int]:
    """Return an opaque discrete atlas color, with transparent background ID 0."""
    if region_id == 0:
        return 0, 0, 0, 0
    color = ontology.get(region_id, {}).get("color_hex_triplet")
    if not isinstance(color, str) or not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        color = deterministic_color(region_id).lstrip("#")
    red, green, blue = (int(color[offset:offset + 2], 16) for offset in (0, 2, 4))
    return red, green, blue, 255


def annotation_slice_rgba(
    labels: np.ndarray, ontology: dict[int, dict]
) -> np.ndarray:
    """Map a 2D categorical annotation slice to stable, non-interpolated RGBA."""
    region_ids, inverse = np.unique(labels, return_inverse=True)
    palette = np.array(
        [region_rgba(int(region_id), ontology) for region_id in region_ids],
        dtype=np.uint8,
    )
    return palette[inverse].reshape(*labels.shape, 4)


def annotation_slice_grid(
    labels: np.ndarray,
    spacing: tuple[float, float],
    position_um: float,
    ontology: dict[int, dict],
) -> pv.ImageData:
    """Build a cell-colored coronal plane whose cell centers match annotation pixels."""
    if labels.ndim != 2:
        raise ValueError("Annotation slice must be a 2D array.")
    spacing_x, spacing_y = spacing
    plane = pv.ImageData(
        dimensions=(labels.shape[0] + 1, labels.shape[1] + 1, 1),
        spacing=(spacing_x, spacing_y, 1),
        origin=(-spacing_x / 2, -spacing_y / 2, position_um),
    )
    rgba = annotation_slice_rgba(labels, ontology)
    plane.cell_data["region_rgba"] = rgba.transpose(1, 0, 2).reshape(-1, 4)
    return plane


def infer_neuron_soma_id(
    path_or_name: str | Path, valid_soma_ids: set[int]
) -> int | None:
    """Return the only soma ID encoded as a numeric filename token, if unique."""
    name = Path(path_or_name).stem
    matches = {
        value
        for token in re.findall(r"(?<!\d)\d+(?!\d)", name)
        if (value := int(token)) in valid_soma_ids
    }
    return next(iter(matches)) if len(matches) == 1 else None


def color_icon(color: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(16, 16)
    pixmap.fill(QtGui.QColor(color))
    return QtGui.QIcon(pixmap)


def _surface_from_mask(mask: np.ndarray, spacing: tuple[float, float, float], origin) -> pv.PolyData:
    grid = pv.ImageData(dimensions=mask.shape, spacing=spacing, origin=origin)
    grid.point_data["mask"] = mask.astype(np.uint8).ravel(order="F")
    surface = grid.contour([0.5], scalars="mask").clean()
    if surface.n_points:
        surface = surface.smooth(n_iter=25, relaxation_factor=0.08)
        if surface.n_cells > 100_000:
            surface = surface.decimate_pro(0.5, preserve_topology=True)
    return surface


def load_or_create_whole_brain_surface(annotation: RawNrrdMemmap) -> pv.PolyData:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache = CACHE_ROOT / "whole_brain_surface_step4_v3.vtp"
    if cache.exists() and cache.stat().st_mtime >= annotation.path.stat().st_mtime:
        return pv.read(cache)
    step = 4
    mask = annotation.world_labels(step) != 0
    mask = ndimage.binary_closing(mask, iterations=1)
    for z in range(mask.shape[2]):
        mask[:, :, z] = ndimage.binary_fill_holes(mask[:, :, z])
    mask = ndimage.binary_fill_holes(mask)
    spacing = tuple(value * step for value in annotation.spacing)
    surface = _surface_from_mask(mask, spacing, (0, 0, 0))
    surface = surface.connectivity(extraction_mode="largest").extract_surface(
        algorithm="dataset_surface"
    ).clean()
    surface.save(cache)
    return surface


def load_or_create_region_surfaces(
    annotation: RawNrrdMemmap,
    region_ids: list[int],
) -> dict[int, pv.PolyData]:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    surfaces: dict[int, pv.PolyData] = {}
    missing: list[int] = []
    for region_id in region_ids:
        cache = CACHE_ROOT / f"region_{region_id}_step2.vtp"
        if cache.exists() and cache.stat().st_mtime >= annotation.path.stat().st_mtime:
            surfaces[region_id] = pv.read(cache)
        else:
            missing.append(region_id)
    if not missing:
        return surfaces

    step = 2
    labels = annotation.world_labels(step)
    spacing = tuple(value * step for value in annotation.spacing)
    for region_id in missing:
        stored = annotation.stored_value(region_id)
        coordinates = np.argwhere(labels == stored)
        if not len(coordinates):
            continue
        lower = coordinates.min(axis=0)
        upper = coordinates.max(axis=0)
        crop = labels[
            lower[0]:upper[0] + 1,
            lower[1]:upper[1] + 1,
            lower[2]:upper[2] + 1,
        ] == stored
        padded = np.pad(crop, 1)
        origin = (lower - 1) * np.asarray(spacing)
        surface = _surface_from_mask(padded, spacing, origin)
        cache = CACHE_ROOT / f"region_{region_id}_step2.vtp"
        surface.save(cache)
        surfaces[region_id] = surface
    return surfaces


def load_or_create_structure_surface(
    annotation: RawNrrdMemmap,
    ontology: dict[int, dict],
    region_id: int,
) -> pv.PolyData | None:
    """Create a parent structure from its own and all descendant atlas labels."""
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache = CACHE_ROOT / f"region_tree_{region_id}_step2.vtp"
    if cache.exists() and cache.stat().st_mtime >= annotation.path.stat().st_mtime:
        return pv.read(cache)
    stored_values = [
        annotation.stored_value(value)
        for value in structure_family_ids(ontology, region_id)
    ]
    if not stored_values:
        return None
    step = 2
    bounds = load_or_create_region_bounds(annotation)
    boxes = [bounds.get(str(value)) for value in stored_values]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    lower = np.min([box[:3] for box in boxes], axis=0)
    upper = np.max([box[3:] for box in boxes], axis=0)
    labels = annotation.world_labels(step)
    crop_labels = labels[
        lower[0]:upper[0], lower[1]:upper[1], lower[2]:upper[2]
    ]
    mask = np.isin(crop_labels, stored_values)
    coordinates = np.argwhere(mask)
    if not len(coordinates):
        return None
    local_lower = coordinates.min(axis=0)
    local_upper = coordinates.max(axis=0) + 1
    padded = np.pad(mask[
        local_lower[0]:local_upper[0],
        local_lower[1]:local_upper[1],
        local_lower[2]:local_upper[2],
    ], 1)
    spacing = tuple(value * step for value in annotation.spacing)
    origin = (lower + local_lower - 1) * np.asarray(spacing)
    surface = _surface_from_mask(padded, spacing, origin)
    temporary = cache.with_name(cache.stem + ".part.vtp")
    try:
        surface.save(temporary)
        temporary.replace(cache)
    finally:
        if temporary.exists():
            temporary.unlink()
    return surface


def sparse_label_bounds(labels: np.ndarray, is_background, chunk_depth: int = 16):
    """Find sparse uint32 label bounds without allocating up to max(region_id)."""
    bounds: dict[str, list[int]] = {}
    for start in range(0, labels.shape[2], chunk_depth):
        stop = min(start + chunk_depth, labels.shape[2])
        chunk = np.asarray(labels[:, :, start:stop])
        values, inverse = np.unique(chunk, return_inverse=True)
        compact = inverse.reshape(chunk.shape) + 1
        for value, slices in zip(values, ndimage.find_objects(compact), strict=True):
            label = int(value)
            if slices is None or is_background(label):
                continue
            current = [
                slices[0].start, slices[1].start, slices[2].start + start,
                slices[0].stop, slices[1].stop, slices[2].stop + start,
            ]
            previous = bounds.get(str(label))
            if previous is not None:
                current = [
                    min(previous[0], current[0]), min(previous[1], current[1]),
                    min(previous[2], current[2]), max(previous[3], current[3]),
                    max(previous[4], current[4]), max(previous[5], current[5]),
                ]
            bounds[str(label)] = current
    return bounds


def load_or_create_region_bounds(annotation: RawNrrdMemmap) -> dict[str, list[int]]:
    """Build a one-time 20 um label bounding-box index for local surface extraction."""
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache = CACHE_ROOT / f"region_bounds_step2_v{REGION_CACHE_VERSION}.json"
    if cache.exists() and cache.stat().st_mtime >= annotation.path.stat().st_mtime:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))["bounds"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    labels = annotation.world_labels(2)
    bounds = sparse_label_bounds(labels, annotation.is_background)
    payload = {
        "version": REGION_CACHE_VERSION,
        "annotation_size": annotation.path.stat().st_size,
        "annotation_mtime_ns": annotation.path.stat().st_mtime_ns,
        "bounds": bounds,
    }
    temporary = cache.with_suffix(".part.json")
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(cache)
    finally:
        if temporary.exists():
            temporary.unlink()
    return bounds


def region_library_ids(
    annotation: RawNrrdMemmap, ontology: dict[int, dict], mode: str
) -> list[int]:
    bounds = load_or_create_region_bounds(annotation)
    present_ids = {
        annotation.atlas_id(int(stored)) for stored in bounds
    }
    if mode == "standard":
        return sorted(present_ids)
    return sorted(
        region_id for region_id in ontology
        if any(descendant in present_ids for descendant in structure_family_ids(ontology, region_id))
    )


def prepare_region_library(
    annotation: RawNrrdMemmap,
    ontology: dict[int, dict],
    mode: str,
    progress_callback=None,
    cancelled=None,
) -> tuple[int, int]:
    ids = region_library_ids(annotation, ontology, mode)
    completed = 0
    for index, region_id in enumerate(ids, 1):
        if cancelled is not None and cancelled():
            raise AtlasSetupCancelled()
        info = ontology.get(region_id, {})
        if progress_callback is not None:
            progress_callback(index - 1, len(ids), str(info.get("acronym", region_id)))
        surface = load_or_create_structure_surface(annotation, ontology, region_id)
        if surface is not None and surface.n_points:
            completed += 1
        del surface
    manifest = CACHE_ROOT / f"region_library_{mode}_v{REGION_CACHE_VERSION}.json"
    temporary = manifest.with_suffix(".part.json")
    try:
        temporary.write_text(json.dumps({
            "version": REGION_CACHE_VERSION,
            "mode": mode,
            "annotation_size": annotation.path.stat().st_size,
            "annotation_mtime_ns": annotation.path.stat().st_mtime_ns,
            "completed": completed,
            "total": len(ids),
        }, indent=2), encoding="utf-8")
        temporary.replace(manifest)
    finally:
        if temporary.exists():
            temporary.unlink()
    if progress_callback is not None:
        progress_callback(len(ids), len(ids), "Complete")
    return completed, len(ids)


class RegionCacheWorker(QtCore.QObject):
    progress = QtCore.Signal(int, int, str)
    succeeded = QtCore.Signal()
    failed = QtCore.Signal(str)

    def __init__(self, annotation, ontology, mode=None, region_id=None):
        super().__init__()
        self.annotation = annotation
        self.ontology = ontology
        self.mode = mode
        self.region_id = region_id
        self.cancel_requested = False
        self.result = None
        self.error: Exception | None = None

    @QtCore.Slot()
    def run(self) -> None:
        try:
            if self.region_id is not None:
                self.progress.emit(0, 1, str(
                    self.ontology.get(self.region_id, {}).get("acronym", self.region_id)
                ))
                self.result = load_or_create_structure_surface(
                    self.annotation, self.ontology, self.region_id
                )
                self.progress.emit(1, 1, "Complete")
            else:
                self.progress.emit(0, 1, "atlas label index")
                self.result = prepare_region_library(
                    self.annotation,
                    self.ontology,
                    self.mode,
                    lambda done, total, label: self.progress.emit(done, total, label),
                    lambda: self.cancel_requested,
                )
            self.succeeded.emit()
        except AtlasSetupCancelled as exc:
            self.error = exc
            self.failed.emit("Preparation cancelled. Completed cache files were kept.")
        except Exception as exc:
            self.error = exc
            LOGGER.exception("Region cache preparation failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class RegionProgressController(QtCore.QObject):
    def __init__(self, progress, outcome):
        super().__init__(progress)
        self.progress_dialog = progress
        self.outcome = outcome

    @QtCore.Slot(int, int, str)
    def update(self, done: int, total: int, label: str) -> None:
        self.progress_dialog.setMaximum(max(total, 1))
        self.progress_dialog.setValue(done)
        self.progress_dialog.setLabelText(
            f"Preparing {label}\n{done} / {total} region surfaces"
        )

    @QtCore.Slot()
    def succeeded(self) -> None:
        self.outcome["ok"] = True
        self.progress_dialog.accept()

    @QtCore.Slot(str)
    def failed(self, message: str) -> None:
        self.outcome["error"] = message
        self.progress_dialog.reject()


def run_region_cache_job(
    parent,
    annotation: RawNrrdMemmap,
    ontology: dict[int, dict],
    mode: str | None = None,
    region_id: int | None = None,
):
    title = "Prepare brain region library" if mode else "Prepare brain region"
    progress = QtWidgets.QProgressDialog("Starting...", "Cancel", 0, 1, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    thread = QtCore.QThread(parent)
    worker = RegionCacheWorker(annotation, ontology, mode, region_id)
    worker.moveToThread(thread)
    outcome = {"ok": False, "error": ""}
    controller = RegionProgressController(progress, outcome)
    worker.progress.connect(controller.update)
    worker.succeeded.connect(controller.succeeded)
    worker.failed.connect(controller.failed)
    worker.succeeded.connect(thread.quit)
    worker.failed.connect(thread.quit)
    progress.canceled.connect(lambda: setattr(worker, "cancel_requested", True))
    thread.started.connect(worker.run)
    thread.start()
    progress.exec()
    if thread.isRunning():
        thread.quit()
    thread.wait()
    result = worker.result
    error = worker.error
    del controller
    del worker
    thread.deleteLater()
    if error is not None and not isinstance(error, AtlasSetupCancelled):
        raise error
    return result if outcome["ok"] else None


def region_library_is_current(annotation: RawNrrdMemmap, mode: str = "standard") -> bool:
    manifest = CACHE_ROOT / f"region_library_{mode}_v{REGION_CACHE_VERSION}.json"
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return (
            payload.get("version") == REGION_CACHE_VERSION
            and payload.get("annotation_size") == annotation.path.stat().st_size
            and payload.get("annotation_mtime_ns") == annotation.path.stat().st_mtime_ns
            and payload.get("completed") == payload.get("total")
        )
    except (OSError, ValueError, TypeError):
        return False


def choose_and_prepare_region_library(parent, annotation, ontology) -> bool:
    prompt = QtWidgets.QMessageBox(parent)
    prompt.setWindowTitle("Brain Region Library Preparation")
    prompt.setText("<b>Prepare atlas brain-region surfaces</b>")
    prompt.setInformativeText(
        "This one-time step prevents Add region from scanning the atlas during normal use.\n\n"
        "Standard prepares every region label present in the annotation plus common parent "
        "structures. Complete also prepares all valid hierarchy structures and takes longer."
    )
    standard = prompt.addButton("Standard (recommended)", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    complete = prompt.addButton("Complete", QtWidgets.QMessageBox.ButtonRole.ActionRole)
    later = prompt.addButton("Later", QtWidgets.QMessageBox.ButtonRole.RejectRole)
    prompt.setDefaultButton(standard)
    prompt.exec()
    if prompt.clickedButton() is later:
        return False
    mode = "complete" if prompt.clickedButton() is complete else "standard"
    result = run_region_cache_job(parent, annotation, ontology, mode=mode)
    if result is not None:
        completed, total = result
        QtWidgets.QMessageBox.information(
            parent,
            "Brain region library ready",
            f"Prepared {completed} / {total} region surfaces.\n\n"
            "Add region will now load these regions directly from cache.",
        )
        return True
    return False


class StartupSplash(QtWidgets.QWidget):
    """Small branded splash that remains responsive during synchronous loading."""

    def __init__(self):
        super().__init__(
            None,
            QtCore.Qt.WindowType.SplashScreen | QtCore.Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(620, 390)

        panel = QtWidgets.QFrame(self)
        panel.setObjectName("splashPanel")
        panel.setStyleSheet(
            "#splashPanel { background: #181d1a; border: 1px solid #50665a; "
            "border-radius: 16px; }"
        )
        panel.setGeometry(self.rect().adjusted(8, 8, -8, -8))
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(38, 25, 38, 25)
        layout.setSpacing(8)

        logo = QtWidgets.QLabel()
        logo.setPixmap(
            QtGui.QPixmap(str(APP_LOGO)).scaled(
                430,
                220,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
        logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo, stretch=1)
        layout.setAlignment(logo, QtCore.Qt.AlignmentFlag.AlignCenter)

        title = QtWidgets.QLabel("fMOST Brain Viewer")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24pt; font-weight: 700; color: #f1f5f2;")
        layout.addWidget(title)
        subtitle = QtWidgets.QLabel("Registered neurons and Allen CCF visualization")
        subtitle.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #aebbb2;")
        layout.addWidget(subtitle)

        self.message = QtWidgets.QLabel("Starting...")
        self.message.setStyleSheet("color: #cbd5ce;")
        layout.addWidget(self.message)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 5px; background: #303b34; "
            "height: 10px; } QProgressBar::chunk { border-radius: 5px; "
            "background: #67ed88; }"
        )
        layout.addWidget(self.bar)
        version = QtWidgets.QLabel(f"Version {__version__}")
        version.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        version.setStyleSheet("color: #89978e; font-size: 9pt;")
        layout.addWidget(version)

    def setLabelText(self, message: str) -> None:  # noqa: N802
        self.message.setText(message)

    def setValue(self, value: int) -> None:  # noqa: N802
        self.bar.setValue(value)

class CaptureDialog(QtWidgets.QDialog):
    FORMATS = {
        "TIFF (lossless)": (".tif", "TIFF files (*.tif *.tiff)"),
        "PNG (lossless)": (".png", "PNG files (*.png)"),
        "JPEG": (".jpg", "JPEG files (*.jpg *.jpeg)"),
        "BMP": (".bmp", "BMP files (*.bmp)"),
    }

    def __init__(self, viewer: "ViewerWindow"):
        super().__init__(viewer)
        self.viewer = viewer
        self.setWindowTitle("Capture current 3D view")
        self.setWindowIcon(QtGui.QIcon(str(APP_ICON)))
        self.setMinimumWidth(650)

        root = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("<b>Export current viewer image</b>")
        heading.setStyleSheet("font-size: 15px;")
        root.addWidget(heading)
        root.addWidget(
            QtWidgets.QLabel(
                "Choose which currently visible scene elements to include. "
                "The live viewer will be restored after export."
            )
        )

        columns = QtWidgets.QHBoxLayout()
        contents_group = QtWidgets.QGroupBox("Capture contents")
        contents_layout = QtWidgets.QVBoxLayout(contents_group)
        current = viewer.capture_content_visibility()
        self.content_checks: dict[str, QtWidgets.QCheckBox] = {}
        for key, label in EXPORT_CONTENT_LABELS.items():
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.setChecked(current[key])
            contents_layout.addWidget(checkbox)
            self.content_checks[key] = checkbox
        contents_layout.addStretch(1)
        columns.addWidget(contents_group, stretch=1)

        output_group = QtWidgets.QGroupBox("Output")
        output_layout = QtWidgets.QFormLayout(output_group)
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(self.FORMATS)
        output_layout.addRow("Format", self.format_combo)

        self.scale_combo = QtWidgets.QComboBox()
        self.scale_combo.addItems(["1×", "2×", "3×", "4×"])
        self.scale_combo.setCurrentIndex(1)
        output_layout.addRow("Resolution", self.scale_combo)
        self.dimensions_label = QtWidgets.QLabel()
        output_layout.addRow("Output size", self.dimensions_label)

        self.transparent_check = QtWidgets.QCheckBox("Transparent background")
        self.transparent_check.setChecked(False)
        output_layout.addRow(self.transparent_check)
        self.quality_spin = QtWidgets.QSpinBox()
        self.quality_spin.setRange(50, 100)
        self.quality_spin.setValue(95)
        self.quality_spin.setSuffix(" %")
        output_layout.addRow("JPEG quality", self.quality_spin)

        path_row = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit(str(self._default_path()))
        browse = QtWidgets.QPushButton("Browse...")
        path_row.addWidget(self.path_edit, stretch=1)
        path_row.addWidget(browse)
        output_layout.addRow("Save as", path_row)
        output_layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        columns.addWidget(output_group, stretch=2)
        root.addLayout(columns)

        note = QtWidgets.QLabel(
            "TIFF uses lossless LZW compression. JPEG does not support transparency."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("Capture")
        root.addWidget(buttons)

        browse.clicked.connect(self._browse)
        buttons.accepted.connect(self._capture)
        buttons.rejected.connect(self.reject)
        self.format_combo.currentTextChanged.connect(self._format_changed)
        self.scale_combo.currentIndexChanged.connect(self._update_dimensions)
        self._format_changed(self.format_combo.currentText())
        self._update_dimensions()
        for widget_type in (
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QSlider,
            QtWidgets.QComboBox,
        ):
            for widget in self.findChildren(widget_type):
                widget.installEventFilter(viewer)

    def _default_path(self) -> Path:
        settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
        pictures = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.PicturesLocation
        )
        folder = Path(str(settings.value("last_capture_directory", pictures)))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return folder / f"brain_{self.viewer.brain_id}_{stamp}.tif"

    def _format_changed(self, label: str) -> None:
        suffix, _ = self.FORMATS[label]
        path = Path(self.path_edit.text())
        known = {item[0] for item in self.FORMATS.values()} | {".tiff", ".jpeg"}
        if path.suffix.lower() in known:
            self.path_edit.setText(str(path.with_suffix(suffix)))
        is_jpeg = label == "JPEG"
        self.quality_spin.setEnabled(is_jpeg)
        self.transparent_check.setEnabled(label in ("TIFF (lossless)", "PNG (lossless)"))
        if not self.transparent_check.isEnabled():
            self.transparent_check.setChecked(False)

    def _update_dimensions(self) -> None:
        scale = self.scale_combo.currentIndex() + 1
        width, height = self.viewer.plotter.window_size
        self.dimensions_label.setText(f"{width * scale} × {height * scale} px")

    def _browse(self) -> None:
        suffix, file_filter = self.FORMATS[self.format_combo.currentText()]
        suggested = Path(self.path_edit.text()).with_suffix(suffix)
        selected, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save captured viewer image", str(suggested), file_filter
        )
        if selected:
            self.path_edit.setText(str(Path(selected).with_suffix(suffix)))

    def _capture(self) -> None:
        suffix, _ = self.FORMATS[self.format_combo.currentText()]
        path = Path(self.path_edit.text().strip()).expanduser()
        if not path.name:
            QtWidgets.QMessageBox.warning(self, "Missing filename", "Choose an output file.")
            return
        path = path.with_suffix(suffix)
        self.path_edit.setText(str(path))
        if not path.parent.is_dir():
            QtWidgets.QMessageBox.warning(
                self, "Folder not found", f"The output folder does not exist:\n{path.parent}"
            )
            return
        if path.exists() and QtWidgets.QMessageBox.question(
            self,
            "Replace existing image?",
            f"The file already exists:\n{path}\n\nReplace it?",
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        contents = {
            key: checkbox.isChecked() for key, checkbox in self.content_checks.items()
        }
        try:
            self.viewer.export_capture(
                path,
                contents,
                scale=self.scale_combo.currentIndex() + 1,
                transparent=self.transparent_check.isChecked(),
                jpeg_quality=self.quality_spin.value(),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Capture failed", str(exc))
            return
        settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
        settings.setValue("last_capture_directory", str(path.parent))
        settings.sync()
        QtWidgets.QMessageBox.information(
            self, "Capture saved", f"Image saved successfully:\n{path}"
        )
        self.accept()


class RecordGifDialog(QtWidgets.QDialog):
    def __init__(self, viewer: "ViewerWindow"):
        super().__init__(viewer)
        self.viewer = viewer
        self.setWindowTitle("Record rotating GIF")
        self.setWindowIcon(QtGui.QIcon(str(APP_ICON)))
        self.setMinimumWidth(650)

        root = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("<b>Export an automatically rotating GIF</b>")
        heading.setStyleSheet("font-size: 15px;")
        root.addWidget(heading)
        root.addWidget(
            QtWidgets.QLabel(
                "The scene rotates 360 degrees around the current focal point. "
                "The live camera and visibility are restored afterward."
            )
        )

        columns = QtWidgets.QHBoxLayout()
        contents_group = QtWidgets.QGroupBox("Recording contents")
        contents_layout = QtWidgets.QVBoxLayout(contents_group)
        current = viewer.capture_content_visibility()
        self.content_checks: dict[str, QtWidgets.QCheckBox] = {}
        for key, label in EXPORT_CONTENT_LABELS.items():
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.setChecked(current[key])
            contents_layout.addWidget(checkbox)
            self.content_checks[key] = checkbox
        contents_layout.addStretch(1)
        columns.addWidget(contents_group, stretch=1)

        output_group = QtWidgets.QGroupBox("Animation")
        output_layout = QtWidgets.QFormLayout(output_group)
        self.direction_combo = QtWidgets.QComboBox()
        self.direction_combo.addItems(["Clockwise", "Counterclockwise"])
        output_layout.addRow("Rotation", self.direction_combo)
        self.frames_spin = QtWidgets.QSpinBox()
        self.frames_spin.setRange(12, 72)
        self.frames_spin.setValue(36)
        output_layout.addRow("Frames", self.frames_spin)
        self.duration_spin = QtWidgets.QDoubleSpinBox()
        self.duration_spin.setRange(1.0, 20.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setValue(4.0)
        self.duration_spin.setSuffix(" s")
        output_layout.addRow("Loop duration", self.duration_spin)
        self.scale_combo = QtWidgets.QComboBox()
        self.scale_combo.addItems(["1x", "2x"])
        output_layout.addRow("Resolution", self.scale_combo)
        self.dimensions_label = QtWidgets.QLabel()
        output_layout.addRow("Output size", self.dimensions_label)

        path_row = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit(str(self._default_path()))
        browse = QtWidgets.QPushButton("Browse...")
        path_row.addWidget(self.path_edit, stretch=1)
        path_row.addWidget(browse)
        output_layout.addRow("Save as", path_row)
        output_layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        columns.addWidget(output_group, stretch=2)
        root.addLayout(columns)

        note = QtWidgets.QLabel(
            "More frames and 2x resolution produce smoother, larger files but take longer."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("Record")
        root.addWidget(buttons)

        browse.clicked.connect(self._browse)
        buttons.accepted.connect(self._record)
        buttons.rejected.connect(self.reject)
        self.scale_combo.currentIndexChanged.connect(self._update_dimensions)
        self._update_dimensions()
        for widget_type in (
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QSlider,
            QtWidgets.QComboBox,
        ):
            for widget in self.findChildren(widget_type):
                widget.installEventFilter(viewer)

    def _default_path(self) -> Path:
        settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
        pictures = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.PicturesLocation
        )
        folder = Path(str(settings.value("last_capture_directory", pictures)))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return folder / f"brain_{self.viewer.brain_id}_{stamp}.gif"

    def _update_dimensions(self) -> None:
        scale = self.scale_combo.currentIndex() + 1
        width, height = self.viewer.plotter.window_size
        self.dimensions_label.setText(f"{width * scale} x {height * scale} px")

    def _browse(self) -> None:
        selected, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save rotating GIF", self.path_edit.text(), "GIF files (*.gif)"
        )
        if selected:
            self.path_edit.setText(str(Path(selected).with_suffix(".gif")))

    def _record(self) -> None:
        raw_path = self.path_edit.text().strip()
        if not raw_path:
            QtWidgets.QMessageBox.warning(self, "Missing filename", "Choose an output file.")
            return
        path = Path(raw_path).expanduser().with_suffix(".gif")
        self.path_edit.setText(str(path))
        if not path.name or not path.parent.is_dir():
            QtWidgets.QMessageBox.warning(
                self, "Invalid output path", f"The output folder does not exist:\n{path.parent}"
            )
            return
        if path.exists() and QtWidgets.QMessageBox.question(
            self, "Replace existing GIF?", f"The file already exists:\n{path}\n\nReplace it?"
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        contents = {
            key: checkbox.isChecked() for key, checkbox in self.content_checks.items()
        }
        direction = -1 if self.direction_combo.currentText() == "Clockwise" else 1
        try:
            completed = self.viewer.export_rotating_gif(
                path,
                contents,
                frames=self.frames_spin.value(),
                duration_seconds=self.duration_spin.value(),
                scale=self.scale_combo.currentIndex() + 1,
                direction=direction,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Recording failed", str(exc))
            return
        if not completed:
            return
        settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
        settings.setValue("last_capture_directory", str(path.parent))
        settings.sync()
        QtWidgets.QMessageBox.information(
            self, "Recording saved", f"Rotating GIF saved successfully:\n{path}"
        )
        self.accept()


class UpdateCheckWorker(QtCore.QObject):
    completed = QtCore.Signal(object, object)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            releases = newer_stable_releases(fetch_github_releases(), __version__)
            self.completed.emit(releases, None)
        except Exception as exc:
            self.completed.emit([], exc)


class UpdateDialog(QtWidgets.QDialog):
    def __init__(self, releases: list[dict], parent=None):
        super().__init__(parent)
        self.releases = releases
        self.setWindowTitle("Update fMOST Brain Viewer")
        self.setWindowIcon(QtGui.QIcon(str(APP_ICON)))
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            f"Installed version: {__version__}\nChoose a newer version:"
        ))
        self.version_combo = QtWidgets.QComboBox()
        for release in releases:
            tag = str(release["tag_name"]).removeprefix("v")
            title = str(release.get("name") or release["tag_name"])
            self.version_combo.addItem(f"{tag} — {title}", release)
        layout.addWidget(self.version_combo)
        note = QtWidgets.QLabel(
            "The installer replaces only application files. Atlas data, projects, "
            "sessions, caches, and user settings are preserved."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.release_button = buttons.addButton(
            "View release page", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole
        )
        self.install_button = buttons.addButton(
            "Download and install", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.rejected.connect(self.reject)
        self.install_button.clicked.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(560, 180)

    def selected_release(self) -> dict:
        return self.version_combo.currentData()


class ViewerWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        projects: list[tuple[str, Path]],
        progress: StartupSplash | None = None,
        session_path: Path | None = None,
        session_config: dict | None = None,
    ):
        super().__init__()
        self.update_check_thread: QtCore.QThread | None = None
        self.update_check_worker: UpdateCheckWorker | None = None
        self.update_check_progress: QtWidgets.QProgressDialog | None = None
        self.startup_progress = progress
        self.session_path = session_path
        self.config = (
            session_config if session_config is not None else self._legacy_config(projects[0])
        )
        settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
        self.mouse_wheel_parameters_enabled = settings.value(
            "enable_parameter_mouse_wheel", False, type=bool
        )
        self.datasets: dict[str, BrainDataset] = {}
        self.dataset_items: dict[str, QtWidgets.QListWidgetItem] = {}
        self.axon_sources: dict[str, Path] = {}
        self.axon_actors: dict[str, object] = {}
        self.axon_actor_last_used: dict[str, int] = {}
        self.axon_actor_use_counter = 0
        self.neuron_ids: dict[str, int | None] = {}
        self.neuron_match_status: dict[str, str] = {}
        self.neuron_datasets: dict[str, str] = {}
        self.neuron_items: dict[str, QtWidgets.QListWidgetItem] = {}
        self.neurons_by_region: dict[int, list[str]] = {}
        self.neuron_region_items: dict[int, QtWidgets.QListWidgetItem] = {}
        self.region_actors: dict[int, object] = {}
        self.region_items: dict[int, QtWidgets.QListWidgetItem] = {}
        self.custom_region_ids: set[int] = set()
        self.slice_actor = None
        self.legend_actor = None
        self.volume_actor = None
        self.template_range = None
        self.current_brain_style = "Surface"
        self._report_progress(5, "Reading soma and atlas metadata...")
        self.annotation = RawNrrdMemmap(ANNOTATION_10)
        self.ontology = load_ontology()
        dataset_settings = {
            str(entry.get("key", "")): entry
            for entry in self.config.get("datasets", [])
        }
        for brain_id, project in projects:
            key = dataset_key(brain_id, project)
            saved = dataset_settings.get(key, {})
            color = str(
                saved.get("soma_color", deterministic_color(f"dataset:{brain_id}"))
            )
            self._read_dataset(brain_id, project, color, bool(saved.get("enabled", True)))
        self.brain_id = "+".join(dataset.brain_id for dataset in self.datasets.values())
        self.region_counts = Counter(
            region_id
            for dataset in self.datasets.values()
            for region_id in dataset.soma_regions.values()
            if region_id is not None
        )
        self.outside_soma_count = sum(
            dataset.outside_soma_count for dataset in self.datasets.values()
        )
        self.unassigned_soma_count = sum(
            region_id is None
            for dataset in self.datasets.values()
            for region_id in dataset.soma_regions.values()
        )
        self.region_colors = {
            region_id: deterministic_color(region_id) for region_id in self.region_counts
        }
        self.manual_colors: dict[str, str] = dict(self.config.get("manual_colors", {}))
        if session_config is None:
            legacy_visible = set(self.config.get("visible_neurons", []))
            for dataset in self.datasets.values():
                legacy_path = project_paths(dataset.brain_id, dataset.project)[2]
                try:
                    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for stem, color in legacy.get("manual_colors", {}).items():
                    self.manual_colors.setdefault(f"{dataset.key}::{stem}", color)
                legacy_visible.update(
                    f"{dataset.key}::{stem}"
                    for stem in legacy.get("visible_neurons", [])
                )
            self.config["visible_neurons"] = sorted(legacy_visible)
        self.setWindowTitle(
            f"fMOST Brain Viewer v{__version__} - {len(self.datasets)} brain dataset(s)"
        )
        self.setWindowIcon(QtGui.QIcon(str(APP_ICON)))
        self.resize(1550, 980)
        self._report_progress(10, "Building the viewer controls...")
        self._build_ui()
        self._load_scene()
        self._restore_config()
        self._report_progress(
            100,
            f"Ready: {len(self.datasets)} brains; {len(self.neuron_items)} neurons; "
            f"{sum(len(dataset.soma_points) for dataset in self.datasets.values())} "
            "somas; "
            f"{self.unassigned_soma_count} unassigned; "
            f"{sum(dataset.manual_region_applied for dataset in self.datasets.values())} "
            "manual region correction(s).",
        )

    def _report_progress(self, value: int, message: str) -> None:
        if hasattr(self, "status"):
            self.status.setText(message)
        if self.startup_progress is not None:
            self.startup_progress.setLabelText(message)
            self.startup_progress.setValue(value)
        QtWidgets.QApplication.processEvents()

    @staticmethod
    def _legacy_config(project: tuple[str, Path]) -> dict:
        brain_id, folder = project
        config_path = project_paths(brain_id, folder)[2]
        if not config_path.exists():
            return {}
        try:
            legacy = json.loads(config_path.read_text(encoding="utf-8"))
            legacy["manual_colors"] = {
                f"{dataset_key(brain_id, folder)}::{stem}": color
                for stem, color in legacy.get("manual_colors", {}).items()
            }
            legacy["visible_neurons"] = [
                f"{dataset_key(brain_id, folder)}::{stem}"
                for stem in legacy.get("visible_neurons", [])
            ]
            return legacy
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_dataset(
        self, brain_id: str, project: Path, color: str, enabled: bool = True
    ) -> BrainDataset:
        axon_dir, soma_path, _config_path = project_paths(brain_id, project)
        missing = [path for path in (axon_dir, soma_path) if not path.exists()]
        if missing:
            raise FileNotFoundError("Missing required path(s):\n" + "\n".join(map(str, missing)))
        key = dataset_key(brain_id, project)
        if key in self.datasets:
            return self.datasets[key]
        soma_ids, soma_points, _ = read_swc_with_ids(soma_path)
        soma_regions, outside = classify_somas(self.annotation, soma_ids, soma_points)
        manual_path = manual_soma_region_csv(brain_id, project)
        manual_regions: dict[int, int | None] = {}
        manual_ignored = 0
        if manual_path is not None:
            try:
                manual_regions, manual_ignored = load_manual_soma_regions(
                    manual_path, self.ontology, set(map(int, soma_ids))
                )
                soma_regions.update(manual_regions)
            except OSError:
                manual_ignored = 1
        dataset = BrainDataset(
            brain_id=brain_id,
            project=project.resolve(),
            key=key,
            axon_dir=axon_dir,
            soma_path=soma_path,
            soma_color=color,
            enabled=enabled,
            soma_ids=soma_ids,
            soma_points=soma_points,
            soma_point_by_id={
                int(soma_id): point for soma_id, point in zip(soma_ids, soma_points)
            },
            soma_regions=soma_regions,
            outside_soma_count=outside,
            manual_region_path=manual_path,
            manual_region_applied=len(manual_regions),
            manual_region_ignored=manual_ignored,
        )
        self.datasets[key] = dataset
        return dataset

    def _build_ui(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction("New session...", self._new_session)
        file_menu.addAction("Open session...", self._open_session)
        self.save_session_action = file_menu.addAction("Save session", self._save_session)
        file_menu.addAction(
            "Save session as...", lambda _checked=False: self._save_session(save_as=True)
        )
        file_menu.addSeparator()
        file_menu.addAction("Add brain datasets...", self._add_brain_datasets)
        file_menu.addAction("Remove selected dataset", self._remove_selected_dataset)
        file_menu.addSeparator()
        capture_action = file_menu.addAction("Capture current view...")
        capture_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+S"))
        capture_action.triggered.connect(self._show_capture)
        record_action = file_menu.addAction("Record rotating GIF...")
        record_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+R"))
        record_action.triggered.connect(self._show_record_gif)
        file_menu.addSeparator()
        atlas_action = file_menu.addAction("Configure Allen CCF atlas...")
        atlas_action.triggered.connect(self._configure_atlas)
        prepare_action = file_menu.addAction("Prepare brain region library...")
        prepare_action.triggered.connect(self._prepare_region_library)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)
        settings_menu = self.menuBar().addMenu("Settings")
        self.mouse_wheel_action = settings_menu.addAction(
            "Enable mouse-wheel parameter adjustment"
        )
        self.mouse_wheel_action.setCheckable(True)
        self.mouse_wheel_action.setChecked(self.mouse_wheel_parameters_enabled)
        self.mouse_wheel_action.setToolTip(
            "Allow the mouse wheel to change spin boxes, sliders, and drop-down settings"
        )
        self.mouse_wheel_action.toggled.connect(self._set_parameter_mouse_wheel)
        brain_rendering_menu = settings_menu.addMenu("3D brain rendering")
        reset_view_action = settings_menu.addAction("Reset default brain view")
        reset_view_action.triggered.connect(self._reset_default_view)
        self.brain_rendering_group = QtGui.QActionGroup(self)
        self.brain_rendering_group.setExclusive(True)
        self.surface_rendering_action = brain_rendering_menu.addAction(
            "Surface (default)"
        )
        self.surface_rendering_action.setCheckable(True)
        self.surface_rendering_action.setChecked(True)
        self.volume_rendering_action = brain_rendering_menu.addAction(
            "Volume (load on demand)"
        )
        self.volume_rendering_action.setCheckable(True)
        self.volume_rendering_action.setToolTip(
            "Load the 25 um atlas volume only when this option is selected"
        )
        self.brain_rendering_group.addAction(self.surface_rendering_action)
        self.brain_rendering_group.addAction(self.volume_rendering_action)
        self.surface_rendering_action.triggered.connect(
            lambda _checked=False: self._set_brain_style("Surface")
        )
        self.volume_rendering_action.triggered.connect(
            lambda _checked=False: self._set_brain_style("Volume")
        )
        self.help_menu = self.menuBar().addMenu("Help")
        update_action = self.help_menu.addAction("Check for updates...")
        update_action.triggered.connect(self._check_for_updates)
        self.help_menu.addSeparator()
        log_action = self.help_menu.addAction("Open log folder")
        log_action.triggered.connect(self._open_log_folder)
        self.help_menu.addSeparator()
        about_action = self.help_menu.addAction("About fMOST Brain Viewer")
        about_action.triggered.connect(self._show_about)

        splitter = QtWidgets.QSplitter()
        controls = QtWidgets.QWidget()
        controls.setMinimumWidth(390)
        controls.setMaximumWidth(520)
        layout = QtWidgets.QVBoxLayout(controls)

        title_row = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel(f"Combined brains: {len(self.datasets)}")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        capture_button = QtWidgets.QPushButton("Capture...")
        capture_button.setToolTip("Export the current 3D view (Ctrl+Shift+S)")
        capture_button.clicked.connect(self._show_capture)
        record_button = QtWidgets.QPushButton("Record...")
        record_button.setToolTip("Export an automatically rotating GIF (Ctrl+Shift+R)")
        record_button.clicked.connect(self._show_record_gif)
        title_row.addWidget(self.title_label, stretch=1)
        title_row.addWidget(capture_button)
        title_row.addWidget(record_button)
        layout.addLayout(title_row)

        datasets_group = QtWidgets.QGroupBox("Datasets")
        datasets_layout = QtWidgets.QVBoxLayout(datasets_group)
        dataset_buttons = QtWidgets.QHBoxLayout()
        add_dataset_button = QtWidgets.QPushButton("Add...")
        remove_dataset_button = QtWidgets.QPushButton("Remove")
        dataset_buttons.addWidget(add_dataset_button)
        dataset_buttons.addWidget(remove_dataset_button)
        datasets_layout.addLayout(dataset_buttons)
        self.dataset_list = QtWidgets.QListWidget()
        dataset_row_height = self.dataset_list.fontMetrics().height() + 8
        self.dataset_list.setFixedHeight(dataset_row_height * 3 + 6)
        datasets_layout.addWidget(self.dataset_list)
        layout.addWidget(datasets_group)
        self._populate_dataset_list()

        display_group = QtWidgets.QGroupBox("Display")
        display_layout = QtWidgets.QFormLayout(display_group)
        self.template_check = QtWidgets.QCheckBox("Show 3D brain")
        self.template_check.setChecked(True)
        display_layout.addRow(self.template_check)
        self.slice_check = QtWidgets.QCheckBox("10 um coronal annotation slice")
        self.slice_check.setChecked(True)
        display_layout.addRow(self.slice_check)
        self.soma_check = QtWidgets.QCheckBox("Show all soma locations")
        self.soma_check.setChecked(True)
        display_layout.addRow(self.soma_check)
        self.grid_check = QtWidgets.QCheckBox("Coordinate grid and bounds")
        self.grid_check.setChecked(False)
        display_layout.addRow(self.grid_check)
        self.legend_check = QtWidgets.QCheckBox("Brain-region legend")
        self.legend_check.setChecked(True)
        display_layout.addRow(self.legend_check)
        layout.addWidget(display_group)

        slice_group = QtWidgets.QGroupBox("Anterior-posterior position (AP / Z)")
        slice_layout = QtWidgets.QVBoxLayout(slice_group)
        self.slice_label = QtWidgets.QLabel()
        self.slice_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slice_slider.setRange(0, self.annotation.shape[2] - 1)
        self.slice_slider.setValue(self.annotation.shape[2] // 2)
        slice_controls = QtWidgets.QHBoxLayout()
        self.slice_previous = QtWidgets.QToolButton()
        self.slice_previous.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowLeft)
        )
        self.slice_previous.setToolTip("Previous coronal slice")
        self.slice_next = QtWidgets.QToolButton()
        self.slice_next.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowRight)
        )
        self.slice_next.setToolTip("Next coronal slice")
        self.coronal_repeat_timer = QtCore.QTimer(self)
        self.coronal_repeat_timer.setInterval(350)
        self.coronal_repeat_timer.timeout.connect(self._repeat_coronal_step)
        self.coronal_repeat_step = 0
        slice_controls.addWidget(self.slice_previous)
        slice_controls.addWidget(self.slice_slider, stretch=1)
        slice_controls.addWidget(self.slice_next)
        slice_layout.addWidget(self.slice_label)
        slice_layout.addLayout(slice_controls)
        layout.addWidget(slice_group)

        appearance_group = QtWidgets.QGroupBox("Appearance")
        appearance_layout = QtWidgets.QFormLayout(appearance_group)
        self.template_opacity = self._spinbox(
            0.0, 1.0, 0.05, DEFAULT_BRAIN_OPACITY, 2
        )
        appearance_layout.addRow("3D brain opacity", self.template_opacity)
        self.axon_width = self._spinbox(0.0, 10.0, 0.5, 0.0, 1)
        appearance_layout.addRow("Axon thickness (×)", self.axon_width)
        self.soma_size = self._spinbox(1.0, 50.0, 1.0, 10.0, 1)
        appearance_layout.addRow("Soma size", self.soma_size)
        layout.addWidget(appearance_group)

        region_group = QtWidgets.QGroupBox("Brain regions")
        region_group.setObjectName("brain_regions_group")
        region_layout = QtWidgets.QVBoxLayout(region_group)
        region_buttons = QtWidgets.QHBoxLayout()
        region_all = QtWidgets.QPushButton("Select all")
        region_none = QtWidgets.QPushButton("Select none")
        region_buttons.addWidget(region_all)
        region_buttons.addWidget(region_none)
        region_layout.addLayout(region_buttons)
        import_row = QtWidgets.QHBoxLayout()
        self.region_search = QtWidgets.QLineEdit()
        example_region = "C" + "eA"
        self.region_search.setPlaceholderText(f"Search atlas (e.g. {example_region})")
        add_region_button = QtWidgets.QPushButton("Add region")
        import_row.addWidget(self.region_search, stretch=1)
        import_row.addWidget(add_region_button)
        region_layout.addLayout(import_row)
        self.region_search_results = QtWidgets.QListWidget()
        self.region_search_results.setMaximumHeight(170)
        self.region_search_results.hide()
        region_layout.addWidget(self.region_search_results)
        self.region_list = QtWidgets.QListWidget()
        list_row_height = self.region_list.fontMetrics().height() + 8
        four_rows_height = list_row_height * 4 + 2 * self.region_list.frameWidth() + 2
        self.region_list.setFixedHeight(four_rows_height)
        region_layout.addWidget(self.region_list)
        self.unassigned_label = QtWidgets.QLabel(
            f"Unassigned: {self.unassigned_soma_count} soma(s) "
            f"({self.outside_soma_count} outside atlas)"
        )
        region_layout.addWidget(self.unassigned_label)
        self.region_opacity = self._spinbox(0.0, 1.0, 0.05, 0.55, 2)
        region_layout.addWidget(QtWidgets.QLabel("Highlighted region opacity"))
        region_layout.addWidget(self.region_opacity)
        layout.addWidget(region_group)

        axon_group = QtWidgets.QGroupBox("Neurons / axons")
        axon_layout = QtWidgets.QVBoxLayout(axon_group)
        self.color_mode = QtWidgets.QComboBox()
        self.color_mode.addItems(["Independent / manual", "By soma region"])
        axon_layout.addWidget(self.color_mode)
        axon_buttons = QtWidgets.QHBoxLayout()
        axon_all = QtWidgets.QPushButton("Select all")
        axon_none = QtWidgets.QPushButton("Select none")
        axon_buttons.addWidget(axon_all)
        axon_buttons.addWidget(axon_none)
        axon_layout.addLayout(axon_buttons)
        self.neuron_list = QtWidgets.QListWidget()
        self.neuron_list.setToolTip("Double-click a neuron to set its independent color")
        self.neuron_region_list = QtWidgets.QListWidget()
        self.neuron_region_list.setToolTip(
            "Check a soma region to show all neurons whose somas are in that region"
        )
        self.neuron_stack = QtWidgets.QStackedWidget()
        self.neuron_stack.setMinimumHeight(four_rows_height)
        self.neuron_stack.addWidget(self.neuron_list)
        self.neuron_stack.addWidget(self.neuron_region_list)
        axon_layout.addWidget(self.neuron_stack)
        layout.addWidget(axon_group, stretch=1)

        self.status = QtWidgets.QLabel("Loading...")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        plot_frame = QtWidgets.QFrame()
        plot_layout = QtWidgets.QVBoxLayout(plot_frame)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(plot_frame)
        plot_layout.addWidget(self.plotter.interactor)
        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        controls_scroll.setMinimumWidth(410)
        controls_scroll.setMaximumWidth(540)
        controls_scroll.setWidget(controls)
        splitter.addWidget(controls_scroll)
        splitter.addWidget(plot_frame)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.template_check.toggled.connect(self._set_brain_visible)
        self.slice_check.toggled.connect(self._set_slice_visible)
        self.soma_check.toggled.connect(self._refresh_soma_points)
        self.grid_check.toggled.connect(self._set_grid_visible)
        self.legend_check.toggled.connect(self._update_region_legend)
        self.slice_slider.valueChanged.connect(self._update_slice)
        self.slice_previous.pressed.connect(lambda: self._start_coronal_repeat(-1))
        self.slice_next.pressed.connect(lambda: self._start_coronal_repeat(1))
        self.slice_previous.released.connect(self._stop_coronal_repeat)
        self.slice_next.released.connect(self._stop_coronal_repeat)
        self.template_opacity.valueChanged.connect(self._set_template_opacity)
        self.axon_width.valueChanged.connect(self._set_axon_width)
        self.soma_size.valueChanged.connect(self._set_soma_size)
        self.region_opacity.valueChanged.connect(self._set_region_opacity)
        self.region_list.itemChanged.connect(self._region_toggled)
        self.neuron_list.itemChanged.connect(self._neuron_toggled)
        self.neuron_list.itemDoubleClicked.connect(self._choose_neuron_color)
        self.neuron_region_list.itemChanged.connect(self._neuron_region_toggled)
        self.dataset_list.itemChanged.connect(self._dataset_toggled)
        self.color_mode.currentTextChanged.connect(self._set_neuron_mode)
        region_all.clicked.connect(lambda: self._check_all_regions(True))
        region_none.clicked.connect(lambda: self._check_all_regions(False))
        add_region_button.clicked.connect(self._add_region_from_search)
        self.region_search.textEdited.connect(self._update_region_search_results)
        self.region_search_results.itemClicked.connect(
            lambda _item: self._add_region_from_search()
        )
        self.region_search.installEventFilter(self)
        axon_all.clicked.connect(lambda: self._check_all_neurons(True))
        axon_none.clicked.connect(lambda: self._check_all_neurons(False))
        add_dataset_button.clicked.connect(self._add_brain_datasets)
        remove_dataset_button.clicked.connect(self._remove_selected_dataset)

        wheel_controls = []
        for widget_type in (
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QSlider,
            QtWidgets.QComboBox,
        ):
            wheel_controls.extend(self.findChildren(widget_type))
        for widget in wheel_controls:
            widget.installEventFilter(self)
        for widget in self.findChildren(QtWidgets.QWidget):
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            event.type() == QtCore.QEvent.Type.MouseButtonPress
            and hasattr(self, "region_search_results")
            and self.region_search_results.isVisible()
            and not self._inside_region_search(watched)
        ):
            self.region_search_results.hide()
        if watched is getattr(self, "region_search", None) and event.type() == QtCore.QEvent.Type.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key.Key_Down, QtCore.Qt.Key.Key_Up):
                count = self.region_search_results.count()
                if count:
                    step = 1 if key == QtCore.Qt.Key.Key_Down else -1
                    row = max(0, min(count - 1, self.region_search_results.currentRow() + step))
                    self.region_search_results.setCurrentRow(row)
                return True
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                self._add_region_from_search()
                return True
            if key == QtCore.Qt.Key.Key_Escape:
                self.region_search_results.hide()
                return True
        if (
            event.type() == QtCore.QEvent.Type.Wheel
            and isinstance(
                watched,
                (QtWidgets.QAbstractSpinBox, QtWidgets.QSlider, QtWidgets.QComboBox),
            )
            and not self.mouse_wheel_parameters_enabled
        ):
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _inside_region_search(self, watched) -> bool:
        current = watched if isinstance(watched, QtWidgets.QWidget) else None
        while current is not None:
            if current in (self.region_search, self.region_search_results):
                return True
            current = current.parentWidget()
        return False

    def _set_parameter_mouse_wheel(self, enabled: bool) -> None:
        self.mouse_wheel_parameters_enabled = enabled
        settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
        settings.setValue("enable_parameter_mouse_wheel", enabled)
        settings.sync()
        state = "enabled" if enabled else "disabled"
        if hasattr(self, "status"):
            self.status.setText(f"Mouse-wheel parameter adjustment {state}.")

    @staticmethod
    def _spinbox(low, high, step, value, decimals) -> QtWidgets.QDoubleSpinBox:
        widget = QtWidgets.QDoubleSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(step)
        widget.setDecimals(decimals)
        widget.setValue(value)
        return widget

    def _show_about(self) -> None:
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("About fMOST Brain Viewer")
        dialog.setWindowIcon(QtGui.QIcon(str(APP_ICON)))
        dialog.setIconPixmap(
            QtGui.QPixmap(str(APP_LOGO)).scaled(
                180, 125,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
        dialog.setText(f"<b>fMOST Brain Viewer</b><br>Version {__version__}")
        dialog.setInformativeText(
            "Interactive visualization of registered fMOST neurons, soma locations, "
            "and Allen CCF brain regions.<br><br>"
            "Orion HU · Li Bo Lab · Westlake University<br>"
            '<a href="https://github.com/orionhu99/fMOST-Brain-Viewer">GitHub repository</a>'
            "<br><br>Atlas data: Allen Mouse Brain CCFv3 (2017)<br>"
            '<a href="https://alleninstitute.org/terms-of-use/">Allen Terms of Use</a>'
            " · "
            '<a href="https://alleninstitute.org/citation-policy/">Citation Policy</a>'
        )
        dialog.setTextFormat(QtCore.Qt.TextFormat.RichText)
        dialog.exec()

    def _check_for_updates(self) -> None:
        if self.update_check_thread is not None:
            self.update_check_progress.show()
            return
        self.update_check_progress = QtWidgets.QProgressDialog(
            "Checking GitHub for updates...", "", 0, 0, self
        )
        self.update_check_progress.setCancelButton(None)
        self.update_check_progress.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.update_check_progress.setMinimumDuration(0)
        self.update_check_progress.show()
        self.update_check_thread = QtCore.QThread(self)
        self.update_check_worker = UpdateCheckWorker()
        self.update_check_worker.moveToThread(self.update_check_thread)
        self.update_check_thread.started.connect(self.update_check_worker.run)
        self.update_check_worker.completed.connect(self._update_check_completed)
        self.update_check_worker.completed.connect(self.update_check_thread.quit)
        self.update_check_worker.completed.connect(self.update_check_worker.deleteLater)
        self.update_check_thread.finished.connect(self._update_check_finished)
        self.update_check_thread.start()

    @QtCore.Slot(object, object)
    def _update_check_completed(self, releases, error) -> None:
        if self.update_check_progress is not None:
            self.update_check_progress.close()
        if error is not None:
            LOGGER.error(
                "Could not check for application updates",
                exc_info=(type(error), error, error.__traceback__),
            )
            QtWidgets.QMessageBox.warning(
                self,
                "Update check failed",
                f"{type(error).__name__}: {error}\n\nCheck your internet connection or use the "
                "GitHub Releases page.",
            )
            return
        if not releases:
            QtWidgets.QMessageBox.information(
                self, "No update available", f"Version {__version__} is up to date."
            )
            return
        dialog = UpdateDialog(releases, self)
        dialog.release_button.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(
                QtCore.QUrl(str(dialog.selected_release().get("html_url", "")))
            )
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        release = dialog.selected_release()
        asset = release_installer_asset(release)
        if asset is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Installer unavailable",
                "This release does not contain the expected Windows installer. "
                "Open its release page to download manually.",
            )
            return
        destination = (
            LOCAL_APP_DATA / "fMOST Brain Viewer" / "updates" / str(asset["name"])
        )
        progress = QtWidgets.QProgressDialog(
            "Downloading update...", "Cancel", 0, max(int(asset.get("size", 0)), 1), self
        )
        progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)

        def report(downloaded: int, total: int) -> None:
            progress.setMaximum(max(total, 1))
            progress.setValue(downloaded)
            QtWidgets.QApplication.processEvents()
            if progress.wasCanceled():
                raise AtlasSetupCancelled("Update download cancelled.")

        try:
            installer = download_release_asset(asset, destination, report)
        except AtlasSetupCancelled:
            progress.close()
            return
        except Exception as exc:
            progress.close()
            LOGGER.exception("Could not download application update")
            QtWidgets.QMessageBox.critical(
                self, "Update download failed", f"{type(exc).__name__}: {exc}"
            )
            return
        progress.close()
        launched = launch_update_installer(installer)
        if not launched:
            QtWidgets.QMessageBox.critical(
                self, "Cannot start installer", f"Run this file manually:\n{installer}"
            )
            return
        QtWidgets.QApplication.quit()

    @QtCore.Slot()
    def _update_check_finished(self) -> None:
        thread = self.update_check_thread
        self.update_check_worker = None
        self.update_check_thread = None
        self.update_check_progress = None
        if thread is not None:
            thread.deleteLater()

    def _open_log_folder(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(LOG_DIR.resolve()))
        ):
            QtWidgets.QMessageBox.warning(
                self, "Cannot open log folder", f"Open this folder manually:\n{LOG_DIR}"
            )

    def _show_capture(self) -> None:
        CaptureDialog(self).exec()

    def _show_record_gif(self) -> None:
        RecordGifDialog(self).exec()

    def _configure_atlas(self) -> None:
        if ensure_atlas_configured(self, force=True):
            QtWidgets.QMessageBox.information(
                self,
                "Atlas configured",
                f"Allen CCF atlas folder saved:\n{ATLAS_ROOT}\n\n"
                "The viewer will now close. Reopen it to load the new atlas.",
            )
            self.close()

    def _prepare_region_library(self) -> None:
        choose_and_prepare_region_library(self, self.annotation, self.ontology)

    def _populate_dataset_list(self) -> None:
        self.dataset_list.blockSignals(True)
        self.dataset_list.clear()
        self.dataset_items.clear()
        for dataset in self.datasets.values():
            neuron_count = sum(1 for _path in dataset.axon_dir.glob("*.swc"))
            match_summary = (
                f"{dataset.matched_axon_count} matched, "
                f"{dataset.unmatched_axon_count} unmatched, "
                f"{dataset.duplicate_axon_count} duplicate"
            )
            item = QtWidgets.QListWidgetItem(
                color_icon(dataset.soma_color),
                f"{self._dataset_label(dataset)}  —  {len(dataset.soma_points)} somas, "
                f"{neuron_count} neurons ({match_summary})",
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, dataset.key)
            tooltip = f"{dataset.project}\nAxon-soma matching: {match_summary}"
            if dataset.manual_region_path is not None:
                tooltip += (
                    f"\nManual soma regions: {dataset.manual_region_applied} applied"
                    f", {dataset.manual_region_ignored} ignored"
                    f"\n{dataset.manual_region_path}"
                )
            item.setToolTip(tooltip)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Checked if dataset.enabled
                else QtCore.Qt.CheckState.Unchecked
            )
            self.dataset_list.addItem(item)
            self.dataset_items[dataset.key] = item
        self.dataset_list.blockSignals(False)

    def _dataset_label(self, dataset: BrainDataset) -> str:
        duplicate = sum(
            value.brain_id == dataset.brain_id for value in self.datasets.values()
        ) > 1
        return (
            f"{dataset.brain_id} ({dataset.project.name})"
            if duplicate else dataset.brain_id
        )

    def _create_soma_actor(self, dataset: BrainDataset) -> None:
        actor_index = sum(
            value.soma_actor is not None for value in self.datasets.values()
        )
        dataset.soma_mesh = pv.PolyData(dataset.soma_points.copy())
        dataset.soma_actor = self.plotter.add_points(
            dataset.soma_mesh,
            color=dataset.soma_color,
            point_size=self.soma_size.value(),
            render_points_as_spheres=True,
            name=f"somas_{actor_index}_{dataset.brain_id}",
            render=False,
        )
        dataset.soma_actor.SetVisibility(dataset.enabled)

    def _index_dataset_neurons(self, dataset: BrainDataset) -> None:
        header = QtWidgets.QListWidgetItem(f"▾ Dataset {self._dataset_label(dataset)}")
        header.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        header.setToolTip(str(dataset.project))
        self.neuron_list.addItem(header)
        paths = sorted(dataset.axon_dir.glob("*.swc"))
        valid_soma_ids = set(dataset.soma_point_by_id)
        inferred = {
            path: infer_neuron_soma_id(path.name, valid_soma_ids) for path in paths
        }
        inferred_counts = Counter(
            neuron_id for neuron_id in inferred.values() if neuron_id is not None
        )
        dataset.matched_axon_count = sum(
            neuron_id is not None and inferred_counts[neuron_id] == 1
            for neuron_id in inferred.values()
        )
        dataset.duplicate_axon_count = sum(
            neuron_id is not None and inferred_counts[neuron_id] > 1
            for neuron_id in inferred.values()
        )
        dataset.unmatched_axon_count = (
            len(paths) - dataset.matched_axon_count - dataset.duplicate_axon_count
        )
        for path in paths:
            inferred_id = inferred[path]
            if inferred_id is None:
                neuron_id = None
                match_status = "Unmatched: no unique soma ID in filename"
                suffix = "  [Unmatched]"
            elif inferred_counts[inferred_id] > 1:
                neuron_id = None
                match_status = f"Duplicate: multiple axons reference soma ID {inferred_id}"
                suffix = "  [Duplicate]"
            else:
                neuron_id = inferred_id
                match_status = f"Matched soma ID {inferred_id}"
                suffix = ""
            key = f"{dataset.key}::{path.stem}"
            self.axon_sources[key] = path
            self.neuron_ids[key] = neuron_id
            self.neuron_match_status[key] = match_status
            self.neuron_datasets[key] = dataset.key
            self.manual_colors.setdefault(
                key, deterministic_color(f"neuron:{key}")
            )
            item = QtWidgets.QListWidgetItem(
                color_icon(self.manual_colors[key]), f"    {path.stem}{suffix}"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
            item.setToolTip(
                f"Dataset {dataset.brain_id}\n{match_status}\n{path}"
            )
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            self.neuron_list.addItem(item)
            self.neuron_items[key] = item

    def _ensure_axon_actor(self, key: str):
        actor = self.axon_actors.get(key)
        if actor is not None:
            self._touch_axon_actor(key)
            return actor
        points, edges = read_swc(self.axon_sources[key])
        size = self.axon_width.value()
        actor = self.plotter.add_mesh(
            axon_tube_mesh(points, edges, size),
            color=self.manual_colors[key],
            line_width=1.0,
            render_lines_as_tubes=False,
            smooth_shading=size > 0,
            name=f"axon_{len(self.axon_actors)}",
            render=False,
        )
        actor.SetVisibility(False)
        self.axon_actors[key] = actor
        self._touch_axon_actor(key)
        return actor

    def _touch_axon_actor(self, key: str) -> None:
        self.axon_actor_use_counter += 1
        self.axon_actor_last_used[key] = self.axon_actor_use_counter

    def _remove_axon_actor(self, key: str) -> None:
        actor = self.axon_actors.pop(key, None)
        self.axon_actor_last_used.pop(key, None)
        if actor is not None:
            self.plotter.remove_actor(actor, reset_camera=False, render=False)

    def _trim_hidden_axon_cache(self) -> int:
        hidden = []
        hidden_points = 0
        for key, actor in self.axon_actors.items():
            if actor.GetVisibility():
                continue
            points = int(actor.mapper.dataset.n_points)
            hidden.append((self.axon_actor_last_used.get(key, 0), key, points))
            hidden_points += points
        hidden.sort()
        removed = 0
        while hidden and (
            len(hidden) > MAX_HIDDEN_AXON_ACTORS
            or hidden_points > MAX_HIDDEN_AXON_POINTS
        ):
            _last_used, key, points = hidden.pop(0)
            self._remove_axon_actor(key)
            hidden_points -= points
            removed += 1
        return removed

    def _dataset_enabled_for_neuron(self, key: str) -> bool:
        return self.datasets[self.neuron_datasets[key]].enabled

    def _apply_neuron_visibility(self, key: str, selected: bool) -> None:
        visible = selected and self._dataset_enabled_for_neuron(key)
        actor = self.axon_actors.get(key)
        if visible and actor is None:
            actor = self._ensure_axon_actor(key)
        if actor is not None:
            actor.SetVisibility(visible)
            self._touch_axon_actor(key)

    def _apply_bulk_neuron_selection(self, keys: list[str], checked: bool) -> None:
        progress = None
        load_keys = [
            key for key in keys
            if checked and self._dataset_enabled_for_neuron(key) and key not in self.axon_actors
        ]
        if load_keys:
            progress = QtWidgets.QProgressDialog(
                "Loading selected axons...", "Cancel", 0, len(load_keys), self
            )
            progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
            progress.setMinimumDuration(0)
        self.neuron_list.blockSignals(True)
        loaded = 0
        for key in keys:
            if progress is not None and progress.wasCanceled():
                break
            self.neuron_items[key].setCheckState(
                QtCore.Qt.CheckState.Checked if checked
                else QtCore.Qt.CheckState.Unchecked
            )
            self._apply_neuron_visibility(key, checked)
            if progress is not None and key in load_keys:
                loaded += 1
                progress.setValue(loaded)
                progress.setLabelText(f"Loading axons: {loaded} / {len(load_keys)}")
                QtWidgets.QApplication.processEvents()
        self.neuron_list.blockSignals(False)
        if progress is not None:
            progress.close()
        self._sync_neuron_region_checks()
        self._refresh_soma_points(render=False)
        self._trim_hidden_axon_cache()
        self.plotter.render()

    def _load_scene(self) -> None:
        self._report_progress(15, "Loading or creating whole-brain surface...")
        brain_surface = load_or_create_whole_brain_surface(self.annotation)
        self.surface_actor = self.plotter.add_mesh(
            brain_surface, color="#d9d9d9", opacity=self.template_opacity.value(),
            smooth_shading=True, name="template_surface",
        )

        for dataset in self.datasets.values():
            self._create_soma_actor(dataset)

        region_ids = sorted(self.region_counts)
        self._report_progress(42, f"Loading {len(region_ids)} brain-region surfaces...")
        region_surfaces = load_or_create_region_surfaces(self.annotation, region_ids)
        self.region_list.blockSignals(True)
        for region_id in region_ids:
            self._create_region_item(
                region_id,
                self.region_counts[region_id],
                region_surfaces.get(region_id),
                checked=False,
            )
        self.region_list.blockSignals(False)

        total_neurons = sum(
            1 for dataset in self.datasets.values() for _path in dataset.axon_dir.glob("*.swc")
        )
        self._report_progress(55, f"Indexing {total_neurons} neurons...")
        self.neuron_list.blockSignals(True)
        for dataset in self.datasets.values():
            self._index_dataset_neurons(dataset)
        self.neuron_list.blockSignals(False)
        self._populate_dataset_list()
        self._build_neuron_region_list()

        self.axes_actor = self.plotter.add_axes(line_width=3)
        self.bounds_actor = self.plotter.show_bounds(
            grid="back", location="outer", xtitle="ML / X (um)",
            ytitle="DV / Y (um)", ztitle="Anterior-posterior / Z (um)",
        )
        self.bounds_actor.SetVisibility(self.grid_check.isChecked())
        self._update_slice(self.slice_slider.value())
        self._update_region_legend()
        self._report_progress(96, "Finishing the 3D scene...")
        self._reset_default_view(render=False)
        self.status.setText(
            f"Ready: {len(self.datasets)} brains; {len(self.neuron_items)} neurons; "
            f"{sum(dataset.matched_axon_count for dataset in self.datasets.values())} matched, "
            f"{sum(dataset.unmatched_axon_count for dataset in self.datasets.values())} unmatched, "
            f"{sum(dataset.duplicate_axon_count for dataset in self.datasets.values())} duplicate; "
            f"{sum(len(dataset.soma_points) for dataset in self.datasets.values())} somas; "
            f"{len(region_ids)} regions; {self.unassigned_soma_count} unassigned; "
            f"{sum(dataset.manual_region_applied for dataset in self.datasets.values())} "
            "manual region correction(s)."
        )

    def _create_region_item(
        self,
        region_id: int,
        soma_count: int,
        surface: pv.PolyData | None,
        checked: bool,
    ) -> None:
        info = self.ontology.get(
            region_id,
            {"acronym": str(region_id), "name": "Unknown structure"},
        )
        color = self.region_colors.setdefault(region_id, deterministic_color(region_id))
        if surface is not None:
            actor = self.plotter.add_mesh(
                surface,
                color=color,
                opacity=self.region_opacity.value(),
                smooth_shading=True,
                name=f"region_{region_id}",
            )
            actor.SetVisibility(checked)
            self.region_actors[region_id] = actor
        item = QtWidgets.QListWidgetItem(
            color_icon(color),
            f"{info['acronym']} — {info['name']} — {soma_count} soma(s)",
        )
        item.setData(QtCore.Qt.ItemDataRole.UserRole, region_id)
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        )
        self.region_list.addItem(item)
        self.region_items[region_id] = item

    def _region_matches(self, query: str) -> list[int]:
        return region_search_matches(self.ontology, query)

    def _update_region_search_results(self, _text: str = "") -> None:
        matches = self._region_matches(self.region_search.text())
        self.region_search_results.clear()
        if not self.region_search.text().strip():
            self.region_search_results.hide()
            return
        for region_id in matches[:50]:
            info = self.ontology[region_id]
            suffix = "  [Added]" if region_id in self.region_items else ""
            item = QtWidgets.QListWidgetItem(
                f"{info['acronym']} — {info['name']}  [ID {region_id}]{suffix}"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, region_id)
            self.region_search_results.addItem(item)
        if len(matches) > 50:
            item = QtWidgets.QListWidgetItem(f"… {len(matches) - 50} more matches")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.region_search_results.addItem(item)
        if not matches:
            item = QtWidgets.QListWidgetItem("No matching Allen atlas structure")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.region_search_results.addItem(item)
        elif self.region_search_results.count():
            self.region_search_results.setCurrentRow(0)
        self.region_search_results.show()

    def _add_region_from_search(self) -> None:
        item = self.region_search_results.currentItem()
        if item is None:
            self._update_region_search_results()
            item = self.region_search_results.currentItem()
        if item is None:
            return
        region_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if region_id is None:
            return
        if self._add_custom_region(int(region_id), checked=True, show_errors=True):
            self._clear_region_search()

    def _clear_region_search(self) -> None:
        self.region_search.clear()
        self.region_search_results.clear()
        self.region_search_results.hide()

    def _add_custom_region(
        self,
        region_id: int,
        checked: bool,
        show_errors: bool,
    ) -> bool:
        if region_id in self.region_items:
            item = self.region_items[region_id]
            item.setCheckState(
                QtCore.Qt.CheckState.Checked if checked else item.checkState()
            )
            self.region_list.setCurrentItem(item)
            self.region_list.scrollToItem(item)
            return True
        info = self.ontology.get(region_id)
        if info is None:
            if show_errors:
                QtWidgets.QMessageBox.warning(
                    self, "Unknown atlas region", f"Atlas ID {region_id} is not in ontology."
                )
            return False
        self.status.setText(
            f"Loading {info['acronym']} and descendant region surfaces..."
        )
        QtWidgets.QApplication.processEvents()
        cache = CACHE_ROOT / f"region_tree_{region_id}_step2.vtp"
        if cache.exists() and cache.stat().st_mtime >= self.annotation.path.stat().st_mtime:
            surface = pv.read(cache)
        else:
            try:
                surface = run_region_cache_job(
                    self, self.annotation, self.ontology, region_id=region_id
                )
            except Exception as exc:
                if show_errors:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Could not prepare brain region",
                        f"{type(exc).__name__}: {exc}\n\nCache: {CACHE_ROOT}",
                    )
                return False
        if surface is None or not surface.n_points:
            if show_errors:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Region absent from volume",
                    f"{info['acronym']} (ID {region_id}) and its descendants have no "
                    "voxels in this annotation volume.",
                )
            return False
        self.region_list.blockSignals(True)
        self._create_region_item(region_id, 0, surface, checked)
        self.region_list.blockSignals(False)
        self.custom_region_ids.add(region_id)
        self.region_list.setCurrentItem(self.region_items[region_id])
        self.region_list.scrollToItem(self.region_items[region_id])
        self.status.setText(f"Added atlas region: {info['acronym']} — {info['name']}")
        self._update_region_legend(render=False)
        self.plotter.render()
        return True

    def _restore_config(self) -> None:
        self.template_opacity.setValue(
            float(self.config.get("brain_opacity", DEFAULT_BRAIN_OPACITY))
        )
        self.axon_width.setValue(max(0.0, float(self.config.get("axon_width", 0.0))))
        self.soma_size.setValue(float(self.config.get("soma_size", 10.0)))
        self.region_opacity.setValue(float(self.config.get("region_opacity", 0.55)))
        self.soma_check.setChecked(bool(self.config.get("show_all_somas", True)))
        self.grid_check.setChecked(bool(self.config.get("show_grid", False)))
        self.legend_check.setChecked(bool(self.config.get("show_region_legend", True)))
        self.current_brain_style = "Surface"
        self.surface_rendering_action.setChecked(True)
        self.color_mode.setCurrentText(self.config.get("color_mode", "Independent / manual"))
        for region_id in self.config.get("custom_regions", []):
            self._add_custom_region(int(region_id), checked=False, show_errors=False)
        visible_regions = set(self.config.get("visible_regions", []))
        self.region_list.blockSignals(True)
        for region_id, item in self.region_items.items():
            checked = region_id in visible_regions
            item.setCheckState(
                QtCore.Qt.CheckState.Checked if checked
                else QtCore.Qt.CheckState.Unchecked
            )
            actor = self.region_actors.get(region_id)
            if actor is not None:
                actor.SetVisibility(checked)
        self.region_list.blockSignals(False)
        visible_neurons = set(self.config.get("visible_neurons", []))
        self.neuron_list.blockSignals(True)
        for key, item in self.neuron_items.items():
            item.setCheckState(
                QtCore.Qt.CheckState.Checked if key in visible_neurons
                else QtCore.Qt.CheckState.Unchecked
            )
        self.neuron_list.blockSignals(False)
        selected_keys = list(visible_neurons & self.neuron_items.keys())
        if selected_keys:
            self._apply_bulk_neuron_selection(selected_keys, True)
        self._sync_neuron_region_checks()
        self._refresh_soma_points(render=False)
        self._update_region_legend(render=False)
        camera = self.config.get("camera_position")
        if valid_camera_position(camera):
            self.plotter.camera_position = camera
        else:
            self._reset_default_view(render=False)
        self.plotter.render()

    def _reset_default_view(self, _checked=False, render=True) -> None:
        self.plotter.view_vector((1.0, 0.0, 0.0), viewup=(0.0, -1.0, 0.0), render=False)
        self.plotter.reset_camera(render=False)
        if render:
            self.plotter.render()

    def _update_slice(self, index: int) -> None:
        position_um = index * self.annotation.spacing[2]
        self.slice_label.setText(
            f"Slice {index}/{self.annotation.shape[2] - 1}   Z = {position_um:.0f} um"
        )
        plane = annotation_slice_grid(
            self.annotation.coronal(index),
            self.annotation.spacing[:2],
            position_um,
            self.ontology,
        )
        if self.slice_actor is not None:
            self.plotter.remove_actor(self.slice_actor, render=False)
        self.slice_actor = self.plotter.add_mesh(
            plane, scalars="region_rgba", rgba=True, opacity=0.72,
            show_scalar_bar=False, name="annotation_slice", render=False,
        )
        self.slice_actor.SetVisibility(self.slice_check.isChecked())
        self.plotter.render()

    def _set_brain_visible(self, visible: bool) -> None:
        self._set_brain_style(self.current_brain_style, visible)

    def _ensure_volume_actor(self) -> bool:
        if self.volume_actor is not None:
            return True
        progress = QtWidgets.QProgressDialog(
            "Loading the 25 um atlas volume...", "", 0, 0, self
        )
        progress.setWindowTitle("Loading volume rendering")
        progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()
        QtWidgets.QApplication.processEvents()
        try:
            template = load_volume(TEMPLATE_25)
            self.template_range = template.get_data_range("intensity")
            self.volume_actor = self.plotter.add_volume(
                template, scalars="intensity", cmap="gray",
                opacity=[0.0, 0.0, 0.12, 0.35], shade=False,
                show_scalar_bar=False, name="template_volume", render=False,
            )
            self.volume_actor.SetVisibility(False)
            return True
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Cannot load volume rendering", str(exc)
            )
            return False
        finally:
            progress.close()

    def _set_brain_style(self, style: str, visible: bool | None = None) -> None:
        if not hasattr(self, "surface_actor"):
            return
        if visible is None:
            visible = self.template_check.isChecked()
        if style == "Volume" and not self._ensure_volume_actor():
            style = "Surface"
        self.current_brain_style = style
        self.surface_rendering_action.setChecked(style == "Surface")
        self.volume_rendering_action.setChecked(style == "Volume")
        self.surface_actor.SetVisibility(visible and style == "Surface")
        if self.volume_actor is not None:
            self.volume_actor.SetVisibility(visible and style == "Volume")
        self._set_template_opacity(self.template_opacity.value(), render=False)
        self.status.setText(f"3D brain rendering: {style}")
        self.plotter.render()

    def _set_template_opacity(self, opacity: float, render: bool = True) -> None:
        if not hasattr(self, "surface_actor"):
            return
        self.surface_actor.GetProperty().SetOpacity(opacity)
        if self.volume_actor is not None and self.template_range is not None:
            opacity_function = self.volume_actor.GetProperty().GetScalarOpacity()
            low, high = self.template_range
            span = high - low
            opacity_function.RemoveAllPoints()
            opacity_function.AddPoint(low, 0.0)
            opacity_function.AddPoint(low + span / 3, 0.0)
            opacity_function.AddPoint(low + 2 * span / 3, opacity * 0.34)
            opacity_function.AddPoint(high, opacity)
        if render:
            self.plotter.render()

    def _set_slice_visible(self, visible: bool) -> None:
        if self.slice_actor is not None:
            self.slice_actor.SetVisibility(visible)
            self.plotter.render()

    def _refresh_soma_points(self, *_args, render: bool = True) -> None:
        if not self.datasets or not all(
            dataset.soma_mesh is not None for dataset in self.datasets.values()
        ):
            return
        for dataset in self.datasets.values():
            if self.soma_check.isChecked():
                points = dataset.soma_points
            else:
                points = [
                    dataset.soma_point_by_id[self.neuron_ids[key]]
                    for key, item in self.neuron_items.items()
                    if self.neuron_datasets[key] == dataset.key
                    and item.checkState() == QtCore.Qt.CheckState.Checked
                    and self.neuron_ids[key] in dataset.soma_point_by_id
                ]
                points = np.asarray(points, dtype=np.float32).reshape((-1, 3))
            dataset.soma_mesh.points = points
            dataset.soma_mesh.Modified()
            dataset.soma_actor.SetVisibility(dataset.enabled)
        if render:
            self.plotter.render()

    def _set_grid_visible(self, visible: bool) -> None:
        if hasattr(self, "bounds_actor"):
            self.bounds_actor.SetVisibility(visible)
            self.plotter.render()

    def _update_region_legend(self, *_args, render: bool = True) -> None:
        if self.legend_actor is not None:
            self.plotter.remove_actor(self.legend_actor, reset_camera=False, render=False)
            self.legend_actor = None
        if not hasattr(self, "legend_check") or not self.legend_check.isChecked():
            if render:
                self.plotter.render()
            return
        entries = []
        for region_id, actor in self.region_actors.items():
            if actor.GetVisibility():
                info = self.ontology.get(region_id, {"acronym": str(region_id)})
                entries.append((
                    str(info["acronym"]), self.region_colors[region_id], region_id
                ))
        if entries:
            entries.sort(key=lambda entry: (entry[0].casefold(), entry[2]))
            maximum_rows = 8
            if len(entries) > maximum_rows:
                hidden = len(entries) - (maximum_rows - 1)
                entries = entries[:maximum_rows - 1] + [
                    (f"+{hidden} more", "#777777", -1)
                ]
            labels = [(label, color) for label, color, _region_id in entries]
            height = 0.014 + 0.021 * len(labels)
            self.legend_actor = self.plotter.add_legend(
                labels,
                bcolor="#181b1f",
                border=False,
                size=(0.085, height),
                loc="upper right",
                face="circle",
                font_family="arial",
                background_opacity=0.46,
            )
            self.legend_actor.SetPadding(1)
            self.legend_actor.SetLockBorder(True)
            text = self.legend_actor.GetEntryTextProperty()
            segoe_ui = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
            if segoe_ui.is_file():
                text.SetFontFamily(4)  # VTK_FONT_FILE
                text.SetFontFile(str(segoe_ui))
            else:
                text.SetFontFamilyToArial()
            text.SetFontSize(9)
            text.SetBold(False)
            text.SetShadow(False)
            text.SetColor(0.92, 0.94, 0.96)
        if render:
            self.plotter.render()

    def capture_content_visibility(self) -> dict[str, bool]:
        return {
            "brain": bool(
                self.surface_actor.GetVisibility()
                or (self.volume_actor is not None and self.volume_actor.GetVisibility())
            ),
            "slice": bool(self.slice_actor and self.slice_actor.GetVisibility()),
            "somas": any(
                bool(dataset.soma_actor and dataset.soma_actor.GetVisibility())
                for dataset in self.datasets.values()
            ),
            "axons": any(actor.GetVisibility() for actor in self.axon_actors.values()),
            "regions": any(actor.GetVisibility() for actor in self.region_actors.values()),
            "grid": bool(self.bounds_actor.GetVisibility()),
            "axes": bool(self.axes_actor.GetVisibility()),
            "legend": bool(self.legend_actor and self.legend_actor.GetVisibility()),
        }

    def _export_actor_groups(self) -> dict[str, list]:
        return {
            "brain": [
                actor for actor in (self.surface_actor, self.volume_actor)
                if actor is not None
            ],
            "slice": [self.slice_actor] if self.slice_actor is not None else [],
            "somas": [
                dataset.soma_actor for dataset in self.datasets.values()
                if dataset.soma_actor is not None
            ],
            "axons": list(self.axon_actors.values()),
            "regions": list(self.region_actors.values()),
            "grid": [self.bounds_actor],
            "axes": [self.axes_actor],
            "legend": [self.legend_actor] if self.legend_actor is not None else [],
        }

    @staticmethod
    def _set_export_visibility(
        actor_groups: dict[str, list], contents: dict[str, bool]
    ) -> list[tuple[object, bool]]:
        original = [
            (actor, bool(actor.GetVisibility()))
            for actors in actor_groups.values()
            for actor in actors
        ]
        for key, actors in actor_groups.items():
            include = bool(contents.get(key, False))
            for actor in actors:
                actor.SetVisibility(include and bool(actor.GetVisibility()))
        return original

    def export_capture(
        self,
        path: Path,
        contents: dict[str, bool],
        scale: int,
        transparent: bool,
        jpeg_quality: int,
    ) -> None:
        actor_groups = self._export_actor_groups()
        original = self._set_export_visibility(actor_groups, contents)
        image_data = None
        try:
            self.plotter.render()
            image_data = self.plotter.screenshot(
                transparent_background=transparent,
                return_img=True,
                scale=scale,
            )
        finally:
            for actor, visible in original:
                actor.SetVisibility(visible)
            self.plotter.render()

        if image_data is None:
            raise RuntimeError("The 3D renderer did not return an image.")
        image = Image.fromarray(np.asarray(image_data))
        suffix = path.suffix.lower()
        if suffix in (".tif", ".tiff"):
            image.save(path, format="TIFF", compression="tiff_lzw")
        elif suffix == ".png":
            image.save(path, format="PNG", optimize=True)
        elif suffix in (".jpg", ".jpeg"):
            image.convert("RGB").save(
                path, format="JPEG", quality=jpeg_quality, subsampling=0, optimize=True
            )
        elif suffix == ".bmp":
            image.convert("RGB").save(path, format="BMP")
        else:
            raise ValueError(f"Unsupported capture format: {suffix}")
        self.status.setText(f"Capture saved: {path}")

    def export_rotating_gif(
        self,
        path: Path,
        contents: dict[str, bool],
        frames: int,
        duration_seconds: float,
        scale: int,
        direction: int,
    ) -> bool:
        actor_groups = self._export_actor_groups()
        original_visibility = self._set_export_visibility(actor_groups, contents)
        original_camera = [tuple(vector) for vector in self.plotter.camera_position]
        images: list[Image.Image] = []
        temporary = path.with_suffix(path.suffix + ".part")
        progress = QtWidgets.QProgressDialog(
            "Rendering rotating GIF...", "Cancel", 0, frames, self
        )
        progress.setWindowTitle("Recording GIF")
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        cancelled = False
        try:
            self.plotter.render()
            angle = direction * 360.0 / frames
            for index in range(frames):
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    cancelled = True
                    break
                image_data = self.plotter.screenshot(return_img=True, scale=scale)
                if image_data is None:
                    raise RuntimeError("The 3D renderer did not return an image.")
                image = Image.fromarray(np.asarray(image_data)).convert("RGB")
                images.append(image.quantize(colors=256))
                self.plotter.camera.Azimuth(angle)
                self.plotter.render()
                progress.setValue(index + 1)
            if not cancelled:
                progress.setLabelText("Encoding GIF...")
                QtWidgets.QApplication.processEvents()
                frame_duration = max(20, round(duration_seconds * 1000 / frames))
                images[0].save(
                    temporary,
                    format="GIF",
                    save_all=True,
                    append_images=images[1:],
                    duration=frame_duration,
                    loop=0,
                    disposal=2,
                    optimize=False,
                )
                temporary.replace(path)
        finally:
            progress.close()
            for actor, visible in original_visibility:
                actor.SetVisibility(visible)
            self.plotter.camera_position = original_camera
            self.plotter.render()
            if temporary.exists():
                temporary.unlink()
        if cancelled:
            self.status.setText("GIF recording cancelled.")
            return False
        self.status.setText(f"Rotating GIF saved: {path}")
        return True

    def _step_coronal(self, step: int) -> None:
        self.slice_slider.setValue(self.slice_slider.value() + step)

    def _start_coronal_repeat(self, step: int) -> None:
        self.coronal_repeat_timer.stop()
        self.coronal_repeat_step = step
        self._step_coronal(step)
        self.coronal_repeat_timer.start()

    def _repeat_coronal_step(self) -> None:
        if self.coronal_repeat_step:
            self._step_coronal(self.coronal_repeat_step)

    def _stop_coronal_repeat(self) -> None:
        self.coronal_repeat_timer.stop()
        self.coronal_repeat_step = 0

    def _set_axon_width(self, width: float) -> None:
        visible = [key for key, actor in self.axon_actors.items() if actor.GetVisibility()]
        for key in list(self.axon_actors):
            self._remove_axon_actor(key)
        for key in visible:
            self._ensure_axon_actor(key).SetVisibility(True)
        self.plotter.render()

    def _set_soma_size(self, size: float) -> None:
        for dataset in self.datasets.values():
            if dataset.soma_actor is not None:
                dataset.soma_actor.GetProperty().SetPointSize(size)
        self.plotter.render()

    def _set_region_opacity(self, opacity: float) -> None:
        for actor in self.region_actors.values():
            actor.GetProperty().SetOpacity(opacity)
        self.plotter.render()

    def _region_toggled(self, item: QtWidgets.QListWidgetItem) -> None:
        region_id = int(item.data(QtCore.Qt.ItemDataRole.UserRole))
        actor = self.region_actors.get(region_id)
        if actor is not None:
            actor.SetVisibility(item.checkState() == QtCore.Qt.CheckState.Checked)
            self._update_region_legend(render=False)
            self.plotter.render()

    def _build_neuron_region_list(self) -> None:
        self.neurons_by_region.clear()
        self.neuron_region_items.clear()
        for key, neuron_id in self.neuron_ids.items():
            dataset = self.datasets[self.neuron_datasets[key]]
            status = self.neuron_match_status.get(key, "")
            if status.startswith("Duplicate"):
                group = -3
            elif neuron_id is None:
                group = -2
            else:
                region_id = dataset.soma_regions.get(neuron_id)
                group = int(region_id) if region_id is not None else -1
            self.neurons_by_region.setdefault(group, []).append(key)

        def sort_key(region_id: int) -> tuple[bool, str]:
            if region_id < 0:
                return True, f"ZZZ{region_id}"
            return False, str(
                self.ontology.get(region_id, {}).get("acronym", region_id)
            )

        self.neuron_region_list.blockSignals(True)
        self.neuron_region_list.clear()
        for region_id in sorted(self.neurons_by_region, key=sort_key):
            members = self.neurons_by_region[region_id]
            if region_id == -1:
                label = f"Unassigned / outside atlas — {len(members)} neurons"
            elif region_id == -2:
                label = f"Unmatched axon — {len(members)} neurons"
            elif region_id == -3:
                label = f"Duplicate soma ID — {len(members)} neurons"
            else:
                info = self.ontology.get(
                    region_id,
                    {"acronym": str(region_id), "name": "Unknown structure"},
                )
                label = (
                    f"{info['acronym']} — {info['name']} — {len(members)} neurons"
                )
            item = QtWidgets.QListWidgetItem(label)
            counts = Counter(
                self._dataset_label(self.datasets[self.neuron_datasets[key]])
                for key in members
            )
            item.setToolTip("\n".join(
                f"Brain {brain_id}: {count} neuron(s)"
                for brain_id, count in sorted(counts.items())
            ))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, region_id)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            self.neuron_region_list.addItem(item)
            self.neuron_region_items[region_id] = item
        self.neuron_region_list.blockSignals(False)

    def _set_neuron_mode(self, mode: str) -> None:
        self.neuron_stack.setCurrentIndex(1 if mode == "By soma region" else 0)
        self._sync_neuron_region_checks()

    def _sync_neuron_region_checks(self) -> None:
        self.neuron_region_list.blockSignals(True)
        for region_id, members in self.neurons_by_region.items():
            selected = [
                self.neuron_items[key].checkState() == QtCore.Qt.CheckState.Checked
                for key in members
            ]
            if selected and all(selected):
                state = QtCore.Qt.CheckState.Checked
            elif any(selected):
                state = QtCore.Qt.CheckState.PartiallyChecked
            else:
                state = QtCore.Qt.CheckState.Unchecked
            self.neuron_region_items[region_id].setCheckState(state)
        self.neuron_region_list.blockSignals(False)

    def _neuron_region_toggled(self, item: QtWidgets.QListWidgetItem) -> None:
        region_id = int(item.data(QtCore.Qt.ItemDataRole.UserRole))
        checked = item.checkState() == QtCore.Qt.CheckState.Checked
        self._apply_bulk_neuron_selection(self.neurons_by_region[region_id], checked)

    def _choose_neuron_color(self, item: QtWidgets.QListWidgetItem) -> None:
        key = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if key is None:
            return
        key = str(key)
        chosen = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.manual_colors[key]), self, f"Color for {item.text().strip()}"
        )
        if chosen.isValid():
            color = chosen.name()
            self.manual_colors[key] = color
            item.setIcon(color_icon(color))
            actor = self.axon_actors.get(key)
            if actor is not None:
                actor.GetProperty().SetColor(
                    chosen.redF(), chosen.greenF(), chosen.blueF()
                )
                self.plotter.render()

    def _neuron_toggled(self, item: QtWidgets.QListWidgetItem) -> None:
        key = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if key is None:
            return
        key = str(key)
        self._apply_neuron_visibility(
            key, item.checkState() == QtCore.Qt.CheckState.Checked
        )
        self._sync_neuron_region_checks()
        self._refresh_soma_points(render=False)
        self._trim_hidden_axon_cache()
        self.plotter.render()

    def _check_all_regions(self, checked: bool) -> None:
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        self.region_list.blockSignals(True)
        for region_id, item in self.region_items.items():
            item.setCheckState(state)
            actor = self.region_actors.get(region_id)
            if actor is not None:
                actor.SetVisibility(checked)
        self.region_list.blockSignals(False)
        self._update_region_legend(render=False)
        self.plotter.render()

    def _check_all_neurons(self, checked: bool) -> None:
        self._apply_bulk_neuron_selection(list(self.neuron_items), checked)

    def _dataset_toggled(self, item: QtWidgets.QListWidgetItem) -> None:
        key = str(item.data(QtCore.Qt.ItemDataRole.UserRole))
        dataset = self.datasets[key]
        dataset.enabled = item.checkState() == QtCore.Qt.CheckState.Checked
        selected = [
            neuron_key for neuron_key, neuron_item in self.neuron_items.items()
            if self.neuron_datasets[neuron_key] == key
            and neuron_item.checkState() == QtCore.Qt.CheckState.Checked
        ]
        if dataset.enabled:
            self._apply_bulk_neuron_selection(selected, True)
        else:
            for neuron_key, actor in self.axon_actors.items():
                if self.neuron_datasets[neuron_key] == key:
                    actor.SetVisibility(False)
            self._refresh_soma_points(render=False)
            self._trim_hidden_axon_cache()
            self.plotter.render()

    def _refresh_aggregate_metadata(self) -> None:
        self.region_counts = Counter(
            region_id
            for dataset in self.datasets.values()
            for region_id in dataset.soma_regions.values()
            if region_id is not None
        )
        self.outside_soma_count = sum(
            dataset.outside_soma_count for dataset in self.datasets.values()
        )
        self.unassigned_soma_count = sum(
            region_id is None
            for dataset in self.datasets.values()
            for region_id in dataset.soma_regions.values()
        )
        new_regions = [rid for rid in self.region_counts if rid not in self.region_items]
        surfaces = load_or_create_region_surfaces(self.annotation, new_regions)
        self.region_list.blockSignals(True)
        for region_id in new_regions:
            self._create_region_item(
                region_id, self.region_counts[region_id], surfaces.get(region_id), False
            )
        for region_id, item in list(self.region_items.items()):
            count = self.region_counts.get(region_id, 0)
            if not count and region_id not in self.custom_region_ids:
                row = self.region_list.row(item)
                self.region_list.takeItem(row)
                self.region_items.pop(region_id, None)
                actor = self.region_actors.pop(region_id, None)
                if actor is not None:
                    self.plotter.remove_actor(actor, reset_camera=False, render=False)
                continue
            info = self.ontology.get(
                region_id, {"acronym": str(region_id), "name": "Unknown structure"}
            )
            item.setText(f"{info['acronym']} — {info['name']} — {count} soma(s)")
        self.region_list.blockSignals(False)
        self.unassigned_label.setText(
            f"Unassigned: {self.unassigned_soma_count} soma(s) "
            f"({self.outside_soma_count} outside atlas)"
        )
        self._build_neuron_region_list()
        self._sync_neuron_region_checks()

    def _add_projects(self, projects: list[tuple[str, Path]]) -> None:
        added = 0
        self.neuron_list.blockSignals(True)
        for brain_id, project in projects:
            key = dataset_key(brain_id, project)
            if key in self.datasets:
                continue
            dataset = self._read_dataset(
                brain_id,
                project,
                deterministic_color(f"dataset:{brain_id}"),
            )
            self._create_soma_actor(dataset)
            self._index_dataset_neurons(dataset)
            added += 1
        self.neuron_list.blockSignals(False)
        if not added:
            QtWidgets.QMessageBox.information(
                self, "No datasets added", "All selected brain datasets are already open."
            )
            return
        self.brain_id = "+".join(dataset.brain_id for dataset in self.datasets.values())
        self._populate_dataset_list()
        self._refresh_aggregate_metadata()
        self._refresh_soma_points()
        self.setWindowTitle(
            f"fMOST Brain Viewer v{__version__} - {len(self.datasets)} brain dataset(s)"
        )
        self.title_label.setText(f"Combined brains: {len(self.datasets)}")
        self.status.setText(
            f"Added {added} dataset(s): "
            f"{sum(dataset.matched_axon_count for dataset in self.datasets.values())} matched, "
            f"{sum(dataset.unmatched_axon_count for dataset in self.datasets.values())} unmatched, "
            f"{sum(dataset.duplicate_axon_count for dataset in self.datasets.values())} duplicate."
        )

    def _add_brain_datasets(self) -> None:
        folder = choose_brain_data_folder(self)
        if folder is None:
            return
        discovered = discover_brain_projects(folder)
        if not discovered:
            QtWidgets.QMessageBox.warning(
                self, "No brain datasets", "No complete brain project was found in this folder."
            )
            return
        projects = choose_discovered_projects(discovered, self)
        if projects:
            self._add_projects(projects)

    def _remove_selected_dataset(self) -> None:
        item = self.dataset_list.currentItem()
        if item is None:
            return
        if len(self.datasets) == 1:
            QtWidgets.QMessageBox.information(
                self, "Dataset required", "A session must contain at least one brain dataset."
            )
            return
        key = str(item.data(QtCore.Qt.ItemDataRole.UserRole))
        dataset = self.datasets[key]
        selected_before = {
            neuron_key for neuron_key, neuron_item in self.neuron_items.items()
            if neuron_item.checkState() == QtCore.Qt.CheckState.Checked
        }
        if QtWidgets.QMessageBox.question(
            self, "Remove dataset", f"Remove brain {dataset.brain_id} from this session?"
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        for neuron_key in [
            value for value, dataset_value in self.neuron_datasets.items()
            if dataset_value == key
        ]:
            self._remove_axon_actor(neuron_key)
            self.axon_sources.pop(neuron_key, None)
            self.neuron_ids.pop(neuron_key, None)
            self.neuron_match_status.pop(neuron_key, None)
            self.neuron_datasets.pop(neuron_key, None)
            self.neuron_items.pop(neuron_key, None)
            self.manual_colors.pop(neuron_key, None)
        if dataset.soma_actor is not None:
            self.plotter.remove_actor(dataset.soma_actor, reset_camera=False, render=False)
        self.datasets.pop(key)
        self.neuron_list.blockSignals(True)
        self.neuron_list.clear()
        self.neuron_items.clear()
        for remaining in self.datasets.values():
            header = QtWidgets.QListWidgetItem(
                f"▾ Dataset {self._dataset_label(remaining)}"
            )
            header.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            font = header.font(); font.setBold(True); header.setFont(font)
            header.setToolTip(str(remaining.project)); self.neuron_list.addItem(header)
            for neuron_key in sorted(
                (value for value, owner in self.neuron_datasets.items() if owner == remaining.key),
                key=lambda value: self.axon_sources[value].name,
            ):
                match_status = self.neuron_match_status.get(neuron_key, "")
                suffix = (
                    "  [Duplicate]" if match_status.startswith("Duplicate")
                    else "  [Unmatched]" if match_status.startswith("Unmatched")
                    else ""
                )
                neuron_item = QtWidgets.QListWidgetItem(
                    color_icon(self.manual_colors[neuron_key]),
                    f"    {self.axon_sources[neuron_key].stem}{suffix}",
                )
                neuron_item.setData(QtCore.Qt.ItemDataRole.UserRole, neuron_key)
                neuron_item.setToolTip(
                    f"Dataset {remaining.brain_id}\n{match_status}\n"
                    f"{self.axon_sources[neuron_key]}"
                )
                neuron_item.setFlags(
                    neuron_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                )
                neuron_item.setCheckState(
                    QtCore.Qt.CheckState.Checked
                    if neuron_key in selected_before
                    else QtCore.Qt.CheckState.Unchecked
                )
                self.neuron_list.addItem(neuron_item)
                self.neuron_items[neuron_key] = neuron_item
        self.neuron_list.blockSignals(False)
        self._populate_dataset_list()
        self._refresh_aggregate_metadata()
        self._refresh_soma_points(render=False)
        self.brain_id = "+".join(dataset.brain_id for dataset in self.datasets.values())
        self.title_label.setText(f"Combined brains: {len(self.datasets)}")
        self.setWindowTitle(
            f"fMOST Brain Viewer v{__version__} - {len(self.datasets)} brain dataset(s)"
        )
        self.plotter.render()

    def _session_payload(self, destination: Path) -> dict:
        datasets = []
        for dataset in self.datasets.values():
            try:
                stored_path = str(dataset.project.relative_to(destination.parent))
                relative = True
            except ValueError:
                stored_path = str(dataset.project)
                relative = False
            datasets.append({
                "key": dataset.key,
                "brain_id": dataset.brain_id,
                "project_path": stored_path,
                "relative_path": relative,
                "soma_color": dataset.soma_color,
                "enabled": dataset.enabled,
            })
        camera = [list(map(float, vector)) for vector in self.plotter.camera_position]
        return {
            "format": "fmost-brain-viewer-session",
            "format_version": 1,
            "app_version": __version__,
            "atlas_signature": ATLAS_SIGNATURE,
            "datasets": datasets,
            "brain_style": self.current_brain_style,
            "brain_opacity": self.template_opacity.value(),
            "show_grid": self.grid_check.isChecked(),
            "show_region_legend": self.legend_check.isChecked(),
            "show_all_somas": self.soma_check.isChecked(),
            "axon_width": self.axon_width.value(),
            "soma_size": self.soma_size.value(),
            "region_opacity": self.region_opacity.value(),
            "color_mode": self.color_mode.currentText(),
            "manual_colors": self.manual_colors,
            "custom_regions": sorted(self.custom_region_ids),
            "visible_regions": [
                region_id for region_id, item in self.region_items.items()
                if item.checkState() == QtCore.Qt.CheckState.Checked
            ],
            "visible_neurons": [
                key for key, item in self.neuron_items.items()
                if item.checkState() == QtCore.Qt.CheckState.Checked
            ],
            "camera_position": camera,
        }

    def _save_session(self, _checked=False, save_as: bool = False) -> bool:
        path = self.session_path
        if save_as or path is None:
            selected, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save fMOST session", str(path or Path.home() / "analysis.fmost-session.json"),
                "fMOST sessions (*.fmost-session.json)",
            )
            if not selected:
                return False
            path = Path(selected)
            if not str(path).lower().endswith(".fmost-session.json"):
                path = Path(str(path) + ".fmost-session.json")
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            temporary.write_text(
                json.dumps(self._session_payload(path), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot save session", str(exc))
            return False
        self.session_path = path
        self.status.setText(f"Session saved: {path}")
        return True

    def _replace_with_projects(
        self, projects: list[tuple[str, Path]], session_path=None, session_config=None
    ) -> None:
        self._replacement_window = ViewerWindow(
            projects, session_path=session_path, session_config=session_config
        )
        self._replacement_window.show()
        self.close()

    def _new_session(self) -> None:
        folder = choose_brain_data_folder(self)
        if folder is None:
            return
        projects = choose_discovered_projects(discover_brain_projects(folder), self)
        if projects:
            self._replace_with_projects(projects)

    def _open_session(self) -> None:
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open fMOST session", str(Path.home()),
            "fMOST sessions (*.fmost-session.json);;JSON files (*.json)",
        )
        if not selected:
            return
        atlas_snapshot = _snapshot_active_atlas()
        loaded = load_session_file(Path(selected), self)
        if loaded is not None:
            projects, config = loaded
            try:
                self._replace_with_projects(projects, Path(selected), config)
            except Exception as exc:
                _restore_active_atlas(atlas_snapshot)
                LOGGER.exception("Could not construct the replacement session window")
                QtWidgets.QMessageBox.critical(
                    self,
                    "Cannot open session",
                    "The new session window could not be created. The previous atlas "
                    f"and viewer remain active.\n\n{exc}",
                )

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.session_path is not None:
            self._save_session()
        if self.update_check_thread is not None and self.update_check_thread.isRunning():
            self.update_check_thread.quit()
            self.update_check_thread.wait(16000)
        self.plotter.close()
        event.accept()


def choose_brain_data_folder(parent=None) -> Path | None:
    settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
    initial = settings.value("last_data_directory", str(DATA_ROOT))
    selected = QtWidgets.QFileDialog.getExistingDirectory(
        parent,
        "Choose a dataset project, axon, soma, or parent folder",
        str(initial),
        QtWidgets.QFileDialog.Option.ShowDirsOnly,
    )
    if not selected:
        return None
    path = Path(selected)
    settings.setValue("last_data_directory", str(path))
    return path


def choose_discovered_projects(
    projects: list[tuple[str, Path]], parent=None
) -> list[tuple[str, Path]]:
    if not projects:
        return []
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Choose brain datasets")
    dialog.setMinimumWidth(620)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(
        "Select one or more registered brains to combine in the Allen CCF coordinate system."
    ))
    listing = QtWidgets.QListWidget()
    for brain_id, project in projects:
        item = QtWidgets.QListWidgetItem(f"{brain_id}  —  {project}")
        item.setData(QtCore.Qt.ItemDataRole.UserRole, (brain_id, str(project)))
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.CheckState.Checked)
        listing.addItem(item)
    layout.addWidget(listing)
    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Open
        | QtWidgets.QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return []
    selected = []
    for row in range(listing.count()):
        item = listing.item(row)
        if item.checkState() == QtCore.Qt.CheckState.Checked:
            brain_id, project = item.data(QtCore.Qt.ItemDataRole.UserRole)
            selected.append((str(brain_id), Path(project)))
    return selected


def _snapshot_active_atlas() -> dict:
    settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
    setting_values = {}
    for key in ("atlas_directory", "atlas_signature"):
        setting_values[key] = (
            settings.contains(key),
            settings.value(key) if settings.contains(key) else None,
        )
    return {
        "globals": (
            ATLAS_ROOT,
            TEMPLATE_25,
            ANNOTATION_10,
            ATLAS_ANNOTATION_SOURCE,
            ATLAS_SIGNATURE,
            CACHE_ROOT,
        ),
        "settings": setting_values,
    }


def _restore_active_atlas(snapshot: dict) -> None:
    global ATLAS_ROOT, TEMPLATE_25, ANNOTATION_10, ATLAS_ANNOTATION_SOURCE
    global ATLAS_SIGNATURE, CACHE_ROOT
    (
        ATLAS_ROOT,
        TEMPLATE_25,
        ANNOTATION_10,
        ATLAS_ANNOTATION_SOURCE,
        ATLAS_SIGNATURE,
        CACHE_ROOT,
    ) = snapshot["globals"]
    settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
    for key, (was_present, value) in snapshot["settings"].items():
        if was_present:
            settings.setValue(key, value)
        else:
            settings.remove(key)
    settings.sync()


def _abort_session_load(atlas_snapshot: dict) -> None:
    _restore_active_atlas(atlas_snapshot)
    return None


def _confirm_session_atlas(config: dict, parent=None) -> bool:
    saved_signature = str(config.get("atlas_signature", ""))
    if not saved_signature or saved_signature == ATLAS_SIGNATURE:
        return True
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
    box.setWindowTitle("Atlas does not match session")
    box.setText(
        "This session was saved with a different Allen CCF atlas identity. "
        "Reconfigure the matching atlas before opening the session."
    )
    configure = box.addButton(
        "Configure atlas...", QtWidgets.QMessageBox.ButtonRole.ActionRole
    )
    cancel = box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
    box.exec()
    if box.clickedButton() is cancel or box.clickedButton() is not configure:
        return False
    if not ensure_atlas_configured(parent, force=True):
        return False
    if saved_signature != ATLAS_SIGNATURE:
        QtWidgets.QMessageBox.warning(
            parent,
            "Atlas still does not match",
            "The selected atlas does not match the atlas identity stored in this session.",
        )
        return False
    return True


def load_session_file(
    path: Path, parent=None
) -> tuple[list[tuple[str, Path]], dict] | None:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        QtWidgets.QMessageBox.critical(parent, "Cannot open session", str(exc))
        return None
    if not isinstance(config, dict):
        QtWidgets.QMessageBox.critical(
            parent, "Cannot open session", "The session root must be a JSON object."
        )
        return None
    if config.get("format") != "fmost-brain-viewer-session":
        QtWidgets.QMessageBox.critical(
            parent, "Cannot open session", "This is not an fMOST Brain Viewer session file."
        )
        return None
    if config.get("format_version") != 1:
        QtWidgets.QMessageBox.critical(
            parent, "Cannot open session", "Unsupported fMOST session format version."
        )
        return None
    entries = config.get("datasets")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        QtWidgets.QMessageBox.critical(
            parent, "Cannot open session", "The session dataset list is invalid."
        )
        return None
    if not isinstance(config.get("manual_colors", {}), dict) or not isinstance(
        config.get("visible_neurons", []), list
    ):
        QtWidgets.QMessageBox.critical(
            parent, "Cannot open session", "The session selection data is invalid."
        )
        return None
    atlas_snapshot = _snapshot_active_atlas()
    if not _confirm_session_atlas(config, parent):
        return _abort_session_load(atlas_snapshot)
    projects: list[tuple[str, Path]] = []
    key_replacements: dict[str, str] = {}
    retained_entries = []
    for entry in entries:
        brain_id = str(entry.get("brain_id", ""))
        stored = Path(str(entry.get("project_path", "")))
        project = (path.parent / stored).resolve() if entry.get("relative_path") else stored
        axon_dir, soma_path, _ = project_paths(brain_id, project)
        if not axon_dir.is_dir() or not soma_path.is_file():
            box = QtWidgets.QMessageBox(parent)
            box.setWindowTitle("Missing brain dataset")
            box.setText(f"Brain {brain_id} cannot be found:\n{project}")
            locate = box.addButton("Locate...", QtWidgets.QMessageBox.ButtonRole.ActionRole)
            skip = box.addButton("Skip", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is cancel:
                return _abort_session_load(atlas_snapshot)
            if box.clickedButton() is skip:
                continue
            selected = QtWidgets.QFileDialog.getExistingDirectory(
                parent, f"Locate project for brain {brain_id}", str(path.parent)
            )
            if not selected:
                return _abort_session_load(atlas_snapshot)
            candidates = [item for item in discover_brain_projects(Path(selected)) if item[0] == brain_id]
            if not candidates:
                QtWidgets.QMessageBox.warning(
                    parent, "Dataset not found", f"The selected folder has no complete brain {brain_id}."
                )
                return _abort_session_load(atlas_snapshot)
            project = candidates[0][1]
        old_key = str(entry.get("key", dataset_key(brain_id, project)))
        new_key = dataset_key(brain_id, project)
        key_replacements[old_key] = new_key
        entry["key"] = new_key
        retained_entries.append(entry)
        projects.append((brain_id, project))
    if not projects:
        QtWidgets.QMessageBox.warning(parent, "Empty session", "No usable brain datasets remain.")
        return _abort_session_load(atlas_snapshot)
    config["datasets"] = retained_entries
    for field_name in ("manual_colors",):
        config[field_name] = {
            next((new + key[len(old):] for old, new in key_replacements.items() if key.startswith(old + "::")), key): value
            for key, value in config.get(field_name, {}).items()
        }
    config["visible_neurons"] = [
        next((new + key[len(old):] for old, new in key_replacements.items() if key.startswith(old + "::")), key)
        for key in config.get("visible_neurons", [])
    ]
    return projects, config


def choose_startup_data(parent=None):
    prompt = QtWidgets.QMessageBox(parent)
    prompt.setWindowTitle("Open fMOST data")
    prompt.setText("<b>Open brain datasets in one Allen CCF coordinate system</b>")
    folder_button = prompt.addButton(
        "Choose data folder...", QtWidgets.QMessageBox.ButtonRole.AcceptRole
    )
    session_button = prompt.addButton(
        "Open session...", QtWidgets.QMessageBox.ButtonRole.ActionRole
    )
    cancel_button = prompt.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
    prompt.setDefaultButton(folder_button)
    prompt.exec()
    if prompt.clickedButton() is cancel_button:
        return None
    if prompt.clickedButton() is session_button:
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent, "Open fMOST session", str(Path.home()),
            "fMOST sessions (*.fmost-session.json);;JSON files (*.json)",
        )
        if not selected:
            return None
        loaded = load_session_file(Path(selected), parent)
        return (*loaded, Path(selected)) if loaded is not None else None
    folder = choose_brain_data_folder(parent)
    if folder is None:
        return None
    discovered = discover_brain_projects(folder)
    if not discovered:
        QtWidgets.QMessageBox.warning(
            parent, "No brain datasets", "No complete brain project was found in this folder."
        )
        return None
    projects = choose_discovered_projects(discovered, parent)
    return (projects, None, None) if projects else None


def maybe_prepare_region_library(parent=None) -> None:
    annotation = RawNrrdMemmap(ANNOTATION_10)
    if region_library_is_current(annotation, "standard") or region_library_is_current(
        annotation, "complete"
    ):
        return
    settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
    signature = f"{ANNOTATION_10.resolve()}|{ANNOTATION_10.stat().st_size}|v{REGION_CACHE_VERSION}"
    if str(settings.value("deferred_region_library", "")) == signature:
        return
    if choose_and_prepare_region_library(parent, annotation, load_ontology()):
        settings.remove("deferred_region_library")
    else:
        settings.setValue("deferred_region_library", signature)
    settings.sync()


def ensure_atlas_configured(parent=None, force: bool = False) -> bool:
    """Load the saved atlas or guide the user through first-run setup."""
    settings = QtCore.QSettings("LiBoLab", "fMOSTBrainViewer")
    if not force:
        candidates = []
        saved = settings.value("atlas_directory", "")
        if saved:
            candidates.append(Path(str(saved)))
        for candidate in candidates:
            try:
                activate_atlas(candidate, parent)
                maybe_prepare_region_library(parent)
                return True
            except (FileNotFoundError, ValueError, OSError, AtlasSetupCancelled):
                continue

    while True:
        prompt = QtWidgets.QMessageBox(parent)
        prompt.setWindowTitle("Allen CCF Atlas Setup")
        prompt.setIconPixmap(
            QtGui.QPixmap(str(APP_LOGO)).scaled(
                170, 110,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
        prompt.setText("<b>Set up the Allen Mouse Brain CCFv3 atlas</b>")
        prompt.setInformativeText(
            "Choose a folder that already contains average_template_25.nrrd and "
            "annotation_10.nrrd, or download both files from the Allen Institute.\n\n"
            "The official 10 um annotation is prepared for fast slicing and requires "
            "approximately 5 GB of free disk space."
        )
        choose_button = prompt.addButton(
            "Choose existing atlas...", QtWidgets.QMessageBox.ButtonRole.ActionRole
        )
        download_button = prompt.addButton(
            "Download official atlas...", QtWidgets.QMessageBox.ButtonRole.ActionRole
        )
        cancel_button = prompt.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        prompt.exec()
        clicked = prompt.clickedButton()
        if clicked is cancel_button:
            return False

        try:
            if clicked is choose_button:
                initial = settings.value("atlas_directory", str(Path.home()))
                selected = QtWidgets.QFileDialog.getExistingDirectory(
                    parent,
                    "Choose Allen CCF atlas folder",
                    str(initial),
                    QtWidgets.QFileDialog.Option.ShowDirsOnly,
                )
                if not selected:
                    continue
                folder = Path(selected)
            elif clicked is download_button:
                default_download = (
                    LOCAL_APP_DATA / "fMOST Brain Viewer" / "atlas" / "CCFv3-2017"
                )
                initial = settings.value(
                    "atlas_download_directory", str(default_download)
                )
                selected = QtWidgets.QFileDialog.getExistingDirectory(
                    parent,
                    "Choose a folder for the Allen CCF atlas download",
                    str(initial),
                    QtWidgets.QFileDialog.Option.ShowDirsOnly,
                )
                if not selected:
                    continue
                folder = Path(selected)
                settings.setValue("atlas_download_directory", str(folder))
                settings.sync()
                download_official_atlas(folder, parent)
            else:
                return False
            activate_atlas(folder, parent)
            maybe_prepare_region_library(parent)
            return True
        except AtlasSetupCancelled:
            return False
        except Exception as exc:
            QtWidgets.QMessageBox.critical(parent, "Atlas setup failed", str(exc))


def choose_discovered_project(
    projects: list[tuple[str, Path]],
) -> tuple[str, Path] | None:
    if not projects:
        return None
    if len(projects) == 1:
        return projects[0]
    choices = [f"{brain_id} — {project}" for brain_id, project in projects]
    selected, accepted = QtWidgets.QInputDialog.getItem(
        None,
        "Choose brain",
        "Multiple complete brain projects were found:",
        choices,
        0,
        False,
    )
    return projects[choices.index(selected)] if accepted else None


def run_self_test(render: bool = True) -> int:
    """Exercise resources, Qt, and VTK; optionally require an OpenGL render."""
    try:
        manifest = load_atlas_manifest()
        ontology = load_ontology()
        if not APP_ICON.is_file() or not APP_LOGO.is_file():
            raise RuntimeError("Application icon or logo is missing.")
        if len(manifest.get("files", {})) != 2:
            raise RuntimeError("Atlas manifest file list is invalid.")

        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError("A QApplication is required for the self-test.")
        smoke_widget = QtWidgets.QWidget()
        smoke_layout = QtWidgets.QVBoxLayout(smoke_widget)
        smoke_layout.addWidget(QtWidgets.QLabel("fMOST Brain Viewer"))
        smoke_widget.show()
        app.processEvents()
        try:
            if not smoke_widget.isVisible():
                raise RuntimeError("The Qt smoke-test widget did not become visible.")
        finally:
            smoke_widget.close()

        sphere = pv.Sphere(theta_resolution=12, phi_resolution=12)
        filtered = sphere.compute_normals(point_normals=True, cell_normals=False)
        if filtered.n_points == 0 or "Normals" not in filtered.point_data:
            raise RuntimeError("The VTK/PyVista geometry pipeline returned no normals.")

        result_label = "Qt/VTK/PyVista pipeline smoke passed"
        if render:
            plotter = pv.Plotter(off_screen=True, window_size=(160, 120))
            try:
                plotter.add_mesh(filtered, color="white")
                image = plotter.screenshot(return_img=True)
                if image is None or image.size == 0:
                    raise RuntimeError("Off-screen VTK rendering returned no image.")
            finally:
                plotter.close()
            result_label = "Qt/VTK/PyVista render passed"
        print(
            f"SELF-TEST OK: fMOST Brain Viewer {__version__}; "
            f"{len(ontology)} Allen structures; {result_label}"
        )
        LOGGER.info("Self-test passed (render=%s)", render)
        return 0
    except Exception as exc:
        LOGGER.exception("Self-test failed")
        print(f"SELF-TEST FAILED: {exc}\nLog: {LOG_PATH}", file=sys.stderr)
        return 1


def main() -> int:
    configure_logging()
    sys.excepthook = _global_exception_hook
    self_test = "--self-test" in sys.argv
    ci_smoke_test = "--ci-smoke-test" in sys.argv
    test_arguments = {"--self-test", "--ci-smoke-test"}
    app_arguments = [argument for argument in sys.argv if argument not in test_arguments]
    app = QtWidgets.QApplication(app_arguments)
    app.setApplicationName("fMOST Brain Viewer")
    app.setOrganizationName("LiBoLab")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QtGui.QIcon(str(APP_ICON)))
    if self_test and ci_smoke_test:
        print("Choose either --self-test or --ci-smoke-test, not both.", file=sys.stderr)
        return 2
    if self_test or ci_smoke_test:
        return run_self_test(render=not ci_smoke_test)
    try:
        if not ensure_atlas_configured():
            return 0
    except Exception as exc:
        LOGGER.exception("Atlas setup failed")
        QtWidgets.QMessageBox.critical(
            None, "Atlas setup failed", f"{exc}\n\nLog: {LOG_PATH}"
        )
        return 1
    startup = choose_startup_data()
    if startup is None:
        return 0
    progress = None
    try:
        projects, session_config, session_path = startup
        progress = StartupSplash()
        progress.setValue(0)
        progress.show()
        QtWidgets.QApplication.processEvents()
        window = ViewerWindow(
            projects,
            progress=progress,
            session_path=session_path,
            session_config=session_config,
        )
    except Exception as exc:
        LOGGER.exception("Cannot open dataset")
        if progress is not None:
            progress.close()
        QtWidgets.QMessageBox.critical(
            None, "Cannot open dataset", f"{exc}\n\nLog: {LOG_PATH}"
        )
        return 1
    window.show()
    progress.close()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
