"""Synthetic regressions for GPU axon segment placement and width updates."""
from __future__ import annotations
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('PYVISTA_OFF_SCREEN', 'true')
import unittest
import numpy as np
import pyvista as pv
from viewer_rendering import AxonActor


class GpuSegmentReviewTests(unittest.TestCase):
    def _image(self, actor):
        plotter = pv.Plotter(off_screen=True, window_size=(160, 160))
        try:
            plotter.set_background('black')
            plotter.disable_anti_aliasing()
            plotter.add_actor(actor, reset_camera=False, render=False)
            actor.GetProperty().SetAmbient(1.0)
            actor.GetProperty().SetDiffuse(0.0)
            plotter.camera_position = [(0, 0, 300), (0, 0, 0), (0, 1, 0)]
            plotter.enable_parallel_projection()
            plotter.camera.parallel_scale = 50
            return plotter.screenshot(return_img=True)[:, :, 0] > 100
        finally:
            plotter.close()

    def test_gpu_matches_explicit_cylinders_for_oblique_segments(self):
        for direction in ((1,0,0), (0,1,0), (0,0,1), (-1,-2,0), (1,2,3)):
            unit = np.array(direction, dtype=float)
            unit /= np.linalg.norm(unit)
            actor = AxonActor(np.array([-30*unit, 30*unit]), [[0,1]], 'white', .5)
            actual = self._image(actor)
            cylinder = pv.Cylinder(center=(0,0,0), direction=unit, radius=2, height=60, resolution=8, capping=True)
            reference = pv.Actor(mapper=pv.DataSetMapper(cylinder))
            reference.GetProperty().SetColor(1,1,1)
            expected = self._image(reference)
            with self.subTest(direction=direction):
                self.assertGreater(np.count_nonzero(actual), 10)
                # An octagonal cross-section may rotate about the segment axis;
                # allow its different silhouette while requiring same placement.
                overlap = np.count_nonzero(actual & expected) / np.count_nonzero(actual | expected)
                self.assertGreater(overlap, .80)

    def test_width_change_keeps_instances_and_updates_radius(self):
        actor = AxonActor([[-30,0,0],[30,0,0]], [[0,1]], 'white', .5)
        mapper = actor.glyph_mapper
        segments = actor.segments
        initial = self._image(actor)
        actor.set_width(2)
        widened = self._image(actor)
        self.assertIs(actor.glyph_mapper, mapper)
        self.assertIs(actor.segments, segments)
        self.assertGreater(np.count_nonzero(widened), 3 * np.count_nonzero(initial))
        actor.set_width(0)
        self.assertIs(actor.GetMapper(), actor.line_mapper)
        actor.set_width(.5)
        self.assertIs(actor.GetMapper(), mapper)
        np.testing.assert_array_equal(self._image(actor), initial)

    def test_zero_length_edges_allow_width_changes(self):
        actor = AxonActor([[1,2,3],[1,2,3]], [[0,1]], 'white', 0)
        actor.set_width(1)
        actor.set_width(0)
        actor.set_width(2)
        self.assertEqual(actor.centerlines.n_lines, 1)


if __name__ == '__main__':
    unittest.main()
