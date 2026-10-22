# Changelog

## 3.1.3 — 2026-10-23 (local update)

- Load surviving valid axon nodes and connections from damaged SWCs without writing to source files or inventing missing geometry. Keep strict parsing for soma data and analysis.
- Mark partial neurons in the list, combine load warnings per selection, and expose affected lines/node IDs through File > SWC load diagnostics.
- Exclude ambiguous duplicate IDs and cycle edges; report skipped records, missing parent connections, and in-memory NUL cleanup. Diagnostic previews are bounded with exact overall counts.

## 3.1.2 — 2026-10-23 (local update)

- Make the fixed geometry budget optional and disabled by default; remember an explicitly selected policy and limit in Settings.
- Separate temporary SWC parsing memory from the remaining axon geometry budget, so neurons that fit are no longer rejected by a 1 KiB-per-node parsing reservation.
- Reduce SWC parsing memory with compact numeric buffers and array-based topology validation while retaining strict corruption checks. Input SWC files remain read-only.
- Report required and remaining geometry budgets when a neuron genuinely exceeds the limit. Bound temporary parsing separately and adapt its allowance to available Windows physical memory.

## 3.1.1 — 2026-10-23 (local update)

- Move the axon memory budget and resource estimate from Appearance into Settings > Axon memory settings, preserving their existing behavior.

## 3.1.0 — 2026-10-23 (local validation build)

- Render axons with GPU-instanced segments instead of expanded tube meshes. Preserve all original nodes and edges; retain world-space thickness. Segment joins may look different from the previous tube interpolation.
- Coalesce thickness edits, reuse actor/instance data, and stop idle periodic renders.
- Bound interactive SWC parsing and geometry allocations, show a configurable axon memory planning budget, and promptly evict hidden geometry.
- Fix the session-open NameError, closed-window geometry retention, soma vertex indexing, and hidden-brain checkbox restoration.
- Keep the interface responsive while cancelling a region-cache task; the current VTK filter still needs to finish.
- Stream GIF frames and atomically replace screenshot/GIF outputs. Protect recovery snapshots from temporary export visibility.
- Decode legacy uint16 atlas labels/background correctly and invalidate the old whole-brain surface cache.
- Enforce the session read/write size limit consistently before overwriting a saved session.
- Refresh vulnerable locked dependencies and add actual GPU, memory, cancellation and export regression tests.

## 3.0.0 — 2026-09-07

- Preserve non-application files during installation and upgrades; stop forcing applications closed.
- Prevent selected axons disappearing after actor-cache eviction or dataset removal.
- Save before switching atlas sessions or starting an update, and keep each viewer bound to its atlas.
- Protect new unsaved sessions, write recovery copies every 30 seconds, and restore slice and volume settings.
- Read version 1 sessions and write version 2 sessions with validated display settings.
- Reject truncated SWC rows, missing parents, cycles, and unsafe numeric values; recover from per-file import errors.
- Bound gzip decompression and store all generated atlas caches outside source folders.
- Add background cancellable installer downloads and normal-user installer launch with explicit elevation fallback.
- Add strict soma classification, provenance CSV export, cache management, and corrupt-cache rebuilding.
- Separate data, session, cache and update helpers; expand renderer, session, input and installer regression coverage.

## 2.3.8 — 2026-09-06

- Keep rapid clicks and double-clicks on neuron checkboxes limited to visibility changes.
- Open the independent neuron color picker only when its color swatch is double-clicked.
- Clarify the neuron-list tooltip for visibility and color editing.

## 2.3.7 — 2026-08-30

- Refuse to run downloaded update installers when GitHub SHA-256 metadata is missing or invalid.
- Keep the viewer open when automatic session saving fails, with Retry, Discard, and Cancel choices.
- Remove incomplete `.part` session files after failed saves.
- Require release tags to be contained in `main` before and after GitHub publication.

## 2.3.6 — 2026-08-28

- Give the application, executable, and Windows icon a true transparent background.
- Request elevation for in-app updates so protected legacy installation directories can be replaced.
- Ask the installer to close applications and DLLs that still hold destination files open.

## 2.3.5 — 2026-08-28

- Select and add an atlas search result with one left click instead of a double-click.
- Adopt the pearl-white anatomical brain artwork as the primary application and Windows icon.
- Preserve the previous line-art identity under `assets/lite` for a possible lightweight edition.
- Keep the Windows icon on its approved black field for reliable small-size rendering.
- Remove the startup reveal and fade transitions in favor of a fast, static startup screen.

## 2.3.4 — 2026-08-27

- Render enlarged axons as continuous smooth tubes instead of segmented blocks.
- Add a thin 0x centerline mode while keeping 1x as the minimum tube width.
- Introduce the finalized fluorescent-neuron logo across application and Windows icon assets.
- Add a startup animation that reveals the exact final logo outward from its central soma.
- Smoothly cross-fade the startup screen into the main viewer window.

## 2.3.3 — 2026-08-26

- Remove unintended SWC vertex cells so axons render without fixed-size square nodes.
- Clear and dismiss live atlas search results after a successful region selection.
- Dismiss atlas search results when clicking elsewhere in the viewer.
- Run GitHub update checks on a background thread so slow networks do not freeze the UI.

## 2.3.2 — 2026-08-26

- Render minimum-width axons as thin screen-space lines instead of rounded tubes.
- Use a 20% default brain opacity and an intuitive +X default camera orientation.
- Store atlas-derived caches under the per-user local application data folder.
- Build sparse uint32 atlas region bounds without allocations tied to maximum label ID.
- Preserve background cache exceptions and distinguish them from genuinely absent regions.
- Show ranked live atlas search results below the brain-region search field.
- Add Help-menu update checking, release selection, verified installer download, and launch.

All notable public changes to fMOST Brain Viewer are documented here. The public
history begins with the privacy-reviewed release; internal development history
and project-specific details are intentionally excluded.

## 2.3.1 — 2026-08-26

### Fixed

- Rendered coronal annotation slices with discrete Allen ontology colors instead
  of a continuous scalar colormap.
- Made background label 0 fully transparent and removed interpolated colors at
  atlas-region boundaries.
- Added stable fallback colors for unknown region IDs while preserving slice
  geometry and world coordinates.

## 2.3.0 — 2026-08-22

### Initial public release

- Added a self-contained Windows x64 installer and portable ZIP; Python and Git
  are not required for end users.
- Added a first-run Allen CCF assistant with verified download/resume and an
  existing-atlas option. Atlas volumes remain separate from the application.
- Added multi-dataset viewing, strict axon–soma ID matching, import diagnostics,
  soma-region grouping, and deterministic colors for arbitrary dataset IDs.
- Added Surface-first startup with on-demand Volume loading, bounded hidden axon
  actor caching, immediate neuron-list mode switching, and consolidated renders.
- Added coronal navigation, atlas-region highlighting, capture export, rotating
  GIF recording, and reusable session files with atlas identity checks.
- Added local rotating logs, an offline packaged self-test, and clear access to
  the log folder. The application contains no telemetry and does not upload user
  datasets.
- Added English and Chinese user documentation, third-party notices, citation
  metadata, synthetic tests, release privacy scanning, and reproducible locked
  dependencies.
