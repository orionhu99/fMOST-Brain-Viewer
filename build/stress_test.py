"""Synthetic SWC geometry stress test; never reads experimental datasets."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from viewer_data import read_swc_with_ids
from fmost_brain_viewer import axon_tube_mesh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=100000)
    args = parser.parse_args()
    if not 2 <= args.nodes <= 1000000:
        parser.error("nodes must be between 2 and 1000000")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic.swc"
        with path.open("w") as stream:
            for index in range(args.nodes):
                parent = -1 if index == 0 else index
                stream.write(f"{index + 1} 2 {index * .1:.1f} {np.sin(index * .001):.6f} 0 1 {parent}\n")
        start = time.perf_counter()
        ids, points, edges = read_swc_with_ids(path)
        parse_time = time.perf_counter() - start
        start = time.perf_counter()
        mesh = axon_tube_mesh(points, edges, 1)
        geometry_time = time.perf_counter() - start
        assert len(ids) == args.nodes and len(edges) == args.nodes - 1
        assert mesh.n_points > args.nodes and mesh.n_verts == 0
        print(json.dumps({"nodes": len(ids), "edges": len(edges), "tube_points": mesh.n_points,
            "parse_seconds": round(parse_time, 3), "geometry_seconds": round(geometry_time, 3)}))


if __name__ == "__main__":
    main()
