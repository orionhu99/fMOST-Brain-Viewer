"""Regression coverage for soma topology and bounded-memory GIF export."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import GifImagePlugin, Image

import fmost_brain_viewer as v


class SomaTopologyTests(unittest.TestCase):
    def test_filter_empty_and_restore_rebuild_vertex_cells(self):
        points = np.arange(15, dtype=np.float32).reshape(5, 3)
        mesh = v.pv.PolyData(points.copy())
        actor = mock.Mock()
        dataset = SimpleNamespace(key="brain", soma_points=points,
                                  soma_point_by_id={2: points[2]},
                                  soma_mesh=mesh, soma_actor=actor, enabled=True)
        item = mock.Mock()
        item.checkState.return_value = v.QtCore.Qt.CheckState.Checked
        window = SimpleNamespace(
            datasets={"brain": dataset}, soma_check=mock.Mock(),
            neuron_items={"n": item}, neuron_ids={"n": 2},
            neuron_datasets={"n": "brain"}, plotter=mock.Mock())
        window.soma_check.isChecked.return_value = False
        v.ViewerWindow._refresh_soma_points(window, render=False)
        self.assertIs(dataset.soma_mesh, mesh)
        np.testing.assert_array_equal(mesh.points, points[2:3])
        np.testing.assert_array_equal(mesh.verts, [1, 0])
        self.assertEqual(mesh.n_cells, 1)
        item.checkState.return_value = v.QtCore.Qt.CheckState.Unchecked
        v.ViewerWindow._refresh_soma_points(window, render=False)
        self.assertEqual(mesh.n_points, 0)
        self.assertEqual(mesh.n_cells, 0)
        window.soma_check.isChecked.return_value = True
        v.ViewerWindow._refresh_soma_points(window, render=False)
        np.testing.assert_array_equal(mesh.points, points)
        np.testing.assert_array_equal(mesh.verts[1::2], np.arange(5))
        self.assertEqual(mesh.n_cells, 5)


class RestoredVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = v.QtWidgets.QApplication.instance() or v.QtWidgets.QApplication([])

    def test_hidden_brain_restores_checkbox_and_rendering_consistently(self):
        checkbox = v.QtWidgets.QCheckBox()
        checkbox.setChecked(True)
        window = SimpleNamespace(
            config={"show_brain": False}, template_check=checkbox,
            annotation=SimpleNamespace(shape=(8, 8, 8)), region_items={},
            neuron_items={}, plotter=mock.Mock(),
        )
        for name in (
            "template_opacity", "axon_width", "soma_size", "region_opacity",
            "soma_check", "grid_check", "legend_check", "slice_slider",
            "slice_check", "color_mode", "region_list", "neuron_list",
            "_set_brain_style", "_sync_neuron_region_checks", "_refresh_soma_points",
            "_update_region_legend", "_reset_default_view",
        ):
            setattr(window, name, mock.Mock())
        v.ViewerWindow._restore_config(window)
        self.assertFalse(checkbox.isChecked())
        window._set_brain_style.assert_called_once_with("Surface", False)
        checkbox.close()


class LegacyAnnotationDisplayTests(unittest.TestCase):
    def test_encoded_labels_are_decoded_and_background_is_transparent(self):
        encoded = v.atlas_id_to_encoded(42)
        labels = np.array([[0, encoded, 65535]], dtype=np.uint16)
        annotation = SimpleNamespace(
            is_background=lambda value: value in (0, 65535),
            atlas_id=v.encoded_to_atlas_id,
        )
        rgba = v.annotation_slice_rgba(
            labels, {42: {"color_hex_triplet": "123456"}}, annotation=annotation)
        np.testing.assert_array_equal(rgba, [[[0, 0, 0, 0], [18, 52, 86, 255], [0, 0, 0, 0]]])
        direct = SimpleNamespace(is_background=lambda value: value == 0,
                                 atlas_id=lambda value: value)
        rgba = v.annotation_slice_rgba(
            labels.astype(np.uint32), {65535: {"color_hex_triplet": "ABCDEF"}},
            annotation=direct)
        np.testing.assert_array_equal(rgba[0, 2], [171, 205, 239, 255])

    def test_whole_brain_excludes_legacy_background_without_changing_uint32(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as directory:
                labels = np.zeros((3, 3, 3), dtype=np.uint16 if legacy else np.uint32)
                labels[0, 0, 0] = 65535
                labels[1, 1, 1] = 42
                annotation = SimpleNamespace(
                    cache_root=Path(directory), path=Path(directory) / "annotation.nrrd",
                    world_labels=lambda _step: labels, spacing=(10, 10, 10),
                    is_background=lambda value: value == 0 or (legacy and value == 65535),
                )
                with mock.patch.object(v, "read_surface_cache", return_value=None), \
                     mock.patch.object(v, "save_surface_cache"), \
                     mock.patch.object(v, "_surface_from_mask", return_value=mock.Mock()) as surface, \
                     mock.patch.object(v.ndimage, "binary_closing", side_effect=lambda mask, **_: mask), \
                     mock.patch.object(v.ndimage, "binary_fill_holes", side_effect=lambda mask: mask):
                    v.load_or_create_whole_brain_surface(annotation)
                    mask = surface.call_args.args[0]
                    self.assertEqual(bool(mask[0, 0, 0]), not legacy)
                    self.assertTrue(mask[1, 1, 1])


class StreamingGifTests(unittest.TestCase):
    def make_window(self):
        actor = mock.Mock()
        actor.GetVisibility.return_value = True
        window = SimpleNamespace(
            plotter=mock.Mock(), status=mock.Mock(),
            _export_actor_groups=lambda: {"axons": [actor]},
            _set_export_visibility=v.ViewerWindow._set_export_visibility,
        )
        window.plotter.camera_position = [(0, 0, 10), (0, 0, 0), (0, 1, 0)]
        window.recovery_timer = mock.Mock()
        window.recovery_timer.isActive.return_value = True
        return window, actor

    def test_frames_are_encoded_before_next_capture_and_roundtrip(self):
        window, actor = self.make_window()
        calls = []
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        def capture(**_kwargs):
            window.recovery_timer.stop.assert_called_once()
            window.recovery_timer.start.assert_not_called()
            index = sum(call == "capture" for call in calls)
            calls.append("capture")
            return np.full((12, 16, 3), colors[index], dtype=np.uint8)
        window.plotter.screenshot.side_effect = capture
        original_encoder = GifImagePlugin.getdata
        def encode(*args, **kwargs):
            calls.append("encode")
            return original_encoder(*args, **kwargs)
        progress = mock.Mock()
        progress.wasCanceled.return_value = False
        camera_before = list(window.plotter.camera_position)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(v.QtWidgets, "QProgressDialog", return_value=progress), \
             mock.patch.object(v.QtWidgets.QApplication, "processEvents"), \
             mock.patch.object(GifImagePlugin, "getdata", side_effect=encode):
            path = Path(directory) / "rotation.gif"
            result = v.ViewerWindow.export_rotating_gif(
                window, path, {"axons": True}, 3, 0.6, 1, 1)
            self.assertTrue(result)
            self.assertEqual(calls, ["capture", "encode"] * 3)
            with Image.open(path) as decoded:
                self.assertEqual(decoded.n_frames, 3)
                self.assertEqual(decoded.info["loop"], 0)
                for index, color in enumerate(colors):
                    decoded.seek(index)
                    self.assertEqual(decoded.info["duration"], 200)
                    self.assertEqual(decoded.convert("RGB").getpixel((0, 0)), color)
            self.assertEqual(list(Path(directory).glob("*.part")), [])
        self.assertEqual(window.plotter.camera_position, camera_before)
        actor.SetVisibility.assert_called_with(True)
        window.recovery_timer.start.assert_called_once()

    def test_cancel_preserves_existing_file_and_restores_scene(self):
        window, actor = self.make_window()
        window.plotter.screenshot.return_value = np.zeros((8, 8, 3), np.uint8)
        progress = mock.Mock()
        progress.wasCanceled.side_effect = [False, True]
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(v.QtWidgets, "QProgressDialog", return_value=progress), \
             mock.patch.object(v.QtWidgets.QApplication, "processEvents"):
            path = Path(directory) / "rotation.gif"
            path.write_bytes(b"previous export")
            self.assertFalse(v.ViewerWindow.export_rotating_gif(
                window, path, {"axons": False}, 3, 0.6, 1, 1))
            self.assertEqual(path.read_bytes(), b"previous export")
            self.assertEqual(list(Path(directory).glob("*.part")), [])
        actor.SetVisibility.assert_called_with(True)
        window.recovery_timer.start.assert_called_once()

    def test_capture_failure_preserves_existing_file_and_cleans_partial(self):
        window, actor = self.make_window()
        window.plotter.screenshot.side_effect = [np.zeros((8, 8, 3), np.uint8), None]
        progress = mock.Mock()
        progress.wasCanceled.return_value = False
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(v.QtWidgets, "QProgressDialog", return_value=progress), \
             mock.patch.object(v.QtWidgets.QApplication, "processEvents"):
            path = Path(directory) / "rotation.gif"
            path.write_bytes(b"previous export")
            with self.assertRaisesRegex(RuntimeError, "did not return an image"):
                v.ViewerWindow.export_rotating_gif(
                    window, path, {"axons": True}, 3, 0.6, 1, 1)
            self.assertEqual(path.read_bytes(), b"previous export")
            self.assertEqual(list(Path(directory).glob("*.part")), [])
        actor.SetVisibility.assert_called_with(True)
        window.recovery_timer.start.assert_called_once()


class AtomicCaptureTests(unittest.TestCase):
    def make_window(self):
        window, actor = StreamingGifTests.make_window(self)
        window.plotter.screenshot.return_value = np.full((12, 16, 4), 128, np.uint8)
        return window, actor

    def test_image_encoding_failure_preserves_existing_capture(self):
        window, actor = self.make_window()
        def failed_save(_image, stream, **_kwargs):
            stream.write(b"incomplete encoding")
            raise OSError("disk full")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.png"
            path.write_bytes(b"old capture")
            with mock.patch.object(Image.Image, "save", failed_save):
                with self.assertRaisesRegex(OSError, "disk full"):
                    v.ViewerWindow.export_capture(window, path, {"axons": False}, 1, True, 95)
            self.assertEqual(path.read_bytes(), b"old capture")
            self.assertEqual(list(Path(directory).glob("*.part")), [])
        actor.SetVisibility.assert_called_with(True)
        window.recovery_timer.start.assert_called_once()

    def test_capture_formats_roundtrip_and_leave_no_temporary_files(self):
        for suffix in (".png", ".tiff", ".jpg", ".bmp"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                window, _actor = self.make_window()
                path = Path(directory) / ("capture" + suffix)
                v.ViewerWindow.export_capture(window, path, {"axons": True}, 1, True, 95)
                with Image.open(path) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.size, (16, 12))
                self.assertEqual(list(Path(directory).glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
