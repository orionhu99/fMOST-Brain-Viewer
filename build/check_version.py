"""Validate that every release surface derives from version.py."""

from __future__ import annotations

import ast
import os
import re
import runpy
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def fail(message: str) -> None:
    raise SystemExit(f"Version check failed: {message}")


def main() -> int:
    version = str(runpy.run_path(ROOT / "version.py")["__version__"])
    if not VERSION_PATTERN.fullmatch(version):
        fail(f"invalid version.py value: {version!r}")

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    if "version" in project or "version" not in project.get("dynamic", []):
        fail("pyproject.toml must declare version as dynamic")
    version_attr = pyproject["tool"]["setuptools"]["dynamic"]["version"].get("attr")
    if version_attr != "version.__version__":
        fail("pyproject.toml must read version.__version__")

    source = ast.parse((ROOT / "fmost_brain_viewer.py").read_text(encoding="utf-8"))
    imports_version = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "version"
        and any(alias.name == "__version__" for alias in node.names)
        for node in ast.walk(source)
    )
    if not imports_version:
        fail("fmost_brain_viewer.py must import __version__ from version")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^##\s+(?:v)?{re.escape(version)}\b", changelog, re.MULTILINE):
        fail(f"CHANGELOG.md has no {version} heading")

    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    if ref_type == "tag" and ref_name != f"v{version}":
        fail(f"tag {ref_name!r} does not match v{version}")

    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
