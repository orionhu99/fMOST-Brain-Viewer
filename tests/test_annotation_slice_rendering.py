from __future__ import annotations

import unittest

import numpy as np

import fmost_brain_viewer as viewer


class AnnotationSliceRenderingTests(unittest.TestCase):
    def test_flatten_ontology_preserves_normalized_allen_color(self) -> None:
        output: dict[int, dict] = {}
        viewer._flatten_ontology(
            [{
                "id": 42,
                "acronym": "TEST",
                "name": "Test region",
                "color_hex_triplet": "#a1b2c3",
                "children": [],
            }],
            output,
        )
        self.assertEqual(output[42]["color_hex_triplet"], "A1B2C3")

    def test_background_is_fully_transparent(self) -> None:
        self.assertEqual(viewer.region_rgba(0, {}), (0, 0, 0, 0))

    def test_known_and_unknown_colors_are_discrete_and_stable(self) -> None:
        ontology = {1: {"color_hex_triplet": "123456"}}
        self.assertEqual(viewer.region_rgba(1, ontology), (0x12, 0x34, 0x56, 255))
        self.assertEqual(viewer.region_rgba(99, ontology), viewer.region_rgba(99, ontology))
        self.assertNotEqual(viewer.region_rgba(99, ontology), viewer.region_rgba(100, ontology))

    def test_large_ids_do_not_change_other_region_colors(self) -> None:
        ontology = {7: {"color_hex_triplet": "ABCDEF"}}
        small = viewer.annotation_slice_rgba(np.array([[0, 7]], dtype=np.uint32), ontology)
        mixed = viewer.annotation_slice_rgba(
            np.array([[0, 7, 576073704]], dtype=np.uint32), ontology
        )
        np.testing.assert_array_equal(small[0, :2], mixed[0, :2])

    def test_rgba_shape_and_cell_order_match_coronal_slice(self) -> None:
        labels = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint32)
        rgba = viewer.annotation_slice_rgba(labels, {})
        self.assertEqual(rgba.shape, labels.shape + (4,))

        grid = viewer.annotation_slice_grid(labels, (10.0, 20.0), 6600.0, {})
        self.assertNotIn("region_rgba", grid.point_data)
        expected = np.array(
            [viewer.region_rgba(int(value), {}) for value in labels.ravel(order="F")],
            dtype=np.uint8,
        )
        np.testing.assert_array_equal(grid.cell_data["region_rgba"], expected)

    def test_cell_grid_geometry_preserves_pixel_centers(self) -> None:
        labels = np.zeros((4, 3), dtype=np.uint32)
        grid = viewer.annotation_slice_grid(labels, (10.0, 20.0), 6600.0, {})
        self.assertEqual(grid.dimensions, (5, 4, 1))
        self.assertEqual(grid.spacing, (10.0, 20.0, 1.0))
        self.assertEqual(grid.origin, (-5.0, -10.0, 6600.0))
        self.assertEqual(grid.n_cells, labels.size)
        self.assertEqual(len(grid.cell_data["region_rgba"]), labels.size)
        np.testing.assert_allclose(grid.cell_centers().points[0], (0.0, 0.0, 6600.0))
        np.testing.assert_allclose(grid.cell_centers().points[-1], (30.0, 40.0, 6600.0))


if __name__ == "__main__":
    unittest.main()
