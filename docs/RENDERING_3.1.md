# 3.1 rendering validation / 渲染优化说明

Rendering changes developed in 3.1.x and included in the 3.2.0 release.
The benchmark below records the 2026-10-23 validation run.

## Rendering changes

The previous application deleted every axon actor, read SWCs again and expanded
new eight-sided tube meshes whenever thickness changed. The new renderer uses
VTK GPU glyph instancing with one small cylinder source per neuron and one
instance per nonzero-length original edge. It retains every node and edge in the
centerline representation, without simplifying coordinates or topology. Radius
is still 4 micrometres per thickness unit. Width 0 uses thin centerlines.

Positive width changes update only the small source geometry. Changes are
coalesced over 200 ms; exported images flush pending changes first. Hidden
instances update when shown. Idle 5 Hz redraw is disabled; interactions and
explicit parameter changes still render. Segment joins differ from the previous
continuous tube interpolation; this display is not a biological diameter estimate.

Since 3.1.2, the **Axon memory budget** in **Settings > Axon memory settings** is
optional and **disabled by default**. Its initial value is 1024 MiB; the explicitly
chosen checkbox and value are remembered on this machine. It is a conservative
planning allowance for geometry, instance matrices and copies, not a measurement
of process RAM or GPU VRAM and not a hard cap on driver memory. If enabled, loading
stops with an explicit message when the budget is reached. Hidden objects are evicted to
make room; existing visible neurons are retained. Since 3.1.2, temporary parsing
is separate from this geometry budget. Compact numeric parsing has a conservative
256-byte-per-node allowance, capped at 512 MiB and, on Windows, one quarter of
currently available physical memory. A 64 MiB per-file input limit also applies.
When the optional budget is enabled, the exact geometry plan is checked before actor creation; errors report the
neuron's requirement and the remaining budget. The budget is machine-specific and
is not written to sessions. Reopening a very large session may load fewer neurons
if the budget is exceeded; unloaded entries are unchecked with an error message.

## Reproducible benchmark

`build/benchmark_rendering.py` runs old tube construction or new GPU instances in
separate processes. It includes actual OpenGL render completion, uses identical
synthetic coordinates and cameras, and does not include SWC disk-reading time.
The old comparator also omits the old application's SWC reread overhead.

100 synthetic neurons × 10,000 nodes, 999,900 edges, 960 × 720 output:

| Metric | Previous tube path | GPU instances |
| --- | ---: | ---: |
| VTK geometry data | 305.47 MiB | 84.08 MiB |
| Geometry construction | 4.08 s | 0.81 s |
| First actual render | 0.55 s | 0.40 s |
| Three thickness changes, each including render | 4.68 / 5.16 / 5.64 s | 0.119 / 0.132 / 0.109 s |
| Process working set after first render | 1166.20 MiB | 630.02 MiB |

These are single-machine measurements, not statistical performance guarantees.
Geometry data and Windows process working set are distinct metrics. GPU VRAM was
not measured separately. The benchmark's many short segments may differ from
real branch distributions. Rendering tests also compare axis-aligned and oblique
GPU segments with independent explicit-cylinder reference images.

## Other fixes

- Since 3.1.3, axon display can load surviving valid data from damaged SWCs in memory. The source file is never rewritten. Invalid rows and every occurrence of ambiguous duplicate IDs are excluded; connections to absent parents and every edge inside a parent cycle are omitted. Remaining coordinates and valid connections are preserved.
- The neuron list marks `[Partial]` geometry or `[Read warning]` for NUL cleanup alone. One warning summarizes each loading action; File > SWC load diagnostics lists skipped lines and the exact child-to-parent references that cannot be drawn. Missing coordinates cannot be reconstructed, and gaps in ID numbering are not proof of missing nodes. Each report previews up to 100 issues, with complete counts and an explicit truncation notice. Strict parsing remains in use for soma/analysis data.
- Session loading now reads the validated dataset list rather than an undefined variable.
- Closed windows release large VTK geometry references and stop timers.
- Soma selection rebuilds vertex cells together with filtered coordinates.
- Region-cache cancellation waits responsively; the active VTK filter must finish.
- GIF export encodes one frame at a time instead of retaining all frames.
- Screenshot/GIF saves use atomic replacement; failed/cancelled writes preserve previous files.
- Temporary export visibility does not leak into autosave recovery snapshots.
- Hidden-brain session restoration updates its checkbox.
- Legacy uint16 atlas encoding/background is decoded for slices and the whole-brain mask.
- Session writes enforce the same 16 MiB UTF-8 size limit as reads, before touching the old file.
- Pillow, certifi and urllib3 are updated to audited fixed versions in the hash lock.

## Remaining limits / 后续建议

Large selections are still proportional to their total original segments. First
loading/parsing and the first switch from lines to cylinders still require work;
GPU instancing cannot guarantee responsiveness for arbitrary datasets or drivers.
Atlas surface/volume creation and dense slice rebuilds remain separate potential
bottlenecks. A future texture-plane slice renderer needs coordinate/color regression
tests before replacing the existing grid. True whole-brain data and long interactive
sessions on the user's GPU remain necessary acceptance steps. No experimental
files were used or modified for these tests.

VTK reference: https://vtk.org/doc/nightly/html/classvtkGlyph3DMapper.html
