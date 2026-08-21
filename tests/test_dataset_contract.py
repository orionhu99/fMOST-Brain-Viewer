from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")

from fmost_brain_viewer import (  # noqa: E402
    deterministic_color,
    discover_brain_projects,
    infer_neuron_soma_id,
    load_manual_soma_regions,
    project_paths,
    read_swc_with_ids,
)


def _write_swc(path: Path, node_ids: tuple[int, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, node_id in enumerate(node_ids):
        parent = -1 if index == 0 else node_ids[index - 1]
        rows.append(f"{node_id} 1 {index}.0 {index + 1}.0 {index + 2}.0 1.0 {parent}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _make_project(parent: Path, dataset_id: str) -> Path:
    project = parent / f"project-{dataset_id}"
    axon_dir, soma_path, _ = project_paths(dataset_id, project)
    _write_swc(soma_path, (101, 102))
    _write_swc(axon_dir / f"{dataset_id}-101_reg.swc", (1, 2, 3))
    return project


class DatasetDiscoveryTests(unittest.TestCase):
    def test_discovers_arbitrary_dataset_ids_from_parent_and_subfolder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_a = _make_project(root, "sample_A")
            project_b = _make_project(root, "pilot-02")

            from_parent = discover_brain_projects(root)
            self.assertEqual(
                {(dataset_id, project.resolve()) for dataset_id, project in from_parent},
                {("sample_A", project_a.resolve()), ("pilot-02", project_b.resolve())},
            )

            axon_dir, _, _ = project_paths("sample_A", project_a)
            self.assertEqual(
                discover_brain_projects(axon_dir),
                [("sample_A", project_a.resolve())],
            )

    def test_synthetic_swc_is_parsed_without_external_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.swc"
            _write_swc(path, (101, 102, 103))
            node_ids, points, edges = read_swc_with_ids(path)
            self.assertEqual(node_ids.tolist(), [101, 102, 103])
            self.assertEqual(points.shape, (3, 3))
            self.assertEqual(edges.tolist(), [[0, 1], [1, 2]])

    def test_duplicate_and_non_integer_node_ids_are_rejected(self) -> None:
        invalid_rows = {
            "duplicate.swc": (
                "1 1 0 0 0 1 -1\n"
                "1 1 1 1 1 1 1\n"
            ),
            "fractional.swc": "1.5 1 0 0 0 1 -1\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in invalid_rows.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(ValueError):
                    read_swc_with_ids(path)


class StrictNeuronMappingTests(unittest.TestCase):
    def test_accepts_only_one_unique_valid_soma_id(self) -> None:
        valid = {101, 102}
        self.assertEqual(infer_neuron_soma_id("sample_A-101_reg.swc", valid), 101)
        self.assertEqual(
            infer_neuron_soma_id("sample_A-neuron_102_reg.swc", valid), 102
        )
        self.assertEqual(
            infer_neuron_soma_id("sample_A-101-copy-101_reg.swc", valid), 101
        )
        self.assertIsNone(
            infer_neuron_soma_id("sample_A-neuron_unknown_reg.swc", valid)
        )
        self.assertIsNone(
            infer_neuron_soma_id("sample_A-101-copy-102_reg.swc", valid)
        )

    def test_manual_regions_accept_generic_examples_and_side_suffixes(self) -> None:
        ontology = {
            10: {"acronym": "MOp", "name": "Primary motor area"},
            20: {"acronym": "VISp", "name": "Primary visual area"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            corrections = Path(temporary) / "corrections.csv"
            corrections.write_text(
                "soma_id,region\n101,MOp\n102,VISp_r\n999,unknown\n",
                encoding="utf-8",
            )
            overrides, ignored = load_manual_soma_regions(
                corrections, ontology, {101, 102}
            )
        self.assertEqual(overrides, {101: 10, 102: 20})
        self.assertEqual(ignored, 1)


class DeterministicColorTests(unittest.TestCase):
    def test_string_ids_are_stable_and_distinct(self) -> None:
        first = deterministic_color("sample_A")
        self.assertEqual(first, deterministic_color("sample_A"))
        self.assertNotEqual(first, deterministic_color("sample_B"))
        self.assertRegex(first, r"^#[0-9a-f]{6}$")


if __name__ == "__main__":
    unittest.main()
