# Version 1.2.0

- **Batch shipping now picks a carrier and service.** A batch is never priced by
  EasyPost, so the service has to be chosen before the batch is created — there
  are no rates to pick from afterwards. A new step on the Batch page lists the
  services each carrier really offers, with a filter for carriers that publish
  hundreds of them.
- **Signature, insurance and tracking options for a batch.** Royal Mail's Signed
  For services are only sold with signature requested, so choosing one now sets
  that automatically instead of failing at purchase.
- **Scheduling a pickup works.** Previously every request was refused.
- **Insurance claims for damage and theft work.** Previously these could not be
  filed at all. Supporting photos and invoices can now be attached.
- **Address verification is honest.** Addresses EasyPost cannot confirm are shown
  as unverified rather than being recorded as verified. An address that fails to
  match reference data can still be used for shipping — flats and newer builds
  often fail to match — and the app now says so instead of implying a problem.
- **Failures explain themselves.** When a carrier declines to quote, or a
  shipment in a batch is not bought, the reason is shown rather than the parcel
  quietly going missing from the list.
- **Print sheets from a batch.** Exporting a label sheet after a batch purchase
  now works.
- **Tracking keeps up.** Bought labels are added to Tracking automatically,
  delivery problems show the specific reason, and a background refresh that
  fails no longer interrupts what you are doing.
- Carrier names, customs declarations, insurance limits and label formats now
  match what the carriers actually expect. Refinements and fixes throughout.

**Windows:** unzip and run `EasyPostDesktop.exe` (a SmartScreen warning is
expected on first run — see the [download page](https://easy-post.spencerfields.com/download.html)).
**macOS:** notarized by Apple. Verify with the SHA-256 checksums below.
