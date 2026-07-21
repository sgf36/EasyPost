#!/usr/bin/env bash
# Fail the build if the direct-download variant flags did not make it into the
# packaged bundle.
#
# This exists because they silently did not, for the whole life of the licence
# feature: CI created app/resources/*.flag in the source tree, but the
# PyInstaller spec never collected them, so LICENSE_REQUIRED evaluated False in
# the shipped app and the paid build launched with no licence gate. Nothing
# about that was visible without unpacking the bundle and looking.
set -euo pipefail

root="${1:?usage: verify_variant_flags.sh <bundle root>}"
[ -d "$root" ] || { echo "::error::$root does not exist"; exit 1; }

missing=0
for flag in license_required.flag mcp_supported.flag; do
  found="$(find "$root" -name "$flag" -print -quit 2>/dev/null || true)"
  if [ -n "$found" ]; then
    echo "  ok      $flag  ->  $found"
  else
    echo "  MISSING $flag"
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  echo "::error::Variant flags were not collected into $root."
  echo "::error::The direct-download build would ship without its licence gate."
  exit 1
fi
echo "Both variant flags are present in the bundle."
