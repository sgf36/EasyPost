# Partner Center listing import

Product `9NDSDL5LV5B5`, submission `1152921505701419684`.

Partner Center round-trips listings as one wide CSV: a row per field, a column
per language. Editing 47 languages by hand in the portal is not realistic, so
listing changes go through export → edit → import.

## Rebuilding the package

1. In Partner Center, open the submission and choose **Export listings**.
2. Run the builder against whatever file that produced, with an optional second
   argument to drop a copy somewhere convenient:

```bash
python store_assets/build_listing_import.py ~/Downloads/listingData-9NDSDL5LV5B5-*.csv ~/Downloads
```

It writes `EasyPost-Store-Listings-IMPORT-v2/` — the CSV plus every referenced
image, flat, beside it. Gitignored: the inputs are committed, the output is
disposable.

3. In Partner Center, choose **Import listings** and select **the folder**.
4. Re-export afterwards and confirm the screenshot rows now carry Partner
   Center asset URLs rather than the filenames sent up.

## Three rules the importer enforces silently

All three produce the identical, useless error: *"We couldn't import listings
for the following languages"* with the language list rendered **blank**, and
`listings/importvalidations` returning `validations: []`. It reads like a
portal fault. It is not. The builder now asserts all three.

**It takes a folder, not a zip.** The dialog wants the directory itself.

**Image paths must include the root folder name.** Microsoft's own example is
`my_folder/screenshot1.png` — a bare `screenshot1.png` does not resolve. This
is the one that cost two failed imports.

**Exactly one .csv in the folder**, alongside the assets.

Worth knowing: the import is all-or-nothing. Nothing is saved until the file
is completely clean, so a failure leaves the listing exactly as it was.

## A note on the previous import

The 46 non-English languages carry screenshot asset URLs that all share a
single asset ID, while the six originally-localised languages each have their
own. That is the signature of a **URL-reuse** import, not a folder import —
the images were uploaded through the portal for six languages, then an export
supplied the URLs that a second CSV fanned out across the rest. So the earlier
folder attempt almost certainly failed for this same path-prefix reason and
was worked around rather than fixed.

## Why it starts from a fresh export

The builder copies the export and rewrites only `DesktopScreenshot1-9` and
`DesktopScreenshotCaption1-9`. Every other row — description, title, short
description, release notes, features, logo overrides — is passed through
byte-identical, so no field can be blanked by omission and no translation has
to be regenerated to change an image.

Two consequences worth knowing:

- **Slots 10-30 are explicitly cleared**, so a stale tenth screenshot cannot
  survive an import that only names nine.
- **The `default` column is left empty**, matching the export. Every listing
  language is populated explicitly, so the catch-all is never consulted.

## Screenshots

Nine per language, in this order, rate shopping first because it is the one
image that shows what the product actually does:

| Slot | Page | File |
|---|---|---|
| 1 | Create Shipment | `<lang>_1_rate_shopping.png` |
| 2 | Tracking | `<lang>_2_tracking.png` |
| 3 | Address Book | `<lang>_3_address_book.png` |
| 4 | Batch Shipments | `<lang>_4_batch_shipments.png` |
| 5 | History | `<lang>_5_history.png` |
| 6 | Reports | `<lang>_6_reports.png` |
| 7 | HTS Lookup | `<lang>_7_hts_lookup.png` |
| 8 | Dashboard | `<lang>_8_dashboard.png` |
| 9 | Settings | `<lang>_9_settings.png` |

Seven languages have their own captured set — `en zh hi es fr de ja`. The
remaining 40 listing languages reference the English images and English
captions, which is normal practice and matches the images they point at. The
application itself stays fully translated into all 50 either way.

German renders at 2075 × 1175 rather than 1924 × 1175 because the labels are
longer and the window grows to fit. Both sit inside the Store's 1366 × 768 to
3840 × 2160 range.

Regenerate the images with `store_assets/shoot_screenshots.py <locale>` before
rebuilding if the interface has changed. That script writes the user's real
`settings.json`, so set the locale back to `en` when finished.
