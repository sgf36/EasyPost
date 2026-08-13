"""A long label must wrap, or it sets the minimum width of its whole page.

Every view is wrapped in a `QScrollArea` with `setWidgetResizable(True)`, which
honours the widget's minimum width. A `QLabel` that does not wrap reports its
entire single line as that minimum. So one explanatory sentence decides how
narrow a page may be — and a sentence that fits in English does not fit in
German, where it runs half again as long.

That is what put a horizontal scrollbar under the Batch page and pushed
"Datei wählen…" off the right-hand edge of a Mac App Store screenshot.

This test reads the source rather than building widgets: constructing views
needs a QApplication and a seeded database, and the property being asserted —
"a long label is wrapped" — is visible in the source and cheaper to check there.
"""

import json
import re
from pathlib import Path

import pytest

from app.i18n import LOCALES_DIR

VIEWS_DIR = Path(__file__).resolve().parent.parent / "app" / "ui" / "views"

# A label created inline inside addWidget/addRow is never assigned to a name,
# so setWordWrap can never have been called on it.
INLINE_LABEL = re.compile(r"(?:addRow|addWidget)\(\s*QLabel\(tr\(\"([a-z_.]+)\"\)\)")

# Above this, a single line is wide enough to squeeze a 1440-point window —
# the size the Mac App Store screenshots are rendered at. Short field labels
# ("From:", "Units:") are legitimately inline and unwrapped.
MAX_INLINE_CHARS = 40


def _catalog(code: str) -> dict:
    return json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))


def _inline_labels():
    english = _catalog("en")
    for path in sorted(VIEWS_DIR.glob("*.py")):
        for key in INLINE_LABEL.findall(path.read_text(encoding="utf-8")):
            yield path.name, key, english.get(key, "")


def test_no_long_label_is_created_inline_and_unwrapped():
    offenders = [
        (name, key, len(text))
        for name, key, text in _inline_labels()
        if len(text) > MAX_INLINE_CHARS
    ]
    assert not offenders, (
        "these labels are long enough to set their page's minimum width and are "
        f"never wrapped — assign them to a name and call setWordWrap(True): {offenders}"
    )


@pytest.mark.parametrize("code", ["de", "fr", "es", "ru", "pl"])
def test_translations_of_short_inline_labels_stay_short(code):
    """A label that is short in English may not be short in translation.

    German runs roughly 1.4x English on UI strings, so a 38-character English
    label can cross the threshold in translation while the test above still
    passes. Allow generous headroom, then fail: the fix is the same either way.
    """
    catalog = _catalog(code)
    offenders = [
        (name, key, len(catalog.get(key, "")))
        for name, key, _text in _inline_labels()
        if len(catalog.get(key, "")) > MAX_INLINE_CHARS * 2
    ]
    assert not offenders, f"{code}: unwrapped inline labels too long: {offenders}"
