import json

from app.i18n import LOCALES_DIR, SUPPORTED_LOCALES, tr


def _locale_codes():
    return [code for code, _en, _native in SUPPORTED_LOCALES]


def test_english_catalog_exists_and_is_valid_json():
    path = LOCALES_DIR / "en.json"
    assert path.exists(), "en.json is the source-of-truth catalog and must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) > 0


def test_every_supported_locale_file_exists_and_matches_english_keys():
    english_keys = set(json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8")).keys())

    missing_files = []
    key_mismatches = {}

    for code in _locale_codes():
        if code == "en":
            continue
        path = LOCALES_DIR / f"{code}.json"
        if not path.exists():
            missing_files.append(code)
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            key_mismatches[code] = f"invalid JSON: {exc}"
            continue
        locale_keys = set(data.keys())
        if locale_keys != english_keys:
            missing = english_keys - locale_keys
            extra = locale_keys - english_keys
            key_mismatches[code] = f"missing={sorted(missing)[:5]} extra={sorted(extra)[:5]}"

    assert not missing_files, f"locale files not found: {missing_files}"
    assert not key_mismatches, f"key mismatches: {key_mismatches}"


def test_tr_falls_back_to_key_when_missing_everywhere():
    assert tr("this.key.does.not.exist.anywhere") == "this.key.does.not.exist.anywhere"


def test_tr_formats_placeholders():
    # Uses a real English key known to take a placeholder.
    result = tr("purchase_confirm.warning_body", description="test action")
    assert "test action" in result
