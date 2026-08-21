from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.privacy_scan import scan_artifact, scan_text


def _joined(*parts: str) -> str:
    return "".join(parts)


class PrivacyTextTests(unittest.TestCase):
    def test_generic_public_text_passes(self) -> None:
        text = "Dataset sample_A; example regions MOp and VISp."
        self.assertEqual(scan_text(text, "README.md"), [])

    def test_coordinate_abbreviation_requires_clear_axis_context(self) -> None:
        abbreviation = _joined("A", "P")
        clear = f"Anterior-posterior position ({abbreviation} / Z)"
        ambiguous = f"Example region: {abbreviation}"
        self.assertEqual(scan_text(clear, "ui.txt"), [])
        self.assertTrue(
            any(
                finding.rule == "ambiguous-region-abbreviation"
                for finding in scan_text(ambiguous, "ui.txt")
            )
        )

    def test_private_terms_and_paths_fail(self) -> None:
        private_text = "\n".join(
            (
                _joined("252", "574"),
                _joined("brainmap for ", "hyh"),
                _joined("area ", "postrema"),
                _joined("D", ":\\", "Research\\private"),
            )
        )
        rules = {finding.rule for finding in scan_text(private_text, "source.txt")}
        self.assertTrue(
            {
                "private-sample-a",
                "legacy-atlas-folder",
                "research-region-full-name",
                "absolute-windows-path",
            }.issubset(rules)
        )

    def test_standard_windows_path_is_public_platform_information(self) -> None:
        public_path = _joined("C", ":\\", "Windows\\Fonts\\segoeui.ttf")
        self.assertEqual(scan_text(public_path, "source.py"), [])

    def test_official_ontology_has_narrow_region_exception(self) -> None:
        ontology_path = "resources/allen_structure_graph_1.json"
        official_regions = " ".join(
            (
                _joined("area ", "postrema"),
                _joined("N", "TS"),
                _joined("C", "eA"),
                _joined("A", "P"),
            )
        )
        self.assertEqual(scan_text(official_regions, ontology_path), [])
        private_id = _joined("252", "610")
        self.assertTrue(scan_text(private_id, ontology_path))


class PrivacyArtifactTests(unittest.TestCase):
    def test_pyinstaller_third_party_tree_allows_packaged_data_and_build_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "application"
            scipy_data = root / "_internal" / "scipy" / "datasets" / "example.csv"
            scipy_data.parent.mkdir(parents=True)
            scipy_data.write_text("x,y\n1,2\n", encoding="utf-8")
            numpy_data = root / "_internal" / "numpy.libs" / "sample.npy"
            numpy_data.parent.mkdir(parents=True)
            numpy_data.write_bytes(b"\x93NUMPY\x00")
            pyvista_source = root / "_internal" / "pyvista" / "example.py"
            pyvista_source.parent.mkdir(parents=True)
            generic_build_path = _joined("C", ":/", "Users/user/build/example")
            pyvista_source.write_text(generic_build_path, encoding="utf-8")

            self.assertEqual(scan_artifact(root), [])

    def test_application_owned_internal_resources_remain_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "application"
            leaked_data = root / "_internal" / "resources" / "sample.csv"
            leaked_data.parent.mkdir(parents=True)
            leaked_data.write_text("synthetic", encoding="utf-8")
            findings = scan_artifact(root)
        self.assertTrue(
            any(
                finding.rule == "experimental-or-generated-file"
                for finding in findings
            )
        )

    def test_third_party_tree_still_rejects_explicit_private_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "application"
            notes = root / "_internal" / "scipy" / "build-notes.txt"
            notes.parent.mkdir(parents=True)
            private_root = _joined("D", ":\\", "Research\\", "fMOST")
            private_sample = _joined("252", "610")
            private_email = _joined("researcher", "@", "q", "q", ".com")
            notes.write_text(
                "\n".join((private_root, private_sample, private_email)),
                encoding="utf-8",
            )
            rules = {finding.rule for finding in scan_artifact(root)}
        self.assertTrue(
            {"private-research-root", "private-sample-b", "private-email"}.issubset(
                rules
            )
        )

    def test_portable_zip_uses_the_same_third_party_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "portable.zip"
            generic_path = _joined("C", ":/", "Users/builder/package")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "application/_internal/matplotlib/sample_data/demo.csv",
                    "x,y\n1,2\n",
                )
                archive.writestr(
                    "application/_internal/pyvista/example.py", generic_path
                )
            self.assertEqual(scan_artifact(archive_path), [])

    def test_zip_rejects_experimental_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "portable.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("application/readme.txt", "generic public build")
                archive.writestr("data/sample_A-101.swc", "synthetic")
            findings = scan_artifact(archive_path)
        self.assertTrue(
            any(
                finding.rule == "experimental-or-generated-file"
                for finding in findings
            )
        )

    def test_clean_zip_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "portable.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("application/readme.txt", "generic public build")
            self.assertEqual(scan_artifact(archive_path), [])

    def test_binary_rejects_embedded_private_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary_path = Path(temporary) / "application.exe"
            private_path = _joined("D", ":\\", "Research\\private")
            binary_path.write_bytes(b"\x00\x01" + private_path.encode() + b"\x00")
            findings = scan_artifact(binary_path)
        self.assertTrue(
            any(finding.rule == "absolute-windows-path" for finding in findings)
        )


if __name__ == "__main__":
    unittest.main()
