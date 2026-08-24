# Easy-Post Desktop — release notes 1.2.7

**There is no Microsoft Store submission for 1.2.7, and that is deliberate.**
The Store stays on **1.2.6.0**. This file exists so the gap in the version
sequence reads as a decision rather than an oversight, and so the same question
is not re-derived at the next release.

## Why

Version 1.2.7 changes two things, and neither is visible to a Store customer.

**The Windows executables are code-signed.** Azure Artifact Signing now signs
`EasyPostDesktop.exe` and `easypost-mcp.exe` with a Public Trust certificate, so
the direct download no longer raises the SmartScreen "unknown publisher" prompt.
The Store package is re-signed by Microsoft on publish and never carried that
prompt, so the certificate changes nothing there. See
`CI-AZURE-SIGNING-SETUP.md`.

**The Android download page came out of Tools.** At 1.2.6 that page was gated in
`main_window.py` on `not (STORE_BUILD or MAS_BUILD) and production_allowed()`, so
it appeared only on a direct-download build held by a production licensee. It was
never reachable on the Store build. Removing it removes nothing a Store customer
could see, and the locale strings deleted alongside it were never rendered there
either.

A "What's new" entry describing either would be untrue for the audience reading
it. Submitting anyway would spend a certification cycle and a manual 90 MB
package upload to deliver no change, and would consume a 47-language translation
run to say nothing.

## What the Store is actually serving

Confirmed on 2026-08-24 by reading the live listing rather than inferring it from
the repository:

| Field | Value |
|---|---|
| `LastUpdateDateUtc` | `2026-08-21T11:26:17Z` |
| `Notes` ("What's new") | verbatim the 1.2.6 copy in `RELEASE-NOTES-1.2.6.md` |
| `FirstAvailableDate` | `2026-07-31T22:43:57Z` |

**`FirstAvailableDate` is the original publication date, not the last update, and
reading it as the latter makes the Store look months out of date when it is
current.** That mistake was made once already. The listing can be read without
Partner Center:

```bash
curl -s "https://storeedgefd.dsx.mp.microsoft.com/v9.0/products/9NDSDL5LV5B5?market=GB&locale=en-GB&deviceFamily=Windows.Desktop"
```

The package **version** is not exposed by that endpoint — it returns `Unknown` —
so a version claim needs Partner Center. The `Notes` field is the better signal,
because release notes are written per version and can be matched against the
files in this directory.

## The manifest is already at 1.2.7.0

`packaging/msix/AppxManifest.xml` was bumped with `app/config.py`, because the two
must not drift. Nothing is wrong with that: Partner Center only requires a
package version to **increase**, so a build made from this tree submits cleanly
whenever the next substantive change ships. That submission carries everything
between 1.2.6 and whatever version it is, so its release notes must cover the
whole span, not only the final increment.

## When the next Store submission happens

Follow `RELEASE-NOTES-1.2.6.md` — the process there is unchanged. In short:

- Write the master English copy **short**. Roughly 560 characters is right
  against the **1500** cap, because English is among the most compact of the 47
  and translations expand. A 1431-character English draft at 1.2.2 put thirteen
  translations over the limit, Tamil worst at 1633.
- Do not translate interface names freely. Take them from
  `app/resources/locales/<code>.json` so the notes name the page the reader will
  actually look for, remembering that three store codes differ from the
  application's locale filenames: `zh-hans` → `zh.json`, `yo-latn` → `yo.json`,
  `ig-latn` → `ig.json`.
- Release notes alone need **no staged import** — a text-only change mints no
  Partner Center assets, so it is a single bare CSV through **Import .csv**.
- Always build from a **fresh** export of the current submission.

```bash
python store_assets/build_releasenotes_import.py \
    --export "<fresh listingData-9NDSDL5LV5B5-*.csv>" \
    --translations store_assets/release-notes-<version>-translations.json \
    --out "<listingData-....IMPORT.csv>"
```

## Separately outstanding on the listing

Not a 1.2.7 matter, and not fixed here, but it should not be lost: the listing
declares **no in-app purchases** while `HasAddOns` is true and the
`production_unlock` add-on is live at $29.99. The "allows users to make
purchases" box under Properties was unticked on 2026-08-03, when the listing had
zero add-ons and the badge was a phantom. That condition no longer holds. Two
things are worth weighing before changing it: the feature bullets already say
"Free in EasyPost test mode; one-time unlock for live labels", so the cost is not
hidden, and re-ticking the box may reopen the age-rating questionnaire.
