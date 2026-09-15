# Easy-Post Desktop — release notes 1.3.2

1.3.1 is already approved in Partner Center, and a package can only replace it
with a higher version, so the fixes merged after the `v1.3.1` tag ship as 1.3.2.

## What is in 1.3.2 for a customer

**DHL eCommerce Solutions customs no longer fails (#80).** Each customs item now
sends its tariff number in the field DHL eCS requires, as well as the usual one.
International DHL eCS shipments with an HTS code were refused with
"code: field required"; other carriers ignore the extra field.

**One batch can mix carriers and services (#80).** The batch spreadsheet takes
optional `carrier` and `service` columns. A row that fills them overrides the
batch-level choice, so Royal Mail domestic and DHL eCS international rows can go
in one import. The preview table shows each row's carrier and service.

**Mac App Store build only (#78, #79).** The unused Downloads entitlement is
removed, sign-up links show as plain text instead of opening Safari and licence
key entry is hidden. None of this changes the Windows or direct-download builds.

## Copy for the store listings

English master. The Mac changes are review compliance, not customer features, so
the copy is the same on both stores.

> • DHL eCommerce Solutions international shipments with a tariff code no longer
>   fail with a customs error.
> • A batch spreadsheet can now set the carrier and service on each row, so one
>   import can mix carriers. Rows that leave them blank use the batch's choice.
