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


def delivery_days(rate) -> Optional[int]:
    """The carrier's estimate in whole days, or None when it gave none."""
    try:
        return int(getattr(rate, "delivery_days", None))
    except (TypeError, ValueError):
        return None


def is_priced(rate) -> bool:
    """A readable figure that is a real quote: not a marker of either kind.

    Only these can be compared with one another. A placeholder cannot be bought
    and an account-billed figure is not what the label costs, so ranking either
    against a real price says nothing true.
    """
    return (
        rate_amount(rate) is not None
        and not is_account_billed(rate)
        and not is_placeholder_rate(rate)
    )


def _currency(rate) -> str:
    return (getattr(rate, "currency", "") or "").upper()


def comparison_currency(rates, preferred: Optional[str] = None) -> Optional[str]:
    """The one currency "cheapest" and "fastest" are judged in.

    Rates come back in each carrier account's own currency, and nothing here
    knows an exchange rate, so 3.25 GBP and 7.04 USD cannot be ranked. Comparing
    the bare numbers crowned a Royal Mail domestic service "cheapest" on a US to
    US parcel, and buying it failed: a carrier quoting in a foreign currency is
    very often one that cannot serve the route at all.

    ``preferred`` is the sender's own currency, and it wins whenever any real
    quote is in it: a London seller thinks in pounds, and those are the carriers
    that serve London. Otherwise the currency most real quotes share, which is
    the market the shipment is actually being priced in. Ties go to the
    alphabetically first code only so the answer never flickers between runs.
    None when nothing is priced.
    """
    counts: dict[str, int] = {}
    for rate in rates:
        if is_priced(rate) and _currency(rate):
            counts[_currency(rate)] = counts.get(_currency(rate), 0) + 1
    if not counts:
        return None
    wanted = (preferred or "").upper()
    if wanted in counts:
        return wanted
    return min(counts, key=lambda code: (-counts[code], code))


def _comparable(rates, currency: Optional[str], preferred: Optional[str]) -> list:
    if currency is None:
        currency = comparison_currency(rates, preferred)
    return [r for r in rates if is_priced(r) and _currency(r) == (currency or "").upper()]


def cheapest_rate_id(rates, currency: Optional[str] = None,
                     preferred: Optional[str] = None) -> Optional[str]:
    """Id of the cheapest real quote in one currency.

    Account-billed rates never count: their sub-penny figure is not a comparable
    amount, so counting them would crown a Royal Mail service "cheapest"
    whatever it really costs. Rates in any other currency never count either
    (see comparison_currency). None when nothing is priced.
    """
    comparable = _comparable(rates, currency, preferred)
    if not comparable:
        return None
    return min(comparable, key=sort_key).id


def fastest_rate_id(rates, currency: Optional[str] = None,
                    preferred: Optional[str] = None) -> Optional[str]:
    """Id of the quickest real quote, judged among the same rates as cheapest.

    Speed has no currency, but the badge is a recommendation, and the rates
    excluded from "cheapest" are exactly the ones least likely to be buyable on
    this route: foreign-currency catalogues and account-billed markers, of which
    Royal Mail returns sixty at a time whether or not they apply. None when no
    such rate carries an estimate.
    """
    timed = [r for r in _comparable(rates, currency, preferred) if delivery_days(r) is not None]
    if not timed:
        return None
    return min(timed, key=delivery_days).id


def order_carriers(rates, currency: Optional[str] = None,
                   declined=()) -> list[str]:
    """Carrier codes in the order their groups should be shown.

    The first group is the one a new customer buys from, so it has to be one that
    can serve the route:

    1. carriers with a real quote in the comparison currency, cheapest first;
    2. carriers whose real quotes are all in another currency;
    3. carriers offering only account-billed or placeholder figures.

    Within each tier a carrier that also reported a problem with this shipment
    (``declined``, from the shipment's messages) sorts after one that did not.
    Names break the remaining ties so the order is stable.
    """
    by_carrier: dict[str, list] = {}
    for rate in rates:
        by_carrier.setdefault(getattr(rate, "carrier", "") or "", []).append(rate)
    currency = (currency or "").upper()
    flagged = {str(code) for code in declined}

    def key(carrier: str):
        priced = [r for r in by_carrier[carrier] if is_priced(r)]
        local = [r for r in priced if _currency(r) == currency]
        if local:
            tier, cheapest = 0, min(sort_key(r) for r in local)
        elif priced:
            tier, cheapest = 1, min(sort_key(r) for r in priced)
        else:
            tier, cheapest = 2, 0.0
        return (tier, carrier in flagged, cheapest, carrier)

    return sorted(by_carrier, key=key)
