#!/usr/bin/env python3
"""Reject private research traces and data artifacts before public release."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


TEXT_SUFFIXES = {
    "", ".cff", ".cfg", ".csv", ".html", ".ini", ".iss", ".json",
    ".md", ".ps1", ".py", ".spec", ".toml", ".txt", ".xml", ".yaml",
    ".yml",
}
DATA_SUFFIXES = {
    ".bat", ".csv", ".gif", ".lnk", ".log", ".npy", ".nrrd", ".raw", ".swc",
    ".tif", ".tiff", ".vbs", ".vtp",
}
DATA_FILENAMES = {"viewer_config.json"}
DATA_PATH_PARTS = {"viewer_cache", "atlas_cache", "captures", "recordings"}
ALLOWED_REPOSITORY_IMAGES = {
    "fmost_brain_icon.png",
    "fmost_brain_logo.png",
    "fmost_brain_logo_black.png",
    "fmost_brain_logo_final_black.png",
    "fmost_brain_logo_final_transparent.png",
    "fmost_brain_viewer.ico",
    *(f"fmost_brain_icon_{size}.png" for size in (16, 24, 32, 48, 64, 128, 256)),
}


def _joined(*parts: str) -> str:
    """Build sensitive patterns without embedding the complete value in source."""
    return "".join(parts)


FORBIDDEN_TEXT = (
    ("private-sample-a", re.compile(_joined("252", "574"))),
    ("private-sample-b", re.compile(_joined("252", "610"))),
    ("project-marker", re.compile(_joined("g", "fral"), re.IGNORECASE)),
    (
        "legacy-atlas-folder",
        re.compile(_joined("brainmap for ", "hyh"), re.IGNORECASE),
    ),
    (
        "research-region-full-name",
        re.compile(_joined("area ", "postrema"), re.IGNORECASE),
    ),
    (
        "research-region-acronym-a",
        re.compile(r"\b" + _joined("N", "TS") + r"\b"),
    ),
    (
        "research-region-acronym-b",
        re.compile(r"\b" + _joined("C", "eA") + r"\b", re.IGNORECASE),
    ),
    (
        "private-email",
        re.compile(r"[\w.+-]+@" + _joined("q", "q") + r"\.com", re.IGNORECASE),
    ),
    (
        "private-research-root",
        re.compile(
            _joined("d", ":", r"[\\/]", "research", r"[\\/]", "fmost"),
            re.IGNORECASE,
        ),
    ),
    (
        "absolute-windows-path",
        re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])[^\s<>\"']+"),
    ),
)
AXIS_ABBREVIATION = re.compile(r"\b" + _joined("A", "P") + r"\b")
AXIS_CONTEXT = re.compile(r"anterior\s*[-–—]\s*posterior", re.IGNORECASE)
ONTOLOGY_EXEMPT_RULES = {
    "research-region-full-name",
    "research-region-acronym-a",
    "research-region-acronym-b",
}
THIRD_PARTY_TEXT_RULES = {
    "private-sample-a",
    "private-sample-b",
    "project-marker",
    "legacy-atlas-folder",
    "private-email",
    "private-research-root",
}
APP_OWNED_INTERNAL_PARTS = {
    "assets",
    "docs",
    "licenses",
    "resources",
    "citation.cff",
    "fmost_brain_viewer.py",
    "license",
    "readme.md",
    "readme_zh-cn.md",
    "requirements.lock",
    "third_party_notices.md",
    "version.py",
}


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.rule}: {self.detail}"


def _is_generated_data_path(name: str, *, repository: bool) -> str | None:
    normalized = PurePosixPath(name.replace("\\", "/"))
    lower_parts = tuple(part.casefold() for part in normalized.parts)
    lower_name = normalized.name.casefold()
    suffix = normalized.suffix.casefold()
    if lower_name.endswith(".fmost-session.json"):
        return "session-file"
    if lower_name in DATA_FILENAMES:
        return "viewer-state-file"
    if any(part in DATA_PATH_PARTS for part in lower_parts):
        return "generated-data-directory"
    if suffix in DATA_SUFFIXES:
        return "experimental-or-generated-file"
    if repository and suffix == ".png" and lower_name not in ALLOWED_REPOSITORY_IMAGES:
        return "unapproved-repository-image"
    if repository and suffix in {".exe", ".msi", ".zip"}:
        return "release-binary-in-repository"
    if any(token in lower_name for token in ("capture", "screenshot")):
        return "capture-file"
    return None


def _is_third_party_artifact_path(name: str) -> bool:
    parts = [part.casefold() for part in PurePosixPath(name.replace("\\", "/")).parts]
    try:
        internal_index = parts.index("_internal")
    except ValueError:
        return False
    if internal_index + 1 >= len(parts):
        return False
    return parts[internal_index + 1] not in APP_OWNED_INTERNAL_PARTS


def _is_public_windows_system_path(value: str) -> bool:
    normalized = value.replace("/", "\\").replace("\\\\", "\\").casefold()
    normalized = normalized.rstrip(".,;:)]}\"'")
    system_root = _joined("c", ":", "\\", "windows")
    return normalized == system_root or normalized.startswith(system_root + "\\")


def scan_text(
    text: str, display_path: str, *, third_party: bool = False
) -> list[Finding]:
    findings: list[Finding] = []
    normalized_path = display_path.replace("\\", "/").casefold()
    ontology_resource = normalized_path.endswith(
        "/resources/allen_structure_graph_1.json"
    ) or normalized_path == "resources/allen_structure_graph_1.json"
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in FORBIDDEN_TEXT:
            if third_party and rule not in THIRD_PARTY_TEXT_RULES:
                continue
            if ontology_resource and rule in ONTOLOGY_EXEMPT_RULES:
                continue
            matches = list(pattern.finditer(line))
            if rule == "absolute-windows-path":
                matches = [
                    match for match in matches
                    if not _is_public_windows_system_path(match.group(0))
                ]
            if matches:
                snippet = line.strip()
                if len(snippet) > 180:
                    snippet = snippet[:177] + "..."
                findings.append(
                    Finding(display_path, rule, f"line {line_number}: {snippet}")
                )
        if (
            not third_party
            and not ontology_resource
            and AXIS_ABBREVIATION.search(line)
            and not AXIS_CONTEXT.search(line)
        ):
            findings.append(
                Finding(
                    display_path,
                    "ambiguous-region-abbreviation",
                    f"line {line_number}: use the abbreviation only beside "
                    "'anterior–posterior'",
                )
            )
    return findings


def _decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _binary_needles() -> tuple[tuple[str, bytes], ...]:
    # Regex construction makes exact extraction unreliable; list the fixed terms.
    return (
        ("private-sample-a", _joined("252", "574").encode()),
        ("private-sample-b", _joined("252", "610").encode()),
        ("project-marker", _joined("g", "fral").encode()),
        ("legacy-atlas-folder", _joined("brainmap for ", "hyh").encode()),
        ("research-region-full-name", _joined("area ", "postrema").encode()),
    )


def scan_binary_stream(
    stream, display_path: str, *, third_party: bool = False
) -> list[Finding]:
    findings: list[Finding] = []
    needles = _binary_needles()
    overlap = b""
    while True:
        chunk = stream.read(4 * 1024 * 1024)
        if not chunk:
            break
        data = overlap + chunk
        lower = data.lower()
        for rule, needle in needles:
            if third_party and rule not in THIRD_PARTY_TEXT_RULES:
                continue
            ascii_needle = needle.lower()
            utf16_needle = b"".join(bytes((value, 0)) for value in ascii_needle)
            if ascii_needle in lower or utf16_needle in lower:
                finding = Finding(display_path, rule, "value found in binary content")
                if finding not in findings:
                    findings.append(finding)
        private_email_marker = _joined("@", "q", "q", ".com").encode()
        generic_private_root = re.compile(
            rb"[a-z]:[\\/](?:users|research)[\\/]", re.IGNORECASE
        )
        research_root_needles = (
            _joined("d", ":\\", "research\\", "fmost").encode(),
            _joined("d", ":/", "research/", "fmost").encode(),
        )
        if private_email_marker in lower:
            finding = Finding(
                display_path, "private-email", "value found in binary content"
            )
            if finding not in findings:
                findings.append(finding)
        if any(
            needle in lower
            or b"".join(bytes((value, 0)) for value in needle) in lower
            for needle in research_root_needles
        ):
            finding = Finding(
                display_path, "private-research-root", "value found in binary content"
            )
            if finding not in findings:
                findings.append(finding)
        if not third_party and generic_private_root.search(lower):
            finding = Finding(
                display_path, "absolute-windows-path", "value found in binary content"
            )
            if finding not in findings:
                findings.append(finding)
        overlap = data[-512:]
    return findings


def scan_bytes(
    data: bytes, display_path: str, suffix: str, *, third_party: bool = False
) -> list[Finding]:
    if suffix.casefold() in TEXT_SUFFIXES or b"\x00" not in data[:4096]:
        return scan_text(_decode_text(data), display_path, third_party=third_party)
    from io import BytesIO

    return scan_binary_stream(BytesIO(data), display_path, third_party=third_party)


def _repository_files(root: Path) -> list[Path]:
    command = [
        "git", "-C", str(root), "ls-files", "--cached", "--others",
        "--exclude-standard", "-z",
    ]
    try:
        result = subprocess.run(
            command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        excluded = {".git", ".venv", "build", "dist", "release", "artifacts"}
        return sorted(
            path for path in root.rglob("*")
            if path.is_file() and not any(part in excluded for part in path.parts)
        )
    names = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return [root / name for name in names if name]


def scan_repository(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in _repository_files(root):
        # `git ls-files --cached` also reports paths deleted in the working tree.
        # They are absent from the release tree and must not create false findings
        # while a removal is waiting to be committed.
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        path_rule = _is_generated_data_path(relative, repository=True)
        if path_rule:
            findings.append(Finding(relative, path_rule, "file must not be published"))
            continue
        try:
            if path.suffix.casefold() in TEXT_SUFFIXES or path.name in {
                ".gitignore", "LICENSE",
            }:
                findings.extend(scan_text(path.read_text(encoding="utf-8"), relative))
            else:
                with path.open("rb") as stream:
                    findings.extend(scan_binary_stream(stream, relative))
        except OSError as exc:
            findings.append(Finding(relative, "unreadable-file", str(exc)))
    return findings


def _scan_zip(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            display = f"{path.name}!/{info.filename}"
            third_party = _is_third_party_artifact_path(info.filename)
            path_rule = None if third_party else _is_generated_data_path(
                info.filename, repository=False
            )
            if path_rule:
                findings.append(Finding(display, path_rule, "file must not be published"))
                continue
            with archive.open(info) as stream:
                if PurePosixPath(info.filename).suffix.casefold() in TEXT_SUFFIXES:
                    findings.extend(
                        scan_text(
                            _decode_text(stream.read()), display,
                            third_party=third_party,
                        )
                    )
                else:
                    findings.extend(
                        scan_binary_stream(stream, display, third_party=third_party)
                    )
    return findings


def scan_artifact(path: Path) -> list[Finding]:
    path = path.resolve()
    if not path.exists():
        return [Finding(str(path), "missing-artifact", "path does not exist")]
    if path.is_dir():
        findings: list[Finding] = []
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            relative = child.relative_to(path).as_posix()
            third_party = _is_third_party_artifact_path(relative)
            path_rule = None if third_party else _is_generated_data_path(
                relative, repository=False
            )
            if path_rule:
                findings.append(Finding(relative, path_rule, "file must not be published"))
                continue
            if child.suffix.casefold() in TEXT_SUFFIXES:
                findings.extend(
                    scan_text(
                        child.read_text(encoding="utf-8", errors="replace"),
                        relative,
                        third_party=third_party,
                    )
                )
            else:
                with child.open("rb") as stream:
                    findings.extend(
                        scan_binary_stream(stream, relative, third_party=third_party)
                    )
        return findings
    if zipfile.is_zipfile(path):
        return _scan_zip(path)
    with path.open("rb") as stream:
        if path.suffix.casefold() in TEXT_SUFFIXES:
            return scan_text(_decode_text(stream.read()), path.name)
        return scan_binary_stream(stream, path.name)


def _deduplicate(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(set(findings), key=lambda item: (item.path, item.rule, item.detail))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1],
        help="repository root to scan (default: parent of scripts directory)",
    )
    parser.add_argument(
        "--artifact", type=Path, action="append", default=[],
        help="additional installed directory, portable ZIP, or installer to scan",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    findings = scan_repository(args.repo_root)
    for artifact in args.artifact:
        findings.extend(scan_artifact(artifact))
    findings = _deduplicate(findings)
    if findings:
        print(f"Privacy scan failed with {len(findings)} finding(s):", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("Privacy scan passed: no private research traces or data artifacts found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
