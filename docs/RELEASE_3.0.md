# 3.0 migration and validation

Version 3.0 reads version 1 session files and writes version 2. Keep an original
copy if a session must also be opened in 2.x; 2.x does not read the new format.
The format now includes coronal slice index/visibility, brain visibility, rendering
mode and strict soma labeling. Switching sessions and updating first offers
Save, Discard and Cancel for modified state, including newly created sessions.

Recovery copies are written every 30 seconds while a session is modified. Use
**File > Recover unsaved session** to find the recovery folder, **Open session**
to load a copy, then **Save session as** to keep it permanently. A clean close
removes that window's own recovery copy. A crash within the first 30 seconds
may occur before the first recovery copy exists.

SWC input is now strict: short rows, missing parents, cycles and unsupported
numeric values report errors. Multiple roots in a soma file remain supported.
An invalid axon is unchecked and reported without disabling the rest of the list.
Original SWC files are never rewritten to repair errors automatically.

**Settings > Strict soma labels** disables the neighborhood fallback. Manual
CSV corrections still take precedence. **File > Export soma diagnostics**
exports each soma's coordinates, raw voxel value, atlas-derived region, final
region and source (`direct`, `neighborhood`, `manual`, `outside`, `unassigned`).
The neighborhood classification is an estimate and should not be treated as a
direct atlas hit. Changing the mode rebuilds the viewer and counts as a change.

All newly generated raw annotations, identity indexes and surfaces are stored
under the per-user application cache. Existing legacy caches beside atlas files
are left untouched. **Settings > Manage atlas cache** reports disk use and can
clear the active atlas's derived surfaces/indexes; it keeps memory-mapped raw
volumes and source files. Damaged surface caches are rebuilt automatically.

The installer overwrites packaged application files without wildcard folder
deletion. User-created files are not intentionally removed during upgrade or
uninstall. Do not save research data using names that collide with packaged
application files. Older unused application files may remain after upgrading;
there is intentionally no blanket cleanup of unknown files.

Updates download in a worker thread, verify GitHub's SHA-256 metadata, and ask
about unsaved state before launching Setup. Normal user permissions are used
first. Administrator launch is an explicit retry for protected old installations.
Cancellation waits for the in-flight network operation; socket operations have
a five-second timeout, while OS DNS resolution can take longer.

## Release verification

- Run the full unittest suite and `--self-test` locally with working OpenGL.
- The window round-trip test uses real Qt controls and an offscreen PyVista
  backend adapter because an embedded Qt OpenGL window is unavailable on some
  non-interactive hosts. It does not replace interactive GPU acceptance.
- `--ci-smoke-test` explicitly skips rendering on hosted CI.
- Compile a separate installer with `/DTestInstall` and use
  `build/test_installer.ps1`; its application ID and registry behavior are
  isolated from production. The test verifies install, upgrade, uninstall and
  preservation of a nested user-created file.
- CI audits hash-locked runtime dependencies with pip-audit. A clean audit is
  a point-in-time check, not a guarantee against unknown vulnerabilities.
- GitHub release builds generate signed Sigstore provenance using
  [actions/attest](https://github.com/actions/attest). Verify a downloaded file
  with `gh attestation verify <file> --repo orionhu99/fMOST-Brain-Viewer`.
  This is build provenance, not Windows Authenticode signing. SmartScreen may
  still warn; a trusted Windows code-signing certificate is not bundled.

Interactive release acceptance should include a representative large dataset,
rapid neuron selection/cache eviction, volume/slice switching, a session round
trip, PNG/GIF export, and cancelling a slow update. Record GPU/driver and dataset
scale locally; do not publish experimental identifiers or files.
