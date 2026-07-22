# Microsoft Store screenshots

63 PNGs, 1924 × 1175 — inside the Store's 1366 × 768 to 3840 × 2160 range, and
9 per language against a limit of 10. German is 2075 × 1175: its labels are
longer, so the window grows to fit, and the result is still well inside range.

Captured from the real application against **live EasyPost test-mode data**, not
mockups: the 18 carrier rates on `02_create_shipment` are genuine USPS, FedEx
and UPS responses, and the five address-book rows were verified through
EasyPost's address API. Nothing was purchased and no real carrier charge was
incurred.

## Localised sets

Seven languages. Five are the most spoken worldwide by total speakers, with
English included as required; German and Japanese are here because the listing
already carried localised imagery for them and falling back to English would
have been a regression.

| Folder | Partner Center language | |
|---|---|---|
| `en/` | English (United States) | `en-us` |
| `zh/` | Chinese (Simplified) | `zh-hans` |
| `hi/` | Hindi | `hi` |
| `es/` | Spanish | `es` |
| `fr/` | French | `fr` |
| `de/` | German | `de` |
| `ja/` | Japanese | `ja` |

## Every other language uses the English set

The remaining 40 listing languages reference `en/` unchanged:

```
am ar bn cs el fa gu ha he hr hu id ig-latn it kn ko ml mr ms ne nl or pa
pl pt ro ru si sv sw ta te th tr uk ur uz vi yo-latn zu
```

The application is still fully translated into all 50 — only the *listing
imagery* falls back to English, which is normal practice and costs nothing in
discoverability.

## What each shot shows

| File | Page |
|---|---|
| `01_dashboard` | Landing view |
| `02_create_shipment` | **Lead image.** Rate shopping with colour-coded carrier chips, Cheapest/Fastest tags, cheapest-first ordering |
| `03_address_book` | Verified address storage |
| `04_tracking` | Parcel tracking |
| `05_history` | Purchased shipments, refunds |
| `06_batch` | CSV bulk import |
| `07_reports` | Spend by carrier |
| `08_hts_lookup` | Live USITC tariff-code search |
| `09_settings` | Label format and size, language |

Use `02_create_shipment` as the first screenshot in the listing — it is the one
that shows what the product actually does.

## Two deliberate choices

**The MCP variant flag was removed before capture.** The Store build cannot run
the MCP server, so these screenshots are of a build configured exactly like the
submitted package. Advertising a feature absent from the uploaded `.msix` would
misrepresent it. "Connect AI Agents" still appears in the sidebar because it is
present in the Store build too — it opens a page explaining the feature needs
the direct download.

**No donation banner appears** because it was removed from the product
entirely. A paid Store listing soliciting donations through an external payment
processor is a certification risk, so it is gone from every build rather than
merely hidden for these captures.

## Reproducing

`store_assets/shoot_screenshots.py <locale>` drives the real window through Qt — sets the
locale, builds `MainWindow`, walks the sidebar, and grabs each page. Scripted
rather than mouse-driven so the five sets are identical but for language.
