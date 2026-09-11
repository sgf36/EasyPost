"""Carrier -> service flow-through: one matching rule, used everywhere.

A carrier reaches this app under three spellings (the metadata endpoint's
"royalmailv3", a rate's "RoyalMailV3", an account's "RoyalMailV3Account"), so
narrowing a list to a carrier is only correct if every site matches the same
way. Before this, the one site that did it — the Batch service picker — used a
case-sensitive ``==`` while an unused, correctly case-folded helper sat in
app/services/carriers.py with no production caller.

The last test is the one that matters most. It fails on any new inline
comparison of a ``carrier`` attribute outside carriers.py, because a rule that
lives in a comment is a rule the next call site does not know about.
"""

import ast
import pathlib
from types import SimpleNamespace

import pytest

from app.services import carriers as C
from app.services import packages as P
from app.services.carriers import (
    ServiceLevel,
    carrier_matches,
    carriers_present_in,
    for_carrier,
)
from app.services.packages import PredefinedPackage

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
RULE_HOME = APP / "services" / "carriers.py"


@pytest.fixture(autouse=True)
def _carrier_names(monkeypatch):
    # carrier_display_name reads the carriers_cache table; hold it in memory so
    # these stay pure and independent of whatever another test seeded.
    monkeypatch.setattr(C, "_carrier_names", {"usps": "USPS", "evri": "Evri"})


LEVELS = [
    ServiceLevel(carrier="royalmailv3", name="RoyalMail1stClass"),
    ServiceLevel(carrier="royalmailv3", name="RoyalMail2ndClassSignedFor"),
    ServiceLevel(carrier="royalmail", name="RoyalMail1stClass"),
    ServiceLevel(carrier="usps", name="First"),
]


def test_carrier_matches_across_endpoint_spellings():
    assert carrier_matches("royalmailv3", "RoyalMailV3")
    # Distinct carriers with distinct catalogues, and picking the wrong one
    # fails at purchase — case-folding must not collapse them.
    assert not carrier_matches("royalmail", "royalmailv3")
    assert not carrier_matches("", "usps")


def test_services_follow_a_carrier_named_in_rate_spelling():
    # The CamelCase code off a rate must reach the lowercase catalogue. With the
    # old inline ``==`` this returned nothing.
    names = [s.name for s in for_carrier(LEVELS, "RoyalMailV3")]
    assert names == ["RoyalMail1stClass", "RoyalMail2ndClassSignedFor"]


def test_filter_works_on_rates_and_packages_too():
    rates = [SimpleNamespace(carrier="RoyalMailV3", id="r1"), SimpleNamespace(carrier="USPS", id="r2")]
    assert [r.id for r in for_carrier(rates, "usps")] == ["r2"]

    packages = [
        PredefinedPackage("usps", "FlatRateEnvelope", None, "", None),
        PredefinedPackage("royalmailv3", "LETTER", None, "", None),
    ]
    assert [p.name for p in for_carrier(packages, "RoyalMailV3")] == ["LETTER"]


def test_no_carrier_selects_nothing_rather_than_everything():
    # "No carrier chosen" must not be indistinguishable from "this carrier
    # offers the lot"; callers show their unfiltered list themselves.
    assert for_carrier(LEVELS, "") == []


def test_carriers_present_in_dedupes_spellings_and_keeps_the_first():
    rates = [
        SimpleNamespace(carrier="RoyalMailV3"),
        SimpleNamespace(carrier="royalmailv3"),
        SimpleNamespace(carrier="USPS"),
        SimpleNamespace(carrier=""),
    ]
    # Ordered by display name: "Royal Mail V3" before "USPS".
    assert carriers_present_in(rates) == ["RoyalMailV3", "USPS"]


def test_every_carrier_offered_filters_to_at_least_one_rate():
    # The property the Create Shipment filter depends on: populated from the
    # rates, it can never offer a carrier that empties the table.
    rates = [SimpleNamespace(carrier=c) for c in ("RoyalMailV3", "USPS", "usps", "Evri")]
    for carrier in carriers_present_in(rates):
        assert for_carrier(rates, carrier)


_PACKAGES = [
    PredefinedPackage("usps", "FlatRateEnvelope", None, "", None),
    PredefinedPackage("royalmailv3", "LETTER", None, "", None),
]


def test_batch_template_packages_narrow_to_the_chosen_carrier(monkeypatch):
    monkeypatch.setattr(P, "list_predefined_packages", lambda: list(_PACKAGES))
    assert P.predefined_package_choices("RoyalMailV3") == ["Royal Mail V3 — LETTER"]
    assert len(P.predefined_package_choices()) == 2


def test_batch_template_falls_back_when_the_carrier_has_no_packages(monkeypatch):
    # An empty dropdown in the recipient's sheet is worse than a long one.
    monkeypatch.setattr(P, "list_predefined_packages", lambda: list(_PACKAGES))
    assert P.predefined_package_choices("evri") == P.predefined_package_choices()


# ---------------------------------------------------------------------------
# The guard: nobody re-implements the rule inline
# ---------------------------------------------------------------------------


def _names_carrier(node: ast.AST) -> bool:
    """Whether an expression reads a carrier: ``x.carrier``,
    ``getattr(x, "carrier", ...)``, either one case-folded, or either inside
    ``(... or "")``."""
    if isinstance(node, ast.Attribute) and node.attr == "carrier":
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "carrier"
        ):
            return True
        if isinstance(func, ast.Attribute) and func.attr in ("casefold", "lower", "upper"):
            return _names_carrier(func.value)
    if isinstance(node, ast.BoolOp):
        return any(_names_carrier(v) for v in node.values)
    return False


def _inline_carrier_comparisons(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        if any(_names_carrier(operand) for operand in (node.left, *node.comparators)):
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize(
    "snippet",
    [
        "matches = [s for s in levels if s.carrier == carrier]",
        'ok = getattr(rate, "carrier", "").lower() == wanted.lower()',
        'ok = (s.carrier or "").casefold() != key',
    ],
)
def test_guard_catches_the_shapes_it_exists_for(snippet):
    # Positive control. Without it, a detector that matched nothing would pass
    # the real scan below and look exactly like a clean codebase.
    assert _inline_carrier_comparisons(snippet)


def test_no_inline_carrier_comparisons_outside_the_rule():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if path == RULE_HOME:
            continue
        for line in _inline_carrier_comparisons(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(APP.parent)}:{line}")
    assert not offenders, (
        "Compare carriers with carriers.carrier_matches / for_carrier, not ==. "
        "One carrier has three spellings across EasyPost's endpoints: "
        + ", ".join(offenders)
    )
