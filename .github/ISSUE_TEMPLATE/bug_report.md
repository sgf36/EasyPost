---
name: Bug report
about: Something in Easy-Post Desktop does not work as expected
title: ''
labels: bug
assignees: ''

---

<!--
NEVER PASTE AN API KEY, A LICENCE KEY OR A TRACKING NUMBER YOU CARE ABOUT.

This repository is public. An EasyPost production key posted here can buy
postage on your account. If a key has already been pasted anywhere public,
revoke it in the EasyPost dashboard first, then edit it out.

Screenshots often contain a key, an address or a customer name. Check before
attaching one.
-->

## What happened

<!-- What the application did. -->

## What was expected instead

## Steps to reproduce

1.
2.
3.

## Environment

| | |
|---|---|
| **Easy-Post Desktop version** | <!-- e.g. 1.2.5 --> |
| **Operating system** | <!-- e.g. Windows 11 24H2, or macOS 15.5 --> |
| **Installed from** | <!-- direct download, Microsoft Store, or Mac App Store --> |
| **Mode** | <!-- test or production --> |

**Mode matters more than anything else here.** A great many differences between
test and production are the API behaving differently, not the application, so a
report without it usually cannot be acted on.

## Where in the application

<!-- Delete those that do not apply. -->

Dashboard · Create shipment · Address book · Batch · Tracking · History ·
Reports · Claims · Insurance · Pickups · HTS lookup · Customs · Settings ·
Mobile pairing · Licence or activation · Installation or update

## Was postage bought

<!--
Answer this even when it seems irrelevant. Anything that may have charged a
carrier account is triaged first.
-->

- [ ] No money changed hands
- [ ] A label was bought
- [ ] Unsure

If a label was bought, give the **carrier and service** and say whether the
charge appeared in the EasyPost dashboard. A shipment identifier beginning
`shp_` is useful and safe to share. A tracking number is not, since it exposes
the recipient.

## Anything else

<!--
An error message, copied as text rather than a screenshot where possible.

The application keeps its database and settings in the per-user application
data directory. Do not attach `easypost_desktop.sqlite3`, which contains the
whole address book, and do not attach `settings.json` without removing any key
from it first.
-->
