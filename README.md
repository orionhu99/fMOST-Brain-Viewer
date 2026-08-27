# fMOST Brain Viewer

[中文说明](README_zh-CN.md) · [User guide](docs/USER_GUIDE.md) · [Report a bug](https://github.com/orionhu99/fMOST-Brain-Viewer/issues)

fMOST Brain Viewer is a Windows desktop application for viewing registered SWC
neurons, soma locations, and Allen Mouse Brain Common Coordinate Framework
(CCFv3) anatomy in one interactive 3D coordinate system. It supports multiple
registered datasets, coronal navigation, brain-region surfaces, screenshots,
rotating GIF export, and reusable sessions.

> The application visualizes data that are already registered to the same Allen
> CCF coordinate convention. It does not perform image registration.

## Install on Windows

Python, Git, and a command prompt are **not** required.

1. Download `fMOST-Brain-Viewer-Setup-2.3.4-win64.exe` from the
   [latest release](https://github.com/orionhu99/fMOST-Brain-Viewer/releases/latest).
2. Double-click the installer and follow the short setup wizard.
3. Start **fMOST Brain Viewer**. On first launch, download the Allen CCF atlas or
   select an existing atlas folder.
4. Choose a registered dataset folder and start viewing.

The unsigned first release may trigger a Microsoft SmartScreen warning. Compare
the installer's SHA-256 hash with `SHA256SUMS.txt` before continuing. A portable
ZIP containing the same application is available on the release page.

## Atlas setup

The application does not bundle Allen atlas volumes. The first-run assistant
offers two options:

- **Download Allen CCF atlas** — recommended; downloads the required files from
  the Allen Institute.
- **Use an existing atlas folder** — select a folder containing
  `average_template_25.nrrd` and `annotation_10.nrrd`.

Atlas data and generated caches are stored separately from the application and
from experimental datasets. Preparing the 10 µm annotation for fast coronal
navigation requires several gigabytes of free space. An interrupted verified
download can be resumed. See [Atlas setup](docs/ATLAS_SETUP.md) for storage,
validation, and troubleshooting details.

Allen Institute content is **not** covered by this project's MIT License. Its use
is governed by the [Allen Institute Terms of Use](https://alleninstitute.org/terms-of-use/)
and [Citation Policy](https://alleninstitute.org/citation-policy/).

## Dataset layout

Dataset IDs may contain letters, numbers, hyphens, and underscores. They do not
need to be numeric or follow a laboratory-specific naming convention.

```text
<project_folder>/
├── <dataset_id>_reg_800/
│   ├── <dataset_id>-<neuron_id>_reg.swc
│   └── ...
└── <dataset_id>/
    └── soma location/
        ├── <dataset_id>_root_reg.swc
        └── soma location_<dataset_id>.csv   # optional manual region corrections
```

The importer scans a selected project folder or a parent folder containing
several projects. Axons are linked to somas only when one unique neuron ID in the
axon filename matches a soma node ID. Ambiguous or missing links are reported as
`Unmatched`; the viewer never guesses from file order.

See [Data format](docs/DATA_FORMAT.md) for SWC requirements, naming examples,
manual region corrections, and import diagnostics.

## Main features

- Overlay one or more registered datasets in a shared Allen CCF coordinate system.
- Show a smooth whole-brain Surface by default; load the 25 µm Volume on demand.
- Navigate the 10 µm annotation along the anterior–posterior axis.
- Select neurons individually or group them by soma region.
- Search atlas structures live by acronym, name, or Allen structure ID, then
  add the highlighted result by keyboard or mouse.
- Render only the connected SWC line topology, without fixed-size node markers.
- Render enlarged axons as continuous smooth tubes while retaining a thin 0x line mode.
- Keep soma display independent or strictly bound to visible axons.
- Save and reopen multi-dataset `.fmost-session.json` sessions.
- Export TIFF, PNG, JPEG, or BMP captures and looping rotating GIFs.
- Limit hidden axon actor caching to reduce memory growth during long sessions.
- Check GitHub Releases in the background from the Help menu and install a
  selected, SHA-256-verified newer version.

## Privacy and data handling

fMOST Brain Viewer contains no telemetry and does not upload user datasets.
Network access occurs only when the user explicitly requests Allen atlas or
ontology resources, or chooses **Help > Check for updates**. SWC, CSV, and NRRD inputs are read-only; derived caches,
settings, logs, captures, and sessions are stored separately. Before sharing a
session, log, or screenshot, review it for paths and identifiers from your own
environment.

## Help

- [English user guide](docs/USER_GUIDE.md)
- [中文用户指南](docs/USER_GUIDE_zh-CN.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- In the application: **Help > Check for updates**, **Open log folder**, and **About fMOST Brain Viewer**

When filing an issue, attach only sanitized diagnostics. Do not upload
experimental data unless you intentionally choose to make them public.

## Development

End users should use the installer or portable ZIP. Source setup and test
commands are documented in [CONTRIBUTING.md](CONTRIBUTING.md).

## Citation and license

Project source code is released under the [MIT License](LICENSE), Copyright
© 2026 Orion HU and Li Bo Lab, Westlake University. Allen data, Qt/PySide, and
other dependencies retain their own terms; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Citation metadata are provided
in [CITATION.cff](CITATION.cff).

Current version: **2.3.4 — Smooth axons and refreshed visual identity**.
