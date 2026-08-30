# Microsoft Store listing — the next submission

**Drafted 2026-08-29.** One submission, carrying four changes. They are bundled deliberately:
each one alone triggers a certification cycle, and the listing has already been through three
rounds this month.

Supersedes the Microsoft section of `marketing/app-store-optimisation.md`, whose proposed copy
**does not fit** — see §2.

---

## 1. What is live today

Read on 2026-08-29 from the display catalogue, which needs no authentication:

```bash
curl -s "https://displaycatalog.mp.microsoft.com/v7.0/products/9NDSDL5LV5B5?languages=en-GB&market=GB"
```

| Field | Live value |
|---|---|
| Package version | `1.2.6.0` (packed integer `281483567038464`) |
| Short description | 172 characters, opens *and* closes on the EasyPost prerequisite |
| Feature bullets | **absent in every language checked** |
| Ratings | **0** |

Feature bullets being absent is a *finding*, not an unknown: `Features` renders publicly, so a
null value is evidence. Search terms are the opposite — they are never public, so their absence
from the response proves nothing and the console is the only way to know.

---

## 2. CORRECTION — the cap is 1000, not 500, and the proposed copy fits

**An earlier version of this document said the ASO pack's 415-character short
description would fail the import. That was wrong.** The cap was taken from
`marketing/app-store-optimisation.md`, which records the live string as
"(172/500)". Nothing checked it.

`store_assets/build_listing_fields.py` is the authority and has been all along:

```python
LIMITS = {
    "ReleaseNotes": 1500,
    "Description": 10000,
    "ShortDescription": 1000,
}
```

Corroborated by `feedback-partner-center-listing-import`, which records the same
caps from a live export and a rejected import. **ShortDescription is 1000.**

So the 415-character draft reaches roughly 528 characters in the worst language
and is comfortably inside the limit. It was never at risk.

### What survives from the mistake

The expansion measurement is still correct and still worth keeping, because it was
taken from this listing's own translations rather than assumed:

| Language | Live length | Factor |
|---|---|---|
| Zulu | 219 | **x1.27** |
| French | 210 | x1.22 |
| Spanish | 207 | x1.20 |
| Yoruba | 203 | x1.18 |
| Italian | 202 | x1.17 |
| *(Chinese, Korean and Japanese contract, to x0.45-0.58)* | | |

At x1.27 the safe **English** ceiling is 787 characters, not 393. The rule still
holds -- write to the worst expander, not to the English cap -- only the number
was wrong.

**The real finding is the opposite of the one this document was written around.**
The live short description is **172 characters against a 1000 limit**: the most-read
string in the listing, the card a stranger sees in search results, is using a sixth
of its budget. The problem was never that the replacement was too long.

---

## 3. Short description

Both candidates fit. This is now an editorial choice rather than a constraint, and
the longer one says more:

> Buy and print carrier shipping labels from a fast native Windows app. Compare live
> rates cheapest-first, print to thermal or plain paper, track every parcel, handle
> customs, insurance and claims. Free to install and free to trial. It runs on your
> own EasyPost account, which is free to open, so postage is billed to you at your
> own rates with no markup. Independent open-source client, not affiliated with
> EasyPost.

415 characters, about 528 in Zulu, against 1000.

The 370-character version written under the mistaken cap is kept below as the
shorter option, not because it is safer:

> Buy and print carrier shipping labels from a fast native Windows app. Compare live
> rates cheapest-first, print to thermal or plain paper and track every parcel, with
> customs, insurance and claims built in. Free to install, on your own free EasyPost
> account, so postage bills at your own rates with no markup. Independent open-source
> client, not affiliated with EasyPost.

Either reorders the copy so the product is described before the prerequisite. The
live string opens with "for your EasyPost account" and closes with "an EasyPost
account is required", so a stranger reading the search card meets the
disqualification twice before learning what the application does.

"Open-source" is accurate and checked: the repository is public and MIT licensed.

---

## 4. Feature bullets — six, none longer than 77 characters

These render prominently and are currently empty, so this is the largest single improvement
available. All are comfortably inside the 200-character cap even at ×1.27.

1. Compare live rates from every carrier on your account, cheapest first
2. Print to a thermal label printer or plain paper, including batch label sheets
3. Track every parcel, with delivery status shown in your own language
4. Customs forms, insurance and claims handled in the same window
5. Reports on spend by carrier, service and destination
6. Free to install and free in test mode; one-time unlock for live labels

Bullet 6 is doing double duty — see §6.

---

## 5. Search terms — seven, not translated

Not public, so confirm in the console whether these are already set before assuming a free win.

`shipping labels` · `print postage` · `label printer` · `parcel tracking` ·
`shipping software` · `thermal label printer` · `EasyPost client`

They are problem words rather than brand words, except the last. Naming EasyPost is deliberate
and defensible — the application genuinely is a client for that service, which is nominative
use — but it is the one term worth dropping if it ever draws an objection.

---

## 6. The in-app purchase declaration — bundle it here

The listing declares **no in-app purchases** while `HasAddOns` is true and `production_unlock`
sells at $29.99. The "allows users to make purchases" box was unticked on 2026-08-03, when the
listing genuinely had zero add-ons and the badge was a phantom. That has not been true since.

It belongs in this submission rather than its own, because it is a Properties change and would
otherwise spend a second certification cycle to correct a single checkbox.

**Two things to weigh before ticking it**, neither of which blocks the rest of this document:

- Re-ticking may reopen the age-rating questionnaire. If it does, answer it and continue; the
  answers have not changed.
- Feature bullet 6 above states the cost in the listing body regardless, so the pricing is
  disclosed either way. Ticking the box makes the listing *accurate*, which is the actual
  reason to do it — not disclosure, which is already handled.

---

## 7. How to submit

Text-only changes need **no staged import** — a bare CSV through **Import .csv** is enough.
Staging is only required when a submission mints new assets, which is what
`feedback-partner-center-listing-import` records.

```bash
python store_assets/build_listing_import.py \
    --export "<fresh listingData-9NDSDL5LV5B5-*.csv>" \
    --translations store_assets/listing-copy-2026-08-translations.json \
    --out "<listingData-....IMPORT.csv>"
```

**Always build from a fresh export of the current submission**, never an older file.

### One thing to check first

The most recent export in this directory, `EasyPost-Store-Listings-IMPORT-v2-stage2.csv`, has
rows for `Description`, `ShortDescription`, `Title`, screenshots and the rest — **but no row for
feature bullets and none for search terms.** Two possible reasons, and they need different work:

- the export omits fields that are currently empty, in which case setting one bullet by hand in
  the console and re-exporting will make the row appear; or
- those fields are not importable by CSV at all, in which case the bullets must be entered in
  the console for each of the 47 languages, which is a materially larger job.

**Determine which before translating anything.** Set a single bullet in English in Partner
Center, re-export, and look for the row. Translating six bullets into 47 languages and then
discovering there is nowhere to import them would be the expensive order to do this in.

### Translation

47 languages. Take interface names from `app/resources/locales/<code>.json` rather than
translating them freely, so the copy names the page the reader will actually look for.
Three store codes differ from the application's locale filenames: `zh-hans` → `zh.json`,
`yo-latn` → `yo.json`, `ig-latn` → `ig.json`.

---

## 8. What this does not fix

Ratings are still zero, and no listing copy changes that. The in-app review prompt shipped for
the desktop application in a separate change; it needs three successful production shipments and
seven days before it can ask anyone, so nothing will appear here for weeks. Watch it with
`python tools/ratings_watch.py` and keep the numbers.

The Microsoft Store is, however, the one storefront **unaffected by the Digital Services Act
trader verification**, which is currently keeping all three Apple applications out of every
European Union storefront. That makes this listing the best available use of the time until
that clears.
