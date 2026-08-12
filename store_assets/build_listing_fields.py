"""Write localised text fields into a Partner Center listing export.

Partner Center's listing grid is field ROWS keyed by ID, with one column per
language. This rewrites whole rows from a `{language: text}` JSON file and
copies every other row through byte for byte, so a what's-new or caption change
cannot clobber descriptions, screenshots or anything else in the export.

    python store_assets/build_listing_fields.py \
        --export "listingData-....csv" \
        --set ReleaseNotes=store_assets/release-notes-1.2.0-translations.json \
        --set DesktopScreenshotCaption7=store_assets/caption7-1.2.0-translations.json \
        --out "listingData-....IMPORT.csv"

Screenshots cannot be set this way. Their cells hold Partner Center asset URLs,
not file paths, so a new image has to be uploaded as a listing asset first and
its URL only appears in a later re-export.

Everything is validated before anything is written, because a rejected import
costs a full re-export to recover from.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

# Field -> maximum characters the Store accepts. Exceeding one is the single
# most common reason an import is rejected, and the message does not say which
# language was at fault.
LIMITS = {
    "ReleaseNotes": 1500,
    "Description": 10000,
    "ShortDescription": 1000,
    "Title": 256,
}
CAPTION_LIMIT = 200
DEFAULT_LIMIT = 1000


def limit_for(field: str) -> int:
    if field.startswith("DesktopScreenshotCaption"):
        return CAPTION_LIMIT
    return LIMITS.get(field, DEFAULT_LIMIT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True)
    parser.add_argument(
        "--set", metavar="FIELD=FILE", action="append", default=[], required=True,
        help="Field row to rewrite from a {language: text} JSON file. Repeatable.",
    )
    parser.add_argument(
        "--set-slotted", metavar="PREFIX=TEXTS.json:SLOTS.json",
        action="append", default=[],
        help="Write one caption per language into a per-language row. SLOTS "
             "maps language -> slot number. The screenshot order is NOT the "
             "same in every language, so a fixed row number writes the right "
             "text onto the wrong picture.",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with io.open(args.export, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header, body = rows[0], rows[1:]
    languages = header[4:]

    jobs = []
    problems = []
    for spec in args.set:
        if "=" not in spec:
            parser.error(f"--set wants FIELD=FILE, got {spec!r}")
        field, path = spec.split("=", 1)
        if field.startswith("DesktopScreenshot") and "Caption" not in field:
            problems.append(f"{field}: screenshots hold asset URLs, not text — "
                            "upload the image as a listing asset instead")
            continue
        texts = json.loads(Path(path).read_text(encoding="utf-8"))
        cap = limit_for(field)

        missing = [code for code in languages if code not in texts]
        if missing:
            problems.append(f"{field}: no text for {missing}")
        for code, value in texts.items():
            if not str(value).strip():
                problems.append(f"{field}/{code}: empty")
            elif len(value) > cap:
                problems.append(f"{field}/{code}: {len(value)} chars, over the {cap} cap")

        target = next((r for r in body if r[0] == field), None)
        if target is None:
            problems.append(f"{field}: no such row in this export")
            continue
        jobs.append((field, target, texts, cap))

    slotted = []
    for spec in args.set_slotted:
        prefix, _, paths = spec.partition("=")
        texts_path, _, slots_path = paths.partition(":")
        if not (prefix and texts_path and slots_path):
            parser.error(f"--set-slotted wants PREFIX=TEXTS.json:SLOTS.json, got {spec!r}")
        texts = json.loads(Path(texts_path).read_text(encoding="utf-8"))
        slots = json.loads(Path(slots_path).read_text(encoding="utf-8"))
        cap = limit_for(prefix + "1")

        for code in languages:
            if code not in texts:
                problems.append(f"{prefix}/{code}: no text")
            elif len(texts[code]) > cap:
                problems.append(
                    f"{prefix}/{code}: {len(texts[code])} chars, over the {cap} cap")
            if code not in slots:
                problems.append(f"{prefix}/{code}: no slot — refusing to guess, "
                                "the wrong slot captions the wrong screenshot")
        slotted.append((prefix, texts, slots, cap))

    for problem in problems:
        print(f"  BLOCKING {problem}")
    if problems:
        print(f"\n{len(problems)} problem(s) — nothing written")
        return 1

    for field, target, texts, cap in jobs:
        changed = 0
        for index, code in enumerate(languages, start=4):
            if target[index] != texts[code]:
                changed += 1
            target[index] = texts[code]
        longest = max(len(texts[c]) for c in languages)
        print(f"  {field}: {changed}/{len(languages)} languages changed, "
              f"longest {longest} of {cap}")

    rows_by_field = {r[0]: r for r in body}
    for prefix, texts, slots, cap in slotted:
        touched, per_slot = 0, {}
        for index, code in enumerate(languages, start=4):
            row = rows_by_field.get(f"{prefix}{slots[code]}")
            if row is None:
                print(f"  BLOCKING {prefix}{slots[code]}: no such row")
                return 1
            row[index] = texts[code]
            touched += 1
            per_slot[slots[code]] = per_slot.get(slots[code], 0) + 1
        spread = ", ".join(f"{prefix}{s}x{n}" for s, n in sorted(per_slot.items()))
        print(f"  {prefix}*: {touched} languages written across {spread}, "
              f"longest {max(len(texts[c]) for c in languages)} of {cap}")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    with io.open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows([header] + body)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
