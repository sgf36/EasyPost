# Easy-Post Desktop — release notes 1.2.2

Master English ("what's new") copy for the version 1.2.2 store listings. House
style: no Oxford commas, no abbreviations, no pronouns, British spelling.

Applies to the Microsoft Store listing (ReleaseNotes field) and, reworded per
platform limits, the App Store "What's New".

Version 1.2.2 is about one failure and everything it exposed. An international
batch could be imported, validated as complete, created against a live account,
and then fail every label at purchase — with the carrier asking for "at least
one item per package" and never once saying the word customs. A batch cannot be
amended before purchase, so there was no way forward but to start again.

Written for a shipper, not a developer.

## en-US ReleaseNotes

The Microsoft Store ReleaseNotes field caps at **1500 characters**, and every
translation has to fit the same cap. The copy below is 1431, leaving 69 of
headroom. A seven-bullet draft came to 1496 — inside the cap but too tight to
edit, and a wall of text to read — so two bullets were merged rather than
trimmed word by word. Check the count before editing.

• International batch shipments now carry a customs declaration. A batch sent abroad used to be created and then fail every label at purchase, with the carrier asking for "at least one item per package" without ever saying customs. The recipient template gains columns for the item description, value, quantity, tariff code and origin country, and the Batch page asks once for the contents type, the signer and what happens to an undeliverable parcel.

• Rows crossing a border are checked before the batch exists, so anything missing appears in the preview while it can still be fixed. Declared values are stated in the sender's own currency, so twelve pounds is declared as pounds rather than dollars.

• Get Rates prices one parcel from the import and narrows the carrier and service lists to what the route really supports, each with its price alongside. Choosing a batch service was previously done blind, against a catalogue of hundreds.

• Country codes are checked, and offered as a dropdown in the workbook. "UK" is not one, and typing it produced a row that looked international when it was not.

• The label sheet is chosen in Settings now, beside the label format and the printer, instead of only inside a dialog that opens after a label has been bought. Choosing a sheet sets the format to PNG, since a sheet is built from label images.

• Live tracking updates no longer drop the connection when a request is refused.

## Translations

All 47 listing languages are in `release-notes-1.2.2-translations.json`, which is
the file `build_listing_import.py` reads by default.

Each translation names the **Get Rates** button with the exact string the app
shows in that language, taken from `app/resources/locales/`, so the note and the
interface agree rather than offering the reader two names for the same button.

## Screenshots

The batch and settings screenshots both change in this version — the Batch page
gains a customs block and a Get Rates button, and Settings gains the label sheet
dropdown. Both are in the listing, so the store screenshots are regenerated for
1.2.2 rather than carried over.

The customs block only appears once an international row is loaded, and the
screenshot harness seeds domestic data, so it is absent from the capture. That
is accurate rather than a miss: it is what the page looks like for a domestic
batch, which is what the rest of that screenshot shows.

## What 1.2.2 contains, for anyone checking the notes against the code

All since the v1.2.1 tag:

| Commit | |
|---|---|
| `558de2f` | customs declarations on international batches |
| `051a8ee` | rate one row to narrow the carrier and service list |
| `2599da2` | country-code validation and workbook dropdowns |
| `ab046f8` | label sheet on the Settings page, and the PNG requirement |
| `4a6e9db` | webhook receiver answers refused requests instead of resetting |
| `e6f73f2` | .gitignore covers the signing-key types actually in use (no user-facing change) |

`4a6e9db` was fixed after v1.2.1 was tagged, so it has never shipped in a
release — it belongs in these notes even though it predates the version bump.

## Sequencing

The same rule as 1.2.1: Partner Center ships listing changes and packages in the
*same* submission, so these notes go up alongside the 1.2.2 package, not before
it. Publishing them against the 1.2.1 build would describe fixes nobody could
download yet.
