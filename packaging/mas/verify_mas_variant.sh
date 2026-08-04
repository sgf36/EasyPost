#!/usr/bin/env bash
# Fail the MAS build if the variant flags in the packaged .app are wrong.
#
# Mirrors packaging/verify_variant_flags.sh (the direct-download guard) and the
# verify_store_variant check in build_msix.py. The Mac App Store build gates
# production behind a StoreKit In-App Purchase, so it must ship its OWN flag and
# must NOT carry any other variant's flag (they are mutually exclusive — see
# app/config.py). Shipping the wrong flag would gate production behind a channel
# this bundle cannot sell through.
#
# Usage: verify_mas_variant.sh <bundle root>   (e.g. dist/EasyPostDesktop.app)
#
# In SPIKE mode the current app is packaged with NO MAS code and NO mas flag
# (brief §6 builds the vanilla app to test the platform). Set MAS_SPIKE=1 to
# assert the inverse: no variant flag of any kind is present.
set -euo pipefail

root="${1:?usage: verify_mas_variant.sh <bundle root>}"
[ -d "$root" ] || { echo "::error::$root does not exist"; exit 1; }

find_flag() { find "$root" -name "$1" -print -quit 2>/dev/null || true; }

# Flags that must never coexist with the MAS build.
for stray in license_required.flag store_build.flag mcp_supported.flag; do
  hit="$(find_flag "$stray")"
  if [ -n "$hit" ]; then
    echo "::error::$stray leaked into the MAS bundle: $hit"
    echo "::error::MAS gates production via StoreKit; a foreign variant flag would misroute the gate."
    exit 1
  fi
done

mas_hit="$(find_flag mas_build.flag)"

if [ "${MAS_SPIKE:-0}" = "1" ]; then
  if [ -n "$mas_hit" ]; then
    echo "::error::MAS_SPIKE=1 but mas_build.flag is present: $mas_hit"
    echo "::error::The Phase 0 spike must package the vanilla app, no MAS flag."
    exit 1
  fi
  echo "Spike bundle OK: no variant flags present, as required for the §6 spike."
  exit 0
fi

if [ -z "$mas_hit" ]; then
  echo "::error::mas_build.flag missing from $root — the MAS build would launch with production unrestricted."
  exit 1
fi
echo "  ok      mas_build.flag  ->  $mas_hit"
echo "MAS variant flags are correct: mas_build.flag present, no foreign flags."
