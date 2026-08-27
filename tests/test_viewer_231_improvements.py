from __future__ import annotations

import inspect
import tempfile
import types
import unittest
import os
from pathlib import Path
from unittest import mock

import numpy as np

import fmost_brain_viewer as viewer


class Viewer231ImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = viewer.QtWidgets.QApplication.instance() or viewer.QtWidgets.QApplication([])

    def test_line_mesh_contains_lines_without_vertex_markers(self) -> None:
        points = np.zeros((3, 3), dtype=float)
        edges = np.array([[0, 1], [1, 2]], dtype=np.int64)
        mesh = viewer.line_mesh(points, edges)
        self.assertEqual(mesh.n_lines, 1)
        self.assertEqual(mesh.n_verts, 0)
        self.assertEqual(mesh.n_cells, 1)

    def test_axon_tube_is_continuous_geometry(self) -> None:
        points = np.array([[0, 0, 0], [10, 0, 0], [20, 5, 0]], dtype=float)
        edges = np.array([[0, 1], [1, 2]], dtype=np.int64)
        tube = viewer.axon_tube_mesh(points, edges, 1.0)
        self.assertGreater(tube.n_strips, 0)
        self.assertEqual(tube.n_lines, 0)
        self.assertGreater(tube.n_points, len(points))

    def test_zero_axon_size_preserves_thin_centerline(self) -> None:
        points = np.array([[0, 0, 0], [10, 0, 0], [20, 5, 0]], dtype=float)
        edges = np.array([[0, 1], [1, 2]], dtype=np.int64)
        centerline = viewer.axon_tube_mesh(points, edges, 0.0)
        self.assertEqual(centerline.n_lines, 1)
        self.assertEqual(centerline.n_strips, 0)
        self.assertEqual(centerline.n_points, len(points))

    def test_successful_region_search_can_be_cleared_and_hidden(self) -> None:
        search = viewer.QtWidgets.QLineEdit("TEST")
        results = viewer.QtWidgets.QListWidget()
        results.addItem("TEST — Synthetic structure [ID 42]")
        results.show()
        fake = types.SimpleNamespace(
            region_search=search, region_search_results=results
        )
        viewer.ViewerWindow._clear_region_search(fake)
        self.assertEqual(search.text(), "")
        self.assertEqual(results.count(), 0)
        self.assertTrue(results.isHidden())

    def test_region_search_result_uses_single_click_selection(self) -> None:
        source = inspect.getsource(viewer.ViewerWindow._build_ui)
        self.assertIn("region_search_results.itemClicked.connect", source)
        self.assertNotIn("region_search_results.itemDoubleClicked.connect", source)

    def test_region_search_inside_detection_includes_widget_children(self) -> None:
        search = viewer.QtWidgets.QLineEdit()
        results = viewer.QtWidgets.QListWidget()
        outside = viewer.QtWidgets.QPushButton()
        fake = types.SimpleNamespace(
            region_search=search, region_search_results=results
        )
        self.assertTrue(viewer.ViewerWindow._inside_region_search(fake, search))
        self.assertTrue(
            viewer.ViewerWindow._inside_region_search(fake, results.viewport())
        )
        self.assertFalse(viewer.ViewerWindow._inside_region_search(fake, outside))

    def test_cache_namespace_is_per_user_and_signature_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "cache" / "atlas"
            with mock.patch.object(viewer, "CACHE_BASE", base):
                root = viewer.derived_cache_root("abcdef0123456789abcdef")
        self.assertEqual(root, base / "derived_abcdef0123456789abcd")

    def test_sparse_bounds_support_large_ids_and_merge_chunks(self) -> None:
        labels = np.zeros((4, 3, 5), dtype=np.uint32)
        labels[1, 1, 0] = 576073704
        labels[2:4, 0:2, 4] = 576073704
        labels[0, 2, 2] = 207
        bounds = viewer.sparse_label_bounds(labels, lambda value: value == 0, 2)
        self.assertEqual(bounds["576073704"], [1, 0, 0, 4, 2, 5])
        self.assertEqual(bounds["207"], [0, 2, 2, 1, 3, 3])
        self.assertNotIn("0", bounds)

    def test_bounds_cache_hits_and_invalidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annotation_path = root / "annotation.raw"
            annotation_path.write_bytes(b"atlas")
            annotation = types.SimpleNamespace(
                path=annotation_path,
                world_labels=mock.Mock(
                    return_value=np.array([[[0, 207]]], dtype=np.uint32)
                ),
                is_background=lambda value: value == 0,
            )
            with mock.patch.object(viewer, "CACHE_ROOT", root / "cache"):
                first = viewer.load_or_create_region_bounds(annotation)
                second = viewer.load_or_create_region_bounds(annotation)
                self.assertEqual(first, second)
                self.assertEqual(annotation.world_labels.call_count, 1)
                cache = next((root / "cache").glob("region_bounds*.json"))
                newer = cache.stat().st_mtime_ns + 1_000_000_000
                os.utime(annotation_path, ns=(newer, newer))
                viewer.load_or_create_region_bounds(annotation)
                self.assertEqual(annotation.world_labels.call_count, 2)

    def test_region_search_ranking(self) -> None:
        ontology = {
            10: {"acronym": "MOp-a", "name": "Motor primary alpha"},
            207: {"acronym": "MOp", "name": "Motor primary"},
            30: {"acronym": "XMOp", "name": "Example area"},
            40: {"acronym": "ZZ", "name": "Primary adjacent"},
        }
        self.assertEqual(viewer.region_search_matches(ontology, "207")[0], 207)
        self.assertEqual(viewer.region_search_matches(ontology, "MOp")[:2], [207, 10])
        self.assertEqual(viewer.region_search_matches(ontology, "prim"), [207, 10, 40])

    def test_cache_namespace_is_writable_when_atlas_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_cache = Path(directory) / "user-cache"
            with mock.patch.object(viewer, "CACHE_BASE", user_cache):
                cache = viewer.derived_cache_root("atlas-signature")
                cache.mkdir(parents=True)
                marker = cache / "probe.txt"
                marker.write_text("ok", encoding="utf-8")
                self.assertEqual(marker.read_text(encoding="utf-8"), "ok")

    def test_worker_preserves_exception_category(self) -> None:
        for error in (
            PermissionError("denied"), MemoryError("too large"), OSError("VTK save failed")
        ):
            with self.subTest(error=type(error).__name__):
                worker = viewer.RegionCacheWorker(object(), {}, region_id=42)
                with mock.patch.object(
                    viewer, "load_or_create_structure_surface", side_effect=error
                ):
                    worker.run()
                self.assertIsInstance(worker.error, type(error))

    def test_custom_region_distinguishes_failure_from_no_voxels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            annotation_path = Path(directory) / "annotation.raw"
            annotation_path.write_bytes(b"atlas")
            fake = types.SimpleNamespace(
                region_items={},
                ontology={42: {"acronym": "TEST", "name": "Test structure"}},
                status=mock.Mock(),
                annotation=types.SimpleNamespace(path=annotation_path),
            )
            with (
                mock.patch.object(viewer, "CACHE_ROOT", Path(directory) / "cache"),
                mock.patch.object(viewer.QtWidgets.QApplication, "processEvents"),
                mock.patch.object(viewer, "run_region_cache_job", side_effect=MemoryError("large")),
                mock.patch.object(viewer.QtWidgets.QMessageBox, "critical") as critical,
            ):
                result = viewer.ViewerWindow._add_custom_region(fake, 42, True, True)
            self.assertFalse(result)
            self.assertIn("MemoryError", critical.call_args.args[2])

            with (
                mock.patch.object(viewer, "CACHE_ROOT", Path(directory) / "cache"),
                mock.patch.object(viewer.QtWidgets.QApplication, "processEvents"),
                mock.patch.object(viewer, "run_region_cache_job", return_value=None),
                mock.patch.object(viewer.QtWidgets.QMessageBox, "warning") as warning,
            ):
                result = viewer.ViewerWindow._add_custom_region(fake, 42, True, True)
            self.assertFalse(result)
            self.assertEqual(warning.call_args.args[1], "Region absent from volume")

    def test_camera_validation_rejects_invalid_saved_views(self) -> None:
        self.assertTrue(
            viewer.valid_camera_position([[10, 0, 0], [0, 0, 0], [0, -1, 0]])
        )
        self.assertFalse(viewer.valid_camera_position([[0, 0, 0]] * 3))
        self.assertFalse(
            viewer.valid_camera_position([[float("nan"), 0, 0], [0, 0, 0], [0, 1, 0]])
        )

    def test_default_opacity_contract(self) -> None:
        self.assertEqual(viewer.DEFAULT_BRAIN_OPACITY, 0.20)
        self.assertEqual(viewer.AXON_TUBE_RADIUS_PER_SIZE_UM, 4.0)
        self.assertEqual(viewer.AXON_TUBE_SIDES, 8)


if __name__ == "__main__":
    unittest.main()
