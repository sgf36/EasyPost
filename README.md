# EasyPost Desktop

A cross-platform (Windows/macOS) desktop app for shipping through
[EasyPost](https://www.easypost.com/): rate shopping, labels, tracking,
address verification, refunds, insurance, pickups, claims, and batch
shipping — all against your own EasyPost account.

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

- **Address Book** — verify and save addresses via EasyPost.
- **Create Shipment** — shop live carrier rates and buy/save/print labels.
- **Tracking** — add tracking numbers, auto-refreshed every 5 minutes.
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

Output: `dist\EasyPostDesktop\EasyPostDesktop.exe` (a folder, not a single
file — see below for why). Copy the whole `EasyPostDesktop` folder anywhere
and run the exe inside it without the dev environment. GitHub Actions builds
this automatically for both Windows and macOS on every push — see the
**Actions** tab for downloadable artifacts.

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
