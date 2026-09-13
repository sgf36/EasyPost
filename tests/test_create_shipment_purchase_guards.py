"""Create Shipment must never buy something other than what the form describes.

Each test drives the view through the path a user takes: Get Rates hands its
request to run_async, and the reply is delivered later by the fake task below,
so a reply can land after the form has been edited exactly as it does when the
network is slow. Nothing here reaches EasyPost.
"""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.core import units
from app.core.client import client_manager
from app.core.credential_store import Credentials
from app.core.db import db_cursor, init_db
from app.i18n import tr


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class FakeTask:
    """Stands in for AsyncTask: records the call and lets the test choose when,
    and with what, it finishes."""

    def __init__(self, fn):
        self.fn = fn
        self.succeeded = _Signal()
        self.failed = _Signal()

    def succeed(self, value):
        for slot in self.succeeded.slots:
            slot(value)

    def fail(self, exc):
        for slot in self.failed.slots:
            slot(exc)


def _addr(id_, country):
    return SimpleNamespace(id=id_, country=country, name="N", company="", phone="1")


def _rate(id_, rate="7.04"):
    return SimpleNamespace(id=id_, carrier="USPS", service="GroundAdvantage", rate=rate,
                           currency="USD", delivery_days=3, delivery_date_guaranteed=False)


def _shipment(id_="shp_1"):
    return SimpleNamespace(id=id_, rates=[_rate("rate_1"), _rate("rate_2", "9.10")], messages=[])


@pytest.fixture
def harness(qapp, monkeypatch):
    import app.ui.views.create_shipment_view as V

    init_db()
    tasks: list[FakeTask] = []
    dialogs: list[str] = []

    def fake_run_async(fn, parent=None):
        task = FakeTask(fn)
        tasks.append(task)
        return task

    monkeypatch.setattr(V, "run_async", fake_run_async)
    monkeypatch.setattr(V, "list_predefined_packages", lambda: [])
    monkeypatch.setattr(V, "list_addresses", lambda: [])
    monkeypatch.setattr(V, "track_shipment", lambda s, mode=None: True)
    monkeypatch.setattr(V, "note_successful_shipment", lambda: None)
    monkeypatch.setattr(V, "schedule_review_prompt", lambda parent: None)
    monkeypatch.setattr(V.QMessageBox, "information", lambda *a, **k: dialogs.append("information"))
    monkeypatch.setattr(V.QMessageBox, "critical", lambda *a, **k: dialogs.append("critical"))
    monkeypatch.setattr(V.QMessageBox, "warning", lambda *a, **k: dialogs.append("warning"))

    view = V.CreateShipmentView()
    view._address_by_id = {
        "adr_us1": _addr("adr_us1", "US"),
        "adr_us2": _addr("adr_us2", "US"),
        "adr_gb": _addr("adr_gb", "GB"),
    }
    for combo in (view._from_combo, view._to_combo):
        for key in view._address_by_id:
            combo.addItem(key, key)
    view._from_combo.setCurrentIndex(view._from_combo.findData("adr_us1"))
    view._to_combo.setCurrentIndex(view._to_combo.findData("adr_us2"))
    tasks.clear()  # the predefined-package load at start-up
    return SimpleNamespace(V=V, view=view, tasks=tasks, dialogs=dialogs)


def _rate_now(h, shipment=None):
    """Click Get Rates and let the reply arrive straight away."""
    h.view._get_rates_btn.click()
    h.tasks[-1].succeed(shipment or _shipment())


def _buy_buttons(view) -> list[QPushButton]:
    tree = view._rates_tree
    buttons = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for j in range(top.childCount()):
            widget = tree.itemWidget(top.child(j), 4)
            if widget is not None:
                buttons.append(widget)
    return buttons


# --- a reply for a parcel the form no longer holds ---------------------------

def test_rates_for_a_parcel_edited_while_rating_are_discarded(harness):
    view = harness.view
    view._get_rates_btn.click()
    in_flight = harness.tasks[-1]
    assert not view._get_rates_btn.isEnabled()

    view._weight_input.setValue(view._weight_input.value() * 10)
    # The request in flight is void, so the user can rate the new parcel.
    assert view._get_rates_btn.isEnabled()

    in_flight.succeed(_shipment("shp_old"))
    assert view._current_shipment is None
    assert view._rates_tree.topLevelItemCount() == 0
    assert _buy_buttons(view) == []


def test_a_superseded_rating_does_not_overwrite_the_newer_one(harness):
    view = harness.view
    view._get_rates_btn.click()
    first = harness.tasks[-1]
    view._weight_input.setValue(view._weight_input.value() + 5)
    view._get_rates_btn.click()
    second = harness.tasks[-1]

    second.succeed(_shipment("shp_new"))
    first.succeed(_shipment("shp_old"))
    assert view._current_shipment.id == "shp_new"


def test_a_failure_for_a_superseded_rating_is_not_reported(harness):
    view = harness.view
    view._get_rates_btn.click()
    in_flight = harness.tasks[-1]
    view._height_input.setValue(view._height_input.value() + 1)
    in_flight.fail(RuntimeError("timeout"))
    assert "critical" not in harness.dialogs


def test_control_a_reply_for_the_unchanged_form_is_buyable(harness):
    _rate_now(harness)
    assert harness.view._current_shipment is not None
    assert all(b.isEnabled() for b in _buy_buttons(harness.view))
    assert len(_buy_buttons(harness.view)) == 2


@pytest.mark.parametrize("edit", [
    lambda v: v._signature_combo.setCurrentIndex(1),
    lambda v: v._reference_input.setText("order 42"),
    lambda v: v._to_combo.setCurrentIndex(v._to_combo.findData("adr_us1")),
])
def test_editing_parcel_addresses_or_options_clears_rates(harness, edit):
    _rate_now(harness)
    edit(harness.view)
    assert harness.view._current_shipment is None
    assert _buy_buttons(harness.view) == []


# --- customs edits -------------------------------------------------------------

def _international(h):
    view = h.view
    view._from_combo.setCurrentIndex(view._from_combo.findData("adr_gb"))
    view._to_combo.setCurrentIndex(view._to_combo.findData("adr_us1"))
    view._customs_signer_input.setText("S Fields")
    view._customs_certify_checkbox.setChecked(True)
    view._customs_items_table.cellWidget(0, 0).setText("Shirt")
    return view


_CUSTOMS_EDITS = {
    "value": lambda v: v._customs_items_table.cellWidget(0, 2).setValue(999),
    "quantity": lambda v: v._customs_items_table.cellWidget(0, 1).setValue(7),
    "weight": lambda v: v._customs_items_table.cellWidget(0, 3).setValue(0.9),
    "description": lambda v: v._customs_items_table.cellWidget(0, 0).setText("Jumper"),
    "hts": lambda v: v._customs_items_table.cellWidget(0, 4).setText("6109.10"),
    "origin": lambda v: v._customs_items_table.cellWidget(0, 5).setCurrentIndex(0),
    "contents_type": lambda v: v._contents_type_combo.setCurrentIndex(2),
    "restriction": lambda v: v._restriction_type_combo.setCurrentIndex(1),
    "non_delivery": lambda v: v._non_delivery_combo.setCurrentIndex(1),
    "signer": lambda v: v._customs_signer_input.setText("Someone Else"),
    "add_item": lambda v: v._on_add_customs_item(),
}


@pytest.mark.parametrize("name", sorted(_CUSTOMS_EDITS))
def test_editing_customs_after_rating_clears_rates(harness, name):
    view = _international(harness)
    _rate_now(harness)
    assert view._current_shipment is not None, "control: the declaration was accepted"
    _CUSTOMS_EDITS[name](view)
    assert view._current_shipment is None
    assert _buy_buttons(view) == []


def test_editing_a_customs_item_added_later_clears_rates(harness):
    view = _international(harness)
    view._on_add_customs_item()
    view._customs_items_table.cellWidget(1, 0).setText("Socks")
    _rate_now(harness)
    assert view._current_shipment is not None
    view._customs_items_table.cellWidget(1, 2).setValue(55)
    assert view._current_shipment is None


# --- customs headers state what is declared ---------------------------------------

def _headers(view):
    table = view._customs_items_table
    return table.horizontalHeaderItem(2).text(), table.horizontalHeaderItem(3).text()


def _set_units(view, system, weight_unit):
    view._system_combo.setCurrentIndex(view._system_combo.findData(system))
    view._weight_unit_combo.setCurrentIndex(view._weight_unit_combo.findData(weight_unit))
    assert (view._unit_system, view._weight_unit) == (system, weight_unit)


@pytest.mark.parametrize("system,unit", [("metric", "kg"), ("metric", "g"),
                                         ("imperial", "oz"), ("imperial", "lb")])
def test_customs_headers_name_the_unit_and_currency_declared(harness, system, unit):
    view = _international(harness)
    _set_units(view, system, unit)
    value_header, weight_header = _headers(view)
    item = view._collect_customs_info()["customs_items"][0]
    assert item["currency"] == "GBP"
    assert value_header == tr("create_shipment.customs_item_col_value", currency="GBP")
    assert weight_header == tr("create_shipment.customs_item_col_weight", unit=unit)
    # Literal checks too: tr() ignores arguments a string has no slot for, so a
    # header hard-wired to one unit would pass the comparison above.
    assert "GBP" in value_header and "USD" not in value_header
    assert f"({unit})" in weight_header
    cell = view._customs_items_table.cellWidget(0, 3).value()
    assert item["weight"] == pytest.approx(units.to_ounces(cell, unit), rel=1e-3)


def test_value_header_follows_the_sender(harness):
    view = _international(harness)
    assert "GBP" in _headers(view)[0]
    view._from_combo.setCurrentIndex(view._from_combo.findData("adr_us2"))
    assert "USD" in _headers(view)[0]


def test_changing_weight_unit_keeps_the_declared_item_weight(harness):
    view = _international(harness)
    _set_units(view, "imperial", "oz")
    view._customs_items_table.cellWidget(0, 3).setValue(8)
    before = view._collect_customs_info()["customs_items"][0]["weight"]
    _set_units(view, "metric", "g")
    after = view._collect_customs_info()["customs_items"][0]["weight"]
    assert after == pytest.approx(before, rel=0.01)


# --- Buy while a purchase is in flight ---------------------------------------------

def test_buy_is_disabled_until_the_purchase_settles_and_back_on_failure(harness):
    view = harness.view
    _rate_now(harness)
    buttons = _buy_buttons(view)
    buttons[0].click()
    buy_task = harness.tasks[-1]
    assert all(not b.isEnabled() for b in _buy_buttons(view))

    buttons[0].click()
    buttons[1].click()
    assert harness.tasks[-1] is buy_task, "a second purchase was started"

    buy_task.fail(RuntimeError("card declined"))
    assert all(b.isEnabled() for b in _buy_buttons(view))


def test_buy_stays_disabled_through_a_carrier_filter_redraw(harness):
    view = harness.view
    _rate_now(harness)
    _buy_buttons(view)[0].click()
    view._render_rates()
    assert all(not b.isEnabled() for b in _buy_buttons(view))


# --- the mode a label is recorded under -----------------------------------------------

@pytest.fixture
def clean_shipments():
    init_db()
    with db_cursor() as cur:
        cur.execute("DELETE FROM shipments")
    yield
    with db_cursor() as cur:
        cur.execute("DELETE FROM shipments")


def test_label_is_recorded_under_the_mode_it_was_bought_in(harness, monkeypatch, clean_shipments):
    from app.services.shipments import list_shipments

    creds = Credentials(test_key="t", production_key="p", active_mode="production")
    monkeypatch.setattr(client_manager, "_credentials", creds)
    monkeypatch.setattr(harness.V, "confirm_if_production", lambda parent, description: True)

    view = harness.view
    _rate_now(harness)
    _buy_buttons(view)[0].click()
    creds.active_mode = "test"  # the banner is flipped while the buy runs

    bought = SimpleNamespace(id="shp_prod", status="purchased", selected_rate=_rate("rate_1"),
                             tracking_code="T1", postage_label=None, insurance=None,
                             refund_status=None, to_address=None, from_address=None)
    harness.tasks[-1].succeed(bought)

    assert list_shipments() == []
    creds.active_mode = "production"
    assert [r.id for r in list_shipments()] == ["shp_prod"]


# --- saving the label in its real format -------------------------------------------

@pytest.mark.parametrize("url,ext", [
    ("https://easypost-files.s3.amazonaws.com/files/postage_label/2026/e8.png", "png"),
    ("https://easypost-files.s3.amazonaws.com/files/postage_label/2026/e8.pdf", "pdf"),
    ("https://easypost-files.s3.amazonaws.com/files/postage_label/2026/e8.zpl", "zpl"),
    ("https://easypost-files.s3.amazonaws.com/files/postage_label/2026/e8.epl2", "epl2"),
])
def test_save_label_offers_the_labels_real_format(harness, monkeypatch, tmp_path, url, ext):
    V, view = harness.V, harness.view
    offered = {}

    def fake_dialog(parent, title, name, filters):
        offered.update(name=name, filters=filters)
        return str(tmp_path / f"out.{ext}"), filters

    monkeypatch.setattr(V.QFileDialog, "getSaveFileName", fake_dialog)
    monkeypatch.setattr(V.requests, "get", lambda u, timeout: SimpleNamespace(
        content=b"label-bytes", raise_for_status=lambda: None))

    view._pending_label_url = url
    view._on_save_label()

    assert offered["name"] == f"label.{ext}"
    assert f"(*.{ext})" in offered["filters"]
    assert "pdf" not in offered["filters"].lower() or ext == "pdf"
    assert (tmp_path / f"out.{ext}").read_bytes() == b"label-bytes"


def test_save_label_button_does_not_promise_a_pdf(harness):
    assert "PDF" not in harness.view._save_label_btn.text()


def test_tracker_is_recorded_under_the_mode_it_was_bought_in(monkeypatch):
    from easypost.easypost_object import convert_to_easypost_object

    from app.services import tracking

    init_db()
    with db_cursor() as cur:
        cur.execute("DELETE FROM trackers")
    creds = Credentials(test_key="t", production_key="p", active_mode="test")
    monkeypatch.setattr(client_manager, "_credentials", creds)
    shipment = convert_to_easypost_object({
        "id": "shp_prod", "object": "Shipment",
        "tracker": {"id": "trk_prod", "object": "Tracker", "status": "pre_transit"},
    })
    assert tracking.track_shipment(shipment, "production") is True
    assert tracking.list_trackers() == []
    creds.active_mode = "production"
    assert [t.id for t in tracking.list_trackers()] == ["trk_prod"]
