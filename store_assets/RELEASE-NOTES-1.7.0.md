# Easy-Post Desktop — release notes 1.7.0

## What is in 1.7.0 for a customer

**Envelope printing.** Six envelope-size label templates — DL, C5, C4 (ISO) and
#10, 6×9, 9×12 (US) — join the existing die-cut and plain-paper sheet templates.
Select an envelope size in the Print Sheet dialog and print directly onto the
envelope: each label gets its own envelope, the same calibration offsets and
printer-type margins (laser/inkjet) apply, and the preview shows the real layout.

**Mixed carrier/service batches.** A single batch spreadsheet can now send rows
via different carriers and services — for example Royal Mail domestic alongside
DHL eCS international. Fill the `carrier` and `service` columns per row in the
.xlsx template; when every row carries its own, the batch-level service picker
is skipped entirely. The downloaded template now includes a carrier dropdown and
a "Service Reference" sheet listing every available service code per carrier.

**Per-row carrier/service validation.** The import preview catches invalid
carrier or service codes before a batch is created, instead of failing at
purchase. Carrier names are validated against the live catalogue, and a service
without a carrier (or vice versa) is flagged immediately.

## Copy for the store listings

English master. The same copy applies on both stores.

> Print shipping labels directly onto envelopes — DL, C5, C4, #10, 6×9, and
> 9×12 sizes, with laser and inkjet margins.
>
> • Mix carriers and services in one batch: fill per-row carrier and service
>   columns in the spreadsheet template instead of choosing one for all rows.
> • Downloaded batch templates now include a carrier dropdown and a service
>   reference sheet.
> • Invalid carrier or service codes are caught at import, before a batch is
>   created.
