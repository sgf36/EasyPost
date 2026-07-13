"""Insurance: add coverage to an EasyPost-bought shipment, or insure a
shipment that was labeled outside EasyPost using its tracking code."""

from app.core.client import client_manager
from app.core.db import db_cursor


def insure_existing_shipment(shipment_id: str, amount: str):
    """Adds/increases insurance on a shipment already purchased through
    EasyPost.
    """
    client = client_manager.get_client()
    return client.shipment.insure(shipment_id, amount=amount)


def create_standalone_insurance(
    *, tracking_code: str, carrier: str, amount: str, reference: str = ""
):
    """Insures a shipment/label that was NOT purchased through EasyPost,
    identified only by its tracking code and carrier.
    """
    client = client_manager.get_client()
    return client.insurance.create(
        tracking_code=tracking_code,
        carrier=carrier,
        amount=amount,
        reference=reference or None,
    )


def update_shipment_insured_amount(shipment_id: str, amount: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE shipments SET insured_amount = ? WHERE id = ?",
            (amount, shipment_id),
        )
