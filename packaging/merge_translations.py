"""Merge translated key fragments into the locale catalogues, safely.

Translation work produces one small file per locale containing only the NEW
keys. This merges each fragment into the corresponding full catalogue, which
already holds ~580 established translations.

Every check happens before anything is written, and each file is written
atomically, because a half-written catalogue is worse than an untranslated one:
a missing key falls back to English and the app carries on, whereas malformed
JSON takes the whole locale down.

Usage:
    python packaging/merge_translations.py <fragments-dir> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES = REPO_ROOT / "app" / "resources" / "locales"

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(str(text)))


def check_fragment(code: str, english: dict, fragment: dict) -> list[str]:
    """Everything that would make this fragment unsafe to merge."""
    problems = []

    unknown = sorted(set(fragment) - set(english))
    if unknown:
        problems.append(f"keys not in en.json: {unknown[:5]}")

    for key, value in fragment.items():
        if key not in english:
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{key}: empty or non-string")
            continue
        # A dropped or renamed placeholder raises KeyError at render time, in
        # front of the user, in a language nobody on the team reads.
        want, got = placeholders(english[key]), placeholders(value)
        if want != got:
            problems.append(f"{key}: placeholders {sorted(want)} -> {sorted(got)}")
        # A value identical to English is usually a skipped string rather than a
        # genuine loanword. Worth reporting, not worth blocking.
        if value == english[key] and len(value) > 25:
            problems.append(f"{key}: NOTE identical to English")
    return problems


def write_atomic(path: Path, data: dict) -> None:
    """Write via a temporary file and replace, so an interrupted run can never
    leave a truncated catalogue behind."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(sorted(data.items())), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fragments", help="directory of <code>.json fragments")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    english = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    fragments_dir = Path(args.fragments)

    merged, skipped, notes = [], [], []
    for fragment_path in sorted(fragments_dir.glob("*.json")):
        code = fragment_path.stem
        target = LOCALES / f"{code}.json"
        if not target.exists():
            skipped.append(f"{code}: no such locale catalogue")
            continue
        try:
            fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            skipped.append(f"{code}: fragment is not valid JSON ({exc})")
            continue

        problems = check_fragment(code, english, fragment)
        blocking = [p for p in problems if "NOTE" not in p]
        notes.extend(f"{code}: {p}" for p in problems if "NOTE" in p)
        if blocking:
            skipped.append(f"{code}: {'; '.join(blocking[:3])}")
            continue

        catalogue = json.loads(target.read_text(encoding="utf-8"))
        before = len(catalogue)
        catalogue.update(fragment)
        if not args.dry_run:
            write_atomic(target, catalogue)
        merged.append(f"{code}: {before} -> {len(catalogue)} keys")

    for line in merged:
        print(f"  merged  {line}")
    for line in notes:
        print(f"  note    {line}")
    for line in skipped:
        print(f"  SKIPPED {line}")

    print(f"\n{len(merged)} merged, {len(skipped)} skipped"
          f"{' (dry run — nothing written)' if args.dry_run else ''}")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
