from app.core.countries import (
    COUNTRIES,
    postal_label_kind_for,
    state_label_kind_for,
)


def test_countries_have_unique_codes_and_names():
    codes = [code for code, _name in COUNTRIES]
    names = [name for _code, name in COUNTRIES]
    assert len(codes) == len(set(codes))
    assert len(names) == len(set(names))
    assert len(COUNTRIES) > 190


def test_countries_sorted_by_name():
    names = [name for _code, name in COUNTRIES]
    assert names == sorted(names)


def test_state_label_kind_overrides():
    assert state_label_kind_for("US") == "state"
    assert state_label_kind_for("CA") == "province"
    assert state_label_kind_for("GB") == "county"
    assert state_label_kind_for("JP") == "prefecture"


def test_state_label_kind_defaults_for_uncurated_country():
    assert state_label_kind_for("ZZ") == "default"
    assert state_label_kind_for("") == "default"


def test_postal_label_kind_overrides():
    assert postal_label_kind_for("US") == "zip"
    assert postal_label_kind_for("CA") == "postal"
    assert postal_label_kind_for("ZZ") == "postal"


def test_lookup_is_case_insensitive():
    assert state_label_kind_for("us") == "state"
    assert postal_label_kind_for("us") == "zip"
