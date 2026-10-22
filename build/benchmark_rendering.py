"""Isolated synthetic old/new geometry and actual OpenGL comparison."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time

import numpy as np
import pyvista as pv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fmost_brain_viewer import axon_tube_mesh
from viewer_rendering import AxonActor


def rss():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    counter = Counters()
    counter.cb = ctypes.sizeof(counter)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(counter), counter.cb)
    return counter.WorkingSetSize / 1024**2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['old', 'new'], required=True)
    parser.add_argument('--neurons', type=int, default=20)
    parser.add_argument('--nodes', type=int, default=10000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    p = pv.Plotter(off_screen=True, window_size=(960, 720))
    p.set_background('white')
    data = []
    for i in range(args.neurons):
        t = np.linspace(0, 30, args.nodes, dtype=np.float32)
        points = np.column_stack((t * 30, np.sin(t) * 50 + (i % 10) * 150,
                                  np.cos(t) * 50 + (i // 10) * 150)).astype(np.float32)
        edges = np.column_stack((np.arange(args.nodes - 1), np.arange(1, args.nodes)))
        data.append((points, edges))
    before = rss()
    actors, meshes = [], []
    start = time.perf_counter()
    for i, (points, edges) in enumerate(data):
        if args.mode == 'old':
            mesh = axon_tube_mesh(points, edges, 1)
            meshes.append(mesh)
            actor = p.add_mesh(mesh, color='#cc3344', smooth_shading=True, render=False)
        else:
            actor = AxonActor(points, edges, '#cc3344', 1)
            p.add_actor(actor, render=False)
        actors.append(actor)
    build = time.perf_counter() - start
    geometry = sum(m.GetActualMemorySize()*1024 for m in meshes) if meshes else sum(a.geometry_bytes for a in actors)
    p.view_xy()
    p.reset_camera()
    start = time.perf_counter()
    p.show(auto_close=False, interactive=False)
    first_render = time.perf_counter()-start
    first_rss = rss()
    image = p.screenshot(return_img=True)
    assert np.any(image[:, :, 0] != image[:, :, 1]), 'No colored geometry rendered'
    p.screenshot(str(args.output.with_suffix('.png')))
    times = []
    for width in (2, 3, 1):
        start = time.perf_counter()
        if args.mode == 'old':
            for actor in actors:
                p.remove_actor(actor, render=False)
            actors.clear()
            meshes.clear()
            for points, edges in data:
                mesh = axon_tube_mesh(points, edges, width)
                meshes.append(mesh)
                actors.append(p.add_mesh(mesh, color='#cc3344', smooth_shading=True, render=False))
        else:
            for actor in actors:
                actor.set_width(width)
        p.render()
        times.append(time.perf_counter() - start)
    result = dict(mode=args.mode, neurons=args.neurons, nodes=args.nodes,
                  edges=args.neurons*(args.nodes-1), geometry_MiB=geometry/1024**2,
                  build_seconds=build, first_render_seconds=first_render,
                  width_update_seconds=times, working_set_before_MiB=before,
                  working_set_after_render_MiB=first_rss, working_set_final_MiB=rss(),
                  render_window=p.render_window.GetClassName())
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    p.close()


if __name__ == '__main__':
    main()
