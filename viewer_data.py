"""Strict, compact SWC parsing; valid multi-root soma files remain supported."""
from array import array
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path

import numpy as np


_MAX_EXACT_ID = 2**53 - 1
_MAX_COORDINATE = float(np.finfo(np.float32).max)


def _swc_integer(text: str) -> int:
    """Accept integral decimal/exponent notation without rounding fractional IDs."""
    try:
        value = int(text)
    except ValueError:
        try:
            decimal = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("SWC IDs must be integers") from exc
        if not decimal.is_finite() or decimal != decimal.to_integral_value():
            raise ValueError("SWC IDs must be finite integers")
        if decimal.copy_abs() > _MAX_EXACT_ID:
            raise ValueError("SWC IDs exceed the exactly representable range")
        value = int(decimal)
    if abs(value) > _MAX_EXACT_ID:
        raise ValueError("SWC IDs exceed the exactly representable range")
    return value


def read_swc_with_ids(path: Path, *, max_nodes: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    # Typed buffers avoid retaining seven Python floats and a list for every row.
    # NumPy views keep these buffers alive without duplicating their contents.
    id_buffer = array("q")
    point_buffer = array("f")
    parent_buffer = array("q")
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            text = line.replace("\x00", "").strip()
            if not text or text.startswith("#"):
                continue
            if max_nodes is not None and len(id_buffer) >= max_nodes:
                raise MemoryError("SWC exceeds the temporary parsing safety limit; close other applications or load a smaller SWC")
            fields = text.split(maxsplit=7)
            if len(fields) < 7:
                raise ValueError(f"SWC row needs seven columns: {path}, line {line_number}")
            try:
                node_id = _swc_integer(fields[0])
                kind, x, y, z, radius = map(float, fields[1:6])
                parent_id = _swc_integer(fields[6])
            except ValueError as exc:
                raise ValueError(f"Invalid SWC row in {path}, line {line_number}: {exc}") from exc
            if not all(map(isfinite, (kind, x, y, z, radius))):
                raise ValueError(f"SWC contains a non-finite value: {path}, line {line_number}")
            if node_id <= 0:
                raise ValueError(f"SWC node IDs must be positive: {path}, line {line_number}")
            if max(abs(x), abs(y), abs(z)) > _MAX_COORDINATE:
                raise ValueError(f"SWC coordinates exceed supported range: {path}, line {line_number}")
            id_buffer.append(node_id)
            point_buffer.extend((x, y, z))
            parent_buffer.append(parent_id)
    if not id_buffer:
        raise ValueError(f"No valid seven-column SWC rows found: {path}")
    ids = np.frombuffer(id_buffer, dtype=np.int64)
    points = np.frombuffer(point_buffer, dtype=np.float32).reshape(-1, 3)
    parents = np.frombuffer(parent_buffer, dtype=np.int64)

    # Resolve parents with dense arrays instead of multiple Python dictionaries
    # and sets. Input order is preserved, including forward references.
    order = np.argsort(ids)
    sorted_ids = ids[order]
    duplicate_mask = sorted_ids[1:] == sorted_ids[:-1]
    if duplicate_mask.any():
        duplicates = np.unique(sorted_ids[1:][duplicate_mask])
        preview = ", ".join(map(str, duplicates[:8]))
        suffix = "..." if len(duplicates) > 8 else ""
        raise ValueError(f"SWC contains duplicate node ID(s) {preview}{suffix}: {path}")
    del duplicate_mask
    parent_indices = np.searchsorted(sorted_ids, parents)
    np.minimum(parent_indices, len(ids) - 1, out=parent_indices)
    roots = parents == -1
    missing_mask = (sorted_ids[parent_indices] != parents) & ~roots
    if missing_mask.any():
        missing = np.unique(parents[missing_mask])[:8].tolist()
        raise ValueError(f"SWC references missing parent IDs {missing}: {path}")
    parent_indices = order[parent_indices]
    parent_indices[roots] = -1
    del parents, parent_buffer, order, sorted_ids, roots, missing_mask

    # Each node is visited at most twice. A byte per node replaces per-chain
    # Python sets and also handles deep chains without recursion.
    state = bytearray(len(ids))
    for node in range(len(ids)):
        if state[node]:
            continue
        current = node
        while current != -1 and state[current] == 0:
            state[current] = 1
            current = int(parent_indices[current])
        if current != -1 and state[current] == 1:
            raise ValueError(f"SWC parent cycle at node {int(ids[current])}: {path}")
        current = node
        while current != -1 and state[current] == 1:
            state[current] = 2
            current = int(parent_indices[current])
    del state
    children = np.flatnonzero(parent_indices >= 0)
    edges = np.empty((len(children), 2), dtype=np.int64)
    edges[:, 0] = parent_indices[children]
    edges[:, 1] = children
    return ids, points, edges


def read_swc(path: Path, *, max_nodes: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    _, points, edges = read_swc_with_ids(path, max_nodes=max_nodes)
    return points, edges


@dataclass(frozen=True)
class SwcPartialIssue:
    kind: str
    line: int
    node_id: int | None = None
    parent_id: int | None = None
    detail: str = ""


@dataclass
class SwcPartialReport:
    """Bounded diagnostics; counts cover the whole file, previews cover 100 issues."""

    source_path: str
    total_rows: int = 0
    loaded_nodes: int = 0
    loaded_edges: int = 0
    invalid_rows: int = 0
    duplicate_ids_count: int = 0
    duplicate_rows: int = 0
    missing_parent_edges: int = 0
    cycle_edges: int = 0
    nul_bytes: int = 0
    nul_lines: int = 0
    component_count: int = 0
    minimum_node_id: int | None = None
    issue_count: int = 0
    issues: list[SwcPartialIssue] = field(default_factory=list)

    @property
    def skipped_rows(self) -> int:
        return self.total_rows - self.loaded_nodes

    @property
    def issues_truncated(self) -> int:
        return self.issue_count - len(self.issues)

    @property
    def has_warnings(self) -> bool:
        return self.issue_count > 0

    @property
    def is_partial(self) -> bool:
        return bool(self.skipped_rows or self.missing_parent_edges or self.cycle_edges)

    def _add(self, kind, line, node_id=None, parent_id=None, detail=""):
        self.issue_count += 1
        if len(self.issues) < 100:
            self.issues.append(SwcPartialIssue(kind, int(line), node_id, parent_id, detail[:160]))

    def summary_text(self) -> str:
        parts = [f"Loaded {self.loaded_nodes:,} nodes and {self.loaded_edges:,} edges"]
        if self.skipped_rows:
            parts.append(f"skipped {self.skipped_rows:,} {'row' if self.skipped_rows == 1 else 'rows'}")
        if self.missing_parent_edges:
            parts.append(f"{self.missing_parent_edges:,} missing-parent connections omitted")
        if self.cycle_edges:
            parts.append(f"{self.cycle_edges:,} cycle connections omitted")
        if self.nul_bytes:
            parts.append(f"{self.nul_bytes:,} NUL bytes ignored in memory")
        return "; ".join(parts) + "."

    def detail_text(self) -> str:
        lines = [self.source_path, self.summary_text(),
                 f"Retained components: {self.component_count:,}; minimum retained node ID: {self.minimum_node_id}.",
                 f"Invalid rows: {self.invalid_rows:,}; duplicate IDs: {self.duplicate_ids_count:,}; "
                 f"otherwise valid duplicate rows excluded: {self.duplicate_rows:,}.",
                 "The source file was not modified. No missing coordinates or connections were invented.",
                 "Missing-parent entries identify explicit references only. Gaps in node numbering do not prove missing nodes.",
                 "Data absent from the file cannot be reconstructed or counted without an intact reference.",
                 "IDs shown for invalid rows are tentative first-field interpretations; a truncated row may start mid-record."]
        for issue in self.issues:
            label = f"Line {issue.line}: {issue.kind}"
            if issue.node_id is not None:
                label += f"; {'tentative node ID' if issue.kind == 'Invalid row' else 'node'} {issue.node_id}"
            if issue.parent_id is not None:
                label += f" -> parent {issue.parent_id}"
            if issue.detail:
                label += f"; {issue.detail}"
            lines.append(label)
        if self.issues_truncated:
            lines.append(f"Preview truncated: {self.issues_truncated:,} additional issues omitted; counts above include all issues.")
        return "\n".join(lines)


def read_swc_partial(path: Path, *, max_nodes: int | None = None
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray, SwcPartialReport]:
    """Read salvageable geometry without changing the file or inventing topology.

    Strict readers above remain appropriate for numerical analysis and soma data.
    The safety limit counts every non-comment data row, including rejected rows.
    """
    report = SwcPartialReport(str(path))
    id_buffer, parent_buffer, line_buffer = array("q"), array("q"), array("q")
    point_buffer = array("f")
    invalid_id_buffer, invalid_line_buffer = array("q"), array("q")
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            nul_count = line.count("\x00")
            text = line.replace("\x00", "").strip()
            if nul_count:
                report.nul_bytes += nul_count
                report.nul_lines += 1
                report._add("NUL cleanup", line_number, detail=f"Ignored {nul_count} NUL bytes while reading")
            if text.startswith("#") or (not text and not nul_count):
                continue
            if max_nodes is not None and report.total_rows >= max_nodes:
                raise MemoryError("SWC exceeds the temporary parsing safety limit; close other applications or load a smaller SWC")
            report.total_rows += 1
            fields = text.split(maxsplit=7)
            node_id = None
            try:
                # Even a malformed row can make another occurrence of its ID
                # ambiguous. Remember identifiable IDs before validating fields.
                if fields:
                    node_id = _swc_integer(fields[0])
                    if node_id <= 0:
                        node_id = None
                        raise ValueError("SWC node IDs must be positive")
                if len(fields) < 7:
                    raise ValueError("SWC row needs seven columns")
                kind, x, y, z, radius = map(float, fields[1:6])
                parent_id = _swc_integer(fields[6])
                if not all(map(isfinite, (kind, x, y, z, radius))):
                    raise ValueError("SWC contains a non-finite value")
                if max(abs(x), abs(y), abs(z)) > _MAX_COORDINATE:
                    raise ValueError("SWC coordinates exceed supported range")
            except ValueError as exc:
                report.invalid_rows += 1
                report._add("Invalid row", line_number, node_id, detail=str(exc))
                if node_id is not None:
                    invalid_id_buffer.append(node_id)
                    invalid_line_buffer.append(line_number)
                continue
            id_buffer.append(node_id)
            point_buffer.extend((x, y, z))
            parent_buffer.append(parent_id)
            line_buffer.append(line_number)

    if not id_buffer:
        raise ValueError(f"No usable SWC nodes remain: {path}; {report.invalid_rows} invalid rows")
    ids = np.frombuffer(id_buffer, dtype=np.int64)
    points = np.frombuffer(point_buffer, dtype=np.float32).reshape(-1, 3)
    parents = np.frombuffer(parent_buffer, dtype=np.int64)
    lines = np.frombuffer(line_buffer, dtype=np.int64)
    invalid_ids = np.frombuffer(invalid_id_buffer, dtype=np.int64)
    observed_ids = np.concatenate((ids, invalid_ids)) if len(invalid_ids) else ids
    sorted_ids = np.sort(observed_ids)
    duplicate_ids = np.unique(sorted_ids[1:][sorted_ids[1:] == sorted_ids[:-1]])
    report.duplicate_ids_count = len(duplicate_ids)
    del observed_ids, sorted_ids
    if len(duplicate_ids):
        duplicate_rows = np.isin(ids, duplicate_ids, kind="sort")
        report.duplicate_rows = int(np.count_nonzero(duplicate_rows))
        for index, duplicate in enumerate(duplicate_rows):
            if duplicate:
                report._add("Duplicate ID", lines[index], int(ids[index]),
                            detail="All records with this ID are excluded")
        for node_id, line_number in zip(invalid_id_buffer, invalid_line_buffer):
            index = np.searchsorted(duplicate_ids, node_id)
            if index < len(duplicate_ids) and duplicate_ids[index] == node_id:
                report._add("Duplicate ID", line_number, node_id,
                            detail="Invalid record also shares an ambiguous ID")
        keep = ~duplicate_rows
        ids, points, parents, lines = ids[keep], points[keep], parents[keep], lines[keep]
        del keep, duplicate_rows
    del id_buffer, point_buffer, parent_buffer, line_buffer
    del invalid_ids, invalid_id_buffer, invalid_line_buffer
    if not len(ids):
        raise ValueError(f"No usable SWC nodes remain after excluding duplicate IDs: {path}")

    order = np.argsort(ids)
    sorted_ids = ids[order]
    parent_indices = np.searchsorted(sorted_ids, parents)
    np.minimum(parent_indices, len(ids) - 1, out=parent_indices)
    roots = parents == -1
    missing = (sorted_ids[parent_indices] != parents) & ~roots
    report.missing_parent_edges = int(np.count_nonzero(missing))
    for index, is_missing in enumerate(missing):
        if is_missing:
            parent = int(parents[index])
            duplicate_index = np.searchsorted(duplicate_ids, parent)
            ambiguous = duplicate_index < len(duplicate_ids) and duplicate_ids[duplicate_index] == parent
            report._add("Missing parent", lines[index], int(ids[index]), parent,
                        "Parent ID excluded because it is duplicated" if ambiguous else "No usable parent record exists")
    parent_indices = order[parent_indices]
    parent_indices[roots | missing] = -1
    del parents, order, sorted_ids, roots, missing, duplicate_ids

    # Functional graph traversal: cut every edge within each cycle. Keep all
    # nodes and genuine non-cycle branches. No recursion or per-node dicts.
    state = bytearray(len(ids))
    for node in range(len(ids)):
        if state[node]:
            continue
        current = node
        while current != -1 and state[current] == 0:
            state[current] = 1
            current = int(parent_indices[current])
        if current != -1 and state[current] == 1:
            cycle_start = current
            while True:
                parent = int(parent_indices[current])
                report.cycle_edges += 1
                report._add("Cycle edge omitted", lines[current], int(ids[current]), int(ids[parent]),
                            "Node retained; every edge within this cycle is omitted")
                parent_indices[current] = -1
                state[current] = 2
                current = parent
                if current == cycle_start:
                    break
        current = node
        while current != -1 and state[current] == 1:
            state[current] = 2
            current = int(parent_indices[current])
    del state, lines
    children = np.flatnonzero(parent_indices >= 0)
    edges = np.empty((len(children), 2), dtype=np.int64)
    edges[:, 0] = parent_indices[children]
    edges[:, 1] = children
    report.loaded_nodes = len(ids)
    report.loaded_edges = len(edges)
    report.component_count = len(ids) - len(edges)
    report.minimum_node_id = int(np.min(ids))
    return ids, points, edges, report
