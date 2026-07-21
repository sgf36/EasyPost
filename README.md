# EasyPost Desktop

A cross-platform (Windows/macOS) desktop app for shipping through
[EasyPost](https://www.easypost.com/): rate shopping, labels, tracking,
address verification, refunds, insurance, pickups, claims, and batch
shipping — all against your own EasyPost account.

Product site: **[easy-post.spencerfields.com](https://easy-post.spencerfields.com)**

> **Status.** The source is complete and CI-green on Windows and macOS. The
> two distribution channels are at different stages — see
> [Distribution](#distribution) for exactly what is and is not ready.

## Contents

- [First-time setup](#first-time-setup) — running from source
- [Features](#features)
- [Distribution](#distribution) — the two channels and how they differ
- [Licensing](#licensing-direct-downloads-only) — how the offline licence gate works
- [Tracking updates](#tracking-updates-polling-vs-real-time-webhook-push)
- [Running tests](#running-tests)
- [Building a standalone app](#building-a-standalone-app)
- [Repository layout](#repository-layout)

## First-time setup

```
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m app.main
```

(On macOS/Linux: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m app.main`)

On first launch you'll be asked to paste your EasyPost API key(s). They're
stored via your OS's native credential vault (Windows Credential Manager /
macOS Keychain / Linux Secret Service, via the `keyring` library) — never
written in plain text, never sent anywhere but EasyPost's API.

Use your **test** key first. A banner at the top of the app always shows
which mode (test/production) is active, and any action that spends real
money asks for confirmation while in production mode.

## Features

- **Address Book** — verify and save addresses via EasyPost, with a
  filterable country dropdown (197 countries, type to narrow the list) that
  relabels the state/postal fields to match local convention (Province,
  County, Prefecture, Postal Code, etc.). If EasyPost can't verify an
  address, you're asked whether to save it anyway rather than being blocked.
  Saved addresses can be edited in place — EasyPost addresses are immutable,
  so editing re-verifies as a new address and replaces the old one locally.
- **Create Shipment** — shop live carrier rates and buy/save/print labels.
  Rates are sorted cheapest-first and tagged "Cheapest"/"Fastest", each row
  carrying a colour-coded carrier chip, and the purchased label is rendered
  in-app beside the rate list rather than only opening in a browser.
  International shipments prompt for full customs information (contents
  type, itemized customs items, signer/certification) before rates can be
  fetched, since carriers otherwise reject the label purchase outright.
  Packages can be entered as custom dimensions, saved as a named preset for
  reuse, or picked from each carrier's real predefined packages (USPS flat
  rate boxes, FedEx envelopes, etc.), fetched live from EasyPost.
- **Quick price check** — rate a route from two postal codes alone, before
  any address has been saved. Quotes are deliberately not purchasable:
  carriers need a complete recipient address to issue a label, so the Buy
  button stays disabled until you switch back to full addresses.
- **Label format and size** — choose PNG, PDF, ZPL or EPL and the printed
  size (4x6, 4x7, 4x8, 4x5, 8.5x11) in Settings, per
  [EasyPost's supported sizes](https://support.easypost.com/hc/en-us/articles/360044915671-Shipping-Label-Sizes).
  Only sizes that make sense for the chosen format are offered, and the
  carrier caveats (UPS defaults to 4x7; LaserShip/OnTrac are ZPL-only) are
  shown alongside. Applies to single and batch shipments; carriers fix the
  size at shipment creation, so it never alters labels already bought.
- **HTS Lookup** — search the U.S. International Trade Commission's live
  Harmonized Tariff Schedule database for customs codes, with results
  cached locally so repeat searches work even if that API is unreachable.
- **Tracking** — add tracking numbers, auto-refreshed every 5 minutes by
  default, or instantly via an opt-in real-time webhook push — see below.
- **History** — browse purchased shipments, request refunds, add insurance.
- **Insurance** — insure a shipment bought outside EasyPost by tracking code.
- **Pickups** — schedule/buy/cancel carrier pickups for purchased shipments.
- **Claims** — file and track insurance claims for lost/damaged/stolen packages.
- **Batch Shipments** — import a CSV of recipients, validate, bulk rate + buy,
  generate combined labels.
- **Reports** — local spend-by-carrier chart, label counts, refund breakdown.
- **50 languages** — pick one in Settings; restart to apply. Translations are
  AI-generated (not professionally reviewed) — open an issue if something
  reads wrong.
- **Support the project** — an optional, dismissible donation banner (and a
  permanent link in Settings) points to a Stripe-hosted "pay what you want"
  page. Purely optional; nothing in the app depends on it.

## Distribution

The app ships through two channels, and they are deliberately **not** the
same build.

| | Microsoft Store | Direct download |
|---|---|---|
| Package | `.msix` | `.dmg` (macOS), `.exe` folder (Windows) |
| Price | Set in Partner Center | $29 one-time via Paddle |
| Licence gate | **Off** | **On** |
| Signing | Store re-signs on publish | Apple Developer ID + notarization |
| Status | Draft; blocked on payout profile | Blocked on notarization + Paddle approval |

The licence gate is compiled in or out by the presence of
`app/resources/license_required.flag`, which CI creates on the macOS leg
only (see `.github/workflows/build.yml`). The Store build omits it, because
gating a Store purchase behind a second paid unlock would breach Microsoft's
policies — and would be a poor experience regardless. `app/config.py` reads
the flag once at import into `LICENSE_REQUIRED`.

### Why direct download exists at all

Selling a $29 licence through an app store costs 15–30% in commission.
Selling the same licence directly, with Paddle as Merchant of Record, costs
roughly 5% + 50c and keeps Paddle responsible for VAT/GST registration and
remittance worldwide. Apple's Guideline 3.1.1 forbids *unlocking* App Store
app functionality with an externally-bought key, which is why the licence
gate is scoped strictly to builds distributed outside any store.

## Licensing (direct downloads only)

Licence keys are **Ed25519-signed and verified entirely offline** — the app
never phones home, and there is no activation server to go down or to leak
customer data.

```
EPD1.<base64url(payload)>.<base64url(signature)>
payload = {"v":1,"product":"easypost-desktop","email":…,"order":…,"iat":…}
```

- `app/core/license.py` holds the **public** key and verifies. The private
  key never ships.
- `tools/issue_license.py` mints keys by hand — the manual fallback, and how
  refunds/replacements get handled.
- `server/paddle-license-webhook-worker/` is a Cloudflare Worker that turns a
  completed Paddle transaction into an emailed key automatically. It verifies
  Paddle's HMAC signature, checks the price id, mints, and sends via Resend.
  Deployed and live; see its own README for the four secrets it needs.

Because `iat` is taken from the Paddle event's `occurred_at`, a webhook retry
mints a byte-identical key rather than a second one.

## Tracking updates: polling vs. real-time webhook push

By default, tracking updates are pulled by polling EasyPost every 5 minutes
(or on demand via "Refresh all now") — this always works, no setup required,
and stays on regardless of the option below.

For instant push updates instead of waiting on the poll, Settings has an
**opt-in, off-by-default** "Real-time tracking (advanced)" toggle. Turning
it on:

1. Starts a local HTTP server bound to `127.0.0.1` only (never exposed
   directly on your LAN).
2. Opens a [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
   (`cloudflared tunnel --url ...`) — zero signup, an anonymous
   `https://*.trycloudflare.com` URL is what actually makes the local server
   internet-reachable; the tunnel connects out to it, so the port is never
   opened on your router/firewall.
3. Registers (or updates) an EasyPost webhook pointed at that URL, using a
   locally-generated secret (stored in your OS credential vault) to verify
   every incoming request's HMAC signature via the SDK's built-in
   `easypost.util.validate_webhook` — unsigned or mis-signed requests are
   rejected with 401 before touching anything.

**Requires `cloudflared` installed and on your PATH** — the app deliberately
does not auto-download and execute a fetched binary. Install it with:
- Windows: `winget install --id Cloudflare.cloudflared`
- macOS: `brew install cloudflared`
- Linux: see [Cloudflare's install docs](https://pkg.cloudflare.com/index.html) for your distro, or grab a release binary from [github.com/cloudflare/cloudflared/releases](https://github.com/cloudflare/cloudflared/releases)

If `cloudflared` isn't found, Settings shows the install command instead of
silently failing. Since Cloudflare Quick Tunnels are an anonymous, best
-effort service (not a guaranteed-uptime product) and the URL changes every
time the tunnel restarts, the app re-registers the webhook's URL with
EasyPost on every launch while this is enabled. Turning the toggle back off
deletes the EasyPost webhook registration for a clean teardown; merely
closing the app leaves it registered against a now-dead URL until you either
relaunch (which re-points it) or explicitly disable it.

## Running tests

```
.venv\Scripts\python.exe -m pytest tests/ -v
```

To also run the live end-to-end smoke test against EasyPost's test mode
(safe — no real carrier charges):

```
$env:EASYPOST_TEST_API_KEY = "test_..."
.venv\Scripts\python.exe -m pytest tests/smoke_test.py -v
```

## Building a standalone app

```
.venv\Scripts\python.exe -m PyInstaller packaging\build_exe.spec --noconfirm
```

Output:
- Windows: `dist\EasyPostDesktop\EasyPostDesktop.exe` (a folder, not a single
  file — see below for why). Copy the whole `EasyPostDesktop` folder anywhere
  and run the exe inside it without the dev environment.
- macOS: `dist/EasyPostDesktop.app` — a proper app bundle with the icon set,
  ready to drag into `/Applications`.

GitHub Actions builds both automatically on every push — see the **Actions**
tab for downloadable artifacts.

### Microsoft Store package (MSIX)

The app is also reserved on Microsoft Partner Center as **Easy-Post
Desktop** (Store ID `9NDSDL5LV5B5`) for eventual Microsoft Store submission.
Build the `.msix` from an existing `dist\EasyPostDesktop\` build:

```
.venv\Scripts\python.exe packaging\build_msix.py
```

This produces `dist\EasyPostDesktop.msix`, using the identity Partner
Center assigned (`packaging\msix\AppxManifest.xml`) and the app's existing
icon resized to the required tile sizes — no separate maintenance needed
when the icon changes, since assets are generated at build time.

GitHub Actions builds and signs this automatically on every Windows run
(alongside the plain `.exe`) with a throwaway self-signed certificate — see
the next paragraph for why that's sufficient. To test-install the locally
built package instead, from an **elevated** PowerShell window (trusting a certificate into
the Local Machine store requires admin rights):

```
.\packaging\sign_msix_local.ps1
Add-AppxPackage -Path dist\EasyPostDesktop.msix
```

MSIX packages must carry some signature to be structurally valid, but for
Microsoft Store submissions specifically, Microsoft explicitly documents
that a **self-signed** certificate is fine — the Store strips it and
re-signs with its own certificate during publishing. That means no
purchased code-signing certificate and no stored secrets are needed for
this path, unlike the plain `.exe`'s SmartScreen problem below. Uninstall a
local test install with `Get-AppxPackage SFields.Easy-PostDesktop |
Remove-AppxPackage`.

### macOS signing and notarization

macOS Gatekeeper refuses an unsigned or un-notarized app downloaded from the
internet, so the direct-download `.dmg` is signed with a **Developer ID
Application** certificate, notarized by Apple, and stapled. This runs in CI
and is gated on `MACOS_CERTIFICATE_P12_BASE64` being present, so forks
without the secret still build normally.

Required repository secrets:

| Secret | What it is |
|---|---|
| `MACOS_CERTIFICATE_P12_BASE64` | Developer ID cert + key, PKCS#12, base64 |
| `MACOS_CERTIFICATE_PASSWORD` | password for that `.p12` |
| `MACOS_SIGN_IDENTITY` | e.g. `Developer ID Application: … (TEAMID)` |
| `APPLE_ID`, `APPLE_APP_PASSWORD`, `APPLE_TEAM_ID` | notarytool credentials |

Two traps worth recording, both of which cost real time here:

- **Export the `.p12` with `-legacy -macalg sha1`.** OpenSSL 3.x writes
  PKCS#12 using AES/PBKDF2 with a SHA-256 MAC, which macOS `security import`
  cannot read — it fails with `MAC verification failed (wrong password?)`
  even when the password is perfectly correct.
  ```
  openssl pkcs12 -export -legacy -macalg sha1 \
    -inkey key.pem -in cert.pem -out DeveloperID.p12
  ```
- **`ditto`, not `cp`, for the `.app` bundle.** `cp -R` breaks the symlinked
  Qt frameworks and drops the executable bit, and arm64 macOS refuses a
  bundle whose signature no longer matches.

Notarization is submitted and waited on separately rather than with
`submit --wait`, so the submission id is always captured even if polling
dies. The wait is bounded (`--timeout 30m`, plus step and job timeouts). To
query a submission afterwards without rebuilding, run the **Notarization
status** workflow from the Actions tab with the submission id.

### Windows SmartScreen warning

Running a freshly-built `EasyPostDesktop.exe` on Windows will likely show a
blue "Windows protected your PC" SmartScreen prompt. This is expected for
**any** new, low-download-volume executable from an unrecognized publisher —
it isn't specific to this app, and it isn't a sign the build is unsafe.

**What actually fixes it:** a paid code-signing certificate (from a CA like
DigiCert/SSL.com, or Microsoft's cheaper Trusted Signing service) applied to
every release. Signing an executable is what lets Windows attribute it to a
real, verified publisher and build reputation over time. This repo's build
doesn't do that yet since it requires purchasing a certificate under a real
identity — `.github/workflows/build.yml` already has a signing step wired
up and ready to go (currently a no-op) for whenever `WINDOWS_CODE_SIGNING_CERT_BASE64`
and `WINDOWS_CODE_SIGNING_CERT_PASSWORD` repo secrets are added.

**What this repo does to reduce false positives in the meantime:**
- The build uses PyInstaller's `--onedir` mode rather than `--onefile`.
  Onefile builds self-extract into a temp folder on every launch, which is a
  strong heuristic signal antivirus engines and SmartScreen associate with
  packers/droppers; onedir avoids that.
- Every CI build includes a `SHA256SUMS.txt` alongside the executable so you
  can verify the download wasn't corrupted or tampered with in transit
  (`Get-FileHash -Algorithm SHA256 EasyPostDesktop.exe` on Windows, `shasum
  -a 256` on macOS/Linux, and compare against the matching line in
  `SHA256SUMS.txt`).

**If you hit the prompt:** click "More info" → "Run anyway". That's safe to
do for a build you compiled yourself or downloaded from this repo's own
GitHub Actions runs.

## Repository layout

```
app/
  config.py              paths, constants, LICENSE_REQUIRED flag detection
  core/                  client, credentials, SQLite, settings, licence,
                         label_options, webhook manager, HTTP receiver, tunnel
  services/              one module per EasyPost resource (shipments, batches,
                         addresses, tracking, insurance, pickups, claims,
                         packages, hts_lookup) — thin SDK wrapper + local sync
  ui/                    theme.py (Fusion + stylesheet), main_window.py (shell
                         and grouped nav), views/, widgets/
  resources/locales/     50 language catalogues; en.json is the source of truth
packaging/               PyInstaller spec, MSIX manifest/builder, signing
                         scripts, macOS entitlements
server/
  paddle-license-webhook-worker/   Cloudflare Worker: Paddle -> licence email
  paddle-license-webhook/          container/FastAPI equivalent, if self-hosting
site/                    easy-post.spencerfields.com — product site, policies
                         and the PHP contact form
tools/issue_license.py   mint a licence key by hand
tests/                   pytest suite, no network access required
```

### Conventions worth knowing before contributing

- **`en.json` is the source of truth for strings.** `tests/test_i18n.py`
  asserts every one of the other 49 catalogues has an identical key set, so
  adding a key means adding it everywhere. That test is the safety net for
  machine-generated translations at this scale.
- **No network in tests.** Every external call is mocked. A test that reaches
  a live API will pass on your machine and fail in CI the day that service is
  slow — which is exactly what happened once here.
- **Services own persistence, views own presentation.** A view should not
  build EasyPost request bodies, and a service should not import Qt.
- **Carrier names are text, never logo artwork.** See
  `app/ui/widgets/chips.py` for why, and what would have to change to ship
  real logos.
