"""Write the 1.2.0 "What's new" into a Partner Center listing export.

Partner Center's listing grid is field-ROWS keyed by ID, with one column per
language. "What's new" is the `ReleaseNotes` row, **field ID 3**. Only that row
is touched: every other field in the export is copied through byte for byte, so
descriptions, screenshots and captions cannot be clobbered by this step.

    python store_assets/build_releasenotes_import.py \
        --export "<listingData-....csv>" \
        --translations store_assets/release-notes-1.2.0-translations.json \
        --out "<listingData-....IMPORT.csv>"

The Store rejects any ReleaseNotes value over 1500 characters, so every value is
checked before anything is written — a rejected import costs a full re-export.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

RELEASE_NOTES_FIELD_ID = "3"
MAX_CHARS = 1500


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, help="listingData CSV from Partner Center")
    parser.add_argument("--translations", required=True, help="{lang: text} JSON")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with io.open(args.export, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header, body = rows[0], rows[1:]
    languages = header[4:]

    texts = json.loads(Path(args.translations).read_text(encoding="utf-8"))

    # The expected bullet count is taken from en-US rather than hard-coded: it
    # was 9 for 1.2.0, 5 for 1.2.4 and 3 for 1.2.6, so a fixed number silently
    # blocks every later release. What matters is that no translation quietly
    # gained or dropped a bullet against the English master.
    expected_bullets = texts.get("en-us", "").count('•')

    problems = []
    missing = [code for code in languages if code not in texts]
    if missing:
        problems.append(f"no translation for: {missing}")
    for code, value in texts.items():
        if len(value) > MAX_CHARS:
            problems.append(f"{code}: {len(value)} chars, over the {MAX_CHARS} cap")
        if expected_bullets and value.count("•") != expected_bullets:
            problems.append(
                f"{code}: {value.count(chr(0x2022))} bullets, expected {expected_bullets} from en-us"
            )
    extra = sorted(set(texts) - set(languages))
    if extra:
        problems.append(f"NOTE translations not in this export, ignored: {extra}")

    blocking = [p for p in problems if not p.startswith("NOTE")]
    for problem in problems:
        print(("  " if problem.startswith("NOTE") else "  BLOCKING ") + problem)
    if blocking:
        print(f"\n{len(blocking)} blocking problem(s) — nothing written")
        return 1

    target = next((r for r in body if r[1] == RELEASE_NOTES_FIELD_ID), None)
    if target is None:
        print(f"no row with field ID {RELEASE_NOTES_FIELD_ID} (ReleaseNotes)")
        return 1
    if target[0] != "ReleaseNotes":
        print(f"field ID {RELEASE_NOTES_FIELD_ID} is '{target[0]}', not ReleaseNotes — "
              "the export format has changed, refusing to guess")
        return 1

    changed = 0
    for index, code in enumerate(languages, start=4):
        before = target[index]
        target[index] = texts[code]
        changed += before != texts[code]

    print(f"\nReleaseNotes updated for {changed}/{len(languages)} languages "
          f"(longest {max(len(texts[c]) for c in languages)} chars)")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    with io.open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows([header] + body)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
