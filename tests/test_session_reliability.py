from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import fmost_brain_viewer as viewer


class SessionReliabilityTests(unittest.TestCase):
    def test_failed_atomic_save_removes_part_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.fmost-session.json"
            window = SimpleNamespace(
                session_path=path,
                _session_payload=lambda _path: {"version": 1},
                status=mock.Mock(),
            )
            with (
                mock.patch.object(Path, "replace", side_effect=OSError("disk full")),
                mock.patch.object(viewer.QtWidgets.QMessageBox, "critical"),
            ):
                saved = viewer.ViewerWindow._save_session(window)
            self.assertFalse(saved)
            self.assertFalse(path.with_suffix(path.suffix + ".part").exists())

    def test_close_is_ignored_when_session_cannot_be_saved(self) -> None:
        window = SimpleNamespace(
            _confirm_session_close=mock.Mock(return_value=False),
            update_check_thread=None,
            plotter=mock.Mock(),
        )
        event = mock.Mock()
        viewer.ViewerWindow.closeEvent(window, event)
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        window.plotter.close.assert_not_called()

    def test_replacement_is_not_created_when_session_close_is_cancelled(self) -> None:
        window = SimpleNamespace(_confirm_session_close=mock.Mock(return_value=False))
        replace_with_projects = viewer.ViewerWindow._replace_with_projects
        with mock.patch.object(viewer, "ViewerWindow") as replacement:
            replace_with_projects(window, [("brain", Path("dataset"))])
        replacement.assert_not_called()


if __name__ == "__main__":
    unittest.main()
