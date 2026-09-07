"""Recoverable mesh caches; source atlas files are never modified."""
from pathlib import Path
import logging
import uuid
import pyvista as pv

LOGGER = logging.getLogger("fmost_brain_viewer")


def read_surface_cache(path: Path, source: Path):
    if not path.is_file() or path.stat().st_mtime < source.stat().st_mtime:
        return None
    try:
        surface = pv.read(path)
        if not isinstance(surface, pv.PolyData) or not surface.n_points:
            raise ValueError("Empty or invalid surface cache")
        return surface
    except Exception:
        LOGGER.warning("Rebuilding invalid surface cache: %s", path, exc_info=True)
        return None


def save_surface_cache(surface, path: Path):
    temporary = path.with_name(path.stem + "." + uuid.uuid4().hex + ".part.vtp")
    try:
        surface.save(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
