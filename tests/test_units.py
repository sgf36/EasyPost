"""Unit-conversion tests for the Create Shipment measurement toggle.

EasyPost is always fed inches/ounces, so these guard the normalisation that the
form relies on.
"""
import pytest

from app.core import units


def test_dimension_to_inches():
    assert units.to_inches(6, "in") == 6
    assert units.to_inches(2.54, "cm") == pytest.approx(1.0)
    assert units.to_inches(30, "cm") == pytest.approx(30 / 2.54)


def test_dimension_from_inches():
    assert units.from_inches(6, "in") == 6
    assert units.from_inches(1.0, "cm") == pytest.approx(2.54)


def test_weight_to_ounces():
    assert units.to_ounces(5, "oz") == 5
    assert units.to_ounces(1, "lb") == 16
    assert units.to_ounces(1, "kg") == pytest.approx(35.27396194958041)
    assert units.to_ounces(1000, "g") == pytest.approx(35.27396194958041)


def test_weight_from_ounces():
    assert units.from_ounces(16, "lb") == pytest.approx(1.0)
    assert units.from_ounces(35.27396194958041, "kg") == pytest.approx(1.0)
    assert units.from_ounces(35.27396194958041, "g") == pytest.approx(1000.0)


def test_roundtrips_preserve_value():
    for unit in ("in", "cm"):
        assert units.from_inches(units.to_inches(12.3, unit), unit) == pytest.approx(12.3)
    for unit in ("oz", "lb", "kg", "g"):
        assert units.from_ounces(units.to_ounces(3.3, unit), unit) == pytest.approx(3.3)


def test_unknown_units_raise():
    with pytest.raises(ValueError):
        units.to_inches(1, "m")
    with pytest.raises(ValueError):
        units.from_inches(1, "m")
    with pytest.raises(ValueError):
        units.to_ounces(1, "st")
    with pytest.raises(ValueError):
        units.from_ounces(1, "st")


def test_metadata_is_self_consistent():
    assert set(units.WEIGHT_UNITS["metric"]) == {"kg", "g"}
    assert set(units.WEIGHT_UNITS["imperial"]) == {"oz", "lb"}
    for system, dim_unit in units.DIM_UNIT.items():
        assert dim_unit in units.DIM_SPIN
        assert dim_unit in units.DIM_DEFAULT
    for weight_units in units.WEIGHT_UNITS.values():
        for unit in weight_units:
            assert unit in units.WEIGHT_SPIN
            assert unit in units.WEIGHT_DEFAULT
