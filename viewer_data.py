"""Strict SWC parsing; valid multi-root soma files remain supported."""
from pathlib import Path
from collections import Counter
import numpy as np

def read_swc_with_ids(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    parsed_rows = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            text = line.replace("\x00", "").strip()
            if not text or text.startswith("#"):
                continue
            fields = text.split()
            if len(fields) < 7:
                raise ValueError(f"SWC row needs seven columns: {path}, line {line_number}")
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
    if np.any(rows[:, 0] <= 0) or np.any(np.abs(rows[:, [0, 6]]) > 2**53 - 1):
        raise ValueError(f"SWC IDs must be positive, exactly representable integers: {path}")
    if np.any(np.abs(rows[:, 2:5]) > np.finfo(np.float32).max):
        raise ValueError(f"SWC coordinates exceed supported range: {path}")
    points = rows[:, 2:5].astype(np.float32)
    parents = np.rint(rows[:, 6]).astype(np.int64)
    index_by_id = {node_id: index for index, node_id in enumerate(ids)}
    missing = sorted(set(map(int, parents)) - set(map(int, ids)) - {-1})
    if missing:
        raise ValueError(f"SWC references missing parent IDs {missing[:8]}: {path}")
    parent_by_id = dict(zip(map(int, ids), map(int, parents)))
    finished = set()
    for node in parent_by_id:
        trail = set()
        current = node
        while current != -1 and current not in finished:
            if current in trail:
                raise ValueError(f"SWC parent cycle at node {current}: {path}")
            trail.add(current)
            current = parent_by_id[current]
        finished.update(trail)
    edges = np.asarray(
        [
            (index_by_id[parent], index)
            for index, parent in enumerate(parents)
            if parent in index_by_id
        ],
        dtype=np.int64,
    )
    return ids, points, edges.reshape(-1, 2)


def read_swc(path: Path) -> tuple[np.ndarray, np.ndarray]:
    _, points, edges = read_swc_with_ids(path)
    return points, edges
