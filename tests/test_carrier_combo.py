"""The shared carrier picker, under a real QApplication.

Covers the behaviours every page relies on: "All carriers" reads as no carrier,
a rate's CamelCase code selects the catalogue's lowercase entry, a refill keeps
the user's choice, and free-text mode sends what is on screen — including on a
first run offline, when the catalogue is empty.
"""

import pytest
from PySide6.QtWidgets import QApplication

from app.services import carriers as C
from app.ui.widgets.carrier_combo import CarrierCombo


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _carrier_names(monkeypatch):
    monkeypatch.setattr(C, "_carrier_names", {"usps": "USPS"})


def test_all_carriers_entry_reads_as_no_carrier(qapp):
    combo = CarrierCombo(include_all=True)
    combo.set_carriers(["royalmailv3", "usps"])
    assert combo.count() == 3
    assert combo.current_carrier() == ""
    assert combo.itemText(1) == "Royal Mail V3"


def test_a_rate_spelling_selects_the_catalogue_entry(qapp):
    combo = CarrierCombo(include_all=True)
    combo.set_carriers(["royalmailv3", "usps"])
    combo.set_current_carrier("RoyalMailV3")
    assert combo.current_carrier() == "royalmailv3"


def test_refilling_keeps_the_selection(qapp):
    combo = CarrierCombo(include_all=True)
    combo.set_carriers(["royalmailv3", "usps"])
    combo.set_current_carrier("usps")
    combo.set_carriers(["dhlexpress", "royalmailv3", "usps"])
    assert combo.current_carrier() == "usps"


def test_refilling_without_the_selected_carrier_falls_back_to_all(qapp):
    combo = CarrierCombo(include_all=True)
    combo.set_carriers(["royalmailv3", "usps"])
    combo.set_current_carrier("usps")
    combo.set_carriers(["royalmailv3"])
    assert combo.current_carrier() == ""


def test_refill_signals_once_and_only_when_the_carrier_changed(qapp):
    combo = CarrierCombo(include_all=True)
    combo.set_carriers(["royalmailv3", "usps"])
    combo.set_current_carrier("usps")
    seen = []
    combo.carrier_changed.connect(seen.append)

    combo.set_carriers(["royalmailv3", "usps"])
    # The same carrier survived the refill: nothing changed, so nothing redraws.
    assert seen == []

    combo.set_carriers(["royalmailv3"])
    assert seen == [""]


def test_free_text_sends_what_is_on_screen(qapp):
    combo = CarrierCombo(free_text=True)
    combo.set_carriers(["usps"])
    combo.set_current_carrier("usps")
    assert combo.current_carrier() == "usps"
    # Qt keeps currentIndex on the old match while the user types over it;
    # reading currentData would send USPS for a box that says DHLExpress.
    combo.setEditText("DHLExpress")
    assert combo.current_carrier() == "DHLExpress"


def test_free_text_with_an_empty_catalogue_still_takes_a_carrier(qapp):
    combo = CarrierCombo(free_text=True)
    combo.set_carriers([])
    combo.set_current_carrier("RoyalMailV3")
    assert combo.current_carrier() == "RoyalMailV3"
    combo.set_current_carrier("")
    assert combo.current_carrier() == ""
