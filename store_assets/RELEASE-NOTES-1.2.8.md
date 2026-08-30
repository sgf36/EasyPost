# Easy-Post Desktop — release notes 1.2.8

**The Microsoft Store submission for this version must cover everything since
1.2.6**, not just the increment. 1.2.7 was deliberately never submitted — see
`RELEASE-NOTES-1.2.7.md` — so the Store is still serving 1.2.6.0 and its
customers have seen none of the three changes below.

## What is in 1.2.8

The only change since 1.2.7 is the in-app review prompt (#31). It behaves
differently per channel, and only two of the three channels have anything to
announce.

> **Correction, 2026-08-30, made while submitting.** The sections below claimed
> the Tools-page removal is visible to a Store customer. It is not. At `v1.2.6`
> that entry was gated in `main_window.py` on
> `not (STORE_BUILD or MAS_BUILD) and production_allowed()`, so it never
> appeared on a Store or Mac App Store build at all — which is exactly what
> `RELEASE-NOTES-1.2.7.md` says, and why 1.2.7 was skipped on both. Verified by
> reading the code at the tag rather than by trusting either document.
>
> **Both submissions therefore describe the review prompt and nothing else.**
> The signing paragraph is dropped for the same reason it was dropped from
> 1.2.7: Microsoft re-signs Store packages, so the certificate changes nothing
> for the person reading the note. What shipped is in
> `release-notes-1.2.8-translations.json` (47 languages, longest 516 characters
> against the 1500 cap) and, for the Mac App Store, in 28 locales through the
> App Store Connect API.

**On the Microsoft Store and the Mac App Store**, the application may now ask
whether you would rate it. It asks at the moment a label renders successfully,
never on a first label, never within seven days of installation, never within
120 days of a previous ask, and never after anything has gone wrong in that
session — a failed purchase, a refund, an API error or a licence dialog all
stand it down. Three asks is the lifetime ceiling, deliberately below Apple's
own three per year.

**On the direct download there is no prompt at all**, and that is not an
oversight. There is nowhere to leave a review: Microsoft requires ownership
through the Store, and a Mac App Store review requires the application to have
come from it. Prompting a direct-download user would send them somewhere they
cannot act. That channel gets a passive item under Help instead, opening the
repository.

## Copy for the Store listing

English master, deliberately short. Roughly 560 characters is right against the
1500 cap because English is among the most compact of the 47 and translations
expand — a 1431-character draft at 1.2.2 put thirteen translations over, Tamil
worst at 1633.

> Easy-Post Desktop can now ask whether you would like to rate it. It asks only
> after several successful shipments, never in the first week, never twice in
> four months, and never after something has gone wrong. If it is not a good
> moment, dismissing it costs nothing.
>
> The Windows download is now signed with a Public Trust certificate, so
> Windows no longer warns about an unknown publisher.
>
> The Android download page has been removed from Tools. An application
> installed from a downloaded file cannot update itself, and the link made that
> everyone's problem quietly.

Only the first paragraph is new in 1.2.8. The second and third describe 1.2.7,
which this listing has never carried, and both are visible to somebody
installing from the Store: the certificate is not (Microsoft re-signs Store
packages) but the Tools page removal is, and the signing note matters to anyone
who also uses the direct download.

**Reconsider the signing paragraph before submitting.** `RELEASE-NOTES-1.2.7.md`
argues the certificate changes nothing for a Store customer, which is why 1.2.7
was skipped. If that reasoning holds, drop it and describe only the prompt and
the Tools removal. It is included here so the decision is made rather than
inherited.

## Before submitting

- **Translate into all 47 languages.** Take interface names from
  `app/resources/locales/<code>.json` rather than translating freely, and
  remember three store codes differ from the application's locale filenames:
  `zh-hans` → `zh.json`, `yo-latn` → `yo.json`, `ig-latn` → `ig.json`.
- **Release notes alone need no staged import** — a text-only change mints no
  Partner Center assets, so it is a single bare CSV through **Import .csv**.
  Staging is only for screenshots.
- **Always build from a fresh export** of the current submission. A stale base
  is rejected with "We couldn't process this .csv file."

```bash
python store_assets/build_releasenotes_import.py \
    --export "<fresh listingData-9NDSDL5LV5B5-*.csv>" \
    --translations store_assets/release-notes-1.2.8-translations.json \
    --out "<listingData-....IMPORT.csv>"
```

## Still outstanding on the listing, and not fixed here

~~The listing declares **no in-app purchases** while `production_unlock` is live
at $29.99.~~ **Not a defect — checked 2026-08-30 and closed.** The live listing
returns `HasAddOns: True`: Microsoft derives the in-app-purchase flag from the
published add-on and there is no checkbox to tick. The only related control in
Properties reads *"This product allows users to make purchases, but does not use
the Microsoft Store commerce system"*, and it must stay **unticked** —
`production_unlock` is a Store add-on and does use that system, so ticking it
would be a false declaration.

**The screenshots need nothing.** They show the current interface: nothing on
those nine screens has changed since 1.2.6, and the only two commits touching
`app/ui` since are the review prompt and the Android page removal, which was
never visible on a Store build.

A same-day audit claimed otherwise and was wrong. Hashing all 423 published
images showed slots 1-3 byte-identical to English in 36 of 47 languages, which
looked like a partial re-render. Compared position-free instead, **exactly 40
languages carry the nine English images reordered and exactly 7 have their own
set (en, de, es, fr, hi, ja, zh)** — the split `LISTING_IMPORT.md` describes as
normal practice. Slot order differs per language, so comparing slot against slot
compares different pages.
