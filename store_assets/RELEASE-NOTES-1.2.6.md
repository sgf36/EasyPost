# Easy-Post Desktop — release notes 1.2.6

Master English ("what's new") copy for the version 1.2.6 store listings. House
style: no Oxford commas, no abbreviations, no pronouns, British spelling.

Applies to the Microsoft Store listing (`ReleaseNotes` field, field ID 3) and,
reworded per platform limits, the App Store "What's New".

Version 1.2.6 is a two-fault release, both in the same place: batch. A batch
could be bought, and then neither of the two things a shipper does next
worked. The combined label file came back unusable, and the shipments
themselves never reached History, so a bulk run vanished the moment it was
paid for.

Written for a shipper, not a developer — so the notes describe what arrives on
the page, not which endpoint produced it.

## The two faults

**Combined labels.** "Generate combined labels" asked the shipping platform to
merge the batch server-side. For raster labels it returned landscape US Letter
pages, each drawing the same label **twice** at different offsets, so the
address block sat across the 1D barcode. Nothing scanned. The source labels
were fine — recomposing those identical images locally produced a clean sheet,
which is what identified the merge rather than the labels as the fault.

**History.** `save_shipment_locally` was called from the create-shipment,
History and agent paths but never from the batch flow, which wrote only the
batch row and its trackers. Six production labels were bought on 2026-08-20 and
the local shipments table still held two unrelated test rows.

## en-US ReleaseNotes

Three bullets. Deliberately short at roughly 560 characters against a **1500**
cap, because English is among the most compact of the 47 and the cap is what
actually bites: 1.2.2's six-bullet English draft fitted at 1431 and then put
**thirteen** translations over the limit, Tamil worst at 1633. Starting short
leaves every language room without a second round of cutting.

> • Combined batch labels are now built on the computer rather than fetched
> ready-merged. The earlier file could arrive with two overlapping copies of
> every label and the address block lying across the barcode, which no scanner
> would read. Each label now prints one to a page at its true size.
> • Shipments bought under Batch Shipments now appear in History alongside
> every other purchase. Several rows can be ticked there and their labels
> combined onto a single sheet of peel-off labels.
> • Label sheets now follow the resolution recorded inside each label, so a
> high-resolution label lands at the correct physical size on the page.

## Naming

"History" and "Batch Shipments" are **not** translated freely. Each language
uses the string the application itself shows in its navigation, read from
`app/resources/locales/<code>.json` keys `main_window.nav_history` and
`main_window.nav_batch_shipments` — so the notes name the page the reader will
actually look for. Three store codes differ from the application's locale
filenames: `zh-hans` → `zh.json`, `yo-latn` → `yo.json`, `ig-latn` → `ig.json`.

## Building the import

Release notes alone need **no staged import**. Staging exists because image
cells mint one Partner Center asset per language; a text-only change uploads
nothing, so it is a single bare CSV via **Import .csv**, not Import folder.

```bash
python store_assets/build_releasenotes_import.py \
    --export "<fresh listingData-9NDSDL5LV5B5-*.csv>" \
    --translations store_assets/release-notes-1.2.6-translations.json \
    --out "<listingData-....IMPORT.csv>"
```

Only field ID 3 is rewritten; every other row is copied through byte for byte.
Always build from a **fresh** export of the current submission — a stale base is
rejected with "We couldn't process this .csv file. Please export your listings
again."
