# Easy-Post Desktop — release notes 1.2.9

**Both stores are still serving 1.2.6**, so this submission must cover
everything since then. 1.2.7 was deliberately skipped (see
`RELEASE-NOTES-1.2.7.md`) and 1.2.8 never reached a customer: it was submitted
to both stores and withdrawn before certification, because a defect was found
in the screenshots being prepared for the same release.

## Why 1.2.9 exists rather than a second 1.2.8

The rates table cut carrier and service names off in every language whose
interface words are longer than English — the Store screenshots for Tamil showed
`Stanc` and `Royal Mail 2` where the app should read `Standard` and
`Royal Mail 2nd Class`. Fixed in #43.

The fix landed after `v1.2.8` was tagged and released, so the tagged binaries,
the Mac App Store submission and the Partner Center package all carried code
that no longer matched the repository. Rebuilding under the same number would
have left two different builds both called 1.2.8, which is the drift these files
exist to prevent. The Mac submission was developer-rejected rather than allowed
to certify.

## What is in 1.2.9 for a customer

Two things, and only the first is visible on every channel.

**A review prompt on the Microsoft Store and Mac App Store editions.** It asks
after three labels have printed successfully, never in the first week, never
twice within four months, and stands down entirely if anything went wrong in
that session — a failed purchase, a refund, a problem reaching EasyPost. Three
requests is the lifetime ceiling. The direct download has no prompt, because
there is nowhere for that customer to leave a review.

**Carrier and service names are no longer cut off.** Only visible to somebody
using the app in a language other than English, which is most of the forty-seven
the listing is published in.

1.2.7's two changes stay unannounced on both stores for the reason
`RELEASE-NOTES-1.2.7.md` gives: the Windows signing is invisible to a Store
customer because Microsoft re-signs Store packages, and the Android download
page was gated on `not (STORE_BUILD or MAS_BUILD)`, so it never appeared on a
Store or Mac build at all.

## Copy for the store listings

English master. Deliberately short: English is among the most compact of the
forty-seven and translations expand, so a 1431-character draft at 1.2.2 put
thirteen translations over the 1500 cap, Tamil worst at 1633.

> Easy-Post Desktop can now ask whether you would like to rate it. It asks only
> after several successful labels, never in the first week, never twice in four
> months, and never after something has gone wrong. Dismissing it costs nothing.
>
> Carrier and service names in the rates table are no longer cut short in
> languages with longer interface words.

## Before submitting

- **Translate into all 47 languages** for the Microsoft Store and all 28 App
  Store locales for the Mac App Store. Take interface names from
  `app/resources/locales/<code>.json`, and remember three store codes differ
  from the application's locale filenames: `zh-hans` → `zh.json`,
  `yo-latn` → `yo.json`, `ig-latn` → `ig.json`.
- **Release notes alone need no staged import** — a text-only change mints no
  Partner Center assets, so it is a single bare CSV through **Import .csv**.
  Staging is only for screenshots.
- **Always build from a fresh export** of the current submission. Each
  successful import supersedes the export you built from.

## Screenshots

The forty non-English listings are being localised for the first time in this
release, which is what surfaced the truncation above. See `LISTING_IMPORT.md`
for the staged import, and note that Partner Center does not preserve the slot
order a folder import sends — captions must be set from the arrangement the
export comes back with, never from the order the folder was built in.
