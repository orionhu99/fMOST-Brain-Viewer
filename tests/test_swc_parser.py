"""Regression coverage for compact parsing without weakening SWC validation."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from viewer_data import read_swc, read_swc_with_ids


class CompactSwcParserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "neuron.swc"

    def write(self, content):
        self.path.write_text(content, encoding="utf-8")

    def test_preserves_order_forward_parents_roots_and_extra_columns(self):
        content = (
            "# an SWC comment\n\n"
            "30 2 3 4 5 1 10 extra columns are permitted\n"
            "10.0 1 1 2 3 1 -1 # root\n"
            "20 2 2 3 4 1 1e1\x00\n"
            "90 1 9 8 7 1 -1\n"
        )
        self.write(content)
        original = self.path.read_bytes()
        ids, points, edges = read_swc_with_ids(self.path)
        np.testing.assert_array_equal(ids, [30, 10, 20, 90])
        np.testing.assert_array_equal(points, [[3, 4, 5], [1, 2, 3], [2, 3, 4], [9, 8, 7]])
        np.testing.assert_array_equal(edges, [[1, 0], [1, 2]])
        self.assertEqual(ids.dtype, np.int64)
        self.assertEqual(points.dtype, np.float32)
        self.assertEqual(edges.dtype, np.int64)
        self.assertEqual(self.path.read_bytes(), original)

    def test_node_limit_counts_only_data_rows(self):
        self.write("# comment\n\n1 1 0 0 0 1 -1\n# comment\n2 2 1 0 0 1 1\n")
        with self.assertRaises(MemoryError):
            read_swc(self.path, max_nodes=1)
        points, edges = read_swc(self.path, max_nodes=2)
        self.assertEqual(points.shape, (2, 3))
        self.assertEqual(edges.shape, (1, 2))
        with self.assertRaises(MemoryError):
            read_swc(self.path, max_nodes=0)

    def test_malformed_first_row_is_not_silently_skipped(self):
        self.write("3.2 4.5 -1\n2 2 1 0 0 1 -1\n")
        with self.assertRaisesRegex(ValueError, "seven columns.*line 1"):
            read_swc(self.path)

    def test_rejects_missing_parent_on_either_side_of_sorted_ids(self):
        for parent in (0, -2, 1, 999):
            self.write(f"10 1 0 0 0 1 -1\n20 2 1 0 0 1 {parent}\n")
            with self.subTest(parent=parent), self.assertRaisesRegex(ValueError, "missing parent"):
                read_swc(self.path)

    def test_rejects_duplicate_ids_and_disconnected_cycles(self):
        for content, message in (
            ("3 1 0 0 0 1 -1\n1 1 0 0 0 1 -1\n3 2 1 0 0 1 1\n", "duplicate"),
            ("1 1 0 0 0 1 -1\n30 2 1 0 0 1 20\n20 2 1 0 0 1 30\n", "cycle"),
            ("1 1 0 0 0 1 1\n", "cycle"),
        ):
            self.write(content)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                read_swc(self.path)

    def test_rejects_invalid_numbers_in_every_column(self):
        for column in range(7):
            for invalid in ("nan", "inf", "-inf", "broken"):
                row = ["1", "1", "0", "0", "0", "1", "-1"]
                row[column] = invalid
                self.write(" ".join(row) + "\n")
                with self.subTest(column=column, invalid=invalid), self.assertRaises(ValueError):
                    read_swc(self.path)

    def test_ids_are_integral_and_exact_even_near_float_precision_limit(self):
        for invalid in ("1.5", "9007199254740990.1", "9007199254740992", "1e100", "1e999999999"):
            for column in (0, 6):
                row = ["1", "1", "0", "0", "0", "1", "-1"]
                row[column] = invalid
                self.write(" ".join(row) + "\n")
                with self.subTest(column=column, invalid=invalid), self.assertRaises(ValueError):
                    read_swc(self.path)
        self.write("9007199254740991.0 1 0 0 0 1 -1\n")
        ids, _, edges = read_swc_with_ids(self.path)
        self.assertEqual(ids.tolist(), [9007199254740991])
        self.assertEqual(edges.shape, (0, 2))

    def test_nonpositive_node_ids_and_unrepresentable_coordinates_are_rejected(self):
        for content in ("0 1 0 0 0 1 -1\n", "-1 1 0 0 0 1 -1\n", "1 1 1e100 0 0 1 -1\n"):
            self.write(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_swc(self.path)

    def test_deep_forward_chain_does_not_need_recursion(self):
        count = 10000
        self.write("\n".join(f"{node} 2 {node} 0 0 1 {node - 1 if node > 1 else -1}" for node in range(count, 0, -1)))
        ids, points, edges = read_swc_with_ids(self.path)
        self.assertEqual(len(ids), count)
        self.assertEqual(len(points), count)
        self.assertEqual(edges.shape, (count - 1, 2))
        np.testing.assert_array_equal(edges[0], [1, 0])
        np.testing.assert_array_equal(edges[-1], [count - 1, count - 2])


if __name__ == "__main__":
    unittest.main()
