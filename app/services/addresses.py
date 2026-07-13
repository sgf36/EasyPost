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

    verifications = getattr(address, "verifications", None) or {}
    delivery = verifications.get("delivery") if isinstance(verifications, dict) else None
    if delivery and not delivery.get("success", True):
        errors = delivery.get("errors") or []
        messages = [e.get("message", "Unknown verification error") for e in errors]
        raise AddressVerificationError(messages, address)

    return address


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
