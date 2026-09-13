# Easy-Post Desktop — release notes 1.3.0

Both stores and the direct download are serving 1.2.9, so this covers every
change since then. The number was bumped in #49 only to reopen the App Store
build train; the application itself changed afterwards, in the four pull
requests below, so a minor version now describes it honestly.

## What is in 1.3.0 for a customer

**A real Dashboard (#61).** The page the app opens on was a placeholder that
told users, in all fifty languages, that shipments, tracking and reporting "are
added in later build stages" — although every one of them had shipped. It now
shows the active mode's own records: parcels still being tracked, delivery
problems from the last 30 days, refunds waiting for the carrier, postage spend
per currency and the latest shipments. It reads only the local database, so it
opens at once and works offline.

**Services follow the carrier (#55).** Wherever a carrier is chosen — Create
Shipment, Batch, the batch template, Tracking and Insurance — the choices
beneath it narrow to that carrier. When a filter hides rates the page says how
many, so a hidden service is never mistaken for a missing one.

**Tracking accepts Evri, DHL eCommerce and FedEx Ground Economy (#56).**
EasyPost's tracker endpoint refuses the catalogue codes for these three, so
picking them failed every time. Measured in test mode against the whole
catalogue.

**Direct download only: the Buy a licence button works (#63).** It opened a
"purchases coming soon" dialog while the Paddle prices were live. It now opens
the pricing page. Store editions unlock through the Store and never showed this
button, so the store notes do not mention it.

## Copy for the store listings

English master, identical on both stores. Deliberately short: translations
expand, and the Microsoft Store caps "What's new" at 1500 characters.

> Easy-Post Desktop now opens on a Dashboard built from your own records:
> parcels still being tracked, delivery problems from the last 30 days, refunds
> waiting for the carrier, postage spend and your latest shipments. It works
> offline and keeps test and production records apart.
>
> • Choose a carrier and the services, packages and rates beneath it narrow to
> that carrier, everywhere a carrier is chosen.
> • Tracking now works for Evri, DHL eCommerce and FedEx Ground Economy, which
> previously failed when picked.

Translations: `release-notes-1.3.0-translations.json`, keyed by Partner Center
language code. The Mac App Store's 28 locales take the same text through the
App Store Connect API.

## Screenshots

The Mac App Store's seven localised sets still showed an "Android app" page in
the sidebar, retired in #21. They are re-rendered from this release's code for
the 1.3.0 submission. The Microsoft Store listing's screenshots are not changed
in this release.
