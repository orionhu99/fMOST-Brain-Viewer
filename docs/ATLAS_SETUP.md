# Allen CCF atlas setup

## Required resources

fMOST Brain Viewer uses the Allen Mouse Brain CCFv3 (2017 annotation) and
expects these files in one atlas folder:

```text
<atlas_folder>/
├── average_template_25.nrrd
└── annotation_10.nrrd
```

Atlas volumes are not included in the installer or portable ZIP. The application
ships only its atlas manifest and an ontology snapshot needed to identify and
validate resources.

## Recommended: download in the application

1. Choose **Download Allen CCF atlas** in the first-run assistant.
2. Select a writable folder with at least 5.5 GB of free space.
3. Keep the window open until download and validation finish.
4. If desired, choose a region-library preparation level. This cache can also be
   prepared later.

Downloads use temporary `.part` files. Validated files are skipped; an
interrupted partial download is resumed when the server supports HTTP Range.
The application retries transient failures a limited number of times and keeps a
usable partial file for the next attempt.

The Allen download host exposes these files through a `current-release` alias.
This application release pins their exact CCFv3 2017 sizes and SHA-256 values.
If Allen later changes that alias, the viewer will stop with an explicit identity
error instead of accepting different data silently; install an updated viewer or
select an existing verified atlas folder.

Before activation, the application checks the manifest identity, file size,
SHA-256, NRRD header, dimensions, voxel spacing, data type, and expected raw
payload size. A file that fails validation is never treated as a usable atlas.

## Use an existing atlas

Choose **Use an existing atlas folder**, then select the folder containing both
required NRRD files. Files may be on a local drive or stable network location;
a local SSD is strongly recommended for coronal navigation and surface-cache
generation.

Do not rename the two required files. If validation fails, confirm that the
template is the 25 µm resource and the annotation is the 10 µm CCFv3 2017
resource, rather than a similarly named resolution or release.

## Storage and caches

The 10 µm annotation is large after preparation for direct random access. The
viewer also creates whole-brain and region-surface caches. These files are
derived, can be recreated, and must remain outside the application install
directory and experimental project folders.

The application checks write permission and available space before downloading
or preparing data. Cancelling cache preparation keeps completed, validated cache
entries so the task can continue later.

## Sessions and atlas identity

Sessions record an atlas signature. If a session is opened with a different
atlas identity, the viewer warns before displaying data. Reconfigure the atlas or
cancel; do not silently compare sessions generated against different atlas
definitions.

## Source, terms, and citation

Atlas volumes and ontology are Allen Institute content and are not licensed under
the fMOST Brain Viewer MIT License. Review the current documents before use:

- [Allen Institute Terms of Use](https://alleninstitute.org/terms-of-use/)
- [Allen Institute Citation Policy](https://alleninstitute.org/citation-policy/)
- [Allen Mouse Brain CCF resources](https://download.alleninstitute.org/informatics-archive/current-release/mouse_ccf/)

The viewer accesses Allen servers only after the user explicitly requests an
atlas download. It does not upload local data.
