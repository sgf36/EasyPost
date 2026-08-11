"""Batch shipments: import/validation, bulk rate + buy, combined labels.

Recipients are imported from either a plain ``.csv`` or an Excel ``.xlsx``
workbook. The workbook template additionally carries a real dropdown on the
``predefined_package`` column — a data-validation list of carrier package
codes — which a flat CSV cannot express. Either format parses to the same
:class:`BatchRow` list, so everything downstream is unchanged.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.client import client_manager
from app.core.db import db_cursor
from app.services.packages import predefined_package_names
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
    # A carrier package code (e.g. "FlatRateEnvelope"). When set, the carrier's
    # own fixed dimensions apply and length/width/height are ignored — so they
    # stop being required for that row. The .xlsx template offers this as a
    # dropdown; see write_xlsx_template.
    "predefined_package",
    "reference",
]

# Header-level requirement: these columns must exist. length/width/height are
# additionally required *per row* only when that row names no predefined
# package (see _validate_row) — a predefined package supplies its own.
REQUIRED_COLUMNS = {"to_street1", "to_city", "to_state", "to_zip", "to_country", "weight"}

DIMENSION_COLUMNS = ("length", "width", "height")

_SAMPLE_ROW = {
    "to_name": "Jane Doe", "to_company": "", "to_street1": "123 Main St",
    "to_street2": "", "to_city": "Boston", "to_state": "MA", "to_zip": "02110",
    "to_country": "US", "to_phone": "5551234567", "to_email": "jane@example.com",
    "length": "10", "width": "6", "height": "4", "weight": "16",
    "predefined_package": "", "reference": "order-1001",
}


def write_csv_template(path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        writer.writerow([_SAMPLE_ROW[col] for col in CSV_COLUMNS])


def write_xlsx_template(path: str, package_choices: Optional[list[str]] = None) -> None:
    """Write the batch template as an Excel workbook with a package dropdown.

    ``package_choices`` populates a data-validation list on every cell of the
    ``predefined_package`` column, so the recipient sheet offers the carrier
    package codes as a real dropdown rather than free text. The choices are
    kept on a second, hidden sheet and referenced by range — an inline list
    formula is capped near 255 characters, which the full carrier set blows
    past. When no choices are available (offline, empty cache) the column is
    left as free text.
    """
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    choices = [c for c in (package_choices or []) if c]

    wb = Workbook()
    ws = wb.active
    ws.title = "Recipients"
    ws.append(CSV_COLUMNS)
    ws.append([_SAMPLE_ROW[col] for col in CSV_COLUMNS])
    ws.freeze_panes = "A2"

    if choices:
        opts = wb.create_sheet("Packages")
        opts.append(["predefined_package"])
        for name in choices:
            opts.append([name])
        opts.sheet_state = "hidden"

        pkg_col = get_column_letter(CSV_COLUMNS.index("predefined_package") + 1)
        ref = f"Packages!$A$2:$A${len(choices) + 1}"
        dv = DataValidation(type="list", formula1=f"={ref}", allow_blank=True)
        # Apply to the data rows below the header, not the header itself.
        dv.add(f"{pkg_col}2:{pkg_col}1048576")
        ws.add_data_validation(dv)

    wb.save(path)


@dataclass
class BatchRow:
    line_number: int
    fields: dict
    errors: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _validate_row(line_number: int, fields: dict) -> BatchRow:
    fields = {k: (str(v) if v is not None else "").strip() for k, v in fields.items()}
    errors = [col for col in REQUIRED_COLUMNS if not fields.get(col)]

    # A predefined package brings its own dimensions, so length/width/height
    # are only required when no package is named. When they are supplied either
    # way, they must still be numeric.
    if not fields.get("predefined_package"):
        errors.extend(col for col in DIMENSION_COLUMNS if not fields.get(col))

    for numeric_col in (*DIMENSION_COLUMNS, "weight"):
        value = fields.get(numeric_col)
        if value:
            try:
                float(value)
            except ValueError:
                errors.append(f"{numeric_col} is not a number")

    return BatchRow(line_number=line_number, fields=fields, errors=errors)


def _check_header(fieldnames) -> None:
    missing = REQUIRED_COLUMNS - set(fieldnames or [])
    if missing:
        raise ValueError(f"File is missing required columns: {', '.join(sorted(missing))}")


def _parse_xlsx(path: str) -> list[BatchRow]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Recipients"] if "Recipients" in wb.sheetnames else wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = [str(c).strip() if c is not None else "" for c in next(it, []) or []]
    _check_header(header)

    rows: list[BatchRow] = []
    for line_number, raw in enumerate(it, start=2):
        if raw is None or all(c is None or str(c).strip() == "" for c in raw):
            continue  # skip fully blank rows Excel often trails with
        fields = {header[i]: raw[i] if i < len(raw) else "" for i in range(len(header)) if header[i]}
        rows.append(_validate_row(line_number, fields))
    return rows


def _parse_csv(path: str) -> list[BatchRow]:
    rows: list[BatchRow] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        _check_header(reader.fieldnames)
        for line_number, raw_row in enumerate(reader, start=2):
            rows.append(_validate_row(line_number, raw_row))
    return rows


def parse_import(path: str) -> list[BatchRow]:
    """Parse a recipient file, dispatching on extension (.xlsx or .csv)."""
    if Path(path).suffix.lower() in (".xlsx", ".xlsm"):
        return _parse_xlsx(path)
    return _parse_csv(path)


# Back-compat alias: the import entry point was CSV-only before .xlsx support.
parse_csv = parse_import


def _row_to_shipment_params(row: BatchRow, from_address_id: str) -> dict:
    f = row.fields
    # A predefined package carries fixed dimensions, so send its code alone and
    # omit length/width/height — the same rule as a single Create Shipment
    # (app/services/shipments.py). Otherwise send the parcel's own dimensions.
    parcel = {"weight": float(f["weight"])}
    if f.get("predefined_package"):
        parcel["predefined_package"] = f["predefined_package"]
    else:
        parcel.update({
            "length": float(f["length"]),
            "width": float(f["width"]),
            "height": float(f["height"]),
        })
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
        "parcel": parcel,
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
