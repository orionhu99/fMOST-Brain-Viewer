"""Validated, atomic session storage shared by save and recovery workflows."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import uuid

from PySide6 import QtGui

MAX_SESSION_BYTES = 16 * 1024 * 1024


def validate_session(config: object) -> dict:
    if not isinstance(config, dict):
        raise ValueError("The session root must be a JSON object.")
    if config.get("format") != "fmost-brain-viewer-session":
        raise ValueError("This is not an fMOST Brain Viewer session.")
    if type(config.get("format_version")) is not int or config["format_version"] not in (1, 2):
        raise ValueError("Unsupported session format version.")
    entries = config.get("datasets")
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError("Invalid dataset list.")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid dataset entry.")
        for field in ("brain_id", "project_path"):
            if not isinstance(entry.get(field), str) or not entry[field] or "\0" in entry[field]:
                raise ValueError(f"Invalid dataset {field}.")
        if not re.fullmatch(r"[\w-]+", entry["brain_id"]):
            raise ValueError("Dataset IDs may contain only letters, numbers, underscores and hyphens.")
        for field in ("enabled", "relative_path"):
            if field in entry and type(entry[field]) is not bool:
                raise ValueError(f"Invalid dataset {field}.")
        if "key" in entry and not isinstance(entry["key"], str):
            raise ValueError("Invalid dataset key.")
        identity = (entry["brain_id"], entry["project_path"], entry.get("relative_path", False))
        if identity in seen:
            raise ValueError("Duplicate dataset entry.")
        seen.add(identity)
        if "soma_color" in entry:
            _color(entry["soma_color"])
    for field in ("atlas_signature", "app_version"):
        if field in config and not isinstance(config[field], str):
            raise ValueError(f"Invalid {field}.")
    for field in ("show_grid", "show_region_legend", "show_all_somas", "show_slice", "show_brain", "strict_soma_labels"):
        if field in config and type(config[field]) is not bool:
            raise ValueError(f"Invalid {field}.")
    for field, low, high in (("brain_opacity", 0, 1), ("region_opacity", 0, 1), ("axon_width", 0, 100), ("soma_size", 0, 1000)):
        if field in config:
            value = config[field]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid {field}.")
    if "slice_index" in config and (type(config["slice_index"]) is not int or not 0 <= config["slice_index"] <= 100000):
        raise ValueError("Invalid slice index.")
    if config.get("brain_style", "Surface") not in ("Surface", "Volume"):
        raise ValueError("Invalid brain rendering style.")
    if "color_mode" in config and config["color_mode"] not in ("Independent / manual", "By soma region"):
        raise ValueError("Invalid neuron color mode.")
    for field in ("visible_regions", "custom_regions", "visible_neurons"):
        values = config.get(field, [])
        expected = str if field == "visible_neurons" else int
        if not isinstance(values, list) or any(type(value) is not expected for value in values):
            raise ValueError(f"Invalid {field}.")
        if expected is int and any(value < 0 or value > 2**32 - 1 for value in values):
            raise ValueError(f"Invalid {field} atlas ID.")
    colors = config.get("manual_colors", {})
    if not isinstance(colors, dict):
        raise ValueError("Invalid manual colors.")
    for key, color in colors.items():
        if not isinstance(key, str):
            raise ValueError("Invalid neuron color key.")
        _color(color)
    if "camera_position" in config:
        camera = config["camera_position"]
        if not isinstance(camera, list) or len(camera) != 3 or any(
            not isinstance(row, list) or len(row) != 3 or any(
                type(value) not in (int, float) or not math.isfinite(value) for value in row
            ) for row in camera
        ):
            raise ValueError("Invalid camera position.")
    return config


def _color(value):
    if not isinstance(value, str) or not QtGui.QColor(value).isValid():
        raise ValueError("Invalid session color.")


def read_session(path: Path) -> dict:
    with path.open("rb") as stream:
        payload = stream.read(MAX_SESSION_BYTES + 1)
    if len(payload) > MAX_SESSION_BYTES:
        raise ValueError("Session exceeds the 16 MiB size limit.")
    return validate_session(json.loads(payload.decode("utf-8-sig")))


def read_legacy_session(path: Path) -> dict:
    with path.open("rb") as stream:
        payload = stream.read(MAX_SESSION_BYTES + 1)
    if len(payload) > MAX_SESSION_BYTES:
        raise ValueError("Legacy session exceeds the size limit.")
    config = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("Legacy session must be a JSON object.")
    config.setdefault("format", "fmost-brain-viewer-session")
    config.setdefault("format_version", 1)
    config.setdefault("datasets", [])
    return validate_session(config)


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
