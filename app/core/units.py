"""Unit handling for the Create Shipment form.

EasyPost's API is fixed at inches (dimensions) and ounces (weight), so the UI
lets the user enter either measurement system and everything is normalised to
in/oz before any API call or saved-package write. Saved packages are stored in
the canonical in/oz too, and converted back for display.

Kept dependency-free and pure so it is unit-tested without Qt.
"""

CM_PER_INCH = 2.54
OZ_PER_LB = 16.0
OZ_PER_KG = 35.27396194958041
OZ_PER_G = OZ_PER_KG / 1000.0

# Dimension unit for each system.
DIM_UNIT = {"metric": "cm", "imperial": "in"}
# Weight units offered per system; the first is the default for that system.
WEIGHT_UNITS = {"metric": ["kg", "g"], "imperial": ["oz", "lb"]}

# Spin-box configuration per unit: (minimum, maximum, decimals, step).
# Ranges mirror the original in/oz limits (1000 in, 5000 oz) converted across.
DIM_SPIN = {
    "in": (0.0, 1000.0, 2, 1.0),
    "cm": (0.0, 2540.0, 1, 1.0),
}
WEIGHT_SPIN = {
    "oz": (0.1, 5000.0, 1, 1.0),
    "lb": (0.01, 312.5, 2, 0.1),
    "kg": (0.001, 141.75, 3, 0.1),
    "g": (1.0, 141748.0, 0, 10.0),
}

# Starting value shown on a fresh form for each unit (roughly the same physical
# parcel across systems). Only used when there is no prior value to convert.
DIM_DEFAULT = {"in": 6.0, "cm": 15.0}
WEIGHT_DEFAULT = {"oz": 16.0, "lb": 1.0, "kg": 0.5, "g": 500.0}


def to_inches(value: float, unit: str) -> float:
    """Convert a dimension in `unit` (in|cm) to inches."""
    if unit == "in":
        return value
    if unit == "cm":
        return value / CM_PER_INCH
    raise ValueError(f"unknown dimension unit: {unit!r}")


def from_inches(value_in: float, unit: str) -> float:
    """Convert a dimension in inches to `unit` (in|cm)."""
    if unit == "in":
        return value_in
    if unit == "cm":
        return value_in * CM_PER_INCH
    raise ValueError(f"unknown dimension unit: {unit!r}")


def to_ounces(value: float, unit: str) -> float:
    """Convert a weight in `unit` (oz|lb|kg|g) to ounces."""
    if unit == "oz":
        return value
    if unit == "lb":
        return value * OZ_PER_LB
    if unit == "kg":
        return value * OZ_PER_KG
    if unit == "g":
        return value * OZ_PER_G
    raise ValueError(f"unknown weight unit: {unit!r}")


def from_ounces(value_oz: float, unit: str) -> float:
    """Convert a weight in ounces to `unit` (oz|lb|kg|g)."""
    if unit == "oz":
        return value_oz
    if unit == "lb":
        return value_oz / OZ_PER_LB
    if unit == "kg":
        return value_oz / OZ_PER_KG
    if unit == "g":
        return value_oz / OZ_PER_G
    raise ValueError(f"unknown weight unit: {unit!r}")
