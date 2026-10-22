"""Local update preferences and read-only scene handoff between app versions."""
from __future__ import annotations

import math
from pathlib import Path
import re
import time

from viewer_session import atomic_json, read_session, validate_session

CHECK_INTERVAL_SECONDS = 24 * 60 * 60
RESUME_NAME = re.compile(r"resume-[0-9a-f]{32}\.fmost-session\.json")


def setting_timestamp(settings, key: str) -> float:
    try:
        value = float(settings.value(key, 0))
        return value if math.isfinite(value) and value >= 0 else 0
    except (TypeError, ValueError):
        return 0


def automatic_check_due(settings, now: float | None = None) -> bool:
    if not settings.value("updates/automatic_check", True, type=bool):
        return False
    now = time.time() if now is None else now
    last = setting_timestamp(settings, "updates/last_check")
    remind = setting_timestamp(settings, "updates/remind_after")
    # Recover from a system clock correction instead of disabling checks forever.
    if remind > now and remind - now <= CHECK_INTERVAL_SECONDS:
        return False
    return not last or now < last or now - last >= CHECK_INTERVAL_SECONDS


def checked_resume_path(path: Path, root: Path) -> Path:
    path = path.resolve()
    if path.parent != root.resolve() or not RESUME_NAME.fullmatch(path.name):
        raise ValueError("The update recovery file must be in this user's update folder.")
    return path


def write_update_resume(path: Path, root: Path, payload: dict, *,
                        original_session: Path | None, atlas_directory: Path,
                        target_version: str, was_dirty: bool) -> None:
    path = checked_resume_path(path, root)
    if not re.fullmatch(r"\d+\.\d+\.\d+", target_version):
        raise ValueError("Invalid update target version.")
    validate_session(payload)
    snapshot = dict(payload)
    snapshot["update_resume"] = {
        "format_version": 1,
        "original_session": str(original_session.resolve()) if original_session else None,
        "atlas_directory": str(atlas_directory.resolve()),
        "target_version": target_version,
        "was_dirty": bool(was_dirty),
    }
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(path, snapshot)


def read_update_resume(path: Path, root: Path) -> tuple[Path, dict]:
    path = checked_resume_path(path, root)
    payload = read_session(path)
    info = payload.get("update_resume")
    if not isinstance(info, dict) or info.get("format_version") != 1:
        raise ValueError("Invalid update recovery metadata.")
    if type(info.get("was_dirty")) is not bool:
        raise ValueError("Invalid update recovery state.")
    if not isinstance(info.get("target_version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", info["target_version"]):
        raise ValueError("Invalid update recovery version.")
    for key in ("atlas_directory", "original_session"):
        value = info.get(key)
        if key == "original_session" and value is None:
            continue
        if not isinstance(value, str) or not value or any(c in value for c in '\x00\r\n"') or not Path(value).is_absolute():
            raise ValueError(f"Invalid update recovery {key}.")
    return path, payload
