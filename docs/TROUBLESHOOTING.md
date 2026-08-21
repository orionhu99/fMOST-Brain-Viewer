# Troubleshooting

## Windows blocks the installer

The first public installer is unsigned. Verify its SHA-256 against
`SHA256SUMS.txt` from the same GitHub Release. If it matches the official Release,
open **More info** in SmartScreen and choose **Run anyway**. Never bypass a warning
for an installer obtained from a third-party mirror.

## The application does not start

1. Restart it once from the Start menu rather than an old shortcut.
2. Run the packaged executable with `--self-test` and wait for its exit code. Exit
   code `0` means the resource and off-screen render checks passed. The windowed
   build may not print text in the terminal.
3. In a running viewer, use **Help > Open log folder**. If the viewer cannot start,
   look under `%LOCALAPPDATA%\fMOST Brain Viewer\logs`.
4. Report the version, Windows version, self-test exit code, and a sanitized final
   log section. The log records `Self-test passed` or the failure details.

Do not attach complete logs before removing local paths and dataset identifiers.

## Atlas validation fails

- Confirm both required files are in the same selected folder and retain their
  original names.
- Confirm the template is 25 µm and the annotation is 10 µm CCFv3 2017.
- Do not substitute a partially downloaded `.part` file.
- If a previous download is damaged, start the in-application download again;
  complete verified files will be skipped.

## Atlas download stops

Check network access, free space, and write permission. Start the same download
again in the same folder to resume a valid partial file. If a proxy blocks Range
requests, the application may restart only the affected file. Choosing an
existing atlas folder provides a fully offline alternative.

## “No complete dataset was found”

Compare the selected folder with the canonical layout in
[Data format](DATA_FORMAT.md). The dataset ID must be consistent across the axon
directory, soma directory, and root soma filename. Confirm that the axon folder
contains at least one readable SWC file.

## Axons are reported as Unmatched

An axon filename must identify exactly one numeric node ID present in the soma
SWC. Rename a copy of the axon file to include the intended unique ID, then import
again. Do not rely on alphabetical order. If several valid IDs occur in the same
filename, the result remains intentionally unmatched.

## Region search finds no result

Search by exact Allen acronym, full structure name, or numeric structure ID. Try
general structures such as `MOp` or `VISp`. If the ontology is unavailable, use
**File > Configure Allen CCF atlas...** to verify the atlas resources and local
ontology snapshot.

## Adding a region is slow the first time

The first request can generate a 3D surface and cache it. Use
**File > Prepare brain region library...** to build surfaces ahead of analysis.
Subsequent use of an unchanged atlas should load the validated cache.

## Neuron loading is slow or memory use grows

Axons are loaded only when first selected. Selecting hundreds of large SWC files
still requires parsing and VTK actor creation; use region groups or smaller
selections when possible. Hidden actors are kept in a bounded cache and may be
reloaded after eviction. Surface mode has a lower startup and memory cost than
Volume mode.

## Capture or GIF export fails

Choose a writable destination with enough free space. Reduce the scale, frame
count, or image dimensions. If transparent output is required, use PNG or TIFF;
JPEG has no alpha channel. The preview state should return after cancellation or
failure.

## A session opens with an atlas warning

The session's atlas signature differs from the configured atlas. Select the atlas
used to create the session or cancel. Continuing silently would make spatial
comparisons unreliable.

## Reporting a reproducible bug

Use the GitHub bug template. Include a minimal synthetic dataset if needed, the
exact action sequence, expected and observed behavior, version, Windows version,
and sanitized diagnostics. Do not upload real experimental data by default.
