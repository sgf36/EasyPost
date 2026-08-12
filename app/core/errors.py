"""Formats exceptions and carrier messages for display in the UI.

EasyPost's client-facing exceptions carry a generic top-level `message`
(e.g. "The request could not be understood by the server due to malformed
syntax.") alongside a much more specific `errors` list (e.g. "From address
error: missing required customs address data: name of person or company").
Every error dialog in this app used to interpolate the exception directly
(`str(exc)`), which only surfaces the generic message and silently drops
the actionable detail — the exact reason Create Shipment errors were so
hard to diagnose.

Two shapes have to be handled, because EasyPost uses both. An entry in `errors`
is sometimes an object with `field`/`message`, and sometimes a bare string; and
an object entry can itself nest a further `errors` list, which is where the
genuinely specific reason often sits. Reading only `e["message"]` on dict
entries dropped both of the others on the floor.
"""

from typing import Any


def _get(item: Any, key: str, default=None):
    """Read a key from a dict or an EasyPostObject alike.

    ``EasyPostObject`` implements ``.get()`` but does **not** subclass ``dict``,
    so an ``isinstance(item, dict)`` test is False for every object that comes
    back from the API — the same trap that once disabled address verification
    entirely.
    """
    getter = getattr(item, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            return default
    return default


def _flatten_error(item: Any, depth: int = 0) -> list[str]:
    """One line per error, following nested `errors` lists down."""
    if item is None:
        return []
    if isinstance(item, str):
        return [item.strip()] if item.strip() else []
    if isinstance(item, (list, tuple)):
        return [line for entry in item for line in _flatten_error(entry, depth)]

    # Guard against a self-referential payload rather than trusting the shape.
    nested = _flatten_error(_get(item, "errors"), depth + 1) if depth < 5 else []
    if nested:
        return nested

    message = _get(item, "message")
    if isinstance(message, (list, tuple)):
        return _flatten_error(message, depth + 1)
    if not message:
        return []

    field = _get(item, "field")
    text = f"{field}: {message}" if field else str(message)
    suggestion = _get(item, "suggestion")
    return [f"{text} ({suggestion})" if suggestion else text]


def format_api_error(exc: Exception) -> str:
    """The most specific description of a failure that the exception carries."""
    lines = _flatten_error(getattr(exc, "errors", None))
    # De-duplicate while preserving order: one cause repeated across several
    # fields should read once.
    seen: set[str] = set()
    unique = [line for line in lines if not (line in seen or seen.add(line))]
    if unique:
        return "; ".join(unique)
    return str(exc)


def carrier_messages(obj) -> list[str]:
    """Carrier-level notes attached to a shipment, pickup or order.

    These are not errors — the call succeeded — but they are the only place
    EasyPost explains why a given carrier returned no rate at all ("Unable to
    retrieve rates: dimensions exceed maximum"). Without them a carrier simply
    goes missing from the rates table with no reason given anywhere.
    """
    messages = getattr(obj, "messages", None) or []
    lines: list[str] = []
    for entry in messages:
        text = _get(entry, "message")
        if not text:
            continue
        carrier = _get(entry, "carrier")
        lines.append(f"{carrier}: {text}" if carrier else str(text))
    seen: set[str] = set()
    return [line for line in lines if not (line in seen or seen.add(line))]
