# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir specification for the Windows desktop release."""

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import re
import sys

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).parent

datas = []
binaries = []
hiddenimports = [
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtSvg",
    "vtkmodules.qt.QVTKRenderWindowInteractor",
]

for directory in ("assets", "resources", "docs", "licenses"):
    source = ROOT / directory
    if source.is_dir():
        datas.append((str(source), directory))

for filename in (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "CITATION.cff",
    "README.md",
    "README_zh-CN.md",
    "requirements.lock",
):
    source = ROOT / filename
    if source.is_file():
        datas.append((str(source), "."))


def locked_distribution_names():
    """Return the exact runtime/build distributions used by this release."""
    names = set()
    requirement = re.compile(r"^([A-Za-z0-9_.-]+)==")
    for lock_name in ("requirements.lock", "requirements-build.lock"):
        lock_path = ROOT / lock_name
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            match = requirement.match(line.strip())
            if match:
                names.add(match.group(1))
    return sorted(names, key=str.casefold)


def collect_distribution_licenses(name):
    """Collect license/notice files without bundling unrelated package data."""
    try:
        package = distribution(name)
    except PackageNotFoundError as exc:
        raise RuntimeError(f"Locked distribution is not installed: {name}") from exc
    canonical = re.sub(r"[^A-Za-z0-9_.-]+", "-", package.metadata["Name"])
    collected = []
    for entry in package.files or ():
        parts = tuple(str(part) for part in entry.parts)
        lower_parts = tuple(part.casefold() for part in parts)
        basename = parts[-1].casefold()
        dist_info_index = next(
            (index for index, part in enumerate(lower_parts) if part.endswith(".dist-info")),
            None,
        )
        if dist_info_index is None:
            continue
        metadata_parts = lower_parts[dist_info_index + 1 :]
        is_notice = (
            "licenses" in metadata_parts
            or basename.startswith(("license", "copying", "notice", "authors"))
        )
        if not is_notice:
            continue
        source = Path(package.locate_file(entry))
        if not source.is_file():
            continue
        relative_parts = parts[dist_info_index + 1 :]
        destination = Path("third_party_licenses") / canonical
        if len(relative_parts) > 1:
            destination = destination.joinpath(*relative_parts[:-1])
        collected.append((str(source), str(destination)))
    return collected


for package_name in locked_distribution_names():
    datas.extend(collect_distribution_licenses(package_name))

python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.is_file():
    raise RuntimeError(f"Python license file is missing: {python_license}")
datas.append((str(python_license), "third_party_licenses/Python"))

# PyInstaller already supplies hooks for Qt, VTK, NumPy, SciPy and Pillow.
# PyVista and pyvistaqt load modules/data dynamically, so collect those two
# packages explicitly without widening the bundle to unrelated Qt stacks.
for package in ("pyvista", "pyvistaqt"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# PySide6 6.11 loads this runtime beside QtCore.pyd's DLL search root, while
# PyInstaller's shiboken hook keeps it only inside the package subdirectory.
if sys.platform == "win32":
    shiboken_runtime = Path(
        distribution("shiboken6").locate_file("shiboken6/shiboken6.abi3.dll")
    )
    if not shiboken_runtime.is_file():
        raise RuntimeError(f"Shiboken runtime is missing: {shiboken_runtime}")
    binaries.append((str(shiboken_runtime), "."))

analysis = Analysis(
    [str(ROOT / "fmost_brain_viewer.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter"],
    noarchive=False,
    optimize=1,
)

# Qt 6.11 intentionally uses the unversioned Windows ICU compatibility DLL.
# A build host may expose Poppler's incompatible same-named ICU 78 DLL first;
# never bundle that copy because it shadows the supported Windows system DLL.
if sys.platform == "win32":
    incompatible_icu = {"icuuc.dll", "icudt78.dll"}
    analysis.binaries = type(analysis.binaries)(
        entry for entry in analysis.binaries
        if Path(entry[0]).name.casefold() not in incompatible_icu
    )

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="fMOST Brain Viewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "fmost_brain_viewer.ico"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="fMOST Brain Viewer",
)
