"""Regression checks for retained scene data and cancellable region preparation."""
from __future__ import annotations

import gc
import os
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")
import fmost_brain_viewer as viewer


class ResourceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = viewer.QtWidgets.QApplication.instance() or viewer.QtWidgets.QApplication([])

    def test_close_releases_retained_vtk_scene_and_stops_pending_render(self):
        plotter = viewer.pv.Plotter(off_screen=True)
        actor = plotter.add_mesh(viewer.pv.Sphere(), render=False)
        actor_ref = weakref.ref(actor)
        listing = viewer.QtWidgets.QListWidget()
        listing.addItem("neuron")
        timer = viewer.QtCore.QTimer()
        timer.start(1000)
        window = SimpleNamespace(
            _confirm_session_close=lambda: True,
            update_check_thread=None, plotter=plotter,
            annotation=mock.Mock(), axon_actors={"neuron": actor},
            region_actors={42: actor}, datasets={"sample": SimpleNamespace(soma_actor=actor)},
            surface_actor=actor, volume_actor=actor, neuron_list=listing,
            axon_width_timer=timer,
        )
        del actor
        event = mock.Mock()
        viewer.ViewerWindow.closeEvent(window, event)
        gc.collect()
        self.assertIsNone(actor_ref())
        self.assertEqual(window.axon_actors, {})
        self.assertEqual(window.datasets, {})
        self.assertIsNone(window.volume_actor)
        self.assertIsNone(window.annotation)
        self.assertFalse(timer.isActive())
        self.assertEqual(listing.count(), 0)
        event.accept.assert_called_once()
        # A retained, closed replacement window may be closed again harmlessly.
        viewer.ViewerWindow.closeEvent(window, mock.Mock())

    def test_cancel_close_keeps_scene_and_pending_timer(self):
        actor = object()
        timer = mock.Mock()
        window = SimpleNamespace(_confirm_session_close=lambda: False,
            axon_actors={"n": actor}, axon_width_timer=timer)
        event = mock.Mock()
        viewer.ViewerWindow.closeEvent(window, event)
        self.assertIs(window.axon_actors["n"], actor)
        timer.stop.assert_not_called()
        event.ignore.assert_called_once()

    def test_region_cancel_keeps_gui_responsive_until_worker_finishes(self):
        ticks_after_cancel = []
        cancelled = []
        timer = viewer.QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks_after_cancel.append(time.monotonic()) if cancelled else None)
        timer.start()

        def slow_surface(*_args):
            time.sleep(0.25)
            return object()

        def cancel():
            for widget in self.app.topLevelWidgets():
                if isinstance(widget, viewer.RegionCacheDialog):
                    cancelled.append(True)
                    widget.reject()

        viewer.QtCore.QTimer.singleShot(30, cancel)
        try:
            with mock.patch.object(viewer, "load_or_create_structure_surface", side_effect=slow_surface):
                result = viewer.run_region_cache_job(None, object(), {}, region_id=42)
            self.assertIsNone(result)
            self.assertTrue(cancelled)
            self.assertGreater(len(ticks_after_cancel), 3)
        finally:
            timer.stop()

    def test_region_success_and_failure_leave_no_running_worker(self):
        sentinel = object()
        with mock.patch.object(viewer, "load_or_create_structure_surface", return_value=sentinel):
            self.assertIs(viewer.run_region_cache_job(None, object(), {}, region_id=42), sentinel)
        with mock.patch.object(viewer, "load_or_create_structure_surface", side_effect=ValueError("invalid surface")):
            with self.assertRaisesRegex(ValueError, "invalid surface"):
                viewer.run_region_cache_job(None, object(), {}, region_id=42)


if __name__ == "__main__":
    unittest.main()
