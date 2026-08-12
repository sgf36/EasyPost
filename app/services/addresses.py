"""Address verification (EasyPost) + local address book persistence."""

from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor


@dataclass
class AddressRecord:
    id: str
    mode: str
    label: Optional[str]
    name: Optional[str]
    company: Optional[str]
    street1: Optional[str]
    street2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]
    country: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    verified: bool
    is_favorite: bool


class AddressVerificationError(RuntimeError):
    """Raised when EasyPost reports the address could not be verified. The
    address is still created on EasyPost's side (we use non-strict `verify`
    rather than `verify_strict`, specifically so this always carries a real
    address the caller can choose to save anyway)."""

    def __init__(self, messages: list[str], address) -> None:
        super().__init__("; ".join(messages) or "Address could not be verified.")
        self.messages = messages
        self.address = address


def verify_address(
    *,
    name: str = "",
    company: str = "",
    street1: str,
    street2: str = "",
    city: str,
    state: str,
    zip: str,
    country: str,
    phone: str = "",
    email: str = "",
):
    """Create + verify an address via EasyPost. Returns the EasyPost Address
    object on success; raises AddressVerificationError (carrying that same
    address, for an explicit user override) on verification failure.
    """
    client = client_manager.get_client()
    address = client.address.create(
        verify=True,
        name=name or None,
        company=company or None,
        street1=street1,
        street2=street2 or None,
        city=city,
        state=state,
        zip=zip,
        country=country,
        phone=phone or None,
        email=email or None,
    )

    delivery = _delivery_verification(address)
    # `success` is only trustworthy when it is explicitly True. Anything else —
    # False, or absent — must not be reported to the user as verified.
    if delivery is not None and delivery.get("success") is not True:
        errors = delivery.get("errors") or []
        messages = [_verification_message(e) for e in errors]
        raise AddressVerificationError(messages, address)

    return address


def _delivery_verification(address):
    """The `verifications.delivery` sub-object, or None when absent.

    Deliberately NOT guarded with ``isinstance(..., dict)``: the SDK converts
    every nested response object into an ``EasyPostObject``, which implements
    ``.get()`` but does **not** subclass ``dict``. An isinstance check against
    dict is therefore always False, which silently disabled failure detection
    entirely and marked every address — including undeliverable ones — as
    verified.
    """
    verifications = getattr(address, "verifications", None)
    if verifications is None:
        return None
    return verifications.get("delivery")


def _verification_message(error) -> str:
    """One human-readable line per verification error, with EasyPost's own
    suggested correction appended when it offers one."""
    message = error.get("message") or "Unknown verification error"
    suggestion = error.get("suggestion")
    return f"{message} ({suggestion})" if suggestion else message


def address_is_verified(address) -> bool:
    """Whether EasyPost positively confirmed this address is deliverable.

    Only an explicit ``success is True`` counts. EasyPost returns no delivery
    verification at all for countries where address validation does not apply,
    and "we did not check" is not the same as "this is deliverable" — so the
    absent case is reported as unverified rather than assumed good.
    """
    delivery = _delivery_verification(address)
    return delivery is not None and delivery.get("success") is True


def save_address_locally(
    address, *, label: Optional[str] = None, favorite: bool = False, verified: bool = True
) -> None:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO addresses (
                id, mode, label, name, company, street1, street2, city,
                state, zip, country, phone, email, verified, is_favorite
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label=excluded.label, is_favorite=excluded.is_favorite,
                verified=excluded.verified
            """,
            (
                address.id,
                mode,
                label,
                getattr(address, "name", None),
                getattr(address, "company", None),
                getattr(address, "street1", None),
                getattr(address, "street2", None),
                getattr(address, "city", None),
                getattr(address, "state", None),
                getattr(address, "zip", None),
                getattr(address, "country", None),
                getattr(address, "phone", None),
                getattr(address, "email", None),
                1 if verified else 0,
                1 if favorite else 0,
            ),
        )


def list_addresses(favorites_only: bool = False) -> list[AddressRecord]:
    mode = client_manager.active_mode
    query = "SELECT * FROM addresses WHERE mode = ?"
    params: list = [mode]
    if favorites_only:
        query += " AND is_favorite = 1"
    query += " ORDER BY is_favorite DESC, created_at DESC"

    with db_cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [
        AddressRecord(
            id=row["id"],
            mode=row["mode"],
            label=row["label"],
            name=row["name"],
            company=row["company"],
            street1=row["street1"],
            street2=row["street2"],
            city=row["city"],
            state=row["state"],
            zip=row["zip"],
            country=row["country"],
            phone=row["phone"],
            email=row["email"],
            verified=bool(row["verified"]),
            is_favorite=bool(row["is_favorite"]),
        )
        for row in rows
    ]


def delete_address(address_id: str) -> None:
    with db_cursor() as cur:
        cur.execute("DELETE FROM addresses WHERE id = ?", (address_id,))


def set_favorite(address_id: str, favorite: bool) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE addresses SET is_favorite = ? WHERE id = ?",
            (1 if favorite else 0, address_id),
        )


def address_choice_label(rec) -> str:
    """One line describing a saved address for a dropdown.

    Joins only the parts that exist. The previous f-string always inserted the
    comma before `state`, so every United Kingdom address — which has no state —
    read "London," with a stranded trailing comma.
    """
    where = ", ".join(part for part in (rec.city, rec.state) if part)
    name = rec.label or rec.name or rec.id
    return f"{name} — {where}" if where else str(name)
