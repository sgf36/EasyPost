# Handoff — 1.1.4: batch-import failure diagnosed, mode-switch fix, Store MSIX built & installed

Written by a Claude Code session on 2026-08-12 (~16:20 BST) for the *other* Claude Code
session working in this repo. Read this before touching `dist/`, `build/`, or
`app/resources/*.flag` — we were both packaging at once and it produced a bad artifact
(see §6). The user paused you so this session could finish; the build is now done.

Everything below is either a change I made or a fact I verified by running a command.
Where I did **not** verify something, it says so.

---

## 1. TL;DR

- The reported "error importing `batch_template.xlsx`" was **not** the file and **not** packaging.
  It was a **stale "Ship from" address**: a *test-mode* address ID submitted to the
  *production* Batch API, which failed all 5 shipments at creation. Root cause: an
  orphaned Qt signal. Details in §2 — **please don't re-investigate this.**
- Fix applied to `app/ui/main_window.py`, plus new `tests/test_mode_switch.py`. §3.
- `openpyxl` **is** correctly bundled; the MSIX already uploaded to Partner Center has
  no packaging defect. Verified four ways. §4.
- **Store-variant 1.1.4.0 MSIX built, signed, and installed.** Artifacts + checksums in §5.
- **`app/resources/` currently holds `store_build.flag` only.** Anyone building the
  direct-download zip next must swap the flags first. §6.
- Verification commands you can run to confirm all of the above: §7.

---

## 2. The original bug (diagnosed — do not redo)

**Symptom:** user imported `C:\Users\SpencerFields\Downloads\batch_template.xlsx` into
Batch Shipments and got an error.

**The import itself succeeded** — the app parsed 5 rows, 5 valid, 0 errors. The failure
was the next step. Local DB (`%LOCALAPPDATA%\EasyPostDesktop\easypost_desktop.sqlite3`):

```
batches: id=batch_4597fe7ac9cd4b309bd0dc8a0009ac9d
         mode=production  status=creation_failed  num_shipments=5
         source_csv=C:/Users/SpencerFields/Downloads/batch_template.xlsx
```

`batch.retrieve` confirmed `state=creation_failed`, `{"creation_failed": 5}`, and returned
no per-shipment messages — which is why the UI could only show a generic failure.

**Cause — the `from_address` was a test-mode ID used against production:**

| Picker entry (what was selected) | ID | Mode |
|---|---|---|
| `Home — London, Greater London` | `adr_a6c7f65890af11f199370022480b361d` | **test** |
| `Home - London` (the correct one) | `adr_cb792775926511f184dc00224804dbec` | production |

Retrieving each with the production key:

- `adr_a6c7f658…` → `NotFoundError: The requested resource could not be found.`
- `adr_cb792775…` → OK (a real saved London address, redacted)

A saved address ID only exists in the account that created it, so every shipment in the
batch failed. I ruled the row data out by rebuilding the exact same shipment in **test**
mode (GB→GB, `predefined_package: LETTER`, weight 3.5, `PNG`/`4x6`): created fine, 50 rates.
Bare/mixed-case `LETTER`, and explicit dimensions instead of a predefined package, all
succeeded too.

**Why a test address was selectable in production:** `ModeBanner` (`app/ui/widgets/mode_banner.py`)
declares and emits `mode_changed`, but **nothing in `MainWindow` was connected to it** — only
`production_locked` was. The mode selector lives in the always-visible banner, so mode can be
flipped without leaving the page. Every view loads its mode-scoped data in its on-show refresh
(`_nav_actions`, run from `_on_nav_changed`), so any view navigated to *afterwards* is correct —
but the page already on screen kept the previous mode's rows. `BatchView._from_combo` therefore
still held test-mode addresses while the banner read PRODUCTION.

**The user's `.xlsx` needs no changes.** It is a valid openpyxl workbook (inline strings,
`Recipients` + hidden `Packages` sheet, data-validation list on `predefined_package`).

---

## 3. Source changes I made

Only these two files. Everything else uncommitted in the tree was already there before I
started (§3.2) — **it is not mine, and I have not reviewed it.**

### 3.1 Mine

**`app/ui/main_window.py`** — two hunks:

1. In `_build_app_shell()`, next to the existing `production_locked` connection:
   ```python
   self._mode_banner.mode_changed.connect(self._on_mode_changed)
   ```
2. New handler after `_on_nav_changed()`: `_on_mode_changed(self, _mode: str)`, which re-runs
   the **visible** view's registered on-show callable:
   ```python
   on_show = self._nav_actions.get(self._view_stack.currentIndex())
   if on_show is not None:
       on_show()
   ```
   Deliberately only the current view — off-screen views already refresh when navigated to,
   and several of those refreshes hit the API.

**`tests/test_mode_switch.py`** — new, 4 tests. Calls the handler unbound against a small
stub with just `_view_stack`/`_nav_actions`, so it doesn't drag in the credential store,
licence gate or DB. Covers: visible view refreshes; other views don't; a view with no
on-show callable is a no-op; `ModeBanner.mode_changed` still exists.

**Test run:** `272 passed, 1 skipped` (full `tests/` suite, `.venv` Python 3.14.6).

### 3.2 Uncommitted work that was already in the tree (not mine)

Present before I started, on `main`, and **baked into the 1.1.4 build described in §5**:

| File | What it is |
|---|---|
| `app/config.py` | `APP_VERSION` already bumped `1.1.3` → `1.1.4` |
| `packaging/msix/AppxManifest.xml` | `Version` already `1.1.3.0` → `1.1.4.0` |
| `app/services/packages.py` (+86) | new carrier-qualified package labels + `package_code_from_choice` |
| `app/services/batches.py` (+15) | template dropdown now uses carrier-qualified labels; reduces to the bare code at submit |
| `app/ui/views/batch_view.py` | `predefined_package_names` → `predefined_package_choices` |
| `tests/test_batches.py`, `tests/test_packages.py` | tests for the above |

Untracked: `store_assets/EasyPost-Store-Listing-1.1.1-*` (zips + dir), and this file.

One thing I did check, because it bears on the user's existing file: `package_code_from_choice`
passes a bare code through unchanged (`'LETTER'` → `'LETTER'`) and only strips the carrier
prefix on the em-dash form (`'Royal Mail — LETTER'` → `'LETTER'`). A hyphen form
(`'Royal Mail - LETTER'`) is **not** reduced — probably fine since the template writes the em
dash, but flagging it in case you intended both.

---

## 4. openpyxl / Partner Center — settled

The already-uploaded MSIX has **no packaging problem**. I went down this path first and was
wrong; recording the reasoning so you don't repeat it:

> `_internal/` has no `openpyxl` folder — but that proves nothing. PyInstaller keeps
> **pure-Python** packages in the PYZ archive inside the exe. Folders in `_internal/` are
> packages with data files or binaries (collected via `collect_all`/`collect_data_files`).
> openpyxl comes in through `collect_submodules` → `hiddenimports` → PYZ.

Verified:

1. `PYZ-00.toc` (the GUI exe) lists **190** openpyxl modules — exactly what
   `collect_submodules('openpyxl')` returns in this venv.
2. The literal `openpyxl` appears in the shipped exe binary (190 occurrences in the installed 1.1.4).
3. `requirements.txt` pins `openpyxl>=3.1.0`; CI installs `requirements-dev.txt`, whose first
   line is `-r requirements.txt`. The spec's collection is not variant-gated.
4. Decisive: the installed 1.1.3 build **actually parsed the user's .xlsx** (that's how the
   batch got created), which required `from openpyxl import load_workbook` to succeed frozen.

**But note:** the mode-switch bug of §2 *is* in the uploaded 1.1.3 — it's a source defect, so
Store users will have it until 1.1.4 ships.

Not a bug, so you don't chase it: `PYZ-01.toc` (the `easypost-mcp.exe` helper) has **zero**
openpyxl entries. `app/mcp_server.py` never touches batches, so the helper cannot reach the
`.xlsx` path.

---

## 5. What I built and installed

Store variant, clean build (workpath wiped first).

```
dist/EasyPostDesktop.msix
  version   1.1.4.0
  variant   store_build.flag only (no license_required.flag / mcp_supported.flag)
  entries   566
  size      96,640,544 bytes
  sha256    9703858377E3D5A84F915C84BDADDFF41EB7BDD106D56A03A60715D37755A8D3
  signed    yes (AppxSignature.p7x present; local self-signed test cert,
            CN=A7D4B6C0-27D4-4F66-82EB-82F5DD466788, in LocalMachine\TrustedPeople)
  contains  resources.pri, easypost-mcp.exe, Assets/{StoreLogo,Square44x44Logo,Square150x150Logo}.png
```

`build_msix.py` printed: `Store variant verified: store_build.flag + MCP helper present, no
direct-only flag, resources.pri present.`

Installed, replacing 1.1.3.0 (the app was not running):

```
SFields.Easy-PostDesktop  1.1.4.0
C:\Program Files\WindowsApps\SFields.Easy-PostDesktop_1.1.4.0_x64__qhnp6qavahs3g
```

Installed package spot-checks: `store_build.flag` present, `easypost-mcp.exe` present,
openpyxl present in the exe. Only 1.1.4.0 remains under `WindowsApps` — 1.1.3 was cleanly replaced.

**I did not run the runtime acceptance test** (it needs clicking through the UI, and I was
asked not to drive the user's screen). See §7.F — worth doing.

**Exact commands used**, in order, from the repo root:

```bash
# app/resources already contained store_build.flag only
rm -rf build/build_exe build/msix_staging
.venv/Scripts/python.exe -m PyInstaller packaging/build_exe.spec --noconfirm \
    --workpath <TEMP>/wp          # see §6 (OneDrive)
.venv/Scripts/python.exe packaging/build_msix.py
```
```powershell
.\packaging\sign_msix_local.ps1     # elevated pwsh; writes LocalMachine\TrustedPeople
Add-AppxPackage -Path .\dist\EasyPostDesktop.msix
```

---

## 6. Current repo/build state, and traps

### 6.1 Variant flags are set to **Store** right now

```
app/resources/store_build.flag        <- present
app/resources/license_required.flag   <- ABSENT
app/resources/mcp_supported.flag      <- ABSENT
```

These are gitignored, created per-build, and **decide which product you get**
(`store_build.flag` → `STORE_BUILD` true → production gated behind the Store add-on;
`license_required.flag` → the Paddle key gate). They were direct-download when I first looked;
one of us switched them to Store. **Set them deliberately before every PyInstaller run.**
To build the direct download:

```bash
rm -f app/resources/store_build.flag
: > app/resources/license_required.flag
: > app/resources/mcp_supported.flag
```

### 6.2 A mis-variant MSIX existed on disk — this is the trap worth knowing

At 16:04 I inspected a `dist/EasyPostDesktop.msix` that was **version 1.1.4.0 but contained
`license_required.flag` + `mcp_supported.flag` and no `store_build.flag`**, unsigned. It has
since been replaced by the good package in §5, but the mechanism will recur:

**`build_msix.py` calls `pack()` *before* `verify_store_variant()`.** So when the variant check
fails, the bad `.msix` has already been written and stays on disk. A failed build leaves a
plausible-looking package sitting at the exact path you'd upload from. Uploading that one would
put the app's own Paddle licence gate on top of a Store purchase — which the script's own
comments call a policy breach — and leave `STORE_BUILD` false so the Store "Production unlock"
add-on wouldn't gate anything.

Two takeaways: **always re-verify the `.msix` itself** (§7.C), and consider making `pack()`
write to a temp name and only move it into place after `verify_store_variant()` passes, or
delete `output_msix` on failure. I have not made that change.

### 6.3 `dist/EasyPostDesktop-Windows-x64.zip` is stale and is the *other* variant

```
dist/EasyPostDesktop-Windows-x64.zip   15:58, 95,846,460 bytes
  sha256 2259d801c8faa0b7a809715626fd891cfc3a5a4e3c7ee1a03a9ac7d1f12e31ac
```

Not built by me. It came from the 15:57 **direct-download** PyInstaller output, which postdates
my 15:51 source edit, so it should contain the mode-switch fix — but I did not verify its
contents, and it does not correspond to the current `dist/EasyPostDesktop/` tree (Store variant,
16:16). If you're shipping the direct download for 1.1.4, rebuild it after swapping flags per §6.1.

### 6.4 OneDrive locks the build directory

The repo lives under OneDrive. My first PyInstaller run died with:

```
PermissionError: [WinError 32] ... build\build_exe\base_library.zip
```

OneDrive had a handle on a freshly written build file. The lock was transient (the file deleted
fine seconds later), but the reliable fix is to keep PyInstaller's workpath **outside** OneDrive:

```bash
.venv/Scripts/python.exe -m PyInstaller packaging/build_exe.spec --noconfirm \
    --workpath "$LOCALAPPDATA/Temp/epd_build"
```

`--distpath` must stay the default `dist/` because `build_msix.py` reads `dist/EasyPostDesktop`.
Note this moves `PYZ-00.toc` etc. out of `./build` — adjust the §7.D path accordingly.

### 6.5 Don't build concurrently

`dist/`, `build/`, and `app/resources/*.flag` are shared mutable state with no locking. While I
was inspecting artifacts, a build of yours deleted `dist/EasyPostDesktop/` and the `.msix`
mid-read (`FileNotFoundError` on a path that existed seconds earlier). One session owns the
build at a time.

---

## 7. Verification checklist

Run from the repo root. Nothing here mutates the build.

### A. Source scope and tests

```bash
git status --short
git diff --stat app/ui/main_window.py            # expect 2 hunks, +~19 lines
grep -n "mode_changed" app/ui/main_window.py app/ui/widgets/mode_banner.py
.venv/Scripts/python.exe -m pytest tests/ -q     # expect 272 passed, 1 skipped
```

The `grep` must show `mode_changed.connect(self._on_mode_changed)` in `main_window.py`. If it
shows only the `Signal` declaration and the `emit`, the fix is not in the tree.

### B. Version consistency

```bash
grep APP_VERSION app/config.py                             # 1.1.4
grep 'Version="1' packaging/msix/AppxManifest.xml          # 1.1.4.0
```

`APP_VERSION` feeds `update_check`; the manifest feeds the Store. Both must move together, and
the release tag with them.

### C. Variant correctness — source, built tree, and package

```bash
ls app/resources/ | grep -i flag                     # exactly one variant set
find dist/EasyPostDesktop -name "*.flag"             # must match the intent
```

```powershell
# The package itself is the authority — check it, not just the staging tree.
$msix = ".\dist\EasyPostDesktop.msix"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $msix))
$n = $z.Entries | ForEach-Object { $_.FullName }
"entries          : $($n.Count)"
"flags            : " + (($n | Where-Object { $_ -like '*.flag' }) -join ', ')
"resources.pri    : " + ($n -contains 'resources.pri')
"AppxSignature.p7x: " + ($n -contains 'AppxSignature.p7x')
"easypost-mcp.exe : " + [bool]($n | Where-Object { $_ -like '*easypost-mcp.exe' })
$e = $z.GetEntry('AppxManifest.xml'); $sr = [System.IO.StreamReader]::new($e.Open())
"manifest version : " + ([regex]::Match($sr.ReadToEnd(), 'Version="([0-9.]+)"').Groups[1].Value)
$sr.Close(); $z.Dispose()
(Get-FileHash $msix -Algorithm SHA256).Hash
```

For the Store package expect: exactly `store_build.flag`; `resources.pri` true;
`easypost-mcp.exe` true; version `1.1.4.0`. Hash should equal §5 if you haven't rebuilt.

### D. All-inclusiveness of lazily-imported deps

The spec collects `openpyxl`, `mcp`, `websockets` and the WinRT/StoreKit modules inside
`try/except` blocks that **swallow failures and continue** (`"never break a build over this"`).
A build machine missing one of those packages produces a silently incomplete app, so check the
TOC rather than trusting the build's exit code:

```bash
# ./build/build_exe is PyInstaller's default workpath; use your --workpath if you moved it
T=build/build_exe/PYZ-00.toc
grep -o "'openpyxl"   $T | wc -l     # expect 190  (== collect_submodules('openpyxl'))
grep -o "'mcp\."      $T | wc -l     # expect 109
grep -o "'websockets" $T | wc -l     # expect  52
ls dist/EasyPostDesktop/_internal/app/resources/   # icons, locales, and the right flag
ls dist/EasyPostDesktop/easypost-mcp.exe
```

(Counts are from the 1.1.4 build in §5. Treat them as "non-zero and in this ballpark" — they
shift with dependency versions. Zero is the failure signal.)

Zero openpyxl entries means the `except` branch fired — look for
`[build_exe.spec] openpyxl collect_submodules skipped:` in the build log.
`PYZ-01.toc` (helper exe) having no openpyxl is expected and harmless (§4).

Functional check, worth more than any TOC: launch the app and import a `.xlsx` in
Batch Shipments. If openpyxl were missing, `_parse_xlsx` would raise `ModuleNotFoundError`,
which `batch_view.py:198` surfaces as a message box titled with
`batch_shipments.invalid_csv_title` and the body `No module named 'openpyxl'`. That misleading
"invalid CSV" title on an .xlsx is the signature of a packaging problem rather than a data one.

### E. Signature and install

```powershell
Get-AppxPackage -Name "SFields.Easy-PostDesktop" | Select-Object Name, Version, InstallLocation
Get-ChildItem "C:\Program Files\WindowsApps" -Directory -Filter "SFields.Easy-PostDesktop*" |
    Select-Object -ExpandProperty Name       # only the current version should remain
Get-ChildItem Cert:\LocalMachine\TrustedPeople |
    Where-Object Subject -eq "CN=A7D4B6C0-27D4-4F66-82EB-82F5DD466788"
```

The self-signed cert is **local testing only** — the Store re-signs on publish, so an unsigned
or self-signed package uploads fine.

### F. Runtime acceptance test for the actual fix (not yet done)

This is the one test that proves §2 is fixed. It needs the UI, so it wasn't run:

1. Launch 1.1.4 in **test** mode, go to **Batch Shipments**.
2. Note the **Ship from** entries — test-mode addresses, including one labelled `Home`.
3. **Without leaving the page**, switch the banner selector to **Production**.
4. **Expected:** the list repopulates with production addresses — `Home - London` appears and
   the test-only `Home` disappears. Before the fix it kept the test entries, which is what
   caused the failed batch.
5. Reverse it (production → test) and confirm it swaps back.
6. Also spot-check that switching mode on **Create Shipment** and **Address Book** refreshes
   those, since they go through the same `_nav_actions` path.

Then the end-to-end: import `C:\Users\SpencerFields\Downloads\batch_template.xlsx` unchanged
in production with `Home - London` selected, and confirm the batch reaches `created` rather
than `creation_failed`. **That step creates a real production batch — get the user's go-ahead
first, and don't buy postage.** The old failed batch
(`batch_4597fe7ac9cd4b309bd0dc8a0009ac9d`) cost nothing and just sits in history.

### G. Before any Partner Center upload

Re-run C (variant + version + signature) and D (all-inclusiveness) against the **exact file**
you're about to upload, and compare its sha256 to §5. §6.2 is the reason: a failed build leaves
a wrong-variant `.msix` at the path you'd upload from.

---

## 8. Not done / open

- Runtime acceptance test (§7.F) — the main outstanding item.
- Nothing committed. All changes, mine and the pre-existing 1.1.4 work, are still in the
  working tree on `main`. No release tag, no `dist/EasyPostDesktop-Windows-x64.zip` rebuild.
- The direct-download zip is stale and the other variant (§6.3).
- `pack()`-before-`verify_store_variant()` in `build_msix.py` (§6.2) — unfixed suggestion.
- Hyphen vs em-dash in `package_code_from_choice` (§3.2) — flagged, not changed.
- Release notes / listing text for 1.1.4 — untouched. Worth a line, since this fix is
  user-visible: switching test/production no longer leaves a stale Ship-from address.
