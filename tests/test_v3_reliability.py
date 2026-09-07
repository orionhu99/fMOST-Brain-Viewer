from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace, MethodType
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")
import numpy as np
import nrrd
import fmost_brain_viewer as v
import viewer_updater as updater
from viewer_session import validate_session, read_session, atomic_json
from viewer_cache import read_surface_cache, save_surface_cache


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = v.QtWidgets.QApplication.instance() or v.QtWidgets.QApplication([])

    def test_actor_survives_middle_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.swc"
            path.write_text("1 1 0 0 0 1 -1\n2 2 10 0 0 1 1\n")
            plotter = v.pv.Plotter(off_screen=True)
            window = SimpleNamespace(axon_actors={}, axon_actor_last_used={}, axon_actor_use_counter=0,
                axon_sources={key: path for key in "ABC"}, manual_colors={key: "red" for key in "ABC"},
                axon_width=SimpleNamespace(value=lambda: 0), plotter=plotter)
            window._touch_axon_actor = MethodType(v.ViewerWindow._touch_axon_actor, window)
            try:
                v.ViewerWindow._ensure_axon_actor(window, "A")
                b = v.ViewerWindow._ensure_axon_actor(window, "B")
                b.SetVisibility(True)
                v.ViewerWindow._remove_axon_actor(window, "A")
                v.ViewerWindow._ensure_axon_actor(window, "C")
                self.assertIn(b, plotter.renderer.actors.values())
                self.assertTrue(b.GetVisibility())
                self.assertEqual(len(plotter.renderer.actors), 2)
            finally:
                plotter.close()

    def test_bad_swc_does_not_block_list_or_good_neuron(self):
        listing = v.QtWidgets.QListWidget()
        items = {key: v.QtWidgets.QListWidgetItem(key, listing) for key in ("bad", "good")}
        calls = []
        def apply(key, checked):
            if key == "bad":
                raise ValueError("bad row")
            calls.append(key)
        window = SimpleNamespace(neuron_list=listing, neuron_items=items,
            axon_actors={key: object() for key in items}, axon_sources={key: Path(key) for key in items},
            _dataset_enabled_for_neuron=lambda key: True, _apply_neuron_visibility=apply,
            _remove_axon_actor=mock.Mock(), _sync_neuron_region_checks=mock.Mock(),
            _refresh_soma_points=mock.Mock(), _trim_hidden_axon_cache=mock.Mock(), plotter=mock.Mock())
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning"):
            v.ViewerWindow._apply_bulk_neuron_selection(window, list(items), True)
        self.assertFalse(listing.signalsBlocked())
        self.assertEqual(calls, ["good"])
        self.assertEqual(items["bad"].checkState(), v.QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(items["good"].checkState(), v.QtCore.Qt.CheckState.Checked)

    def test_rejects_swc_corruption_and_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.swc"
            for content in ("1 1 0 0 0 1\n", "1 1 0 0 0 1 2\n", "1 1 0 0 0 1 1\n",
                "1 1 0 0 0 1 2\n2 2 0 0 0 1 1\n", "1 1 1e100 0 0 1 -1\n"):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        v.read_swc_with_ids(path)
            path.write_text("1 1 0 0 0 1 -1\n2 1 1 1 1 1 -1\n")
            self.assertEqual(v.read_swc_with_ids(path)[2].shape, (0, 2))

    def test_gzip_bounded_and_cache_outside_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            path = source / "test.nrrd"
            header = b"NRRD0005\ntype: uint32\ndimension: 3\nsizes: 2 2 2\nencoding: gzip\nendian: little\nspace directions: (10,0,0) (0,10,0) (0,0,10)\n\n"
            path.write_bytes(header + gzip.compress(b"\0" * 1048576))
            progress = mock.Mock()
            progress.wasCanceled.return_value = False
            with mock.patch.object(v, "CACHE_BASE", root / "cache"), mock.patch.object(v, "_progress_dialog", return_value=progress):
                with self.assertRaisesRegex(OSError, "exceeds"):
                    v.prepare_annotation_for_memmap(path, source)
                self.assertFalse(list((root / "cache").rglob("*.part")))
                path.write_bytes(header + gzip.compress(b"\0" * 32))
                result = v.prepare_annotation_for_memmap(path, source)
                self.assertTrue(result.is_relative_to(root / "cache"))
                self.assertEqual(v.RawNrrdMemmap(result).shape, (2, 2, 2))
                self.assertEqual(list(source.iterdir()), [path])

    def test_new_unsaved_session_save_discard_cancel(self):
        window = SimpleNamespace(_has_unsaved_changes=lambda: True, _save_session=mock.Mock(return_value=True))
        for choice, expected, saves in ((v.QtWidgets.QMessageBox.StandardButton.Cancel, False, 0),
                (v.QtWidgets.QMessageBox.StandardButton.Discard, True, 0),
                (v.QtWidgets.QMessageBox.StandardButton.Save, True, 1)):
            window._save_session.reset_mock()
            with mock.patch.object(v.QtWidgets.QMessageBox, "warning", return_value=choice):
                self.assertEqual(v.ViewerWindow._confirm_session_close(window), expected)
            self.assertEqual(window._save_session.call_count, saves)

    def test_failed_save_can_cancel_close(self):
        window = SimpleNamespace(_has_unsaved_changes=lambda: True, _save_session=lambda: False)
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning", side_effect=[v.QtWidgets.QMessageBox.StandardButton.Save, v.QtWidgets.QMessageBox.StandardButton.Cancel]):
            self.assertFalse(v.ViewerWindow._confirm_session_close(window))

    def test_session_open_saves_before_atlas_mutation_and_rolls_back(self):
        sequence = []
        window = SimpleNamespace(_confirm_session_close=lambda: sequence.append("save") or True,
            _replace_with_projects=mock.Mock(return_value=False), setEnabled=mock.Mock())
        def load(*args):
            sequence.append("load")
            return [("sample_A", Path("project"))], {}
        with mock.patch.object(v.QtWidgets.QFileDialog, "getOpenFileName", return_value=("test.json", "")), \
             mock.patch.object(v, "_snapshot_active_atlas", return_value={}), \
             mock.patch.object(v, "load_session_file", side_effect=load), \
             mock.patch.object(v, "_restore_active_atlas") as restore:
            v.ViewerWindow._open_session(window)
        self.assertEqual(sequence, ["save", "load"])
        restore.assert_called_once_with({})

    def test_session_open_cancel_does_not_load(self):
        window = SimpleNamespace(_confirm_session_close=lambda: False)
        with mock.patch.object(v.QtWidgets.QFileDialog, "getOpenFileName", return_value=("test.json", "")), \
             mock.patch.object(v, "load_session_file") as load:
            v.ViewerWindow._open_session(window)
        load.assert_not_called()

    def test_nested_session_validation(self):
        base = {"format": "fmost-brain-viewer-session", "format_version": 2, "datasets": []}
        for field, value in (("visible_neurons", [None]), ("manual_colors", {"x": "not-a-color"}),
            ("brain_opacity", float("nan")), ("custom_regions", ["42"]), ("show_slice", "false"),
            ("slice_index", -1), ("camera_position", [[0]]), ("datasets", [{"brain_id": "../x", "project_path": "x"}])):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_session({**base, field: value})
        self.assertEqual(validate_session({**base, "format_version": 1})["format_version"], 1)

    def test_atomic_save_preserves_previous_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text("previous")
            with mock.patch.object(Path, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    atomic_json(path, {"value": 1})
            self.assertEqual(path.read_text(), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_surface_cache_corruption_is_rebuildable(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.nrrd"
            cache = Path(directory) / "mesh.vtp"
            source.write_bytes(b"synthetic")
            cache.write_bytes(b"broken")
            self.assertIsNone(read_surface_cache(cache, source))
            save_surface_cache(v.pv.Sphere(), cache)
            self.assertGreater(read_surface_cache(cache, source).n_points, 0)

    def test_soma_provenance_and_strict_mode(self):
        data = np.full((5, 5, 5), 42, dtype=np.uint32)
        data[2, 2, 2] = 0
        annotation = SimpleNamespace(shape=data.shape, spacing=(10, 10, 10), direct_atlas_ids=True,
            value_at=lambda index: int(data[tuple(index)]), is_background=lambda value: value == 0,
            atlas_id=int, world_crop=lambda low, high: data[tuple(slice(a,b) for a,b in zip(low,high))])
        detail = {}
        regions, _ = v.classify_somas(annotation, np.array([1]), np.array([[20,20,20]]), diagnostics=detail)
        self.assertEqual(regions[1], 42)
        self.assertEqual(detail[1]["source"], "neighborhood")
        regions, _ = v.classify_somas(annotation, np.array([1]), np.array([[20,20,20]]), strict=True, diagnostics=detail)
        self.assertIsNone(regions[1])
        self.assertEqual(detail[1]["raw_value"], 0)

    def test_download_cancellation_and_untrusted_url(self):
        asset = {"digest": "sha256:" + "0"*64, "browser_download_url": "https://example.invalid/test.exe"}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "URL"):
                updater.download_release_asset(asset, Path(directory) / "test.exe")
            with self.assertRaises(updater.AtlasSetupCancelled):
                updater.download_release_asset(asset, Path(directory) / "test.exe", cancelled=lambda: True)

    def test_worker_cancelled_without_launch(self):
        def cancelled_download(asset, path, progress, cancelled):
            while not cancelled():
                v.QtCore.QThread.msleep(5)
            raise updater.AtlasSetupCancelled()
        worker = updater.DownloadWorker({}, Path("unused"), downloader=cancelled_download)
        worker.start()
        worker.requestInterruption()
        self.assertTrue(worker.wait(2000))
        self.assertIsInstance(worker.error, updater.AtlasSetupCancelled)
        self.assertIsNone(worker.result)

    def test_update_cancel_prevents_installer_launch(self):
        window = SimpleNamespace(update_check_thread=None, update_check_progress=None,
            _confirm_session_close=mock.Mock(return_value=False), close=mock.Mock())
        release = {"tag_name": "v9.0.0", "assets": [{"name": "fMOST-Brain-Viewer-Setup-9.0.0-win64.exe"}]}
        dialog = mock.Mock()
        dialog.exec.return_value = v.QtWidgets.QDialog.DialogCode.Accepted
        dialog.selected_release.return_value = release
        download = mock.Mock()
        download.exec.return_value = v.QtWidgets.QDialog.DialogCode.Accepted
        download.worker.result = Path("test.exe")
        with mock.patch.object(v, "UpdateDialog", return_value=dialog), \
             mock.patch.object(v, "DownloadDialog", return_value=download), \
             mock.patch.object(v, "launch_update_installer", return_value=True) as launch:
            v.ViewerWindow._update_check_completed(window, [release], None)
        window._confirm_session_close.assert_called_once()
        launch.assert_not_called()
        window.close.assert_not_called()

    def test_download_dialog_remains_responsive_until_cancelled(self):
        def slow(asset, path, progress, cancelled):
            while not cancelled():
                v.QtCore.QThread.msleep(5)
            raise updater.AtlasSetupCancelled()
        worker_class = updater.DownloadWorker
        with mock.patch.object(updater, "DownloadWorker", side_effect=lambda asset, path, parent: worker_class(asset, path, parent, downloader=slow)):
            dialog = updater.DownloadDialog({}, Path("unused"))
            heartbeat = []
            v.QtCore.QTimer.singleShot(10, lambda: heartbeat.append(True))
            v.QtCore.QTimer.singleShot(30, dialog.reject)
            self.assertEqual(dialog.exec(), v.QtWidgets.QDialog.DialogCode.Rejected)
            self.assertEqual(heartbeat, [True])
            self.assertFalse(dialog.worker.isRunning())

    def test_installer_has_no_wildcard_deletion(self):
        root = Path(v.__file__).parent
        text = (root / "installer" / "fmost_brain_viewer.iss").read_text()
        self.assertNotIn("[InstallDelete]", text)
        self.assertNotIn("CloseApplications=force", text)


class ViewerRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = v.QtWidgets.QApplication.instance() or v.QtWidgets.QApplication([])

    def test_real_window_session_roundtrip_and_recovery(self):
        class HeadlessInteractor(v.pv.Plotter):
            interactor = None
            def __init__(self, parent):
                super().__init__(off_screen=True)
                self.interactor = v.QtWidgets.QWidget(parent)
        if os.environ.get("VIEWER_NATIVE_TEST") == "1":
            HeadlessInteractor = v.QtInteractor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atlas = root / "annotation.nrrd"
            template = root / "template.nrrd"
            labels = np.zeros((32, 32, 32), dtype=np.uint32)
            labels[4:28,4:28,4:28] = 42
            nrrd.write(str(atlas), labels, header={"encoding": "raw", "space directions": np.eye(3)*10})
            nrrd.write(str(template), (labels * 100).astype(np.uint16), header={"encoding": "raw", "space directions": np.eye(3)*10})
            axons, soma, _ = v.project_paths("sample_A", root)
            soma.parent.mkdir(parents=True)
            axons.mkdir()
            soma.write_text("1 1 100 100 100 1 -1\n")
            (axons / "sample_A-1_reg.swc").write_text("1 1 100 100 100 1 -1\n2 2 150 150 150 1 1\n")
            ontology = {42: {"id": 42, "acronym": "TEST", "name": "Synthetic structure", "children": [], "rgb_triplet": [100,100,100]}}
            with mock.patch.object(v, "ANNOTATION_10", atlas), mock.patch.object(v, "CACHE_ROOT", root / "cache"), \
                 mock.patch.object(v, "TEMPLATE_25", template), mock.patch.object(v, "ATLAS_SIGNATURE", "synthetic-A"), mock.patch.object(v, "load_ontology", return_value=ontology), \
                 mock.patch.object(v, "QtInteractor", HeadlessInteractor):
                first = v.ViewerWindow([("sample_A", root)])
                second = None
                try:
                    first.slice_slider.setValue(8)
                    first.slice_check.setChecked(False)
                    first._set_brain_style("Volume")
                    key = next(iter(first.neuron_items))
                    first._apply_bulk_neuron_selection([key], True)
                    self.assertTrue(first._has_unsaved_changes())
                    first.recovery_path = root / "recovery.json"
                    first._write_recovery()
                    self.assertTrue(first.recovery_path.is_file())
                    first.session_path = root / "session.json"
                    self.assertTrue(first._save_session())
                    self.assertFalse(first._has_unsaved_changes())
                    payload = read_session(first.session_path)
                    self.assertEqual(payload["atlas_signature"], "synthetic-A")
                    with mock.patch.object(v, "ATLAS_SIGNATURE", "synthetic-B"):
                        self.assertEqual(first._session_payload(first.session_path)["atlas_signature"], "synthetic-A")
                    second = v.ViewerWindow([("sample_A", root)], session_path=first.session_path, session_config=payload)
                    self.assertEqual(second.slice_slider.value(), 8)
                    self.assertFalse(second.slice_check.isChecked())
                    self.assertEqual(second.current_brain_style, "Volume")
                    self.assertTrue(second.axon_actors[key].GetVisibility())
                    self.assertEqual(second._session_payload(first.session_path), payload)
                    if os.environ.get("VIEWER_NATIVE_TEST") == "1":
                        screenshot = second.plotter.screenshot(return_img=True)
                        self.assertGreater(np.unique(screenshot).size, 1)
                finally:
                    for window in (second, first):
                        if window is not None:
                            window._skip_session_save_once = True
                            window.close()


if __name__ == "__main__":
    unittest.main()
