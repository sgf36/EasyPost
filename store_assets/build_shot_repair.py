"""Repair the three English screenshots sitting in 36 non-English listings.

Audited 2026-08-30 by hashing all 423 published images: slots 1, 2 and 3 were
byte-identical to the English ones in 36 of 47 languages, while slots 4-9 in the
same listings were correctly localised. Three languages carried an extra English
slot. Only those cells are rewritten here.

Deliberately NOT using store_assets/build_listing_import.py. Its ORDER writes
batch/history/reports/hts/settings/pickups into slots 4-9, but the LIVE listing
runs settings/reports/history/pickups/batch/hts -- read off the English captions
slot by slot. Running it would reorder six slots in the languages it touches and
leave them disagreeing with the ten it does not. This writes cells, not layouts.

The ReleaseNotes row is rewritten too. The export this is built from predates the
1.2.8 notes import, and every row not rewritten is passed through byte-identical
-- so leaving it alone would quietly revert 47 languages to the 1.2.6 text.
"""
import csv, io, json, pathlib, shutil, sys

EXPORT = pathlib.Path(r"C:\Users\SpencerFields\Downloads"
                      r"\listingData-9NDSDL5LV5B5-1152921505701770595.csv")
NOTES = pathlib.Path("release-notes-1.2.8-translations.json")
SHOTS = pathlib.Path(r"C:\shots")
SIZE = "1366x768"          # 2160x1440 clips service names in RTL; 1366 does not
PACKAGE = "EP-shots-1.2.8"
OUT = pathlib.Path(r"C:\Users\SpencerFields\OneDrive - Spencer Fields\Apps"
                   r"\Claude\EasyPost-Desktop-App\dist") / PACKAGE

# Slot order as PUBLISHED, read off the English captions, not as any script
# would generate it.
SLOT_VIEW = {1: "CreateShipmentView", 2: "TrackingView", 3: "AddressBookView",
             4: "SettingsView", 5: "ReportsView", 6: "HistoryView",
             7: "PickupsView", 8: "BatchView", 9: "HtsLookupView"}

ODD = {"ig-latn": "ig", "yo-latn": "yo"}
BROKEN = ['am','ar','bn','cs','el','fa','gu','ha','hr','hu','id','ig-latn','it',
          'kn','ko','ml','mr','ms','ne','nl','or','pl','pt','ro','ru','si','sv',
          'sw','th','tr','uk','ur','uz','vi','yo-latn','zu']
EXTRA = {"gu": [6], "id": [5], "uk": [4, 5]}      # beyond slots 1-3

def main():
    with io.open(EXPORT, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    hdr = rows[0]
    col = {l: i for i, l in enumerate(hdr) if i >= 4 and l}
    notes = json.loads(NOTES.read_text(encoding="utf8"))

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    rowix = {r[0]: n for n, r in enumerate(rows) if r}
    written = 0
    for lang in BROKEN:
        app = ODD.get(lang, lang)
        for slot in [1, 2, 3] + EXTRA.get(lang, []):
            src = SHOTS / app / "windows" / app / SIZE / f"window-{SLOT_VIEW[slot]}.png"
            if not src.is_file():
                sys.exit(f"missing render: {src}")
            name = f"{lang}-{slot}.png"
            shutil.copy2(src, OUT / name)
            # The folder name must prefix the path. A bare filename is silently
            # unresolvable and fails the whole import with a blank error.
            rows[rowix[f"DesktopScreenshot{slot}"]][col[lang]] = f"{PACKAGE}/{name}"
            written += 1

    nrow = rows[rowix["ReleaseNotes"]]
    for lang, i in col.items():
        if lang in notes:
            nrow[i] = notes[lang]

    with io.open(OUT / f"{PACKAGE}.csv", "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)

    total = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"{written} images written across {len(BROKEN)} languages")
    print(f"release notes set for {sum(1 for l in col if l in notes)} languages")
    print(f"folder: {OUT}")
    print(f"total size: {total/1e6:.2f} MB against the 10 MB upload ceiling")

main()
