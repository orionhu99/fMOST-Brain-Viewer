"""Compact axon geometry: GPU-instanced segments, never expanded tube meshes."""
from __future__ import annotations

import numpy as np
import pyvista as pv
# Register the concrete actor/mapper implementations before the first actor is
# constructed, including when a caller has not created a Plotter yet.
from vtkmodules import vtkRenderingOpenGL2  # noqa: F401
from vtkmodules.vtkRenderingCore import vtkActor, vtkGlyph3DMapper, vtkPolyDataMapper


class AxonActor(vtkActor):
    """One actor per neuron; line and cylinder mappers share the original topology.

    Cylinders have world-space radius (4 micrometres per thickness unit).
    No node decimation or connectivity approximation is performed. Cylinder joins
    differ from the interpolated joints of a CPU tube filter.
    """

    def __init__(self, points, edges, color, width=0.0):
        super().__init__()
        points = np.ascontiguousarray(points, dtype=np.float32)
        edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
        self.geometry_estimate = self.estimate_bytes(len(points), len(edges))
        self.centerlines = pv.PolyData()
        self.centerlines.points = points
        cells = np.empty((len(edges), 3), dtype=np.int64)
        cells[:, 0] = 2
        cells[:, 1:] = edges
        self.centerlines.lines = cells.ravel()
        self.line_mapper = vtkPolyDataMapper()
        self.line_mapper.SetInputData(self.centerlines)
        self.line_mapper.ScalarVisibilityOff()
        self.glyph_mapper = None
        self._glyphs_empty = False
        self.segments = None
        self.cylinder = None
        self.width = -1.0
        self.GetProperty().SetColor(pv.Color(color).float_rgb)
        self.GetProperty().SetLineWidth(1.0)
        self.set_width(width)

    @staticmethod
    def estimate_bytes(point_count, edge_count):
        # Conservative planning allowance for inputs, instance matrices and GPU
        # copies; not a measurement of total process RAM or driver VRAM.
        return int(point_count * 24 + edge_count * 256)

    @property
    def geometry_bytes(self):
        meshes = (self.centerlines, self.segments, self.cylinder)
        return sum(m.GetActualMemorySize() * 1024 for m in meshes if m is not None)

    def _prepare_glyphs(self):
        edges = self.centerlines.lines.reshape(-1, 3)[:, 1:]
        points = self.centerlines.points
        a, b = points[edges[:, 0]], points[edges[:, 1]]
        direction = b.astype(np.float64) - a
        lengths = np.linalg.norm(direction, axis=1)
        valid = lengths > 0
        if not np.any(valid):
            self._glyphs_empty = True
            return
        # Zero-length edges cannot have a cylinder orientation. They remain in
        # centerlines; do not invent a direction or change the original nodes.
        direction = direction[valid]
        lengths = lengths[valid]
        if not np.isfinite(lengths).all() or np.any(lengths > np.finfo(np.float32).max):
            raise ValueError("Axon segment length exceeds the rendering range")
        self.segments = pv.PolyData()
        self.segments.points = ((a[valid].astype(np.float64) + b[valid]) * 0.5).astype(np.float32)
        self.segments.point_data['direction'] = (direction / lengths[:, None]).astype(np.float32)
        scales = np.ones((len(lengths), 3), dtype=np.float32)
        scales[:, 0] = lengths
        self.segments.point_data['scale'] = scales
        self.cylinder = pv.Cylinder(direction=(1, 0, 0), radius=1, height=1,
                                    resolution=8, capping=True)
        self._unit_points = self.cylinder.points.copy()
        mapper = vtkGlyph3DMapper()
        mapper.SetInputData(self.segments)
        mapper.SetSourceData(self.cylinder)
        mapper.SetOrientationArray('direction')
        mapper.SetOrientationModeToDirection()
        mapper.OrientOn()
        mapper.SetScaleArray('scale')
        mapper.SetScaleModeToScaleByVectorComponents()
        mapper.ScalingOn()
        mapper.ScalarVisibilityOff()
        self.glyph_mapper = mapper

    def set_width(self, width):
        width = float(width)
        if not np.isfinite(width) or not 0 <= width <= 10:
            raise ValueError('Axon thickness must be between 0 and 10')
        if width == self.width:
            return
        if width <= 0 or not self.centerlines.n_lines or self._glyphs_empty:
            self.SetMapper(self.line_mapper)
        else:
            if self.glyph_mapper is None:
                self._prepare_glyphs()
            if self.glyph_mapper is None:
                self.SetMapper(self.line_mapper)
                self.width = width
                return
            updated = self._unit_points.copy()
            updated[:, 1:] *= width * 4.0
            self.cylinder.points = updated
            self.SetMapper(self.glyph_mapper)
        self.width = width
