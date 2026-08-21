# Third-party notices

This file distinguishes the license for fMOST Brain Viewer source code from the
terms that apply to atlas content and third-party software. It is a practical
notice, not legal advice. The license files shipped with the exact binary
distribution are authoritative.

## fMOST Brain Viewer

Copyright © 2026 Orion HU and Li Bo Lab, Westlake University.

The project's original source code and documentation are distributed under the
[MIT License](LICENSE). That license does not grant rights to third-party data,
libraries, names, or trademarks.

## Allen Institute content

Allen Mouse Brain CCF atlas volumes, structure ontology, and other Allen
Institute content are not part of the project's MIT-licensed work. Atlas volumes
are not bundled in the installer or portable archive. The user may explicitly
download them from the Allen Institute or select an existing local copy. A
structure graph snapshot may be packaged for offline atlas identification and
retains the Allen Institute's terms.

Use of Allen Institute content is subject to the current:

- [Allen Institute Terms of Use](https://alleninstitute.org/terms-of-use/)
- [Allen Institute Citation Policy](https://alleninstitute.org/citation-policy/)

At the time of this release, the Terms describe research or other noncommercial
use and require appropriate citation. Users are responsible for checking the
current terms and obtaining any additional permission needed for their use.

## Runtime and build dependencies

The Windows distribution contains unmodified binary components from open-source
projects. The primary direct dependencies are summarized below.

| Component | License family | Project license/source |
|---|---|---|
| Python | PSF License | [Python license](https://docs.python.org/3/license.html) |
| PySide6 / Qt for Python | LGPL-3.0-only or GPL-3.0-only; commercial licensing is also offered | [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) |
| Qt libraries | Primarily LGPL-3.0-only or GPL-3.0-only; some modules and embedded components have separate terms | [Qt licensing](https://doc.qt.io/qt-6/licensing.html) |
| VTK | BSD-3-Clause | [VTK licensing](https://docs.vtk.org/en/latest/about.html) |
| PyVista | MIT | [PyVista project](https://github.com/pyvista/pyvista) |
| PyVistaQt | MIT | [PyVistaQt project](https://github.com/pyvista/pyvistaqt) |
| NumPy | BSD-3-Clause | [NumPy license](https://numpy.org/doc/stable/license.html) |
| SciPy | BSD-3-Clause | [SciPy license](https://github.com/scipy/scipy/blob/main/LICENSE.txt) |
| Pillow | HPND | [Pillow license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| pynrrd | MIT | [pynrrd project](https://github.com/mhe/pynrrd) |
| PyInstaller | GPL-2.0-only with a special exception for distributing bundled applications | [PyInstaller license](https://pyinstaller.org/en/stable/license.html) |

Indirect dependencies retain their own notices. Release builds preserve the
license and notice files delivered by dependency wheels in the packaged
`third_party_licenses` directory. Standard GPLv3 and LGPLv3 texts used by the
Qt/PySide6 open-source distribution are included under `licenses`. Nothing in
the fMOST Brain Viewer MIT License replaces those terms.

The community PySide6/Qt libraries are dynamically loaded from separate files in
the one-folder application. Recipients can replace compatible shared libraries
as permitted by the applicable LGPL terms. Source and corresponding license
information for the exact Qt release are available from
[The Qt Company](https://download.qt.io/official_releases/QtForPython/).

## Trademarks

Allen Institute, Qt, Python, VTK, PyVista, NumPy, SciPy, and other names are the
property of their respective owners. Their appearance identifies compatibility
or provenance and does not imply endorsement.
