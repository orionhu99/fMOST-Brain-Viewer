# Changelog

All notable public changes to fMOST Brain Viewer are documented here. The public
history begins with the privacy-reviewed release; internal development history
and project-specific details are intentionally excluded.

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
