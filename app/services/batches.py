"""Batch shipments: CSV import/validation, bulk rate + buy, combined labels."""

import csv
from dataclasses import dataclass
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor
from app.services.shipments import preferred_label_options

CSV_COLUMNS = [
    "to_name",
    "to_company",
    "to_street1",
    "to_street2",
    "to_city",
    "to_state",
    "to_zip",
    "to_country",
    "to_phone",
    "to_email",
    "length",
    "width",
    "height",
    "weight",
    "reference",
]

REQUIRED_COLUMNS = {"to_street1", "to_city", "to_state", "to_zip", "to_country", "length", "width", "height", "weight"}


def write_csv_template(path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        writer.writerow(
            [
                "Jane Doe", "", "123 Main St", "", "Boston", "MA", "02110", "US",
                "5551234567", "jane@example.com", "10", "6", "4", "16", "order-1001",
            ]
        )


@dataclass
class BatchRow:
    line_number: int
    fields: dict
    errors: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def parse_csv(path: str) -> list[BatchRow]:
    rows: list[BatchRow] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing_columns))}")

        for line_number, raw_row in enumerate(reader, start=2):
            fields = {k: (v or "").strip() for k, v in raw_row.items()}
            errors = [col for col in REQUIRED_COLUMNS if not fields.get(col)]
            for numeric_col in ("length", "width", "height", "weight"):
                value = fields.get(numeric_col)
                if value:
                    try:
                        float(value)
                    except ValueError:
                        errors.append(f"{numeric_col} is not a number")
            rows.append(BatchRow(line_number=line_number, fields=fields, errors=errors))
    return rows


def _row_to_shipment_params(row: BatchRow, from_address_id: str) -> dict:
    f = row.fields
    return {
        "to_address": {
            "name": f.get("to_name") or None,
            "company": f.get("to_company") or None,
            "street1": f["to_street1"],
            "street2": f.get("to_street2") or None,
            "city": f["to_city"],
            "state": f["to_state"],
            "zip": f["to_zip"],
            "country": f["to_country"],
            "phone": f.get("to_phone") or None,
            "email": f.get("to_email") or None,
        },
        "from_address": {"id": from_address_id},
        "parcel": {
            "length": float(f["length"]),
            "width": float(f["width"]),
            "height": float(f["height"]),
            "weight": float(f["weight"]),
        },
        "reference": f.get("reference") or None,
        # Same printed-label format/size as a single shipment — label_size is
        # only honoured at creation time, so a batch has to carry it too.
        "options": preferred_label_options(),
    }


def create_batch(from_address_id: str, rows: list[BatchRow]):
    valid_rows = [r for r in rows if r.is_valid]
    if not valid_rows:
        raise ValueError("No valid rows to submit.")
    client = client_manager.get_client()
    shipments = [_row_to_shipment_params(r, from_address_id) for r in valid_rows]
    return client.batch.create(shipments=shipments)


def retrieve_batch(batch_id: str):
    client = client_manager.get_client()
    return client.batch.retrieve(batch_id)


def buy_batch(batch_id: str):
    client = client_manager.get_client()
    return client.batch.buy(batch_id)


def generate_batch_label(batch_id: str, file_format: str = "PDF"):
    client = client_manager.get_client()
    return client.batch.label(batch_id, file_format=file_format)


def _batch_state(batch) -> Optional[str]:
    return getattr(batch, "state", None) or getattr(batch, "status", None)


def save_batch_locally(batch, source_csv: str = "") -> None:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO batches (id, mode, status, num_shipments, source_csv)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, num_shipments=excluded.num_shipments
            """,
            (
                batch.id,
                mode,
                _batch_state(batch),
                getattr(batch, "num_shipments", None),
                source_csv or None,
            ),
        )


@dataclass
class BatchRecord:
    id: str
    mode: str
    status: Optional[str]
    num_shipments: Optional[int]
    source_csv: Optional[str]


_BATCH_FIELDS = [f for f in BatchRecord.__dataclass_fields__]


def list_batches() -> list[BatchRecord]:
    mode = client_manager.active_mode
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM batches WHERE mode = ? ORDER BY created_at DESC", (mode,)
        )
        rows = cur.fetchall()
    return [BatchRecord(**{k: row[k] for k in _BATCH_FIELDS}) for row in rows]
