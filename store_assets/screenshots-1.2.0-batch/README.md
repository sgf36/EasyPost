# Batch screenshot replacements — 1.2.0

Version 1.2.0 added a "Choose carrier and service" step to the Batch page, so
the batch screenshot on the store listings shows a page that no longer exists
in that form. These replace it. 2000x1250, matching the existing listing
images exactly.

Regenerate with:

    python packaging/make_screenshots.py --platform store --locale <code> \
        --window BatchView --out dist/screenshots

## The slot number is not the same in every language

The screenshot order differs per language, so the file name carries the slot
that language actually uses:

| Languages | Slot |
|---|---|
| en-us | **7** |
| id, uk | **5** |
| all other 44 | **4** |

Only seven languages have localised images at all (en, de, es, fr, hi, ja, zh);
the other forty reuse the English ones. Uploading `en_7` into slot 4, or a
localised image into the English slot, captions the wrong picture.

The captions themselves are text and are handled by
`store_assets/build_listing_fields.py --set-slotted`, which reads the same slot
map. Images cannot go through the CSV: those cells hold Partner Center asset
URLs, so a new image is uploaded as a listing asset first and its URL appears
only in a later re-export.
