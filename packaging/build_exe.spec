# PyInstaller spec for EasyPost Desktop.
# Build from the project root with:
#   .venv\Scripts\python.exe -m PyInstaller packaging\build_exe.spec --noconfirm
#
# Builds in --onedir mode (a folder, not a single self-extracting exe).
# Onefile builds unpack themselves into a temp directory at every launch,
# which is a strong heuristic signal antivirus/SmartScreen use to flag
# packers/droppers. Onedir avoids that runtime self-extraction, which
# meaningfully reduces false-positive flags for an unsigned build (see
# README's "Windows SmartScreen warning" section for the full picture —
# this alone does not eliminate the warning, only code signing does that).

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH).parent
icons_dir = project_root / "packaging" / "icons"

a = Analysis(
    [str(project_root / "app" / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "app" / "resources" / "locales"), "app/resources/locales"),
        (str(project_root / "app" / "resources" / "icons"), "app/resources/icons"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EasyPostDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icons_dir / "app_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="EasyPostDesktop",
)

# Wraps the onedir output into a real, Finder-icon-able EasyPostDesktop.app
# bundle on macOS. Meaningless on Windows (BUNDLE is a no-op there), so only
# invoke it when actually building on macOS.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="EasyPostDesktop.app",
        icon=str(icons_dir / "app_icon.icns"),
        bundle_identifier="com.spencerfields.easypostdesktop",
    )
