"""Exercise session opening through its public loader, including path remapping."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fmost_brain_viewer as viewer
import viewer_session as storage


class SessionLoaderTests(unittest.TestCase):
    def make_project(self, root):
        axons, somas, _ = viewer.project_paths("sample_A", root)
        axons.mkdir(parents=True)
        somas.parent.mkdir(parents=True, exist_ok=True)
        somas.write_text("1 1 10 10 10 1 -1\n", encoding="utf-8")
        return root

    def write_session(self, path, datasets, **extra):
        payload = {
            "format": "fmost-brain-viewer-session",
            "format_version": 2,
            "datasets": datasets,
            **extra,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def load(self, path):
        # Avoid reading the user's atlas settings. Keep JSON reading, schema
        # validation, project discovery and neuron-key remapping real.
        with mock.patch.object(viewer, "_snapshot_active_atlas", return_value={}), \
             mock.patch.object(viewer, "_confirm_session_atlas", return_value=True):
            return viewer.load_session_file(path)

    def test_open_valid_saved_session_through_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root / "project")
            key = viewer.dataset_key("sample_A", project)
            path = root / "session.fmost-session.json"
            self.write_session(path, [{"brain_id": "sample_A", "project_path": str(project), "key": key}])
            projects, config = self.load(path)
            self.assertEqual(projects, [("sample_A", project)])
            self.assertEqual(config["datasets"][0]["key"], key)

    def test_relative_project_move_remaps_selected_neurons_and_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root / "project")
            old_key = viewer.dataset_key("sample_A", root / "old_location" / "project")
            new_key = viewer.dataset_key("sample_A", project)
            path = root / "session.fmost-session.json"
            self.write_session(path, [{"brain_id": "sample_A", "project_path": "project", "relative_path": True, "key": old_key}],
                visible_neurons=[old_key + "::sample_A-1_reg"],
                manual_colors={old_key + "::sample_A-1_reg": "#123456"})
            projects, config = self.load(path)
            self.assertEqual(projects, [("sample_A", project.resolve())])
            self.assertEqual(config["visible_neurons"], [new_key + "::sample_A-1_reg"])
            self.assertEqual(config["manual_colors"], {new_key + "::sample_A-1_reg": "#123456"})

    def test_empty_session_returns_none_and_restores_atlas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.fmost-session.json"
            self.write_session(path, [])
            with mock.patch.object(viewer.QtWidgets.QMessageBox, "warning"), \
                 mock.patch.object(viewer, "_restore_active_atlas") as restore:
                self.assertIsNone(self.load(path))
                restore.assert_called_once_with({})

    def test_invalid_session_does_not_reconfigure_atlas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.fmost-session.json"
            self.write_session(path, [{"brain_id": "../bad", "project_path": "project"}])
            with mock.patch.object(viewer.QtWidgets.QMessageBox, "critical"), \
                 mock.patch.object(viewer, "_snapshot_active_atlas") as snapshot:
                self.assertIsNone(viewer.load_session_file(path))
                snapshot.assert_not_called()

    def test_oversized_save_checks_utf8_bytes_before_opening_any_file(self):
        payload = {"format": "fmost-brain-viewer-session", "format_version": 2,
            "datasets": [], "visible_neurons": ["神经元"]}
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previous.json"
            path.write_bytes(b"previous session")
            with mock.patch.object(storage, "MAX_SESSION_BYTES", len(serialized) - 1), \
                 mock.patch.object(Path, "open", side_effect=AssertionError("size must be checked before opening a file")):
                with self.assertRaisesRegex(ValueError, "size limit"):
                    storage.atomic_json(path, payload)
            self.assertEqual(path.read_bytes(), b"previous session")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_save_at_exact_byte_limit_is_readable(self):
        payload = {"format": "fmost-brain-viewer-session", "format_version": 2,
            "datasets": [], "visible_neurons": ["神经元"]}
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boundary.json"
            with mock.patch.object(storage, "MAX_SESSION_BYTES", len(serialized)):
                storage.atomic_json(path, payload)
                self.assertEqual(path.stat().st_size, len(serialized))
                self.assertEqual(storage.read_session(path), payload)


if __name__ == "__main__":
    unittest.main()
