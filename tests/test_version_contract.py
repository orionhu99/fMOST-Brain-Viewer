from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path

from version import __version__


ROOT = Path(__file__).resolve().parents[1]


class VersionContractTests(unittest.TestCase):
    def test_public_release_version(self) -> None:
        self.assertEqual(__version__, "2.3.8")

    def test_package_uses_version_module_as_single_source(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("version", pyproject["project"]["dynamic"])
        self.assertEqual(
            pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "version.__version__",
        )

        module = ast.parse((ROOT / "fmost_brain_viewer.py").read_text(encoding="utf-8"))
        imports_version = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "version"
            and any(alias.name == "__version__" for alias in node.names)
            for node in ast.walk(module)
        )
        self.assertTrue(imports_version)

    def test_public_metadata_agrees(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertIn(f"Current version: **{__version__}", readme)
        self.assertRegex(changelog, rf"(?m)^## {re.escape(__version__)}\s+—")
        self.assertRegex(citation, rf"(?m)^version: {re.escape(__version__)}$")


if __name__ == "__main__":
    unittest.main()
