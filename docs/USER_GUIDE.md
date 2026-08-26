# fMOST Brain Viewer user guide

## 1. Install and start

1. Download the Windows Setup executable from the latest GitHub Release.
2. Run Setup. The default per-user installation does not require administrator
   rights.
3. Start **fMOST Brain Viewer** from the Start menu.
4. On first launch, download the Allen CCF atlas or select an existing atlas
   folder.

The portable ZIP is useful on managed computers. Extract the entire archive to
a writable folder before starting the application; do not run it from inside the
ZIP.

## 2. Configure the atlas

Choose one of the two options shown on first launch:

- **Download Allen CCF atlas** downloads and validates the required resources.
- **Use an existing atlas folder** uses local copies of
  `average_template_25.nrrd` and `annotation_10.nrrd`.

The atlas is shared by all datasets. It can be changed later from
**File > Configure Allen CCF atlas...**. Changing the atlas requires a restart.
See [Atlas setup](ATLAS_SETUP.md) for disk-space and recovery details.

## 3. Open data

At startup, choose a dataset folder or open a saved `.fmost-session.json` file.
The selected folder may be:

- one project folder;
- an axon or soma subfolder inside a project; or
- a parent folder containing several projects.

When several complete projects are found, select one or more in the dataset
picker. All selected coordinates are assumed to be registered to the same Allen
CCF convention. Use **File > Add brain datasets...** or **Remove selected
dataset** to change the combination while the viewer is open.

The import summary reports matched, unmatched, and duplicate axon–soma IDs.
Review this summary before interpreting soma-region groups. See
[Data format](DATA_FORMAT.md) for the strict matching rules.

## 4. Viewer controls

### Datasets

Each row is one imported dataset. Its checkbox hides or restores all data from
that source without losing the source's neuron selections. The color swatch is
the soma color for that dataset.

### Display

- **3D brain atlas** toggles the shared atlas.
- **Coronal annotation slice** toggles the current annotation section.
- **Show all soma locations** shows every soma in enabled datasets. When turned
  off, only somas strictly linked to visible axons are shown.
- **Coordinate grid and bounds** is off by default.
- **Brain-region legend** controls the compact legend in the 3D view.

Surface rendering is the stable default. To load the larger grayscale volume,
choose **Settings > 3D brain rendering > Volume**. The volume is loaded only
after that choice is made.

### Anterior–posterior position

Drag the slider to navigate annotation sections. The arrow buttons move one
section at a time; holding a button moves at a controlled rate and stops as soon
as the button is released.

### Appearance

Adjust atlas opacity, axon width, soma size, and highlighted-region opacity.
Axons use connected SWC line cells only; intermediate SWC nodes are not drawn
as separate fixed-size markers.
Mouse-wheel parameter changes are disabled by default to prevent accidental
editing. Enable them from **Settings > Enable mouse-wheel parameter adjustment**.

### Brain regions

Type an acronym, structure name, or Allen structure ID to show ranked results
immediately below the search box. Click to highlight a result; double-click,
press Enter, or choose **Add region** to add it. A successful selection clears
and closes the search, while clicking elsewhere closes the candidate list.
Parent structures include their descendant labels when the surface is generated.
Region checkboxes control visibility; **Select all** and **Select none** affect
only regions already in the list.

### Neurons

- **Individual / manual** lists neurons by dataset. Each axon retains its own
  color.
- **By soma region** groups all matched neurons across enabled datasets by the
  soma's atlas region. Checking one row selects every neuron in that group.

Switching modes changes only the list view and should be immediate. Unmatched
axons remain available in the individual view but are not assigned a soma region.

## 5. Capture and record

Choose **Capture...** to export the current 3D view. The dialog starts with the
current preview state and lets you temporarily include or exclude atlas, slice,
somas, axons, regions, grid, axes, and legend. TIFF is the default lossless
format; PNG, JPEG, and BMP are also available.

Choose **Record rotating GIF...** to create a looping 360° animation. Select the
scene content, direction, frame count, duration, size, and destination. Cancelling
restores the original camera and visibility state.

## 6. Sessions

Use **File > Save Session As...** to save dataset paths, selections, colors,
shared display settings, region highlights, and camera position. Relative paths
are used when possible. Moving a session with its project folders preserves those
links; missing projects can be relocated or skipped when reopening.

Session files can contain local paths and dataset identifiers. Review them before
sharing.

## 7. Logs and support

Choose **Help > Check for updates...** to query published stable GitHub Releases
without blocking the viewer. Select a newer version to download its Windows
installer, verify the GitHub SHA-256 digest, and start Setup. Application files
are replaced; atlas data, projects, sessions, caches, and settings are preserved.

Choose **Help > Open log folder** after a startup or rendering error. Logs are
local and are never uploaded automatically. Remove or replace paths and dataset
identifiers before attaching diagnostics to a public issue.

For common problems, see [Troubleshooting](TROUBLESHOOTING.md).
