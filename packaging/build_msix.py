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
staging_dir = project_root / "build" / "msix_staging"
source_icon = project_root / "app" / "resources" / "icons" / "app_icon.png"
manifest_src = Path(__file__).parent / "msix" / "AppxManifest.xml"
output_msix = dist_dir / "EasyPostDesktop.msix"

# (manifest attribute, output filename, pixel size)
ASSET_SIZES = [
    ("StoreLogo", "StoreLogo.png", 50),
    ("Square44x44Logo", "Square44x44Logo.png", 44),
    ("Square150x150Logo", "Square150x150Logo.png", 150),
]


def find_makeappx() -> Path:
    candidates = sorted(
        Path(r"C:\Program Files (x86)\Windows Kits\10\bin").glob("*/x64/makeappx.exe"),
        key=lambda p: p.parent.parent.name,
    )
    if not candidates:
        raise FileNotFoundError(
            "makeappx.exe not found under C:\\Program Files (x86)\\Windows Kits\\10\\bin\\*\\x64. "
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


def pack() -> None:
    makeappx = find_makeappx()
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
    print("Store variant verified: no licence flag, no MCP helper.")


if __name__ == "__main__":
    stage_package()
    pack()
    verify_store_variant()
    print(f"Built {output_msix}")
