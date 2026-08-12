# Easy-Post Desktop — release notes 1.2.0

Master English ("what's new") copy for the version 1.2.0 store listings. House
style: no Oxford commas, no abbreviations, no pronouns, British spelling.

Applies to the Microsoft Store listing (ReleaseNotes field) and, reworded per
platform limits, the App Store "What's New". Version 1.2.0 is a correctness
release: an audit against the live EasyPost interface found several operations
sending requests the platform does not accept. Batch shipping gains a carrier
and service step, so the Batch screenshot changes; the other pages are unchanged
from 1.1.3.

Written for a shipper, not a developer. Two features could not work at all
before this version, and saying so plainly is more useful than describing the
underlying cause.

## en-US ReleaseNotes

The Microsoft Store ReleaseNotes field caps at **1500 characters**, and every
translation has to fit the same cap. The copy below is 1494. A first draft ran to
1634 and would have been rejected outright, so three optional clauses were cut —
the tail of the first bullet, the wording of the second, and the parenthetical
about flats in the fifth. The 46 translations were compressed the same way, so
they stay faithful to this text rather than drifting from it. Check the count
before editing.

• Batch shipping now chooses a carrier and service — a batch is never priced by EasyPost, so the service has to be set before the batch is created. The new step lists the services each carrier really offers, with a filter for carriers publishing hundreds.

• Signature, insurance and tracking options for a batch — Royal Mail's Signed For services are sold only with signature requested, so choosing one now sets that automatically instead of failing at purchase.

• Scheduling a carrier pickup works. Every request was previously refused.

• Insurance claims for damage and theft can be filed, with supporting photographs and invoices attached. Neither could be filed at all before.

• Address verification is honest — an address EasyPost cannot confirm is shown as unverified rather than recorded as verified. An address that fails to match reference data can still be shipped from, and the app says so rather than implying a fault.

• Failures explain themselves — when a carrier declines to quote, or a shipment within a batch is not bought, the reason appears instead of the parcel quietly vanishing.

• Label print sheets can be exported after a batch purchase.

• Tracking keeps up — bought labels are added to Tracking automatically, delivery problems show the specific reason, and a background refresh that fails no longer interrupts your work.

• Carrier names, customs declarations, insurance limits and label formats now match what carriers expect. Refinements and fixes throughout.

## Notes for the translators

- "Signed For" is a Royal Mail product name. Translate the surrounding sentence
  but keep the product recognisable.
- "carrier" is a shipping company, never a vehicle.
- "batch" is a bulk group of shipments.
- "pickup" is a carrier collection from an address.
- "EasyPost" is a product name and stays untranslated.
