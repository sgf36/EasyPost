# Mac App Store build — session brief for Claude Code (run this ON the Mac)

**You are a Claude Code session running on macOS.** Your job is to build a Mac
App Store (MAS) edition of **Easy-Post Desktop** — an existing, shipping
Python/PySide6 desktop client for the EasyPost shipping API. This file is your
brief. Read it in full, then read the companion strategy document
`MACOS-APP-STORE-PLAN.md` in this same repo (it has the reasoning behind every
decision here). This brief is the *how*; the plan is the *why*.

The project owner has **explicitly waived** the "wait for macOS demand before
building" recommendation in the plan. Proceed with the build now.

> **Why this brief exists:** the code was developed on a Windows machine, where a
> MAS app cannot be compiled, sandboxed, signed or submitted. Everything that is
> platform-independent has been designed and is ready to mirror; the macOS-native
> and Apple-portal work is what you (on the Mac) now do. Nothing here has been
> started yet in code — you are creating the MAS variant from scratch, modelled
> exactly on the already-working Microsoft Store variant.

---

## 0. Orientation

- **Repo:** `github.com/sgf36/EasyPost` (private), default branch `main`. It is
  MIT-licensed and open source.
- **App:** `Easy-Post Desktop` — Python 3.14 + PySide6 (Qt) GUI. It drives the
  user's *own* EasyPost account (their API key). Features: rate shopping, buy +
  print labels (PNG/PDF/ZPL/EPL), tracking, address book + verification, customs
  + HTS lookup, insurance, pickups, claims, batch CSV, history/reporting, 50
  languages, and (on some builds) real-time push + an MCP AI-agent bridge.
- **Business model:** free to install and fully usable in EasyPost **test mode**;
  a one-time **~$29.99** unlock enables **production** (real labels). The app is
  never a reseller — postage is billed by EasyPost directly.
- **Existing distribution channels and how each gates production:**
  - **Direct download** (`.dmg` notarized on macOS / `.exe` on Windows): gated by
    an **Ed25519 licence key** (Paddle purchase → Cloudflare Worker mints key →
    seat activation). Marked by `app/resources/license_required.flag`.
  - **Microsoft Store** (MSIX): gated by a **Store add-on** "Production unlock"
    read from `Windows.Services.Store`. Marked by
    `app/resources/store_build.flag`.
  - **Dev / unflagged:** production unrestricted.
- **Your target — a THIRD channel:** the **Mac App Store**, gated by a **StoreKit
  In-App Purchase** (Apple mandates this; the Paddle/Ed25519 path may not be used
  for a MAS sale). This is the direct macOS analogue of the Microsoft Store
  variant — **mirror that variant's architecture exactly.**

The Microsoft Store variant is your template. Study these files first; you are
building the macOS twin of each:

| Windows Store file | What it does | Your macOS twin |
|---|---|---|
| `app/config.py` → `STORE_BUILD` flag | detects the Store build via a flag file | add `MAS_BUILD` |
| `app/core/store_entitlement.py` | reads the add-on entitlement from WinRT; grace-first; safe off-Store | `app/core/mac_store_entitlement.py` (StoreKit) |
| `app/core/license.py` → `production_allowed()` | routes the gate by build type | add a `MAS_BUILD` branch |
| `app/ui/views/store_unlock.py` → `StoreUnlockGate` | Buy / Restore / multi-seat gate UI | reuse it, or a thin `MacStoreUnlockGate` |
| `app/ui/main_window.py` gate wiring | picks the gate + `_production_ok()` | add the `MAS_BUILD` branch |
| `packaging/build_msix.py` + `packaging/msix/AppxManifest.xml` | builds + declares the Store package | `packaging/mas/` (entitlements, Info.plist, build script) |

---

## 1. Environment preflight

Run these and confirm before touching code:

```bash
sw_vers                       # macOS 12+ expected
xcodebuild -version           # Xcode present (needed for signing/productbuild/StoreKit)
xcrun --find productbuild     # part of the MAS packaging chain
python3 --version             # 3.11+ (repo developed on 3.14)
git clone https://github.com/sgf36/EasyPost.git && cd EasyPost
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q    # establish a green baseline BEFORE changing anything
```

If `pytest` is not green on a clean checkout, stop and report — do not build on a
broken baseline.

Also inspect the **existing macOS build** so you reuse its identity and layout,
not invent a parallel one:

```bash
sed -n '1,200p' packaging/build_exe.spec       # PyInstaller spec (Win + mac)
cat .github/workflows/build.yml                 # the macos-latest leg: notarized .dmg
grep -rn "CFBundleIdentifier\|bundle_identifier\|com\." packaging/ .github/ || true
```

Note the **existing CFBundleIdentifier** used by the notarized `.dmg`. Prefer to
**reuse it** for the MAS app (a bundle id can front both a Developer-ID build and
a MAS build). If none exists, use `com.spencerfields.EasyPostDesktop`.

---

## 2. Prime directive and guardrails

1. **Mirror the Microsoft Store variant.** Same contracts, same method names,
   same grace-first behaviour, same "degrade safely, never raise" discipline. If
   in doubt, open `store_entitlement.py` and do the StoreKit equivalent.
2. **Never break the other builds.** The MAS code path must be reachable *only*
   when `MAS_BUILD` is true. Importing any new module on Windows / direct /
   dev builds must be a no-op. StoreKit imports must be lazy and guarded exactly
   as `store_entitlement.py` guards `winrt` (try/except, return "locked" /
   "unavailable" rather than raising).
3. **Keep the full test suite green** after every change
   (`python -m pytest tests/ -q`). Add tests for the new gating (mirror
   `tests/test_store_entitlement.py`, `tests/test_production_gate.py`,
   `tests/test_mcp_clients_store.py`).
4. **Fail toward the paying customer.** As in the Windows path: a Store/network
   outage must not lock a payer out — trust a recently-confirmed unlock for a
   grace window.
5. **Commit in small, described steps** (the repo uses conventional commits:
   `feat(mas): …`, `build(mas): …`, `docs: …`). End commit messages with
   `Co-Authored-By: Claude <noreply@anthropic.com>`. Do not push or open PRs
   unless the owner asks; if you must branch, branch off `main`.

---

## 3. Apple-account / human prerequisites (NOT codeable — the owner or you-in-a-browser must do these)

The owner already has an **Apple Developer Program** membership (used for the
Developer ID cert + notarised `.dmg`), so there is no new annual fee. These
portal actions must exist before a MAS build can be signed or submitted. Confirm
each with the owner; several need their Apple ID login and cannot be scripted.

1. **App ID / bundle identifier** registered in the Apple Developer portal
   (reuse the existing one if there is one).
2. **App Store Connect app record** (macOS) for that bundle id: name, category,
   privacy details, screenshots, and **App Review notes** (see §8).
3. **In-App Purchase product** in App Store Connect:
   - Type: **Non-Consumable**
   - Reference name: `Production Unlock`
   - Product ID: **`production_unlock`** (reuse the Windows token for
     consistency — this is what your entitlement check will match)
   - Price: the tier nearest **$29.99**
   - Localised display name + description (reuse the Store add-on copy already
     written for Windows).
4. **Certificates:** `Apple Distribution` and `Mac Installer Distribution`
   (created via Xcode → Settings → Accounts → Manage Certificates, or the portal).
5. **Provisioning profile:** a **Mac App Store** profile for the bundle id,
   embedding the entitlements in §5. Xcode-managed signing can generate this.
6. **Small Business Program** enrolment (optional but recommended — drops
   commission to 15%).

> When you (the Mac session) hit any of these, and it needs an Apple ID login or
> a browser portal action, **stop and hand it to the owner** with the exact steps
> — do not attempt to enter their Apple credentials.

---

## 4. What to build in code (the platform-independent majority)

Do these in order. Each mirrors a named Windows-Store file.

### 4a. `MAS_BUILD` flag + feature toggles — `app/config.py`

Mirror the `STORE_BUILD` block. Add:

```python
# The Mac App Store build gates production behind a StoreKit In-App Purchase
# ("Production Unlock") instead of a pasted Paddle key or a Windows Store add-on.
# Marked by a flag file the MAS packaging step writes into the .app bundle.
# Mutually exclusive with LICENSE_REQUIRED and STORE_BUILD.
MAS_BUILD = (Path(__file__).parent / "resources" / "mas_build.flag").exists()
```

Then make the sandbox-hostile / MAS-disallowed features conditional:

- **Real-time push / `cloudflared` tunnel** (`app/core/tunnel.py`,
  `app/core/webhook_manager.py`, its Settings section): the App Sandbox forbids
  spawning `cloudflared` and downloading a binary (guideline 2.5.2). For **MAS
  v1, disable it** — hide the "Real-time tracking (advanced)" Settings section
  and never auto-start it when `MAS_BUILD`. Polling remains (it is the default
  fallback), so tracking still works. (Phase 2 restores push via an outbound
  relay — see the plan doc §3.1; out of scope for v1.)
- **Donation banner** (`app/ui/widgets/donation_banner.py`, Stripe): App Review
  3.2.2 forbids for-profit apps soliciting donations via external links.
  **Do not show it when `MAS_BUILD`.**
- **In-app updater** (if any): the Mac App Store delivers updates. Disable any
  self-update path when `MAS_BUILD`.
- **MCP AI-agent bridge** (`app/config.MCP_SUPPORTED`, `app/core/mcp_clients.py`):
  a sandboxed MAS app cannot install a CLI on PATH or write another app's config.
  For **MAS v1, leave MCP OFF** (do not set it for `MAS_BUILD`). Phase 3 restores
  it via a hosted remote MCP or a separately-distributed helper (plan doc §3.2).
- **Label save** (wherever labels are written to disk): under the sandbox, write
  to the app container or via an `NSSavePanel`/Qt save dialog (user-selected
  path), and prefer `~/Downloads` (granted by the downloads entitlement). Audit
  the label-save path and ensure it does not write to arbitrary locations
  silently when `MAS_BUILD`.

### 4b. StoreKit entitlement — `app/core/mac_store_entitlement.py`

This is the heart of the work. **Replicate the public contract of
`app/core/store_entitlement.py` exactly**, so `license.py` and `main_window.py`
wire to it symmetrically:

Required public surface (same names/semantics as the Windows module):

```python
IN_APP_OFFER_TOKEN = "production_unlock"   # must match the App Store Connect Product ID
STORE_UNLOCK_GRACE_DAYS = 30

class PurchaseResult(Enum):
    PURCHASED, NOT_PURCHASED, UNAVAILABLE, ERROR

def production_unlocked() -> bool: ...      # grace-first routine gate
def refresh_entitlement() -> bool: ...      # force a live check (Restore / post-purchase)
def purchase_unlock(hwnd=None) -> PurchaseResult: ...   # drive the StoreKit purchase
def store_listing_uri() -> str: ...         # macappstore:// deep link fallback
```

Reuse the **same grace mechanism** as the Windows module: persist
`store_unlock_confirmed_at` in `app/core/settings.py` (the field already exists —
reuse it, or add `mac_unlock_confirmed_at` if you prefer a distinct one) and
trust it for `STORE_UNLOCK_GRACE_DAYS`. Copy the `_grace_active` / `_stamp_confirmed`
/ `_clear_confirmed` helpers verbatim in spirit.

**The StoreKit call itself** — decide between two approaches and record the choice
in the module docstring:

- **(A) StoreKit 1 via PyObjC** (`pip install pyobjc-framework-StoreKit`):
  `SKProductsRequest`, `SKPaymentQueue`, an `SKPaymentTransactionObserver`, and
  on-device receipt inspection at `Bundle.main.appStoreReceiptURL`
  (refresh with `SKReceiptRefreshRequest` when absent). Objective-C API, fully
  reachable from PyObjC, no separate binary. Simplest for one non-consumable.
- **(B) A tiny embedded Swift/Obj-C helper** that uses **StoreKit 2**
  (`Transaction.currentEntitlements`, `Product.purchase()`), bundled in the
  `.app`, invoked by the Python layer, reporting entitlement/purchase result via
  stdout/JSON. Cleaner API, but adds a native build step and a signed nested
  binary.

**Recommendation:** start with **(A)** for v1 (least moving parts). Whichever you
pick, everything **must degrade safely** when not running as a real MAS-installed
app (dev run, missing framework): `production_unlocked()` → `False`,
`purchase_unlock()` → `UNAVAILABLE`, never raise — exactly like the WinRT guards.

> **Receipt-validation note:** for a single non-consumable, "does the receipt /
> current entitlements contain `production_unlock`?" is the whole check. Do not
> over-build server-side validation for v1; on-device is acceptable and matches
> the Windows module's local-read approach. Keep the grace window so a first
> launch offline (before the receipt is fetched) does not wrongly lock a payer.

### 4c. Route the gate — `app/core/license.py` → `production_allowed()`

Add the MAS branch alongside the existing `LICENSE_REQUIRED` / `STORE_BUILD`
branches:

```python
if MAS_BUILD:
    from app.core.mac_store_entitlement import production_unlocked
    return production_unlocked()
```

Import `MAS_BUILD` from `app.config`. Keep the lazy import (StoreKit touched only
on the MAS build).

### 4d. Gate UI — `app/ui/main_window.py`

The existing code does:
`self._license_gate = StoreUnlockGate() if STORE_BUILD else LicenseGate()`.
Extend to pick the MAS gate when `MAS_BUILD`. **`StoreUnlockGate` is already
generic** (Buy / Restore / multi-seat-to-website / continue-in-test, emitting
`activated` / `use_test_requested`) and calls into `store_entitlement`. Cleanest
path: parameterise it (or subclass a thin `MacStoreUnlockGate`) so it imports
`mac_store_entitlement` instead of `store_entitlement` when `MAS_BUILD`. Do the
same in `_production_ok()` (add the `MAS_BUILD` branch calling
`mac_store_entitlement.production_unlocked()`).

Reuse the existing `store_unlock.*` i18n keys (already translated into 50
languages) — the copy ("unlock production", "restore purchase", "continue in
test mode", multi-seat link) applies verbatim. Only add new keys if wording must
differ for Apple (e.g. "Restore Purchases" is the App Store convention — check
Apple's terminology guidance).

### 4e. Settings, machine-id, misc

- `app/core/settings.py`: reuse `store_unlock_confirmed_at` (or add
  `mac_unlock_confirmed_at`). No secret storage needed — StoreKit owns the
  entitlement.
- `app/core/activation.py` `machine_id()` shells out to `ioreg` via `subprocess`.
  **Under the sandbox this may be blocked.** On the MAS build the seat-ledger
  activation is NOT used (StoreKit owns entitlement), so `machine_id()` should
  never be called — verify that, and if any MAS path does reach it, replace the
  subprocess with an IOKit call via `ctypes`/PyObjC. Do **not** ship the
  Ed25519/seat-activation flow in the MAS build.

---

## 5. Packaging, entitlements, signing (macOS-native — do on the Mac)

Create `packaging/mas/`:

### 5a. `packaging/mas/EasyPostDesktop.entitlements`

Minimum viable set (the plan doc §5 explains each). The push relay being out of
v1 means **no** `network.server` is needed:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.app-sandbox</key><true/>
  <key>com.apple.security.network.client</key><true/>
  <key>com.apple.security.files.user-selected.read-write</key><true/>
  <key>com.apple.security.files.downloads.read-write</key><true/>
  <key>com.apple.security.print</key><true/>
</dict></plist>
```

Add `keychain-access-groups` only if the credential store (`keyring` → Keychain)
needs an explicit group under the sandbox (test this — the app's own items
usually work without it).

### 5b. `packaging/mas/Info.plist` additions

`CFBundleIdentifier` (the registered id), `CFBundleShortVersionString`
(align with the current app version — Windows is at `1.0.6.0`; use `1.0.6` for
macOS), `LSMinimumSystemVersion` `12.0`, `LSApplicationCategoryType`
(`public.app-category.business` or `productivity`), and the usual bundle name/
display-name keys.

### 5c. `packaging/build_mas.sh` (runs on macOS)

Author it now even though it executes on the Mac. Steps:
1. Write `app/resources/mas_build.flag` (and ensure `license_required.flag` /
   `store_build.flag` are absent — mutually exclusive; mirror
   `verify_store_variant` in `build_msix.py` with a `verify_mas_variant` guard).
2. Build the `.app` (evaluate **py2app** for a cleaner MAS-signable bundle vs the
   existing PyInstaller spec — see §6/§10; PyInstaller `.app` output often needs
   extra re-signing of nested dylibs).
3. **Sign every nested framework/dylib**, then the `.app`, with the
   `Apple Distribution` identity and the entitlements plist and embedded
   provisioning profile (`codesign --deep` is discouraged — sign inside-out).
4. Wrap in a signed installer:
   `productbuild --component "Easy-Post Desktop.app" /Applications --sign "3rd Party Mac Developer Installer: …" EasyPostDesktop.pkg`.
5. Validate/upload with Transporter or
   `xcrun altool --upload-app -f EasyPostDesktop.pkg -t macos -u <appleid> -p <app-specific-pw>`.

Keep this **separate** from the existing notarized-`.dmg` lane — MAS is a
distinct pipeline. A CI lane can come later; local build first.

---

## 6. DO THIS FIRST — the Phase 0 spike (de-risk PySide6 on MAS)

**Before building any of §4/§5 in earnest**, prove the single make-or-break
unknown: *can a sandboxed PySide6/Python `.app` clear Apple's pipeline at all?*
Apple's static analysis rejects private-API use, and Qt/Python MAS bundles are
uncommon.

Spike:
1. Build the *current* app (no MAS code yet) as a sandboxed `.app` with the §5a
   entitlements, signed with the MAS identities + provisioning profile.
2. `productbuild` → `.pkg`, upload to App Store Connect, get it onto
   **TestFlight** (internal).
3. If it installs and launches from TestFlight sandboxed → **the platform risk is
   retired**; proceed to §4/§5.
4. If Apple's validation rejects it (private API, signing, sandbox) → **stop and
   report the exact errors to the owner** with options (py2app vs PyInstaller,
   Qt version bump, or reconsidering). This is the cheap answer to the only
   question that can sink the effort — get it before investing in StoreKit.

---

## 7. StoreKit testing

- Local: add a **StoreKit Configuration file** (Xcode) to exercise
  purchase/restore without real money, or use a **Sandbox Apple ID** tester.
- End-to-end: verify **buy → production unlocks**, **restore on a second
  launch/machine**, and **offline grace** (first launch offline must not lock a
  confirmed owner out — mirror the Windows grace test).
- Confirm the gate's "Continue in test mode" path always leaves a free, working
  app (the app must be fully functional in EasyPost test mode with no purchase).

---

## 8. Submission (App Review)

- **Screenshots** + description: reuse the existing Store copy; make clear it
  drives the user's **own third-party EasyPost account** and is not affiliated
  with EasyPost.
- **Review notes:** explain that an EasyPost account + API key are required, and
  give the reviewer a **test-mode API key** (or clear instructions to obtain a
  free one) so they can exercise the app without buying postage. State that the
  IAP unlocks production (real) label purchasing on the reviewer's own account.
- **Privacy:** data stays on device; the only network calls are to the user's
  EasyPost account, the USITC HTS endpoint, and Apple StoreKit. No analytics/
  telemetry. Fill the App Privacy questionnaire accordingly.
- Submit the `.pkg` via Transporter; respond to any review feedback.

---

## 9. Verification checklist (Definition of Done for v1)

- [ ] `pytest tests/ -q` green, including new MAS-gating tests.
- [ ] Non-MAS builds unaffected: importing `mac_store_entitlement` on a non-MAS
      run is a no-op; Windows/direct/dev gating unchanged.
- [ ] Phase 0 spike passed (sandboxed PySide6 `.app` accepted to TestFlight).
- [ ] MAS build: free + full test mode with no purchase; IAP unlocks production;
      Restore works; offline grace works.
- [ ] Tunnel/push, donation banner, in-app updater, and MCP are OFF under
      `MAS_BUILD`; label-save is sandbox-safe.
- [ ] `.pkg` validates and uploads to App Store Connect.
- [ ] Docs updated (`MACOS-APP-STORE-PLAN.md` status, README MAS section).

---

## 10. Known traps

- **PyInstaller vs py2app:** PyInstaller `.app` output frequently needs manual
  re-signing of nested dylibs/frameworks for MAS and can trip validation. Try
  py2app for the MAS lane if PyInstaller fights you.
- **`codesign --deep` is unreliable for submission** — sign nested code
  inside-out, then the outer bundle.
- **Private-API rejection** is the classic Qt/MAS failure — that is exactly what
  the §6 spike flushes out early.
- **StoreKit 2 is Swift-async** and awkward from PyObjC; StoreKit 1 (Obj-C) is
  the pragmatic PyObjC path for one non-consumable (approach A).
- **Sandbox + `subprocess`:** `ioreg`/`cloudflared`/any spawned binary is
  restricted — the MAS build must not rely on them (it doesn't, if you follow §4).
- **Mutually-exclusive flags:** never ship `mas_build.flag` alongside
  `store_build.flag` or `license_required.flag`; add a `verify_mas_variant` guard
  like `build_msix.py` has.

---

## 11. Hand back to the owner

Anything requiring their Apple ID / App Store Connect / a payment or portal
decision (see §3), plus the §6 spike result. Report progress in terms of the
§9 checklist. If the spike fails, that is a legitimate and valuable outcome —
surface it clearly rather than working around it.

**Kick-off for this session:** confirm §1 preflight (green baseline), then run
the §6 Phase 0 spike. Do not start §4 code until the spike outcome is known.
