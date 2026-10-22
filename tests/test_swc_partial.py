"""Partial rendering keeps only evidenced SWC data and never changes its source."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np

from viewer_data import read_swc_partial, read_swc_with_ids


class PartialSwcParserTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "neuron.swc"

    def write(self, content):
        self.path.write_text(content, encoding="utf-8")

    def test_valid_data_matches_strict_reader_order_and_types(self):
        self.write("# header\n30 2 3 4 5 1 10 extra columns\n10.0 1 1 2 3 1 -1\n90 1 9 8 7 1 -1\n")
        expected = read_swc_with_ids(self.path)
        ids, points, edges, report = read_swc_partial(self.path)
        for actual, strict in zip((ids, points, edges), expected):
            np.testing.assert_array_equal(actual, strict)
            self.assertEqual(actual.dtype, strict.dtype)
        self.assertFalse(report.has_warnings)
        self.assertFalse(report.is_partial)
        self.assertEqual(report.component_count, 2)
        self.assertEqual(report.minimum_node_id, 10)

    def test_bad_rows_are_skipped_and_exact_parent_references_reported(self):
        self.write("truncated row\n"
                   "1 1 0 0 0 1 -1\n"
                   "2 2 nan 0 0 1 1\n"
                   "3 2 1e100 0 0 1 1\n"
                   "4.5 2 0 0 0 1 1\n"
                   "0 2 0 0 0 1 1\n"
                   "7 2 0 0 0 inf 1\n"
                   "8 2 8 0 0 1 2\n"
                   "9 2 9 0 0 1 8\n")
        ids, points, edges, report = read_swc_partial(self.path)
        np.testing.assert_array_equal(ids, [1, 8, 9])
        np.testing.assert_array_equal(points, [[0, 0, 0], [8, 0, 0], [9, 0, 0]])
        np.testing.assert_array_equal(edges, [[1, 2]])
        self.assertEqual(report.invalid_rows, 6)
        self.assertEqual(report.skipped_rows, 6)
        self.assertEqual(report.total_rows, 9)
        self.assertEqual(report.missing_parent_edges, 1)
        missing = [issue for issue in report.issues if issue.kind == "Missing parent"]
        self.assertEqual([(issue.line, issue.node_id, issue.parent_id) for issue in missing], [(8, 8, 2)])
        self.assertTrue(report.has_warnings)
        self.assertTrue(report.is_partial)
        with self.assertRaises(ValueError):
            read_swc_with_ids(self.path)

    def test_all_duplicate_occurrences_and_their_links_are_excluded(self):
        self.write("1 1 0 0 0 1 -1\n"
                   "2 2 2 0 0 1 1\n"
                   "3 2 3 0 0 1 2\n"
                   "2 2 22 0 0 1 1\n"
                   "4 2 4 0 0 1 1\n")
        ids, _, edges, report = read_swc_partial(self.path)
        np.testing.assert_array_equal(ids, [1, 3, 4])
        np.testing.assert_array_equal(edges, [[0, 2]])
        self.assertEqual(report.duplicate_rows, 2)
        self.assertEqual(report.duplicate_ids_count, 1)
        self.assertEqual(report.missing_parent_edges, 1)
        duplicates = [issue for issue in report.issues if issue.kind == "Duplicate ID"]
        self.assertEqual([issue.line for issue in duplicates], [2, 4])
        self.assertIn("Parent ID excluded because it is duplicated", report.detail_text())

    def test_invalid_duplicate_also_makes_the_valid_record_ambiguous(self):
        self.write("1 1 0 0 0 1 -1\n2 2 2 0 0 1 1\n2 broken\n3 2 3 0 0 1 2\n")
        ids, _, edges, report = read_swc_partial(self.path)
        np.testing.assert_array_equal(ids, [1, 3])
        self.assertEqual(edges.shape, (0, 2))
        self.assertEqual(report.duplicate_rows, 1)
        self.assertEqual(report.invalid_rows, 1)
        self.assertEqual(report.skipped_rows, 2)
        self.assertEqual(report.duplicate_ids_count, 1)

    def test_all_cycle_edges_removed_while_branches_and_nodes_survive(self):
        self.write("1 1 0 0 0 1 -1\n30 2 30 0 0 1 10\n10 2 10 0 0 1 20\n"
                   "20 2 20 0 0 1 10\n40 2 40 0 0 1 40\n50 2 50 0 0 1 40\n")
        ids, _, edges, report = read_swc_partial(self.path)
        np.testing.assert_array_equal(ids, [1, 30, 10, 20, 40, 50])
        np.testing.assert_array_equal(edges, [[2, 1], [4, 5]])
        self.assertEqual(report.cycle_edges, 3)
        self.assertEqual(report.component_count, 4)
        self.assertEqual(report.skipped_rows, 0)
        cycle = [issue for issue in report.issues if issue.kind == "Cycle edge omitted"]
        self.assertEqual({(issue.node_id, issue.parent_id) for issue in cycle}, {(10, 20), (20, 10), (40, 40)})

    def test_limits_count_invalid_rows_duplicates_and_nul_only_rows(self):
        for first in ("broken", "1 1 0 0 0 1 -1", "\x00\x00"):
            self.write(f"# comment\n\n{first}\n1 1 0 0 0 1 -1\n")
            with self.subTest(first=first), self.assertRaises(MemoryError):
                read_swc_partial(self.path, max_nodes=1)
        self.write("# comment\n\n1 1 0 0 0 1 -1\n")
        self.assertEqual(read_swc_partial(self.path, max_nodes=1)[3].total_rows, 1)
        with self.assertRaises(MemoryError):
            read_swc_partial(self.path, max_nodes=0)

    def test_all_invalid_or_ambiguous_data_fails_without_empty_geometry(self):
        for content in ("broken\n", "1 1 0 0 0 1 -1\n1 1 1 0 0 1 -1\n", "# empty\n"):
            self.write(content)
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "No usable SWC nodes"):
                read_swc_partial(self.path)

    def test_nul_cleanup_reports_counts_and_does_not_change_hash_or_mtime(self):
        self.write("# header\x00\n1 1 0 0 0 1 -1\n2 2 2 0 0 1 1" + "\x00" * 281 + "\n")
        before_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        before_mtime = self.path.stat().st_mtime_ns
        ids, _, edges, report = read_swc_partial(self.path)
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual(report.nul_bytes, 282)
        self.assertEqual(report.nul_lines, 2)
        self.assertEqual([issue.line for issue in report.issues], [1, 3])
        self.assertTrue(report.has_warnings)
        self.assertFalse(report.is_partial)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(self.path.stat().st_mtime_ns, before_mtime)

    def test_diagnostic_preview_is_bounded_but_counts_are_exact(self):
        self.write("broken\n" * 1000 + "1 1 0 0 0 1 -1\n")
        report = read_swc_partial(self.path)[3]
        self.assertEqual(report.invalid_rows, 1000)
        self.assertEqual(report.issue_count, 1000)
        self.assertEqual(len(report.issues), 100)
        self.assertEqual(report.issues_truncated, 900)
        self.assertIn("Preview truncated: 900", report.detail_text())
        self.assertIn("Gaps in node numbering do not prove missing nodes", report.detail_text())

    def test_deep_forward_chain_uses_iterative_cycle_checks(self):
        count = 10000
        self.write("\n".join(f"{node} 2 {node} 0 0 1 {node - 1 if node > 1 else -1}"
                            for node in range(count, 0, -1)))
        ids, _, edges, report = read_swc_partial(self.path)
        self.assertEqual(len(ids), count)
        self.assertEqual(len(edges), count - 1)
        self.assertEqual(report.component_count, 1)
        self.assertFalse(report.has_warnings)


if __name__ == "__main__":
    unittest.main()
