# fMOST Brain Viewer 3.2

[中文](RELEASE_3.2_zh-CN.md) · [User guide](USER_GUIDE.md)

Version 3.2 adds automatic update notifications and a download-and-restart update
flow. It also brings the rendering and read-only SWC recovery improvements from
3.1.x to users upgrading from 3.0.

## Updating

- While the viewer is running, it checks stable GitHub Releases at most once a
  day. Notifications do not interrupt the current scene; choose Update now,
  Later, or Skip this version. Disable automatic checks in
  **Settings > Software updates...** if preferred. Manual checks stay available.
- A requested installer download runs in the background, supports retry/resume,
  and is checked against the release SHA-256 digest. A cached complete installer
  must pass verification before reuse.
- The packaged Windows application saves an update recovery snapshot before
  installing in place. Successful installation restarts the viewer and restores
  the scene and original session save path, including unsaved state.
- Atlas files, derived caches, settings, original SWC/CSV/NRRD data, and saved
  sessions remain in place. An ordinary upgrade does not require downloading the
  atlas again.

There are no notifications while the application is closed. Users must install
3.2 or later once before receiving automatic notifications for future versions.
From Python source, use the downloaded installer manually.

## Rendering and SWC improvements included

GPU-instanced axon segments avoid rebuilding expanded tube meshes on every width
change. The compact parser reduces temporary memory use. The optional fixed axon
geometry budget is disabled by default and is separate from parsing limits.
See [rendering measurements and limits](RENDERING_3.1.md) for the measured workload
and differences in segment joins; large datasets still require memory and GPU work.

Damaged axon SWCs can display surviving valid geometry without modifying the
source. Partial neurons are labelled `[Partial]`; **File > SWC load diagnostics...**
lists skipped records and missing parent connections. Invalid rows, ambiguous
duplicate IDs, and cycle edges are excluded rather than guessed. These diagnostics
cannot reconstruct coordinates or enumerate content that is entirely absent from
the file. Soma and analysis parsing remain strict.

This release also includes fixes for session reopening, hidden-actor lifecycle,
soma indexing, atlas label decoding, export memory use, and atomic output writes.
See the [changelog](../CHANGELOG.md) for details.

## Data and network behavior

Automatic checks query public GitHub release metadata without uploading dataset
or session contents. Disabling automatic checking prevents these periodic requests;
manual update and atlas downloads remain available. Inputs remain read-only.
Recovery snapshots and logs stay on this computer and may contain local paths.
