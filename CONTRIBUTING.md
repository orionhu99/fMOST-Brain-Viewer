# Contributing to fMOST Brain Viewer

Thank you for helping improve the viewer. Keep changes focused, reproducible,
and safe for research data.

## Before opening an issue

- Search existing issues.
- Confirm the problem with the latest Release.
- Run `fMOST Brain Viewer.exe --self-test` when the packaged application starts
  incorrectly.
- Use only sanitized diagnostics. Do not upload experimental SWC, CSV, NRRD,
  sessions, screenshots, logs, or local paths unless you intentionally choose to
  publish them.

The bug template asks for a minimal synthetic reproducer, not a real dataset.

## Development environment

The supported release environment is Windows 10/11 x64 with Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

`requirements.lock` is the reproducible runtime input. Release tooling uses
`requirements-build.lock` with pip's `--require-hashes` option. Do not update
lock files as part of an unrelated change.

## Run checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\privacy_scan.py --repo-root .
.\.venv\Scripts\python.exe fmost_brain_viewer.py --self-test
```

`--self-test` requires a working OpenGL implementation and performs a real
off-screen render. GitHub's non-interactive Windows runner uses the explicit
`--ci-smoke-test` fallback, which still constructs a Qt window and executes a
PyVista/VTK geometry pipeline but does not claim to validate OpenGL rendering.

Tests create generic fixtures in temporary directories and must not require an
atlas download, network access, or laboratory data. GUI tests should use Qt
offscreen mode when possible.

## Pull requests

1. Create a focused branch from `main`.
2. Add or update a test for behavior changes.
3. Update English and Chinese documentation for user-visible changes.
4. Run all three checks above.
5. Open a pull request describing the user-visible result, validation performed,
   and any compatibility or performance impact.

Keep raw inputs read-only. Write caches, logs, captures, and sessions to the
application-data or user-selected output location, never into source datasets by
default.

## Privacy requirements

Public contributions must not contain:

- real sample or animal identifiers;
- laboratory-specific project names, folder conventions, or local drive paths;
- research-specific brain-region examples;
- credentials, private email addresses, or account metadata;
- experimental data, sessions, captures, caches, logs, or generated binaries.

Use identifiers such as `sample_A`, `sample_B`, `<dataset_id>`, and
`<neuron_id>`. Use general atlas structures such as `MOp` and `VISp` in examples.
The privacy scan is intentionally conservative; if it reports a false positive,
make the public text clearer instead of adding a broad exclusion.

## Versioning and releases

`version.py` is the single source of truth for the application version. Runtime
About text, package metadata, installer metadata, Release tags, filenames, and
the top CHANGELOG entry must agree. Releases are created by the GitHub Actions
workflow after tests, privacy scanning, package self-test, and artifact checks
pass.

## Licensing

By submitting a contribution, you agree that your contribution is licensed under
the project's MIT License. Do not copy material with incompatible or unknown
terms. Atlas content and third-party libraries remain subject to their own terms;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
