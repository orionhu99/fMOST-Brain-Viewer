from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import MethodType
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")

import fmost_brain_viewer as viewer
import viewer_update_state as update_state


def session_payload():
    return {
        "format": "fmost-brain-viewer-session", "format_version": 2, "datasets": [],
        "axon_width": 2.0, "visible_neurons": ["sample_A:neuron_A"],
        "camera_position": [[1, 2, 3], [0, 0, 0], [0, 1, 0]],
    }


class UpdateFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = viewer.QtWidgets.QApplication.instance() or viewer.QtWidgets.QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.update_root = self.root / "updates"
        settings_class = viewer.QtCore.QSettings
        self.settings = settings_class(str(self.root / "preferences.ini"), settings_class.Format.IniFormat)
        settings_patch = mock.patch.object(viewer.QtCore, "QSettings", return_value=self.settings)
        factory = settings_patch.start()
        factory.Status = settings_class.Status
        self.addCleanup(settings_patch.stop)
        root_patch = mock.patch.object(viewer, "UPDATE_ROOT", self.update_root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.release = {
            "tag_name": "v9.0.0",
            "assets": [{"name": "fMOST-Brain-Viewer-Setup-9.0.0-win64.exe"}],
        }

    def window(self):
        window = viewer.QtWidgets.QMainWindow()
        window.update_check_thread = None
        window.update_check_progress = None
        window.update_check_worker = None
        window.update_banner = None
        window._automatic_update_check = True
        window._update_in_progress = False
        window._scene_closed = False
        window._skip_session_save_once = False
        window.session_path = self.root / "saved.fmost-session.json"
        window.session_path.write_bytes(b"original session contents")
        window.atlas_template = self.root / "atlas" / "template.nrrd"
        window._session_payload = mock.Mock(return_value=session_payload())
        window._has_unsaved_changes = mock.Mock(return_value=True)
        window._install_update = mock.Mock()
        window._check_for_updates = mock.Mock()
        window.close = mock.Mock()
        for name in ("_show_update_banner", "_hide_update_banner", "_dismiss_update",
                     "_update_check_completed", "_update_check_finished"):
            setattr(window, name, MethodType(getattr(viewer.ViewerWindow, name), window))

        def cleanup():
            window.hide()
            window.deleteLater()
            self.app.processEvents()

        self.addCleanup(cleanup)
        return window

    def download(self, *, accepted=True):
        dialog = mock.Mock()
        dialog.exec.return_value = (viewer.QtWidgets.QDialog.DialogCode.Accepted if accepted
                                    else viewer.QtWidgets.QDialog.DialogCode.Rejected)
        dialog.worker.result = self.update_root / self.release["assets"][0]["name"]
        dialog.worker.error = None
        dialog.worker.isInterruptionRequested.return_value = not accepted
        return dialog

    def install(self, window, *, launched=True, snapshot_error=None, accepted=True, on_launch=None):
        with (
            mock.patch.object(viewer.sys, "frozen", True, create=True),
            mock.patch.object(viewer.sys, "platform", "win32"),
            mock.patch.object(viewer.sys, "executable", str(self.root / "app" / "viewer.exe")),
            mock.patch.object(viewer.os, "getpid", return_value=12345),
            mock.patch.object(viewer, "DownloadDialog", return_value=self.download(accepted=accepted)),
            mock.patch.object(viewer, "launch_update_installer", return_value=launched, side_effect=on_launch) as launch,
            mock.patch.object(viewer.QtWidgets.QMessageBox, "critical") as critical,
            mock.patch.object(viewer.QtWidgets.QMessageBox, "warning") as warning,
            mock.patch.object(viewer, "write_update_resume", wraps=update_state.write_update_resume,
                              side_effect=snapshot_error) as snapshot,
        ):
            viewer.ViewerWindow._install_update(window, self.release)
        return launch, snapshot, critical, warning

    def test_automatic_checks_default_on_but_respect_switch_and_daily_interval(self):
        now = 200000.0
        self.assertTrue(update_state.automatic_check_due(self.settings, now))
        self.settings.setValue("updates/automatic_check", False)
        self.assertFalse(update_state.automatic_check_due(self.settings, now))
        self.settings.setValue("updates/automatic_check", True)
        self.settings.setValue("updates/last_check", now)
        self.assertFalse(update_state.automatic_check_due(self.settings, now + 86399))
        self.assertTrue(update_state.automatic_check_due(self.settings, now + 86400))

    def test_later_reminder_and_clock_correction_do_not_disable_checks_forever(self):
        now = 200000.0
        self.settings.setValue("updates/remind_after", now + 86400)
        self.assertFalse(update_state.automatic_check_due(self.settings, now))
        self.assertTrue(update_state.automatic_check_due(self.settings, now + 86400))
        self.settings.setValue("updates/last_check", now + 100000)
        self.settings.setValue("updates/remind_after", now + 200000)
        self.assertTrue(update_state.automatic_check_due(self.settings, now))
        for invalid in ("invalid", float("nan"), float("inf"), -1):
            with self.subTest(invalid=invalid):
                self.settings.setValue("updates/last_check", invalid)
                self.settings.remove("updates/remind_after")
                self.assertTrue(update_state.automatic_check_due(self.settings, now))

    def test_idle_visible_window_checks_but_hidden_or_busy_window_does_not(self):
        window = self.window()
        with mock.patch.object(viewer.QtWidgets.QApplication, "activeModalWidget", return_value=None):
            viewer.ViewerWindow._maybe_check_for_updates(window)
            window._check_for_updates.assert_not_called()
            window.show()
            viewer.ViewerWindow._maybe_check_for_updates(window)
            window._check_for_updates.assert_called_once_with(automatic=True)
            window._check_for_updates.reset_mock()
            window._update_in_progress = True
            viewer.ViewerWindow._maybe_check_for_updates(window)
            window._check_for_updates.assert_not_called()

    def test_automatic_check_records_attempt_without_a_progress_dialog(self):
        window = self.window()
        with (
            mock.patch.object(viewer.QtCore, "QThread") as thread,
            mock.patch.object(viewer, "UpdateCheckWorker") as worker,
            mock.patch.object(viewer.QtWidgets, "QProgressDialog") as progress,
            mock.patch.object(viewer.time, "time", return_value=200000.0),
        ):
            viewer.ViewerWindow._check_for_updates(window, automatic=True)
        self.assertEqual(float(self.settings.value("updates/last_check")), 200000.0)
        thread.return_value.start.assert_called_once()
        worker.return_value.moveToThread.assert_called_once_with(thread.return_value)
        progress.assert_not_called()

    def test_automatic_offline_and_no_update_checks_are_quiet(self):
        window = self.window()
        with (
            mock.patch.object(viewer.QtWidgets.QMessageBox, "warning") as warning,
            mock.patch.object(viewer.QtWidgets.QMessageBox, "information") as information,
            mock.patch.object(viewer, "UpdateDialog") as dialog,
            mock.patch.object(viewer.LOGGER, "error"),
        ):
            window._update_check_completed([], OSError("synthetic offline"))
            window._update_check_completed([], None)
        warning.assert_not_called()
        information.assert_not_called()
        dialog.assert_not_called()
        self.assertIsNone(window.update_banner)

    def test_skipped_release_and_disabled_checks_do_not_show_banner(self):
        window = self.window()
        self.settings.setValue("updates/skipped_version", "v9.0.0")
        window._update_check_completed([self.release], None)
        self.assertIsNone(window.update_banner)
        self.settings.remove("updates/skipped_version")
        self.settings.setValue("updates/automatic_check", False)
        window._update_check_completed([self.release], None)
        self.assertIsNone(window.update_banner)

    def test_banner_is_nonmodal_and_actions_update_reminder_preferences(self):
        window = self.window()
        with mock.patch.object(viewer, "UpdateDialog") as dialog:
            window._update_check_completed([self.release], None)
        dialog.assert_not_called()
        self.assertIsNotNone(window.update_banner)
        self.assertEqual(window.update_banner.windowModality(), viewer.QtCore.Qt.WindowModality.NonModal)
        self.assertFalse(window.update_banner.isWindow())
        buttons = {button.text(): button for button in window.update_banner.findChildren(viewer.QtWidgets.QPushButton)}
        buttons["Update now"].click()
        window._install_update.assert_called_once_with(self.release)
        with mock.patch.object(viewer.time, "time", return_value=200000.0):
            buttons["Later"].click()
        self.assertEqual(float(self.settings.value("updates/remind_after")), 286400.0)
        self.assertIsNone(window.update_banner)
        window._show_update_banner(self.release)
        for button in window.update_banner.findChildren(viewer.QtWidgets.QPushButton):
            if button.text() == "Skip this version":
                button.click()
                break
        self.assertEqual(self.settings.value("updates/skipped_version"), "v9.0.0")
        self.assertIsNone(window.update_banner)

    def test_reset_reminders_keeps_automatic_check_preference(self):
        self.settings.setValue("updates/automatic_check", False)
        for key in ("skipped_version", "remind_after", "last_check"):
            self.settings.setValue("updates/" + key, "value")
        viewer.ViewerWindow._reset_update_reminders(self.settings)
        self.assertFalse(self.settings.value("updates/automatic_check", type=bool))
        for key in ("skipped_version", "remind_after", "last_check"):
            self.assertFalse(self.settings.contains("updates/" + key))

    def test_cancelled_download_never_saves_or_launches_or_closes(self):
        window = self.window()
        launch, snapshot, critical, _warning = self.install(window, accepted=False)
        snapshot.assert_not_called()
        launch.assert_not_called()
        critical.assert_not_called()
        window.close.assert_not_called()
        self.assertFalse(window._update_in_progress)

    def test_snapshot_failure_prevents_installer_and_keeps_scene_open(self):
        window = self.window()
        launch, _snapshot, critical, _warning = self.install(window, snapshot_error=OSError("synthetic disk full"))
        launch.assert_not_called()
        window.close.assert_not_called()
        critical.assert_called_once()
        self.assertFalse(window._skip_session_save_once)
        self.assertFalse(self.settings.contains("updates/pending_resume"))
        self.assertFalse(window._update_in_progress)

    def test_failed_installer_launch_keeps_scene_and_saved_recovery_copy(self):
        window = self.window()
        launch, snapshot, _critical, warning = self.install(window, launched=False)
        launch.assert_called_once()
        resume = snapshot.call_args.args[0]
        self.assertTrue(resume.is_file())
        window.close.assert_not_called()
        warning.assert_called_once()
        self.assertFalse(window._skip_session_save_once)
        self.assertFalse(self.settings.contains("updates/pending_resume"))
        self.assertEqual(window.session_path.read_bytes(), b"original session contents")

    def test_success_saves_scene_before_launch_and_passes_current_directory_and_pid(self):
        window = self.window()

        def check_saved_before_launch(_installer, **options):
            _path, saved = update_state.read_update_resume(options["resume_path"], self.update_root)
            self.assertTrue(saved["update_resume"]["was_dirty"])
            self.assertEqual(self.settings.value("updates/pending_resume"), str(options["resume_path"]))
            window.close.assert_not_called()
            return True

        launch, snapshot, critical, warning = self.install(window, on_launch=check_saved_before_launch)
        critical.assert_not_called()
        warning.assert_not_called()
        resume = snapshot.call_args.args[0]
        _path, payload = update_state.read_update_resume(resume, self.update_root)
        self.assertEqual(payload["camera_position"], session_payload()["camera_position"])
        self.assertEqual(payload["visible_neurons"], session_payload()["visible_neurons"])
        self.assertEqual(payload["update_resume"]["original_session"], str(window.session_path.resolve()))
        self.assertTrue(payload["update_resume"]["was_dirty"])
        self.assertEqual(payload["update_resume"]["atlas_directory"], str(window.atlas_template.parent.resolve()))
        launch.assert_called_once_with(
            self.update_root / self.release["assets"][0]["name"],
            install_directory=(self.root / "app").resolve(), resume_path=resume, wait_pid=12345,
        )
        self.assertEqual(self.settings.value("updates/pending_resume"), str(resume))
        self.assertTrue(window._skip_session_save_once)
        window.close.assert_called_once()
        self.assertFalse(window._update_in_progress)
        self.assertEqual(window.session_path.read_bytes(), b"original session contents")

    def test_new_unsaved_scene_is_recorded_as_dirty_without_an_original_file(self):
        window = self.window()
        window.session_path = None
        window._has_unsaved_changes.return_value = False
        _launch, snapshot, _critical, _warning = self.install(window)
        _path, payload = update_state.read_update_resume(snapshot.call_args.args[0], self.update_root)
        self.assertIsNone(payload["update_resume"]["original_session"])
        self.assertTrue(payload["update_resume"]["was_dirty"])

    def resume(self, *, original_session=True, was_dirty=True):
        path = self.update_root / ("resume-" + "a" * 32 + ".fmost-session.json")
        payload = session_payload()
        payload["atlas_signature"] = "synthetic-atlas-signature"
        original = self.root / "saved.fmost-session.json" if original_session else None
        update_state.write_update_resume(
            path, self.update_root, payload, original_session=original,
            atlas_directory=self.root / "atlas", target_version="9.0.0", was_dirty=was_dirty,
        )
        return path, payload

    def test_startup_restores_existing_atlas_and_relative_dataset_without_downloading(self):
        path, payload = self.resume()
        project = self.root / "dataset"
        axons, soma, _config = viewer.project_paths("sample_A", project)
        axons.mkdir(parents=True)
        soma.parent.mkdir(parents=True)
        source = "1 1 0 0 0 1 -1\n"
        soma.write_text(source, encoding="utf-8")
        old_key = "sample_A|old-project"
        payload["datasets"] = [{"brain_id": "sample_A", "project_path": "../dataset",
                                "relative_path": True, "key": old_key}]
        payload["visible_neurons"] = [old_key + "::neuron_A"]
        payload["manual_colors"] = {old_key + "::neuron_A": "#ff0000"}
        update_state.write_update_resume(
            path, self.update_root, payload, original_session=self.root / "saved.json",
            atlas_directory=self.root / "atlas", target_version="9.0.0", was_dirty=True,
        )
        before = path.read_bytes()
        with (
            mock.patch.object(viewer, "activate_atlas") as activate,
            mock.patch.object(viewer, "ATLAS_SIGNATURE", "synthetic-atlas-signature"),
            mock.patch.object(viewer, "ensure_atlas_configured") as setup,
        ):
            projects, restored, original = viewer.load_update_startup(path)
        activate.assert_called_once_with((self.root / "atlas").resolve())
        setup.assert_not_called()
        self.assertEqual(projects, [("sample_A", project.resolve())])
        self.assertEqual(original, (self.root / "saved.json").resolve())
        expected_key = viewer.dataset_key("sample_A", project) + "::neuron_A"
        self.assertEqual(restored["visible_neurons"], [expected_key])
        self.assertEqual(restored["manual_colors"], {expected_key: "#ff0000"})
        self.assertEqual(restored["camera_position"], payload["camera_position"])
        self.assertEqual(soma.read_text(encoding="utf-8"), source)
        self.assertEqual(path.read_bytes(), before)

    def test_startup_atlas_mismatch_restores_previous_atlas_and_keeps_snapshot(self):
        path, _payload = self.resume()
        previous = {"synthetic": "atlas state"}
        with (
            mock.patch.object(viewer, "activate_atlas"),
            mock.patch.object(viewer, "ATLAS_SIGNATURE", "different-signature"),
            mock.patch.object(viewer, "_snapshot_active_atlas", return_value=previous),
            mock.patch.object(viewer, "_restore_active_atlas") as restore,
            mock.patch.object(viewer, "load_session_file") as load,
            self.assertRaisesRegex(ValueError, "atlas no longer matches"),
        ):
            viewer.load_update_startup(path)
        load.assert_not_called()
        restore.assert_called_once_with(previous)
        self.assertTrue(path.is_file())

    def test_startup_failed_scene_load_rolls_back_and_keeps_recovery(self):
        path, _payload = self.resume()
        previous = {"synthetic": "atlas state"}
        with (
            mock.patch.object(viewer, "activate_atlas"),
            mock.patch.object(viewer, "ATLAS_SIGNATURE", "synthetic-atlas-signature"),
            mock.patch.object(viewer, "_snapshot_active_atlas", return_value=previous),
            mock.patch.object(viewer, "_restore_active_atlas") as restore,
            mock.patch.object(viewer, "load_session_file", return_value=None),
            self.assertRaisesRegex(ValueError, "could not be restored"),
        ):
            viewer.load_update_startup(path)
        restore.assert_called_once_with(previous)
        self.assertTrue(path.is_file())

    def test_completed_dirty_resume_preserves_unsaved_state_and_clears_matching_pending(self):
        window = self.window()
        window._saved_state = "clean-state-token"
        window._write_recovery = mock.Mock()
        window.status = mock.Mock()
        path, _payload = self.resume()
        self.settings.setValue("updates/pending_resume", str(path))
        viewer.complete_update_resume(window, path)
        self.assertEqual(window._saved_state, "")
        window._write_recovery.assert_called_once()
        self.assertFalse(self.settings.contains("updates/pending_resume"))
        self.assertEqual(window.session_path, self.root / "saved.fmost-session.json")
        self.assertTrue(path.is_file())

    def test_completed_clean_resume_preserves_saved_state_and_other_pending_handoff(self):
        window = self.window()
        window._saved_state = "clean-state-token"
        window._write_recovery = mock.Mock()
        window.status = mock.Mock()
        path, _payload = self.resume(was_dirty=False)
        other = self.update_root / ("resume-" + "b" * 32 + ".fmost-session.json")
        self.settings.setValue("updates/pending_resume", str(other))
        viewer.complete_update_resume(window, path)
        self.assertEqual(window._saved_state, "clean-state-token")
        window._write_recovery.assert_not_called()
        self.assertEqual(self.settings.value("updates/pending_resume"), str(other))

    def test_completed_unsaved_resume_remains_dirty_even_if_flag_is_clean(self):
        window = self.window()
        window.session_path = None
        window._saved_state = "clean-state-token"
        window._write_recovery = mock.Mock()
        window.status = mock.Mock()
        path, _payload = self.resume(original_session=False, was_dirty=False)
        viewer.complete_update_resume(window, path)
        self.assertEqual(window._saved_state, "")
        window._write_recovery.assert_called_once()
        self.assertIsNone(window.session_path)


class UpdateResumeStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.updates = self.root / "updates"
        self.path = self.updates / ("resume-" + "a" * 32 + ".fmost-session.json")

    def write(self, **kwargs):
        options = {"original_session": self.root / "saved.json", "atlas_directory": self.root / "atlas",
                   "target_version": "9.0.0", "was_dirty": False, **kwargs}
        update_state.write_update_resume(self.path, self.updates, session_payload(), **options)

    def test_handoff_roundtrip_preserves_session_reference_and_clean_state(self):
        self.write()
        path, payload = update_state.read_update_resume(self.path, self.updates)
        self.assertEqual(path, self.path.resolve())
        self.assertEqual(payload["update_resume"]["original_session"], str((self.root / "saved.json").resolve()))
        self.assertFalse(payload["update_resume"]["was_dirty"])
        self.assertEqual(payload["axon_width"], 2.0)
        self.assertFalse((self.root / "saved.json").exists())
        self.assertFalse((self.root / "atlas").exists())

    def test_handoff_rejects_outside_nested_or_arbitrary_named_paths(self):
        for path in (self.root / self.path.name, self.updates / "nested" / self.path.name,
                     self.updates / "arbitrary.json", self.updates / "resume-bad.fmost-session.json"):
            with self.subTest(path=path.name):
                with self.assertRaisesRegex(ValueError, "update folder"):
                    update_state.checked_resume_path(path, self.updates)
                with self.assertRaises(ValueError):
                    update_state.read_update_resume(path, self.updates)

    def test_invalid_handoff_metadata_is_rejected_before_restoration(self):
        bad_values = {
            "format_version": [2, None], "was_dirty": ["false", 1],
            "target_version": ["v9.0.0", "invalid", None],
            "original_session": ["relative.json", "bad\npath"],
            "atlas_directory": ["relative", None, "bad\x00path"],
        }
        for key, values in bad_values.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.write()
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    payload["update_resume"][key] = value
                    self.path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "recovery"):
                        update_state.read_update_resume(self.path, self.updates)

    def test_handoff_requires_valid_scene_and_target_version(self):
        with self.assertRaisesRegex(ValueError, "target version"):
            self.write(target_version="v9.0.0")
        self.updates.mkdir()
        self.path.write_text(json.dumps(session_payload()), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata"):
            update_state.read_update_resume(self.path, self.updates)


if __name__ == "__main__":
    unittest.main()
