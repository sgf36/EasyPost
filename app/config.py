"""App-wide paths and constants."""

import os
from pathlib import Path

import platformdirs

APP_NAME = "EasyPost Desktop"
APP_DIR_NAME = "EasyPostDesktop"
KEYRING_SERVICE_NAME = "EasyPostDesktop"

# The running build's marketing version. The direct-download update check
# (app/core/update_check.py) compares this against the latest GitHub release
# tag to decide whether to prompt the user to update. Keep it in step with the
# release tag and packaging/msix/AppxManifest.xml on every release.
APP_VERSION = "1.2.1"

ICON_PATH = Path(__file__).parent / "resources" / "icons" / "app_icon.png"

# The Paddle license gate is enforced ONLY in direct-download builds, which
# bundle this flag file. Store builds (e.g. the Microsoft Store MSIX) omit it,
# so those users are never asked for a license key on top of their store
# purchase. Create app/resources/license_required.flag before packaging a
# direct-download build (CI does this on the macOS leg).
LICENSE_REQUIRED = (Path(__file__).parent / "resources" / "license_required.flag").exists()

# The Microsoft Store build gates production behind a Store "Production unlock"
# add-on instead of a pasted Paddle licence key: test mode is free, production
# needs the in-app purchase. This flag marks that build so the entitlement is
# read from Windows.Services.Store (see app/core/store_entitlement.py) rather
# than the Ed25519 licence path. Mutually exclusive with LICENSE_REQUIRED — a
# build is either the direct-download one or the Store one, never both.
# packaging/build_msix.py writes app/resources/store_build.flag into the MSIX.
STORE_BUILD = (Path(__file__).parent / "resources" / "store_build.flag").exists()

# Where multi-computer and organisation buyers are sent from inside the Store
# build. The Store add-on unlocks a single computer; seat-managed multi-device
# and team licences live on the website, so the Store unlock screen links here.
MULTI_SEAT_URL = "https://easy-post.spencerfields.com/pricing.html"

# Backend for pairing the Easy-Post Mobile Companion. The desktop shows a QR the
# phone scans; the phone then pairs against this proxy, which stores only an
# encrypted copy of the production key that it cannot read without the phone's
# key. See server/easypost-mobile-proxy and MOBILE-COMPANION-BUILD-BRIEF.md.
PAIR_PROXY_URL = "https://easypost-mobile-proxy.sgf36.workers.dev"

# Direct Android APK download, offered from Tools > Android app while the Google
# Play listing is still pending (organisation-account verification / DUNS). The
# page is shown only on direct-download builds (never the Microsoft Store or Mac
# App Store builds, which forbid linking to off-store app downloads) and only to
# production-licence holders, since the companion pairs with the production
# account. The "latest" URL always resolves to the newest release's asset of this
# name, so shipping a new APK is a new GitHub release, not a code change.
ANDROID_APK_URL = (
    "https://github.com/sgf36/Easy-Post-Mobile-Companion/releases/latest/download/"
    "easypost-mobile-companion.apk"
)
# SHA-256 of the currently published APK, shown so a sideloaded download can be
# verified before installing. Update this whenever a new APK release is cut.
ANDROID_APK_SHA256 = "bf6aa6a8ff5e504786569f5c4e7b4828cfe9763611be2874051eacab04359734"

# The Mac App Store build gates production behind a StoreKit In-App Purchase
# ("Production Unlock") instead of a pasted Paddle key or a Windows Store add-on.
# Apple mandates StoreKit for a Mac App Store sale, so this is the macOS analogue
# of STORE_BUILD: the entitlement is read from StoreKit (see
# app/core/mac_store_entitlement.py), with no pasted key. Marked by a flag file
# packaging/mas/build_mas.sh writes into the .app bundle. Mutually exclusive with
# LICENSE_REQUIRED and STORE_BUILD — a build is exactly one channel.
MAS_BUILD = (Path(__file__).parent / "resources" / "mas_build.flag").exists()

# The AI-agent (MCP) bridge. Direct-download builds gate it on their own flag
# (created at package time); the Store build supports it too — hence the
# `or STORE_BUILD`.
#
# The Store build reaches parity through two MSIX mechanisms that sidestep the
# original packaging worries. The helper process is not launched from the
# ACL-locked, version-stamped install path: it is exposed as an App Execution
# Alias (`easypost-mcp.exe` on PATH — see packaging/msix/AppxManifest.xml and
# app/core/mcp_clients.server_command), launched with the package's own
# identity so its keyring and app data line up with the GUI. And connecting a
# client never depends on a redirected cross-package write: the copy-paste
# snippet in Tools > Connect AI agents always works, with the auto-write kept
# as a convenience.
#
# The Mac App Store build reaches parity through the remote MCP relay
# (server/mcp-relay-worker): the sandboxed app itself is the MCP backend, dialled
# by the AI client through a Cloudflare Worker over an *outbound* WebSocket the
# app opens (only network.client is needed — already entitled). No companion
# helper, no shared container. Hence `or MAS_BUILD`.
MCP_SUPPORTED = (
    (Path(__file__).parent / "resources" / "mcp_supported.flag").exists()
    or STORE_BUILD
    or MAS_BUILD
)

# The deployed remote MCP relay (see server/mcp-relay-worker/README.md). The app
# opens an outbound WebSocket to <MCP_RELAY_URL>/connect and serves MCP over it;
# the AI client reaches the app at <MCP_RELAY_URL>/mcp using a per-app pairing
# token generated in Tools > Connect AI agents.
MCP_RELAY_URL = "https://easypost-mcp-relay.sgf36.workers.dev"

# Normally the per-user application data directory. The environment override
# exists so a tool can run the real app against a throwaway directory —
# packaging/make_screenshots.py uses it to render store screenshots from seeded
# demo data without touching (or publishing) the user's own database.
APP_DATA_DIR = Path(
    os.environ.get("EASYPOST_DESKTOP_DATA_DIR")
    or platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False)
)
DATABASE_PATH = APP_DATA_DIR / "easypost_desktop.sqlite3"
SETTINGS_PATH = APP_DATA_DIR / "settings.json"

MODE_TEST = "test"
MODE_PRODUCTION = "production"


def ensure_app_data_dir() -> Path:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DATA_DIR
