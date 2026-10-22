import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest import TestCase, mock

import numpy as np
from PySide6 import QtTest
import fmost_brain_viewer as v
from viewer_rendering import AxonActor


class RenderPipelineTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = v.QtWidgets.QApplication.instance() or v.QtWidgets.QApplication([])

    def test_width_changes_coalesce_and_do_not_read_or_replace_actor(self):
        actor = AxonActor(np.array([[0,0,0],[100,20,30]], dtype=np.float32), np.array([[0,1]]), 'red', 1)
        actor.SetVisibility(True)
        window = SimpleNamespace(axon_actors={'a': actor}, axon_width=v.QtWidgets.QDoubleSpinBox(),
                                 axon_width_timer=v.QtCore.QTimer(), plotter=mock.Mock())
        window.axon_width_timer.setInterval(200)
        window.axon_width_timer.setSingleShot(True)
        window.axon_width_timer.timeout.connect(lambda: v.ViewerWindow._apply_pending_axon_width(window))
        mapper = actor.GetMapper()
        with mock.patch.object(v, 'read_swc_partial', side_effect=AssertionError('Unexpected reread')):
            for width in range(1, 8):
                window.axon_width.setValue(width)
                v.ViewerWindow._set_axon_width(window, width)
            QtTest.QTest.qWait(300)
        self.assertEqual(actor.width, 7)
        self.assertIs(actor.GetMapper(), mapper)
        self.assertIs(window.axon_actors['a'], actor)
        window.plotter.render.assert_called_once()

    def test_parser_budget_rejects_before_building_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bounded.swc'
            path.write_text('1 1 0 0 0 1 -1\n2 2 10 0 0 1 1\n')
            with self.assertRaises(MemoryError):
                v.read_swc(path, max_nodes=1)
            points, edges = v.read_swc(path, max_nodes=2)
            self.assertEqual(len(points), 2)
            self.assertEqual(len(edges), 1)

    def test_small_remaining_geometry_budget_does_not_become_parser_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'neuron.swc'
            path.write_text('1 1 0 0 0 1 -1\n' + ''.join(
                f'{i} 2 {i} 0 0 1 {i - 1}\n' for i in range(2, 4001)))
            # This neuron plans ~1.07 MiB of geometry. The old parser guard
            # incorrectly demanded ~3.91 MiB from the remaining 2 MiB.
            existing = SimpleNamespace(geometry_estimate=126 * 1024**2,
                                       GetVisibility=lambda: True)
            window = SimpleNamespace(axon_actors={'existing': existing},
                axon_sources={'new': path}, axon_width=SimpleNamespace(value=lambda: 0),
                axon_budget=SimpleNamespace(value=lambda: 128), axon_budget_enforced=True,
                manual_colors={'new': 'red'}, plotter=mock.Mock(),
                _touch_axon_actor=mock.Mock(), _remove_axon_actor=mock.Mock())
            actor = v.ViewerWindow._ensure_axon_actor(window, 'new')
            self.assertEqual(actor.centerlines.n_points, 4000)
            self.assertIs(window.axon_actors['existing'], existing)
            self.assertLess(actor.geometry_estimate, 2 * 1024**2)
            window.plotter.add_actor.assert_called_once()
            # The true geometry limit still rejects a second load before upload.
            window.axon_sources['over'] = path
            window.manual_colors['over'] = 'blue'
            actor.SetVisibility(True)
            with self.assertRaisesRegex(MemoryError, r'needs an estimated 1.1 MiB; 0.9 MiB'):
                v.ViewerWindow._ensure_axon_actor(window, 'over')
            self.assertNotIn('over', window.axon_actors)
            window.plotter.add_actor.assert_called_once()
            # Turning off the optional policy must allow the same valid file.
            window.axon_budget_enforced = False
            extra = v.ViewerWindow._ensure_axon_actor(window, 'over')
            self.assertEqual(extra.centerlines.n_points, 4000)
            self.assertEqual(window.plotter.add_actor.call_count, 2)

    def test_cancelled_reenable_unchecks_unloaded_neurons(self):
        window = v.QtWidgets.QWidget()
        window.neuron_list = v.QtWidgets.QListWidget(window)
        window.neuron_items = {}
        for key in ('a', 'b'):
            item = v.QtWidgets.QListWidgetItem(key, window.neuron_list)
            item.setCheckState(v.QtCore.Qt.CheckState.Checked)
            window.neuron_items[key] = item
        window.axon_actors = {'a': SimpleNamespace(GetVisibility=lambda: False)}
        window._dataset_enabled_for_neuron = lambda key: True
        window._apply_neuron_visibility = mock.Mock()
        window._sync_neuron_region_checks = mock.Mock()
        window._refresh_soma_points = mock.Mock()
        window._trim_hidden_axon_cache = mock.Mock()
        window.plotter = mock.Mock()
        dialog = mock.Mock()
        dialog.wasCanceled.return_value = True
        with mock.patch.object(v.QtWidgets, 'QProgressDialog', return_value=dialog):
            v.ViewerWindow._apply_bulk_neuron_selection(window, ['a','b'], True)
        self.assertTrue(all(item.checkState() == v.QtCore.Qt.CheckState.Unchecked for item in window.neuron_items.values()))
        window._apply_neuron_visibility.assert_not_called()
        window.close()

    def test_hidden_geometry_budget_evicts_but_keeps_visible(self):
        points = np.array([[0,0,0],[100,0,0]], dtype=np.float32)
        actors = {key: AxonActor(points, np.array([[0,1]]), 'red') for key in 'abc'}
        actors['a'].SetVisibility(False)
        actors['b'].SetVisibility(False)
        actors['c'].SetVisibility(True)
        window = SimpleNamespace(axon_actors=actors, axon_actor_last_used={'a':1,'b':2,'c':3}, plotter=mock.Mock())
        window._remove_axon_actor = MethodType(v.ViewerWindow._remove_axon_actor, window)
        with mock.patch.object(v, 'MAX_HIDDEN_AXON_BYTES', 1):
            self.assertEqual(v.ViewerWindow._trim_hidden_axon_cache(window), 2)
        self.assertEqual(list(window.axon_actors), ['c'])
