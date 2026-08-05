"""The rates-table service-name humaniser.

EasyPost returns run-together carrier service names (notably Royal Mail v3);
the Create Shipment rates table spaces them out so they read as words. This is
pure string logic, so it is tested without a running Qt app.
"""

from app.ui.views.create_shipment_view import CreateShipmentView

h = CreateShipmentView._humanize_service


def test_splits_camel_case():
    assert h("InternationalBusinessParcel") == "International Business Parcel"


def test_splits_letter_digit_boundary():
    assert h("Parcels30kg") == "Parcels 30kg"
    assert h("InternationalTrackedParcels30kgETOE") == "International Tracked Parcels 30kg ETOE"


def test_splits_acronym_before_word():
    # An acronym run followed by a capitalised word breaks between them.
    assert h("DDPComp") == "DDP Comp"


def test_keeps_acronyms_together():
    assert h("GlobalpriorityROW") == "Globalpriority ROW"


def test_leaves_spaced_names_untouched():
    assert h("Priority Mail Express") == "Priority Mail Express"


def test_handles_empty_and_none_safely():
    assert h("") == ""
    assert h(None) is None
