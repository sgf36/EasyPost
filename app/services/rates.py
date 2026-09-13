"""What a rate's price actually means, without Qt.

EasyPost hands back ``rate`` as a string, and for some carriers the number is
not a price at all. Two cases look identical on the wire — a figure below one
penny — and mean opposite things:

* **Account-billed.** Royal Mail via EasyPost (OBA billing) returns genuinely
  purchasable services at a nominal 0.01 because the real postage is invoiced
  to the account afterwards. The label can be bought; its price is unknown.
* **Placeholder.** Other carriers that return their whole catalogue price the
  services that do not apply to the route at 0.01. Those cannot be bought.

Anything that compares, totals or limits spend has to tell these apart from a
real quote, or a spending ceiling reads a Royal Mail Special Delivery label as
costing one penny. These rules used to live only inside the Create Shipment
view, so the agent purchase path never applied them.
"""

from __future__ import annotations

from typing import Optional

# No real shipping service costs a penny, so a figure below this is a marker
# (account-billed or placeholder), never a quote.
MIN_REAL_RATE = 0.02

# Carriers that invoice postage to the account rather than charging the label
# price up front. For these, a sub-MIN_REAL_RATE figure means "billed to
# account" and the label genuinely can be bought.
ACCOUNT_BILLED_CARRIERS = frozenset({"RoyalMail", "RoyalMailV3"})


def rate_amount(rate) -> Optional[float]:
    """The rate's figure as a number, or None when it cannot be read.

    None rather than 0: an unparseable price is unknown, and treating it as
    free is exactly the mistake a spending limit must not make.
    """
    try:
        value = float(getattr(rate, "rate", None))
    except (TypeError, ValueError):
        return None
    # NaN and infinity parse as floats but are not prices; both would slip
    # past a "greater than the limit" comparison.
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def is_account_billed(rate) -> bool:
    """True when the figure is a "billed to account" marker, not a quote.

    Such a rate IS purchasable, but its real price is not known until the
    carrier invoices it.
    """
    if getattr(rate, "carrier", "") not in ACCOUNT_BILLED_CARRIERS:
        return False
    amount = rate_amount(rate)
    return amount is not None and amount < MIN_REAL_RATE


def is_placeholder_rate(rate) -> bool:
    """A non-purchasable catalogue placeholder, priced below MIN_REAL_RATE.

    Account-billed rates sit below the same threshold but can be bought, so
    they are never placeholders.
    """
    if is_account_billed(rate):
        return False
    amount = rate_amount(rate)
    return amount is not None and amount < MIN_REAL_RATE


def sort_key(rate) -> float:
    """Cheapest first; an unreadable price sorts last instead of raising."""
    amount = rate_amount(rate)
    return float("inf") if amount is None else amount


def cheapest_rate_id(rates) -> Optional[str]:
    """Id of the cheapest rate, ignoring account-billed rates.

    Their sub-penny figure is not a comparable amount, so counting them would
    crown a Royal Mail service "cheapest" whatever it really costs. None when
    nothing is priced.
    """
    priced = [r for r in rates if not is_account_billed(r)]
    if not priced:
        return None
    return min(priced, key=sort_key).id
