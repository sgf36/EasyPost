"""Claim filing rules.

Every rule here was read off the live API, not the documentation — which lists
`contact_email` and `description` as optional when the endpoint in fact refuses
a claim without them:

    contact_email: field required; description: field required
    At least one supporting documentation attachment is required for theft or
    damage claims.

The attachment field names were the other trap: a plain `attachments` key is
accepted and silently ignored, so the attachment requirement looks impossible
to satisfy. A claim filed with `supporting_documentation_attachments` was
confirmed to succeed against the live API.
"""

import base64
from unittest.mock import Mock, patch

import pytest

from app.services.claims import (
    ClaimRequestError,
    claim_is_open,
    claim_needs_action,
    encode_attachment,
    file_claim,
    validate_claim,
)

B64_PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)


def _manager():
    manager = Mock()
    manager.active_mode = "test"
    return manager


def _file(**over):
    params = dict(
        tracking_code="EZ1000000001", claim_type="loss", amount="100.00",
        description="Never arrived", contact_email="claims@example.com",
    )
    params.update(over)
    manager = _manager()
    with patch("app.services.claims.client_manager", manager), \
            patch("app.services.claims.save_claim_locally"):
        file_claim(**params)
    return manager.get_client.return_value.claim.create.call_args.kwargs


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


def test_contact_email_is_required():
    with pytest.raises(ClaimRequestError, match="email"):
        validate_claim(claim_type="loss", contact_email="", description="lost")


def test_description_is_required():
    with pytest.raises(ClaimRequestError, match="description"):
        validate_claim(claim_type="loss", contact_email="a@b.com", description="  ")


def test_an_unknown_claim_type_is_refused():
    with pytest.raises(ClaimRequestError, match="damage, loss, theft"):
        validate_claim(claim_type="banana", contact_email="a@b.com", description="x")


@pytest.mark.parametrize("claim_type", ["damage", "theft"])
def test_damage_and_theft_need_a_document(claim_type):
    with pytest.raises(ClaimRequestError, match="supporting document"):
        validate_claim(claim_type=claim_type, contact_email="a@b.com", description="x")


def test_a_loss_claim_needs_no_document():
    validate_claim(claim_type="loss", contact_email="a@b.com", description="x")


@pytest.mark.parametrize(
    "field",
    ["supporting_documentation_attachments", "invoice_attachments",
     "email_evidence_attachments"],
)
def test_any_of_the_three_attachment_fields_satisfies_the_requirement(field):
    validate_claim(
        claim_type="damage", contact_email="a@b.com", description="x",
        **{field: [B64_PIXEL]},
    )


def test_an_unknown_payment_method_is_refused():
    with pytest.raises(ClaimRequestError, match="payment method"):
        validate_claim(
            claim_type="loss", contact_email="a@b.com", description="x",
            payment_method="cash",
        )


# ---------------------------------------------------------------------------
# What is sent
# ---------------------------------------------------------------------------


def test_required_fields_are_sent_as_strings_not_none():
    """The old code sent `description or None`, which is exactly what the API
    rejects."""
    kwargs = _file()
    assert kwargs["description"] == "Never arrived"
    assert kwargs["contact_email"] == "claims@example.com"


def test_attachments_use_the_documented_field_name():
    kwargs = _file(claim_type="damage", supporting_documentation_attachments=[B64_PIXEL])
    assert kwargs["supporting_documentation_attachments"] == [B64_PIXEL]
    # A bare `attachments` key is accepted and ignored, which is worse than an
    # error — it looks like the requirement cannot be met.
    assert "attachments" not in kwargs


def test_empty_attachment_lists_are_omitted_entirely():
    kwargs = _file()
    for key in ("supporting_documentation_attachments", "invoice_attachments",
                "email_evidence_attachments", "payment_method"):
        assert key not in kwargs


def test_payment_method_is_sent_when_chosen():
    assert _file(payment_method="easypost_wallet")["payment_method"] == "easypost_wallet"


# ---------------------------------------------------------------------------
# Attachment encoding
# ---------------------------------------------------------------------------


def test_a_file_is_encoded_as_plain_base64(tmp_path):
    """No `data:` URI prefix — the endpoint wants the encoded bytes alone."""
    path = tmp_path / "damage.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n binary content")
    encoded = encode_attachment(str(path))
    assert not encoded.startswith("data:")
    assert base64.b64decode(encoded) == path.read_bytes()


def test_an_oversized_attachment_is_refused(tmp_path):
    path = tmp_path / "huge.png"
    path.write_bytes(b"0" * (6 * 1024 * 1024))
    with pytest.raises(ClaimRequestError, match="larger than"):
        encode_attachment(str(path))


# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["submitted", "in_review", "needs_action"])
def test_open_claims_can_still_change(status):
    assert claim_is_open(status)


@pytest.mark.parametrize(
    "status", ["approved", "approved_partial", "rejected", "cancelled"]
)
def test_settled_claims_are_closed(status):
    assert not claim_is_open(status)


def test_needs_action_is_singled_out():
    """A claim parked here never progresses on its own, so it must not read as
    just another in-flight status."""
    assert claim_needs_action("needs_action")
    assert not claim_needs_action("in_review")
