#!/usr/bin/env bash
# Build, sign and package the Mac App Store edition of Easy-Post Desktop.
# Runs on macOS only. Kept SEPARATE from the notarized-.dmg lane — MAS is a
# distinct pipeline (brief §5c). Local build first; a CI lane can come later.
#
# Two modes:
#   * Normal — writes app/resources/mas_build.flag so app/config.MAS_BUILD is
#     true, gating production behind the StoreKit In-App Purchase.
#   * Spike (MAS_SPIKE=1) — packages the CURRENT app with NO mas flag, to prove a
#     sandboxed PySide6 .app clears Apple's validation before any StoreKit code
#     exists (brief §6). Run this FIRST.
#
# Required environment (all come from the owner's Apple portal setup — see
# OWNER-ACTIONS.md; this script never handles credentials):
#   SIGN_APP_IDENTITY        e.g. "Apple Distribution: Spencer Fields (TEAMID)"
#   SIGN_INSTALLER_IDENTITY  e.g. "3rd Party Mac Developer Installer: Spencer Fields (TEAMID)"
#   PROVISION_PROFILE        path to the Mac App Store .provisionprofile
# Optional:
#   MAS_SPIKE=1              build the vanilla spike bundle (no mas flag)
#   SKIP_VALIDATE=1          skip the final `altool`/`notarytool` validate step
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

app_name="EasyPostDesktop.app"
dist="dist_mas"
work="build_mas"
app_path="$dist/$app_name"
entitlements="$here/EasyPostDesktop.entitlements"
plist_additions="$here/Info.plist.additions"
pkg_out="$dist/EasyPostDesktop.pkg"

: "${SIGN_APP_IDENTITY:?set SIGN_APP_IDENTITY (Apple Distribution identity)}"
: "${SIGN_INSTALLER_IDENTITY:?set SIGN_INSTALLER_IDENTITY (Mac Installer Distribution identity)}"
: "${PROVISION_PROFILE:?set PROVISION_PROFILE (path to the Mac App Store .provisionprofile)}"
[ -f "$PROVISION_PROFILE" ] || { echo "::error::PROVISION_PROFILE not found: $PROVISION_PROFILE"; exit 1; }

python="${PYTHON:-python}"

echo "==> [1/6] Reset variant flags (mutually exclusive)"
rm -f app/resources/license_required.flag \
      app/resources/store_build.flag \
      app/resources/mcp_supported.flag \
      app/resources/mas_build.flag
if [ "${MAS_SPIKE:-0}" = "1" ]; then
  echo "    SPIKE mode: no mas_build.flag written (packaging vanilla app)."
else
  # Marker file that app/config.MAS_BUILD keys off. Content is irrelevant; the
  # PyInstaller spec must collect it (add mas_build.flag to that spec's
  # variant_flags list when wiring the real MAS build — brief §4a/§5c).
  printf 'mas\n' > app/resources/mas_build.flag
  echo "    wrote app/resources/mas_build.flag"
fi

echo "==> [2/6] Build the .app (PyInstaller onedir + BUNDLE)"
rm -rf "$dist" "$work"
"$python" -m PyInstaller packaging/build_exe.spec --noconfirm \
    --distpath "$dist" --workpath "$work"
[ -d "$app_path" ] || { echo "::error::$app_path was not produced"; exit 1; }

echo "==> [3/6] Merge MAS Info.plist keys + embed provisioning profile"
merge_key() { # key type value
  /usr/libexec/PlistBuddy -c "Delete :$1" "$app_path/Contents/Info.plist" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Add :$1 $2 $3" "$app_path/Contents/Info.plist"
}
merge_key CFBundleShortVersionString  string  "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist_additions")"
merge_key CFBundleVersion             string  "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist_additions")"
merge_key LSMinimumSystemVersion      string  "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$plist_additions")"
merge_key LSApplicationCategoryType   string  "$(/usr/libexec/PlistBuddy -c 'Print :LSApplicationCategoryType' "$plist_additions")"
merge_key NSHumanReadableCopyright     string  "$(/usr/libexec/PlistBuddy -c 'Print :NSHumanReadableCopyright' "$plist_additions")"
merge_key ITSAppUsesNonExemptEncryption bool  "$(/usr/libexec/PlistBuddy -c 'Print :ITSAppUsesNonExemptEncryption' "$plist_additions")"
cp "$PROVISION_PROFILE" "$app_path/Contents/embedded.provisionprofile"

# MAS reaches AI clients through the remote relay, served IN-PROCESS by the GUI
# (app/core/mcp_relay_client.py) — the MCP runtime is bundled into the GUI itself
# (see build_exe.spec). The spec still emits a second stdio-helper executable,
# Contents/MacOS/easypost-mcp, for the Store/direct builds; MAS neither needs nor
# can ship it. A second executable also breaks MAS validation: signing the .app
# only applies entitlements to the MAIN executable, so the helper would ship
# un-sandboxed (altool 90296). Remove it — the MAS bundle has exactly one
# executable, the GUI app.
rm -f "$app_path/Contents/MacOS/easypost-mcp"

echo "==> [4/6] Verify variant flags in the bundle"
MAS_SPIKE="${MAS_SPIKE:-0}" bash "$here/verify_mas_variant.sh" "$app_path"

# Strip extended attributes (Finder info, com.apple.metadata/provenance, and the
# xattrs the provisioning profile picks up coming through OneDrive). codesign
# refuses to seal a bundle that carries "resource fork / Finder information"
# detritus, so clear it recursively before signing.
echo "==> [4b/6] Strip extended attributes + fix readability"
xattr -cr "$app_path"
find "$app_path" -name '.DS_Store' -delete 2>/dev/null || true
# The installer must not contain files only root can read, or code-signature
# verification fails at install time (altool 90255). Ensure every file is
# world-readable (and dirs traversable), preserving the executable bit.
chmod -R a+rX "$app_path"

echo "==> [5/6] Sign inside-out (nested Mach-O first, bundle last; no --deep)"
# Sign every nested Mach-O (dylibs, .so, framework binaries), deepest paths
# first, each with the sandbox entitlements, then the two executables, then the
# outer .app. `codesign --deep` is unreliable for submission (brief §10), so we
# walk the tree explicitly. `-r -` = same-team requirement; --timestamp for a
# secure signature; -o runtime hardened runtime.
sign() { codesign --force --sign "$SIGN_APP_IDENTITY" --timestamp --options runtime \
                  --entitlements "$entitlements" "$@"; }

# Nested code, longest paths first so children are signed before their parents.
while IFS= read -r f; do
  # Skip the main app executables here; they are signed with the bundle below.
  case "$f" in
    "$app_path/Contents/MacOS/EasyPostDesktop"|"$app_path/Contents/MacOS/easypost-mcp") continue ;;
  esac
  if file "$f" | grep -q "Mach-O"; then sign "$f"; fi
done < <(find "$app_path/Contents/Frameworks" "$app_path/Contents/Resources" \
              -type f \( -name '*.dylib' -o -name '*.so' -o -perm -u+x \) 2>/dev/null | awk '{print length, $0}' | sort -rn | cut -d' ' -f2-)

# Any nested .framework bundles (sign the versioned bundle dir).
while IFS= read -r fw; do sign "$fw"; done < <(find "$app_path/Contents/Frameworks" -type d -name '*.framework' 2>/dev/null | awk '{print length, $0}' | sort -rn | cut -d' ' -f2-)

# Finally the app bundle itself (seals everything above).
sign "$app_path"
echo "    verifying signature..."
codesign --verify --strict --verbose=2 "$app_path"

echo "==> [6/6] Wrap in a signed installer .pkg"
productbuild --component "$app_path" /Applications \
             --sign "$SIGN_INSTALLER_IDENTITY" "$pkg_out"
echo "    built $pkg_out"

if [ "${SKIP_VALIDATE:-0}" != "1" ]; then
  echo "==> Validate against App Store Connect (needs Apple credentials in env)"
  echo "    Run one of the following yourself (they require your Apple ID):"
  echo "      xcrun altool --validate-app -f \"$pkg_out\" -t macos \\"
  echo "                   -u <apple-id> -p <app-specific-password>"
  echo "    or upload via Transporter.app, or:"
  echo "      xcrun altool --upload-app  -f \"$pkg_out\" -t macos -u <apple-id> -p <app-specific-password>"
fi

echo "Done. Bundle: $app_path   Installer: $pkg_out"
