"""Package the PyInstaller onedir build (dist/EasyPostDesktop/) into an MSIX.

Run after building with PyInstaller:
    .venv\\Scripts\\python.exe -m PyInstaller packaging\\build_exe.spec --noconfirm
    .venv\\Scripts\\python.exe packaging\\build_msix.py

Produces dist/EasyPostDesktop.msix. Signing is a separate step (see
packaging/sign_msix_local.ps1 for local testing, or the CI "Sign MSIX
package" step) since a self-signed cert is enough for Microsoft Store
submission — the Store re-signs on publish.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

from PIL import Image

project_root = Path(__file__).parent.parent
dist_dir = project_root / "dist"
pyinstaller_output = dist_dir / "EasyPostDesktop"
build_dir = project_root / "build"
staging_dir = build_dir / "msix_staging"
source_icon = project_root / "app" / "resources" / "icons" / "app_icon.png"
manifest_src = Path(__file__).parent / "msix" / "AppxManifest.xml"
output_msix = dist_dir / "EasyPostDesktop.msix"

# The single language the manifest declares. Kept in lockstep with
# packaging/msix/AppxManifest.xml's <Resources> — see verify_store_variant().
PACKAGE_LANGUAGE = "en-US"

# (manifest attribute, output filename, pixel size)
ASSET_SIZES = [
    ("StoreLogo", "StoreLogo.png", 50),
    ("Square44x44Logo", "Square44x44Logo.png", 44),
    ("Square150x150Logo", "Square150x150Logo.png", 150),
]


def find_sdk_tool(exe: str) -> Path:
    candidates = sorted(
        Path(r"C:\Program Files (x86)\Windows Kits\10\bin").glob(f"*/x64/{exe}"),
        key=lambda p: p.parent.parent.name,
    )
    if not candidates:
        raise FileNotFoundError(
            f"{exe} not found under C:\\Program Files (x86)\\Windows Kits\\10\\bin\\*\\x64. "
            "Install the Windows 10/11 SDK."
        )
    return candidates[-1]


def generate_assets(assets_dir: Path) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(source_icon) as source:
        source = source.convert("RGBA")
        for _, filename, size in ASSET_SIZES:
            resized = source.resize((size, size), Image.LANCZOS)
            resized.save(assets_dir / filename)


def stage_package() -> None:
    if not pyinstaller_output.exists():
        raise FileNotFoundError(
            f"{pyinstaller_output} not found — run the PyInstaller build first."
        )

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    shutil.copy2(manifest_src, staging_dir / "AppxManifest.xml")
    # The MCP helper is direct-download only: a Store package cannot have another
    # application launch it out of the install location. Shipping it anyway would
    # be ~10 MB of dead weight and an invitation to wire up something that will
    # not work, so drop it here rather than teaching the spec about variants.
    shutil.copytree(
        pyinstaller_output,
        staging_dir / "EasyPostDesktop",
        ignore=shutil.ignore_patterns("easypost-mcp", "easypost-mcp.exe"),
    )
    generate_assets(staging_dir / "Assets")


def generate_pri() -> None:
    """Write resources.pri into the staging root.

    Every packaged app needs a resource index; a package that declares a
    language in its manifest with no resources.pri fails Store deployment even
    though Add-AppxPackage sideloading tolerates it (this is what sank
    certification 10.3.4). The app has no MRT resources of its own, so this is
    a minimal index of the package's (language-neutral) files, stamped with
    en-US as the default qualifier to match the manifest.

    priconfig.xml is written outside the staging directory on purpose, so it is
    not itself swept into the package.
    """
    makepri = find_sdk_tool("makepri.exe")
    priconfig = build_dir / "priconfig.xml"
    resources_pri = staging_dir / "resources.pri"

    build_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(makepri), "createconfig", "/cf", str(priconfig),
         "/dq", PACKAGE_LANGUAGE, "/o"],
        check=True,
    )
    subprocess.run(
        [str(makepri), "new", "/pr", str(staging_dir), "/cf", str(priconfig),
         "/mn", str(staging_dir / "AppxManifest.xml"),
         "/of", str(resources_pri), "/o"],
        check=True,
    )
    if not resources_pri.exists():
        raise SystemExit("makepri did not produce resources.pri")


def pack() -> None:
    makeappx = find_sdk_tool("makeappx.exe")
    if output_msix.exists():
        output_msix.unlink()
    subprocess.run(
        [str(makeappx), "pack", "/d", str(staging_dir), "/p", str(output_msix)],
        check=True,
    )


def verify_store_variant() -> None:
    """The Store package must carry neither variant flag nor the MCP helper.

    The mirror of packaging/verify_variant_flags.sh: that one fails the build
    when the direct download *loses* a flag, this one fails when the Store
    build *gains* one. A flag smuggled in here would gate a Store purchase
    behind a second paid unlock, which breaches Microsoft's policies.
    """
    with zipfile.ZipFile(output_msix) as archive:
        names = archive.namelist()

    strays = [n for n in names if n.endswith(".flag") or "easypost-mcp" in n]
    if strays:
        raise SystemExit(
            "MSIX contains files that belong only to the direct download:\n  "
            + "\n  ".join(strays)
        )
    if "resources.pri" not in names:
        raise SystemExit(
            "MSIX has no resources.pri — Store deployment (cert 10.3.4) will "
            "reject it. generate_pri() must run before pack()."
        )
    print("Store variant verified: no licence flag, no MCP helper, resources.pri present.")


if __name__ == "__main__":
    stage_package()
    generate_pri()
    pack()
    verify_store_variant()
    print(f"Built {output_msix}")
