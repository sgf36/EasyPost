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

# Application manifest embedded into the GUI exe. It declares the process
# Per-Monitor v2 DPI-aware at the manifest level so Windows applies awareness
# at process creation — this is what clears the WACK DPIAwarenessValidation
# warning and keeps the UI crisp on high-DPI / scaled displays. PyInstaller
# preserves the custom windowsSettings block (see the manifest's own comment).
gui_manifest = str(project_root / "packaging" / "EasyPostDesktop.exe.manifest")

# Build-variant flags. app/config.py looks for these next to the other
# resources at runtime, using the same Path(__file__).parent pattern that
# app/i18n.py uses for locales — so they only take effect if they are actually
# collected here. They were previously created in the source tree by CI but
# never bundled, which silently left LICENSE_REQUIRED False in the shipped
# build: the paid direct-download app launched with no licence gate at all.
# Listed individually and conditionally, because copying the whole resources
# directory would sweep the flags into the wrong build.
#
# The variants are mutually exclusive and each build creates only its own
# flag(s) in the source tree before this spec runs:
#   - direct download: license_required.flag (+ mcp_supported.flag)
#   - Microsoft Store:  store_build.flag  → gates production behind the Store
#                       "Production unlock" add-on (see app/core/store_entitlement.py)
variant_flags = [
    (str(project_root / "app" / "resources" / name), "app/resources")
    for name in ("license_required.flag", "mcp_supported.flag", "store_build.flag", "mas_build.flag")
    if (project_root / "app" / "resources" / name).exists()
]

# The Store build's production-unlock check reads Windows.Services.Store through
# the winrt (PyWinRT) packages. app/core/store_entitlement.py imports them
# lazily and inside try/except, so PyInstaller's static import graph never sees
# them and would not bundle them — leaving the shipped Store build unable to
# read the entitlement (production would stay locked for everyone). Collect the
# whole winrt namespace explicitly, Windows-only (the packages are win32-only).
winrt_datas, winrt_binaries, winrt_hiddenimports = [], [], []
if sys.platform.startswith("win"):
    try:
        from PyInstaller.utils.hooks import collect_all
        winrt_datas, winrt_binaries, winrt_hiddenimports = collect_all("winrt")
    except Exception as exc:  # never break the direct build over this
        print(f"[build_exe.spec] winrt collect_all skipped: {exc}")

# The Mac App Store build reads its StoreKit entitlement through PyObjC
# (app/core/mac_store_entitlement.py imports StoreKit/Foundation/CoreFoundation
# lazily, inside try/except). PyInstaller's static graph never sees those lazy
# imports, so the frozen MAS app would ship without StoreKit and could never read
# the purchase. Collect them explicitly on macOS when they are installed; absent
# (e.g. the notarized-.dmg build without pyobjc-framework-StoreKit) this is a
# harmless no-op, exactly like the winrt block above.
storekit_hiddenimports = []
if sys.platform == "darwin":
    for _mod in ("StoreKit", "Foundation", "CoreFoundation", "objc"):
        try:
            __import__(_mod)
            storekit_hiddenimports.append(_mod)
        except Exception as exc:
            print(f"[build_exe.spec] StoreKit import {_mod} skipped: {exc}")

a = Analysis(
    [str(project_root / "app" / "main.py")],
    pathex=[str(project_root)],
    binaries=[*winrt_binaries],
    datas=[
        (str(project_root / "app" / "resources" / "locales"), "app/resources/locales"),
        (str(project_root / "app" / "resources" / "icons"), "app/resources/icons"),
        *variant_flags,
        *winrt_datas,
    ],
    hiddenimports=[*winrt_hiddenimports, *storekit_hiddenimports],
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
    # Same Per-Monitor v2 DPI manifest as the GUI exe. This helper is headless
    # (console, no window) so DPI awareness is functionally moot, but WACK's
    # DPIAwarenessValidation flags any exe in the package that lacks it — now
    # that the Store build ships this helper, manifest it too to keep the
    # certification report clean (a WARNING there does not block submission).
    manifest=gui_manifest,
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
    manifest=gui_manifest,
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
