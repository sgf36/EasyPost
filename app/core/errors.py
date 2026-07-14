"""Formats exceptions for display in error dialogs.

EasyPost's client-facing exceptions carry a generic top-level `message`
(e.g. "The request could not be understood by the server due to malformed
syntax.") alongside a much more specific `errors` list (e.g. "From address
error: missing required customs address data: name of person or company").
Every error dialog in this app used to interpolate the exception directly
(`str(exc)`), which only surfaces the generic message and silently drops
the actionable detail — the exact reason Create Shipment errors were so
hard to diagnose.
"""


def format_api_error(exc: Exception) -> str:
    errors = getattr(exc, "errors", None) or []
    messages = [e.get("message", "") for e in errors if isinstance(e, dict) and e.get("message")]
    if messages:
        return "; ".join(messages)
    return str(exc)
