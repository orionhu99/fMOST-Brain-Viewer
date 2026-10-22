"""Integration checks for non-destructive partial SWC loading in the UI."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

import fmost_brain_viewer as v
from viewer_rendering import AxonActor


PARTIAL_SWC = "truncated first row\n4 2 40 0 0 1 3\n5 2 50 0 0 1 4\n6 2 60 0 0 1 5\n"
COMPLETE_SWC = "1 1 0 0 0 1 -1\n2 2 10 0 0 1 1\n3 2 20 0 0 1 2\n"


class PartialLoadUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = v.QtWidgets.QApplication.instance() or v.QtWidgets.QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.window = v.QtWidgets.QWidget()
        self.addCleanup(self.window.close)
        window = self.window
        window.neuron_list = v.QtWidgets.QListWidget(window)
        window.neuron_items = {}
        window.axon_sources = {}
        window.axon_actors = {}
        window.axon_actor_last_used = {}
        window.axon_actor_use_counter = 0
        window.axon_load_reports = {}
        window.axon_report_notified = {}
        window.manual_colors = {}
        window.neuron_ids = {}
        window.neuron_match_status = {}
        window.neuron_datasets = {}
        window.datasets = {
            "dataset": SimpleNamespace(key="dataset", brain_id="sample_A", enabled=True,
                                       project=self.root)
        }
        window.axon_width = v.QtWidgets.QDoubleSpinBox(window)
        window.axon_budget_enforced = False
        window.plotter = mock.Mock()
        window._sync_neuron_region_checks = mock.Mock()
        window._refresh_soma_points = mock.Mock()
        for name in (
            "_ensure_axon_actor", "_record_axon_load_report", "_update_axon_diagnostic_label",
            "_apply_neuron_visibility", "_apply_bulk_neuron_selection", "_touch_axon_actor",
            "_remove_axon_actor", "_trim_hidden_axon_cache", "_dataset_enabled_for_neuron",
            "_neuron_toggled", "_dataset_label",
        ):
            setattr(window, name, MethodType(getattr(v.ViewerWindow, name), window))

    def add_neuron(self, key, contents):
        path = self.root / f"{key}.swc"
        path.write_text(contents, encoding="utf-8")
        window = self.window
        item = v.QtWidgets.QListWidgetItem(f"    {key}", window.neuron_list)
        item.setData(v.QtCore.Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | v.QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(v.QtCore.Qt.CheckState.Unchecked)
        window.neuron_items[key] = item
        window.axon_sources[key] = path
        window.manual_colors[key] = "red"
        window.neuron_ids[key] = 1
        window.neuron_match_status[key] = "Matched soma ID 1"
        window.neuron_datasets[key] = "dataset"
        return path

    def test_partial_neuron_stays_checked_and_source_stays_unchanged(self):
        path = self.add_neuron("neuron", PARTIAL_SWC)
        before = path.read_bytes()
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning") as warning:
            self.window._apply_bulk_neuron_selection(["neuron"], True)
        actor = self.window.axon_actors["neuron"]
        self.assertIsInstance(actor, AxonActor)
        self.assertTrue(actor.GetVisibility())
        self.assertEqual(actor.centerlines.n_points, 3)
        self.assertEqual(actor.centerlines.n_lines, 2)
        item = self.window.neuron_items["neuron"]
        self.assertEqual(item.checkState(), v.QtCore.Qt.CheckState.Checked)
        self.assertIn("[Partial]", item.text())
        report = self.window.axon_load_reports["neuron"]
        self.assertTrue(report.is_partial)
        self.assertIn(report.summary_text(), item.toolTip())
        warning.assert_called_once()
        self.assertEqual(path.read_bytes(), before)

    def test_partial_and_failed_files_produce_one_batch_notice(self):
        self.add_neuron("partial", PARTIAL_SWC)
        self.add_neuron("failed", "only broken data\n")
        self.add_neuron("complete", COMPLETE_SWC)
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning") as warning:
            self.window._apply_bulk_neuron_selection(["partial", "failed", "complete"], True)
        warning.assert_called_once()
        notice = str(warning.call_args)
        self.assertIn("partial.swc", notice)
        self.assertIn("failed.swc", notice)
        self.assertEqual(set(self.window.axon_actors), {"partial", "complete"})
        for key in ("partial", "complete"):
            self.assertEqual(self.window.neuron_items[key].checkState(),
                             v.QtCore.Qt.CheckState.Checked)
        self.assertEqual(self.window.neuron_items["failed"].checkState(),
                         v.QtCore.Qt.CheckState.Unchecked)

    def test_read_cleanup_warning_is_not_labelled_as_missing_geometry(self):
        path = self.add_neuron("neuron", "\x00" + COMPLETE_SWC)
        before = path.read_bytes()
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning") as warning:
            self.window._apply_bulk_neuron_selection(["neuron"], True)
        report = self.window.axon_load_reports["neuron"]
        self.assertTrue(report.has_warnings)
        self.assertFalse(report.is_partial)
        item = self.window.neuron_items["neuron"]
        self.assertIn("[Read warning]", item.text())
        self.assertNotIn("[Partial]", item.text())
        warning.assert_called_once()
        self.assertEqual(path.read_bytes(), before)

    def test_hidden_cache_eviction_and_reload_preserve_report_without_repeat_notice(self):
        self.add_neuron("neuron", PARTIAL_SWC)
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning") as warning:
            self.window._apply_bulk_neuron_selection(["neuron"], True)
            warning.assert_called_once()
            signature = self.window.axon_report_notified["neuron"]
            warning.reset_mock()
            with mock.patch.object(v, "MAX_HIDDEN_AXON_BYTES", 0):
                self.window._apply_bulk_neuron_selection(["neuron"], False)
            self.assertNotIn("neuron", self.window.axon_actors)
            self.assertIn("neuron", self.window.axon_load_reports)
            self.assertIn("[Partial]", self.window.neuron_items["neuron"].text())
            self.window._apply_bulk_neuron_selection(["neuron"], True)
            self.assertTrue(self.window.axon_actors["neuron"].GetVisibility())
            self.assertEqual(self.window.axon_report_notified["neuron"], signature)
            warning.assert_not_called()

    def test_reload_of_complete_replacement_clears_old_partial_mark(self):
        path = self.add_neuron("neuron", PARTIAL_SWC)
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning") as warning:
            self.window._apply_bulk_neuron_selection(["neuron"], True)
            partial_summary = self.window.axon_load_reports["neuron"].summary_text()
            self.window._apply_bulk_neuron_selection(["neuron"], False)
            self.window._remove_axon_actor("neuron")
            # This replacement is a temporary test fixture, never user data.
            path.write_text(COMPLETE_SWC, encoding="utf-8")
            warning.reset_mock()
            self.window._apply_bulk_neuron_selection(["neuron"], True)
            warning.assert_not_called()
        self.assertNotIn("neuron", self.window.axon_load_reports)
        self.assertNotIn("neuron", self.window.axon_report_notified)
        item = self.window.neuron_items["neuron"]
        self.assertNotIn("[Partial]", item.text())
        self.assertNotIn(partial_summary, item.toolTip())
        self.assertEqual(item.checkState(), v.QtCore.Qt.CheckState.Checked)

    def test_diagnostic_label_update_does_not_reenter_neuron_selection(self):
        self.add_neuron("neuron", PARTIAL_SWC)
        with mock.patch.object(v.QtWidgets.QMessageBox, "warning"):
            self.window._apply_bulk_neuron_selection(["neuron"], True)
        calls = []
        def changed(item):
            calls.append(item.text())
            self.window._neuron_toggled(item)
        self.window.neuron_list.itemChanged.connect(changed)
        # Force a visible text/tooltip change, instead of setting equal values.
        with v.QtCore.QSignalBlocker(self.window.neuron_list):
            self.window.neuron_items["neuron"].setText("old label")
            self.window.neuron_items["neuron"].setToolTip("old tooltip")
        self.window._update_axon_diagnostic_label("neuron")
        self.app.processEvents()
        self.assertEqual(calls, [])
        self.assertIn("[Partial]", self.window.neuron_items["neuron"].text())
        self.assertFalse(self.window.neuron_list.signalsBlocked())


if __name__ == "__main__":
    unittest.main()
