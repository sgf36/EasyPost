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

# Build-variant flags. app/config.py looks for these next to the other
# resources at runtime, using the same Path(__file__).parent pattern that
# app/i18n.py uses for locales — so they only take effect if they are actually
# collected here. They were previously created in the source tree by CI but
# never bundled, which silently left LICENSE_REQUIRED False in the shipped
# build: the paid direct-download app launched with no licence gate at all.
# Listed individually and conditionally, because copying the whole resources
# directory would sweep the flags into the Store build too.
variant_flags = [
    (str(project_root / "app" / "resources" / name), "app/resources")
    for name in ("license_required.flag", "mcp_supported.flag")
    if (project_root / "app" / "resources" / name).exists()
]

a = Analysis(
    [str(project_root / "app" / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "app" / "resources" / "locales"), "app/resources/locales"),
        (str(project_root / "app" / "resources" / "icons"), "app/resources/icons"),
        *variant_flags,
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

# Second entry point: the MCP server an AI client launches as a subprocess.
# It has to be its own console executable rather than a flag on the GUI exe,
# because MCP talks JSON-RPC over stdio and a windowed build has no usable
# stdin/stdout. Sharing the same Analysis keeps the Python runtime and
# dependencies collected once rather than twice.
mcp_a = Analysis(
    [str(project_root / "app" / "mcp_server.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=["app.mcp_server"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
mcp_pyz = PYZ(mcp_a.pure, mcp_a.zipped_data, cipher=block_cipher)
mcp_exe = EXE(
    mcp_pyz,
    mcp_a.scripts,
    [],
    exclude_binaries=True,
    name="easypost-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # stdio transport: a console subsystem is required
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

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
    mcp_exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    mcp_a.binaries,
    mcp_a.zipfiles,
    mcp_a.datas,
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
