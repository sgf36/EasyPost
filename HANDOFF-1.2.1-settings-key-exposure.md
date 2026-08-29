# Easy-Post Desktop — v1.2.1 handoff

**Written:** 2026-08-12, at a usage limit, mid-release.
**Repo:** `sgf36/EasyPost` (PUBLIC), branch `main`, HEAD `c91fff6`.
**Working tree:** dirty — 47 locale files + `app/ui/views/settings_view.py` modified,
`tests/test_settings_keys_not_shown.py` untracked. **Nothing is committed.**

Spencer's instruction was "package 1.2.1 IMMEDIATELY". Finish that.

---

## 1. Why 1.2.1 exists

**v1.2.0 shipped with the Settings page displaying live EasyPost API keys.**

`SettingsView.refresh()` loaded both keys out of the credential store and wrote them
into the two `QLineEdit`s on every visit. The **Show keys** toggle then switched the
echo mode to `Normal`, rendering the production key in plaintext. Anyone looking at
the screen — or any screenshot, screen share or recording — captured it.

It is already fixed in the working tree (§2). It is **not committed, not released**.

Spencer's words: *"The population of API keys on the settings tab is a MASSIVE issue
you should have flagged AGES ago!"* He is right. The prior session had written
`FORBIDDEN_PAGES = {"settings_view", ...}` into `packaging/make_screenshots.py` with
the comment that it "renders API key fields", i.e. recognised the page as too
sensitive to photograph, and treated it as a screenshot problem rather than asking
why a live key was in a widget at all. Do not repeat that reasoning elsewhere.

**Scope was checked and the exposure is desktop-Settings only:**

- Mobile companion has no settings screen holding a key — it pairs by QR and the key
  never reaches the device.
- No key-shaped strings in tracked source, in **any commit in history**, or in the
  shipped MSIX (606 entries scanned).

---

## 2. What is already done (uncommitted)

### `app/ui/views/settings_view.py`

1. **`refresh()` no longer populates the key fields.** It clears them and sets a
   placeholder of `"•" * 12` (`_STORED_MASK`) when a key is stored, empty when not.
   The mask is deliberately not a translated string.
2. **`_on_save()` treats a blank field as "leave unchanged".** This is essential:
   the fields are now empty by design, so the old "blank = clear" rule would have
   wiped both keys the first time anyone opened Settings and pressed Save for an
   unrelated reason. Only a non-empty field overwrites.
3. **"Forget stored keys" button added** (`_on_forget_keys`), with a confirm dialog,
   so removing a key is still possible but must be deliberate.
4. **Printer profile moved into Settings**, inside `_build_label_group()`:
   `Printer` (Laser/Inkjet) plus `Calibration across` / `Calibration down` in mm.
   These write `AppSettings.printer_type`, `label_offset_x_mm`, `label_offset_y_mm`
   immediately on change via `_on_printing_choice_saved` — not behind the Save
   button, which belongs to the API keys.
   - Spencer asked for this: they describe the printer on the desk, not the parcel,
     and were previously reachable only from the Export print sheet dialog, i.e.
     only after buying a label.
   - **Reuses existing translated keys** (`print_sheet.printer_label`,
     `printer_laser`, `printer_inkjet`, `offset_x_label`, `offset_y_label`), so this
     part needed no new i18n.
   - The Export print sheet dialog keeps its own copies for one-off nudges. Both
     read/write the same `AppSettings` fields; last one wins. This is intended.
5. New imports: `QDoubleSpinBox`, and `DEFAULT_PRINTER_TYPE` from
   `app.core.label_sheet`.

### `tests/test_settings_keys_not_shown.py` (new, 5 tests, **all passing**)

Pins: fields empty when keys are stored; the placeholder never contains the key;
no placeholder when nothing is stored; **blank save keeps stored keys**; a typed key
replaces the stored one.

### i18n

Three new English keys in `app/resources/locales/en.json`:

```
settings.forget_keys_button        = "Forget stored keys"
settings.forget_keys_confirm_title = "Forget stored keys"
settings.forget_keys_confirm_body  = "Remove both API keys from this computer? They are not sent anywhere else, so you will need to paste them again before you can ship."
```

**47 of 49 non-English locales already merged** into their files.

---

## 3. THE ONE BLOCKER

**`ml.json` (Malayalam) and `so.json` (Somali) are missing all three new keys.**

`tests/test_i18n.py` asserts every locale file has *exactly* the same key set as
`en.json`, so **the suite fails until these two are filled**. Verify with:

```bash
cd "C:/Users/SpencerFields/OneDrive - Spencer Fields/Apps/Claude/EasyPost-Desktop-App"
./.venv/Scripts/python.exe -u -c "
import json,glob,os
NEW=['settings.forget_keys_button','settings.forget_keys_confirm_title','settings.forget_keys_confirm_body']
print([os.path.basename(p) for p in sorted(glob.glob('app/resources/locales/*.json'))
       if any(k not in json.load(open(p,encoding='utf-8')) for k in NEW)])
"
```

**Cause of the gap:** the translation agents were given locale lists containing `ps`
and `tl`, which **do not exist** in `app/resources/locales/`, while `ml` and `so`
were omitted. Their output for `ps`/`tl` is present in the scratchpad and should be
discarded. Do not create `ps.json`/`tl.json` — `SUPPORTED_LOCALES` in `app/i18n.py`
is the authority on which locales exist.

**To fix:** translate the three strings into `ml` and `so`, reading each file first
and reusing its existing `settings.test_key_label` / `settings.prod_key_label`
rendering of "API key" and its `address_book.delete_confirm_*` tone. "Forget" means
delete from local storage, not a memory lapse. No `{placeholders}`.

A partial agent run for exactly this exists at agent id `a8fc02492a6b9f09a` (it read
both catalogues, then hit the limit before writing). Its intended output path was
`…/scratchpad/fk_c.json` shaped `{"ml": {...}, "so": {...}}`.

The 49 already-merged translations came from `…/scratchpad/fk_a.json` and `fk_b.json`
under
`C:\Users\SPENCE~1\AppData\Local\Temp\claude\C--Users-SpencerFields-OneDrive---Spencer-Fields-Apps-Claude\cd5924e1-3601-43c0-8485-cd8f0c53c813\scratchpad\`
(a temp dir — copy anything still needed before it is cleaned).

---

## 4. Remaining steps for 1.2.1

### 4a. Fill `ml` and `so` (§3), then bump the version — NOT yet done

Both still read 1.2.0:

| File | Line | Change |
|---|---|---|
| `app/config.py` | 16 | `APP_VERSION = "1.2.0"` → `"1.2.1"` |
| `packaging/msix/AppxManifest.xml` | 12 | `Version="1.2.0.0"` → `"1.2.1.0"` |

Check whether the mobile companion should track it — its `pubspec.yaml` is at
`1.2.0+1`. **Spencer has not decided whether the companion versions independently**;
he declined to answer this earlier, so do not tag or release that repo without asking.

### 4b. Full suite, then commit and push

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q
```

Baseline before these changes was **429 passed, 1 skipped**; expect 434 passed with
the 5 new tests. Then commit `settings_view.py`, all locale files, the new test, and
the version bumps together, and push to `main`.

Do **not** `git add -A` — an earlier commit swept 14 MB of listing zips into the
public repo that way. `store_assets/*.zip` is now gitignored, but stage explicit
paths regardless.

### 4c. Release

CI (`.github/workflows/build.yml`) builds on push to `main`: Windows zip + MSIX,
macOS notarized `.dmg`, and — because all five `MAS_*` secrets are set — it also
builds the Mac App Store `.pkg` and **uploads it to App Store Connect automatically**
via `altool`. The 1.2.0 run did exactly that (Delivery UUID
`1cfc8bcb-39ba-4aa6-a676-5fc6621619e2`).

Then, following the v1.2.0 pattern:

1. Wait for CI green; take the artefacts from the run, not a local rebuild.
2. Create the release with the Windows zip, the `.dmg`, and `SHA256SUMS.txt`.
3. **Do NOT attach the MSIX.** CI builds the MSIX *before* applying the Paddle
   licence gate, so it carries `store_build.flag` and no `license_required.flag`;
   published on the licence-gated download channel it hands out an ungated paid app.
   Store distribution goes through Partner Center only. This was caught and removed
   during 1.2.0.
4. Update `site/download.html`: version, sizes, links, and the checksums **taken from
   the release's own `SHA256SUMS.txt`**, never a local build — during 1.2.0 a
   concurrent session re-uploaded with `--clobber` and the local hashes went stale.
5. Deploy the site — **scripted, no browser, no login needed**:
   ```bash
   python packaging/deploy_site.py download.html
   ```
   The cPanel API token is in Windows Credential Manager under service
   `cpanel-easypost-site`, account `spencgh6`. **Never commit it — the repo is
   public.** See the README's "Publishing the product site" for the three traps
   (UAPI returns HTTP 200 on failure; the host sends no charset so a correct file
   reads back as mojibake; whitespace shifts so byte counts never match).

### 4d. What's new copy

`store_assets/RELEASE-NOTES-1.2.0.md` is the model. **The Microsoft Store
ReleaseNotes field caps at 1500 characters per language** and the 1.2.0 English
draft had to be cut from 1634 to 1494 — check the count before translating.

For 1.2.1 the honest single line is that stored API keys are no longer displayed on
the Settings page, plus the printer and calibration settings moving there. Write it
for a shipper, not a developer, in the house style: no Oxford commas, no
abbreviations, British spelling.

---

## 5. OUTSTANDING: Partner Center stage 2 import

Separate from 1.2.1 and can be done in parallel. **Stage 1 is imported. Stage 2 is
built and verified but not yet imported.**

```
C:\Users\SpencerFields\Downloads\EasyPost-Store-Listings-IMPORT-v2-stage2.csv
```

**This one is a plain CSV import — select the file, not a folder.** Only stage 1 is
a folder import, because only stage 1 uploads images. Stage 2 uploads nothing: it
fans out the asset URLs Partner Center minted during stage 1.

Rebuild it (only if a *newer* export is taken — see the warning below):

```bash
python store_assets/build_listing_import.py "<fresh listingData-....csv>" \
    "C:/Users/SpencerFields/Downloads" --stage2
```

### What it does

| | |
|---|---|
| Languages written | the **40 non-localised** ones |
| Left untouched | the 7 localised (`en-us zh-hans hi es fr de ja`) — stage 1 owns those |
| Per language | all 9 screenshot URLs + all 9 captions, copied from `en-us` |
| Also sets | `ReleaseNotes` (1.2.0 what's-new) for those same 40 |

Together the two stages cover all 47 languages without either clobbering the other.

### The defect this version already fixes — do not reintroduce it

**Partner Center does not preserve the slot order a folder import sends.** After the
1.2.0 stage-1 import, slots 4–9 came back permuted: the batch screenshot went up as
slot 4 and came back as slot 5, with Settings in slot 4.

Stage 2 originally copied each image URL out of the export but *regenerated* the
caption from `ORDER`, so the two stopped describing the same picture. That would have
published the Settings screenshot captioned "Import a CSV of recipients, choose a
carrier and service, then buy in bulk", with every slot from 4 to 9 mislabelled, in
all forty languages. Fixed in commit `c91fff6`: both the image and the caption are now
taken from the **same cell of the same export**, which cannot drift.

If you touch `build_listing_import.py`, keep that property.

### Verify before importing

The built CSV was checked and passed on all four points — re-run these if it is
rebuilt:

- shape preserved `454 x 51`
- for every one of the 40 languages, slot *N*'s **image and caption both equal
  `en-us`'s** slot *N*
- **zero** cells changed for the 7 localised languages
- release notes written for 40 of 40, none over the 1500-character cap

### After importing

Re-export and confirm the 40 languages now carry asset URLs. Note the resulting
display order is: rates, tracking, addresses, **settings**, **batch**, dashboard,
history, HTS, reports. Each caption travelled with its image so nothing is
mislabelled, but batch sits 5th rather than 4th. Spencer has been told; if he wants
it earlier that is a reorder in the portal, not another import.

⚠️ **Do not rebuild stage 2 from `listingData-9NDSDL5LV5B5-1.2.0-IMPORT.csv`.** That
is a *generated* file from an earlier approach, not an export. The genuine
post-stage-1 exports are `…-1152921505701643221 (3).csv` and `(4).csv`, which are
byte-identical to each other.

---

## 6. Other work already finished — do not redo

- **v1.2.0 is published**: <https://github.com/sgf36/EasyPost/releases/tag/v1.2.0>,
  tag at `47ee3b1`. Site live at 1.2.0 with verified checksums.
- **App Store Connect already has both builds**, uploaded by CI: desktop `.pkg`
  (`1cfc8bcb-…`) and mobile `.ipa` (`ac35a633-8f0d-4181-ac55-ec46517e145f`). Only
  the version records need creating in the portal.
- **MSIX 1.2.0.0** staged at `C:\Users\SpencerFields\Downloads\EasyPostDesktop-1.2.0.0.msix`
  (CI's binary, `sha256:72ae3ef3…`). Superseded if 1.2.1 ships first.
- **Batch template fixed**: `C:\Users\SpencerFields\Downloads\batch_royalmail_letters_1.2.0.xlsx`,
  5 GB letters, 0 validation errors, `predefined_package` resolving to `Letter`.
  Royal Mail V3's real codes are `Letter`, `LargeLetter`, `Parcel`, `PrintedPapers`
  — **title case**; `LETTER` is the USPS spelling and the app's local package cache
  wrongly agreed with it.

---

## 7. Two traps specific to this repo

**Partner Center reorders screenshot slots on import** — see §5, which is the live
instance of this. The general rule: any tool that copies an image URL out of an
export must take its caption from the *same* slot of the *same* export, never
regenerate it from an intended order.

**Screenshot runs report success while capturing the wrong thing.** `MainWindow`
routes to the first-run wizard when it finds no credentials, so a CI run once
produced 20 images that were all the setup form. `make_screenshots.py` now forces
the app shell and asserts the root stack before painting. Always open the PNG.

**Listing field caps.** ReleaseNotes 1500 characters, captions 200, per language. A
rejected import costs a full re-export and the error does not say which language
failed, so validate before importing — `build_listing_fields.py` refuses to write
rather than produce a package Partner Center will reject.

---

## 8. Suggested opening message for the new session

> Read `EasyPost-Desktop-App/HANDOFF-1.2.1-settings-key-exposure.md`. Two jobs.
>
> First, finish packaging v1.2.1: the working tree already has the fix and 47 of 49
> translations, so start by filling the three `settings.forget_keys_*` keys for `ml`
> and `so`, which are blocking the i18n test, then bump the version, run the suite,
> commit, push and cut the release. Do not attach the MSIX to the GitHub release.
>
> Second, walk me through importing the Partner Center stage 2 CSV (§5) — it is built
> and verified, and it is a plain CSV import, not a folder.
