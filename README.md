# EasyPost Desktop

A Windows desktop app for shipping through [EasyPost](https://www.easypost.com/):
rate shopping, labels, tracking, address verification, refunds, insurance,
pickups, claims, and batch shipping — all against your own EasyPost account.

## First-time setup

```
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m app.main
```

On first launch you'll be asked to paste your EasyPost API key(s). They're
encrypted with Windows DPAPI and stored at `%APPDATA%\EasyPostDesktop\`,
tied to your Windows login — never written in plain text, never sent
anywhere but EasyPost's API.

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

## Note on tracking updates

This is a desktop app with no public URL, so EasyPost can't push webhook
events to it directly. Tracking updates are pulled by polling instead (every
5 minutes, or on demand via "Refresh all now"). A future version could add a
webhook receiver behind an ngrok/Cloudflare tunnel if push updates matter.

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

## Building a standalone .exe

```
.venv\Scripts\python.exe -m PyInstaller packaging\build_exe.spec --noconfirm
```

Output: `dist\EasyPostDesktop.exe`. It's self-contained (bundles Python and
Qt) — copy it anywhere and run it without the dev environment.
