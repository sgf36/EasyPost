# Easy-Post Desktop — release notes 1.6.0

Covers everything since 1.3.2, which was the last published version on both
stores. The version jump to 1.6.0 is deliberate: 1.4.0 was submitted to the Mac
App Store, 1.5.0 went live on the Microsoft Store, and 1.6.0 brings all three
channels (Partner Center, Mac App Store, direct download) back in sync.

## What is in 1.6.0 for a customer

**Royal Mail Click & Drop — ship with Royal Mail directly (#92).** A second
carrier path alongside EasyPost. Enter a Click & Drop API key in Settings, and
Royal Mail services appear in the rates tree next to EasyPost rates. The same
buy-and-track flow as EasyPost: one-click label purchase, local PDF save,
tracking number in History.

**Carrier manifesting / scan forms (#88, #90).** Generate end-of-day manifests
for your carriers, a requirement for some collection services. Royal Mail
manifest PDFs that arrived with formatting errors are now regenerated locally
with the correct layout.

**Smart dropdown filtering.** All address dropdowns now support type-to-filter
with substring matching — start typing any part of a name, company or address
and the list narrows in real time. Works across Create Shipment and Batch Import.

**Inline recipient entry.** Ship to someone without adding them to the address
book first. Select "Enter new recipient…" at the bottom of the To dropdown and
fill in the address fields inline. A "Save to address book" checkbox
(on by default) saves the address automatically after a successful purchase.

**Multiple macOS stability fixes (#83–87).** API key fields, focus behaviour and
entitlements are corrected for macOS Tahoe. These only affect the Mac App Store
build.

## Copy for the store listings

English master. The macOS-specific fixes are review compliance, not customer
features, so the copy is the same on both stores.

> Ship with Royal Mail directly through Click & Drop, alongside your EasyPost
> carriers. Enter a Click & Drop API key in Settings and Royal Mail services
> appear in the rates tree.
>
> • Generate end-of-day carrier manifests (scan forms) from the new Manifests
>   page, and print corrected Royal Mail manifest PDFs.
> • Type-to-filter dropdowns with substring matching across the app.
> • Enter a new recipient directly on the Create Shipment page — no address
>   book entry required.
> • Multiple macOS stability improvements.
