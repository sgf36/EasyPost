"""The rate "Included" column derives its badges from the service name plus
EasyPost's delivery_date_guaranteed flag (see _service_enhancements)."""

from types import SimpleNamespace

from app.ui.views.create_shipment_view import _service_enhancements


def _rate(service="", guaranteed=False):
    return SimpleNamespace(service=service, delivery_date_guaranteed=guaranteed)


def test_tracked_service():
    assert _service_enhancements(_rate("Tracked24")) == ["tracked"]


def test_signed_for_service():
    assert _service_enhancements(_rate("RoyalMail1stClassSignedFor")) == ["signed"]


def test_guaranteed_from_name():
    assert _service_enhancements(_rate("SpecialDeliveryGuaranteed")) == ["guaranteed"]


def test_guaranteed_from_flag():
    # A plain service name but the carrier flags a guaranteed delivery date.
    assert _service_enhancements(_rate("Priority", guaranteed=True)) == ["guaranteed"]


def test_tracked_and_signed_age_variant():
    # "Signature" in the name counts as signed even without "SignedFor".
    assert set(_service_enhancements(_rate("Tracked24SignatureAGE"))) == {"tracked", "signed"}


def test_plain_service_has_no_enhancements():
    assert _service_enhancements(_rate("RoyalMail1stClass")) == []


def test_missing_service_name_is_safe():
    assert _service_enhancements(SimpleNamespace()) == []
