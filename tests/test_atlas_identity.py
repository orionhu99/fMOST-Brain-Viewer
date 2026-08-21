from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import fmost_brain_viewer as viewer


class AtlasIdentityTests(unittest.TestCase):
    def test_official_download_refuses_to_overwrite_existing_atlas(self) -> None:
        manifest = {
            "atlas": "Synthetic CCF",
            "release": "test",
            "files": {
                viewer.ATLAS_TEMPLATE_NAME: {
                    "size": 64,
                    "sha256": "0" * 64,
                },
                viewer.ATLAS_ANNOTATION_NAME: {
                    "size": 64,
                    "sha256": "1" * 64,
                    "dimensions": [2, 2, 2],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / viewer.ATLAS_TEMPLATE_NAME
            original = b"existing-custom-atlas"
            template.write_bytes(original)
            with (
                mock.patch.object(
                    viewer, "load_atlas_manifest", return_value=manifest
                ),
                mock.patch.object(viewer, "_download_file") as download,
            ):
                with self.assertRaisesRegex(FileExistsError, "will not overwrite"):
                    viewer.download_official_atlas(root)
            download.assert_not_called()
            self.assertEqual(template.read_bytes(), original)

    def test_bundled_ontology_matches_manifest(self) -> None:
        manifest = viewer.load_atlas_manifest()
        specification = manifest["ontology"]
        self.assertEqual(viewer.BUNDLED_ONTOLOGY_PATH.name, specification["file"])
        self.assertTrue(
            viewer._manifest_file_is_valid(
                viewer.BUNDLED_ONTOLOGY_PATH, specification
            )
        )

    def test_flipped_or_missing_atlas_directions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / viewer.ATLAS_TEMPLATE_NAME
            annotation = root / viewer.ATLAS_ANNOTATION_NAME
            template.write_bytes(b"header")
            annotation.write_bytes(b"header")
            template_header = {
                "sizes": (528, 320, 456),
                "type": "unsigned short",
                "encoding": "gzip",
                "space directions": np.diag((25.0, 25.0, 25.0)),
                "space origin": np.zeros(3),
            }
            annotation_header = {
                "sizes": (1320, 800, 1140),
                "type": "unsigned int",
                "encoding": "gzip",
                "space directions": np.diag((-10.0, 10.0, 10.0)),
                "space origin": np.zeros(3),
            }
            with mock.patch.object(
                viewer.nrrd,
                "read_header",
                side_effect=[template_header, annotation_header],
            ):
                with self.assertRaisesRegex(ValueError, "space directions"):
                    viewer.validate_atlas_directory(root)

    def test_signature_detects_changes_outside_file_edges(self) -> None:
        manifest = {
            "atlas": "Synthetic CCF",
            "release": "test",
            "files": {
                viewer.ATLAS_TEMPLATE_NAME: {},
                viewer.ATLAS_ANNOTATION_NAME: {},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / viewer.ATLAS_TEMPLATE_NAME
            annotation = root / viewer.ATLAS_ANNOTATION_NAME
            template.write_bytes(b"A" * (3 * 1024 * 1024))
            annotation.write_bytes(b"B" * (3 * 1024 * 1024))
            with mock.patch.object(viewer, "load_atlas_manifest", return_value=manifest):
                before = viewer.atlas_signature(root)
                original_stat = template.stat()
                with template.open("r+b") as stream:
                    # Deliberately write between the spaced sample windows. The
                    # filesystem change token must invalidate the cached full hash
                    # even when size and mtime are restored.
                    stream.seek(1100 * 1024)
                    stream.write(b"changed-in-the-middle")
                os.utime(
                    template,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
                after = viewer.atlas_signature(root)
        self.assertNotEqual(before, after)

    def test_failed_session_atlas_change_restores_active_state(self) -> None:
        class FakeSettings:
            def __init__(self) -> None:
                self.values = {
                    "atlas_directory": "original-atlas",
                    "atlas_signature": "original-signature",
                }

            def contains(self, key: str) -> bool:
                return key in self.values

            def value(self, key: str, default=None):
                return self.values.get(key, default)

            def setValue(self, key: str, value) -> None:  # noqa: N802
                self.values[key] = value

            def remove(self, key: str) -> None:
                self.values.pop(key, None)

            def sync(self) -> None:
                pass

        fake_settings = FakeSettings()
        original_root = Path("original-atlas")

        def reject_after_mutation(_config, _parent=None) -> bool:
            viewer.ATLAS_ROOT = Path("candidate-atlas")
            viewer.ATLAS_SIGNATURE = "candidate-signature"
            fake_settings.setValue("atlas_directory", "candidate-atlas")
            fake_settings.setValue("atlas_signature", "candidate-signature")
            return False

        payload = {
            "format": "fmost-brain-viewer-session",
            "format_version": 1,
            "datasets": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "synthetic.fmost-session.json"
            session.write_text(json.dumps(payload), encoding="utf-8")
            with (
                mock.patch.object(viewer.QtCore, "QSettings", return_value=fake_settings),
                mock.patch.object(viewer, "ATLAS_ROOT", original_root),
                mock.patch.object(viewer, "ATLAS_SIGNATURE", "original-signature"),
                mock.patch.object(
                    viewer, "_confirm_session_atlas", side_effect=reject_after_mutation
                ),
            ):
                result = viewer.load_session_file(session)
                self.assertIsNone(result)
                self.assertEqual(viewer.ATLAS_ROOT, original_root)
                self.assertEqual(viewer.ATLAS_SIGNATURE, "original-signature")
        self.assertEqual(fake_settings.value("atlas_directory"), "original-atlas")
        self.assertEqual(fake_settings.value("atlas_signature"), "original-signature")


if __name__ == "__main__":
    unittest.main()
