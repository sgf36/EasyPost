# Easy-Post Desktop — release notes 1.3.1

Fixes from the wave-one and wave-two audit, merged to `main` at 5155896. Mac
App Store 1.3.0 is in review; this is the first version that carries the audit
fixes on every platform.

## What is in 1.3.1 for a customer

**Rates are readable at any window size (#74).** Service names take their real
width, the label panel sits above the rates, and the "Cheapest" and "Fastest"
badges compare prices only within one currency. After buying, the label panel
scrolls into view instead of hiding 2,300 px below. From and To no longer
default to the same address.

**Key check tells you what went wrong (#73).** Offline, a typo and a key in
the wrong field now give distinct translated messages with an 8-second timeout,
instead of spinning for 84 seconds and then blaming the key.

**Refunded labels no longer count as spend (#72).** Reports and the Dashboard
exclude refunded postage and show pending refunds separately.

**Batch labels are recorded (#68).** Labels bought in a batch now reach
History, Tracking and Reports.

**Decimal-comma amounts are parsed correctly (#69).** "45,00" is now read as
45.00 rather than 4,500.

**Rates are invalidated when the parcel changes (#67).** Editing weight or
customs while rates are showing discards the stale rates and disables Buy. The
label is saved with its real format extension.

**Agent spending limits hold (#70, #72).** Placeholder and unknown prices are
treated as over-cap, successful purchases that fail to save are reconciled with
EasyPost, and the pickup TypeError is fixed.

**Unpair revokes phone access (#71, proxy #66).** "Unpair all phones" revokes
credentials on the server. Changing the production key revokes every paired
phone. The proxy no longer accepts routes that spend money.

## Copy for the store listings

English master, identical on both stores.

> The rates table is redesigned: service names take their real width, the bought
> label sits above the rates instead of below and the cheapest and fastest badges
> compare prices only within one currency. Checking a key now tells you what went
> wrong — offline, a typo and a key in the wrong field each get a distinct message.
>
> • Refunded labels no longer count as spend in Reports or on the Dashboard.
> • Batch labels now reach History, Tracking and Reports.
> • Decimal-comma amounts like "45,00" are parsed correctly.
> • Editing the parcel discards stale rates and disables Buy until fresh rates arrive.

Translations: `release-notes-1.3.1-translations.json`, keyed by Partner Center
language code.
