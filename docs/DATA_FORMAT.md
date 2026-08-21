# Dataset format

## Coordinate requirement

All SWC coordinates must already be registered to the same Allen CCF coordinate
convention and expressed in micrometres. fMOST Brain Viewer does not estimate or
apply registration transforms.

## Canonical project layout

```text
<project_folder>/
├── <dataset_id>_reg_800/
│   ├── <dataset_id>-101_reg.swc
│   ├── <dataset_id>-102_reg.swc
│   └── ...
└── <dataset_id>/
    └── soma location/
        ├── <dataset_id>_root_reg.swc
        └── soma location_<dataset_id>.csv   # optional
```

`<dataset_id>` is an arbitrary stable label such as `sample_A` or
`pilot-02`; it does not need to be numeric. Keep the same label in the axon
directory, soma directory, and soma filename.

## SWC files

The parser accepts standard SWC rows:

```text
node_id type x_um y_um z_um radius parent_id
```

Blank lines and lines beginning with `#` are ignored. The soma file contains the
soma point IDs and registered coordinates. Each axon file contains the points and
parent links for one reconstructed neuron.

Inputs are read-only. The viewer does not rewrite SWC files.

## Strict axon–soma matching

For each axon filename, the importer extracts numeric tokens and compares them
with node IDs in that dataset's soma SWC. A link is accepted only when exactly one
valid soma ID can be identified.

Examples with soma IDs `101` and `102`:

| Axon filename | Result |
|---|---|
| `sample_A-101_reg.swc` | matched to soma `101` |
| `sample_A-neuron_102_reg.swc` | matched to soma `102` |
| `sample_A-neuron_unknown_reg.swc` | unmatched |
| `sample_A-101-copy-102_reg.swc` | ambiguous and unmatched |

If two axon files resolve to the same soma ID, the import summary reports a
duplicate. Unmatched and duplicate axons remain inspectable, but they are not
silently linked by alphabetical order, file order, or row number. Consequently,
they do not enter a soma-region group or create a bound soma point.

The import summary should be reviewed for every newly prepared dataset.

## Optional manual region corrections

Place `soma location_<dataset_id>.csv` beside the soma SWC. Each data row maps a
soma node ID to an Allen structure acronym, structure ID, or an unassigned label:

```csv
soma_id,region
101,MOp
102,VISp_r
103,unassigned
```

The header is optional. `_l`, `_r`, `-left`, and `-right` suffixes are accepted
for review-table compatibility and normalized to the corresponding base Allen
structure; the viewer does not infer hemisphere identity from that suffix.
Unknown soma IDs, unknown structures, malformed rows, and duplicate corrections
are reported or ignored according to the import summary rather than changing the
source file.

## Multiple datasets

Each neuron is identified internally by a dataset key plus the neuron ID, so the
same soma number can safely occur in different projects. Use a stable dataset ID
and project location when saving sessions. A second import of the same resolved
project is rejected; identical dataset labels at different paths remain separate
and are distinguished by their paths.

## Files that should not be committed

Experimental SWC/CSV/NRRD data, sessions, captures, GIFs, logs, and generated
surface caches do not belong in the source repository. The public tests generate
small synthetic fixtures at runtime and remove them after use.
