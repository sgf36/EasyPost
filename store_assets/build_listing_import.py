"""Build a Partner Center listing-import package from the current export.

Partner Center round-trips listings as a wide CSV: one row per field, one
column per language. The safe way to change a listing is therefore to start
from a fresh *export* and edit only the cells that need to move — anything
left untouched keeps whatever is already live, and nothing can be blanked by
omission.

This script does exactly that for the screenshot block: it rewrites
DesktopScreenshot1-9 and their captions for all 47 listing languages and
leaves every other row byte-identical to the export.

It also rewrites the **ReleaseNotes** ("What's new in this version") row from
a per-language JSON catalogue when one is supplied, so the localised release
notes never have to be pasted into 47 cells by hand — the step that has gone
wrong before. The notes follow the same localised/fallback split as the
screenshots: stage 1 sets them for the seven languages it owns and stage 2
sets them for the remaining forty, so the two halves together cover all 47
without either clobbering the other. If no catalogue is present the row is
left byte-identical to the export, exactly as before.

Three rules the importer enforces without saying so:

1. It wants a **folder**, not a zip. The dialog asks for a directory holding
   the CSV and its images.
2. Image paths must be **prefixed with the root folder name** — Microsoft's
   own example is `my_folder/screenshot1.png`, not `screenshot1.png`. This is
   the one that bit us: a bare filename is silently unresolvable.
3. Exactly **one .csv** may sit in the folder.

Breaking any of them produces the same useless error: "we couldn't import
listings for the following languages", with the language list rendered blank
and the backing API returning `validations: []`. Nothing is saved — the import
is all-or-nothing — so a failure leaves the listing untouched.

Usage:
    python store_assets/build_listing_import.py <exported-listingData.csv> [dest]
        [--stage1 | --stage2] [--notes=<catalogue.json> | --no-notes]

    --stage1 / --stage2  the two halves of the staged import (default: full).
    --notes=<path>       release-note catalogue to inject (default: the bundled
                         current-release file); --no-notes leaves ReleaseNotes
                         byte-identical to the export.
"""

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "store_assets" / "screenshots"
# Folder name matches the CSV inside it, the shape the successful import used.
PACKAGE = "EasyPost-Store-Listings-IMPORT-v2"
OUT_DIR = ROOT / "store_assets" / PACKAGE
CSV_NAME = PACKAGE + ".csv"

# Per-language "What's new in this version" catalogue, keyed by Partner Center
# listing code (en-us, zh-hans, …) — the same codes the export uses for its
# language columns. Overridable with --notes=<path>; default is the current
# release. Absent file → the ReleaseNotes row is passed through untouched.
RELEASE_NOTES_JSON = ROOT / "store_assets" / "release-notes-1.2.4-translations.json"
# Partner Center rejects release notes longer than this.
RELEASE_NOTES_LIMIT = 1500

# Screenshot order as it will appear on the Store page. Rate shopping leads:
# it is the one image that shows what the product actually does. Settings
# trails, because nobody installs an app for its settings page.
#
# The dashboard is deliberately absent. DashboardView is still a placeholder —
# its entire body is a label reading "Shipments, tracking, address book, and
# reporting views are added in later build stages" — so the screenshot showed
# an empty screen saying the product was unfinished. It is the one image that
# argued against the app. The source capture is kept in screenshots/ so it can
# be restored in one line once there is a real dashboard to show.
#
# Dropping it takes the set from nine images to eight, but note what the import
# can and cannot do: a blank image cell is a no-op, not a delete — that is the
# very property stage 1 relies on to leave the forty fallback languages alone.
# So this removes the dashboard from everything the import WRITES, and slots 1-8
# are rewritten end to end, but whatever currently sits in slot 9 stays there
# until it is deleted in the portal. clear_unused_slots keeps slots 10-30 empty;
# it cannot empty a slot that already holds an asset.
ORDER = [
    ("02_create_shipment", "rate_shopping"),
    ("04_tracking", "tracking"),
    ("03_address_book", "address_book"),
    ("06_batch", "batch_shipments"),
    ("05_history", "history"),
    ("07_reports", "reports"),
    ("08_hts_lookup", "hts_lookup"),
    ("09_settings", "settings"),
    ("11_pickups", "pickups"),
]

# Locales with their own captured screenshots. Every other listing language
# falls back to the English set — normal practice, and the app itself stays
# fully translated into all 50 regardless.
LOCALISED = {
    "en-us": "en",
    "zh-hans": "zh",
    "hi": "hi",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "ja": "ja",
}
FALLBACK = "en"

CAPTIONS = {
    "en": [
        "Compare live carrier rates side by side, then buy and print the label.",
        "Track every parcel from a single screen, with automatic refresh.",
        "Save and verify addresses, with country-aware fields for 197 countries.",
        "Import a CSV of recipients, choose a carrier and service, then buy in bulk.",
        "Every purchased label in one place, with refunds a click away.",
        "Spend by carrier and label counts at a glance.",
        "Search live Harmonized Tariff Schedule codes for customs paperwork.",
        "Label format, label size and fifty interface languages.",
        "Request a carrier pickup, compare pickup rates and cancel in a click.",
    ],
    "zh": [
        "并排比较各承运商的实时费率，随即购买并打印面单。",
        "在一个界面追踪所有包裹，自动刷新。",
        "保存并验证地址，支持 197 个国家的本地化字段。",
        "导入收件人 CSV 文件，选择承运商和服务，然后批量购买。",
        "所有已购面单集中管理，退款只需一次点击。",
        "一目了然地查看各承运商支出与面单数量。",
        "实时查询协调关税税则（HTS）编码，便于报关。",
        "面单格式、面单尺寸，以及 50 种界面语言。",
        "申请承运商上门取件，比较取件运费，一键取消。",
    ],
    "hi": [
        "कई कैरियर की लाइव दरें साथ-साथ देखें और तुरंत लेबल खरीदें व प्रिंट करें।",
        "सभी पार्सल एक ही स्क्रीन से ट्रैक करें, स्वचालित रिफ़्रेश के साथ।",
        "पते सहेजें और सत्यापित करें, 197 देशों के अनुरूप फ़ील्ड के साथ।",
        "प्राप्तकर्ताओं की CSV आयात करें, वाहक कंपनी और सेवा चुनें, फिर थोक में खरीदें।",
        "खरीदे गए सभी लेबल एक जगह, रिफ़ंड बस एक क्लिक दूर।",
        "कैरियर के अनुसार खर्च और लेबल संख्या एक नज़र में।",
        "सीमा शुल्क दस्तावेज़ों के लिए लाइव HTS टैरिफ़ कोड खोजें।",
        "लेबल फ़ॉर्मैट, लेबल आकार और पचास इंटरफ़ेस भाषाएँ।",
        "वाहक से पिकअप का अनुरोध करें, पिकअप दरें तुलना करें और एक क्लिक में रद्द करें।",
    ],
    "es": [
        "Compara tarifas reales de transportistas y compra la etiqueta al momento.",
        "Sigue todos los envíos desde una sola pantalla, con actualización automática.",
        "Guarda y verifica direcciones, con campos adaptados a 197 países.",
        "Importa un CSV de destinatarios, elige un transportista y un servicio, y compra en lote.",
        "Todas las etiquetas compradas en un solo lugar, con reembolsos a un clic.",
        "Gasto por transportista y recuento de etiquetas de un vistazo.",
        "Busca códigos arancelarios armonizados en directo para la documentación aduanera.",
        "Formato de etiqueta, tamaño de etiqueta y cincuenta idiomas de interfaz.",
        "Solicita una recogida del transportista, compara tarifas y cancela con un clic.",
    ],
    "fr": [
        "Comparez les tarifs réels des transporteurs, puis achetez et imprimez l'étiquette.",
        "Suivez chaque colis depuis un seul écran, avec actualisation automatique.",
        "Enregistrez et vérifiez les adresses, avec des champs adaptés à 197 pays.",
        "Importez un CSV de destinataires, choisissez un transporteur et un service, puis achetez en lot.",
        "Toutes les étiquettes achetées au même endroit, remboursement en un clic.",
        "Dépenses par transporteur et nombre d'étiquettes en un coup d'œil.",
        "Recherchez les codes du tarif douanier harmonisé en direct.",
        "Format d'étiquette, taille d'étiquette et cinquante langues d'interface.",
        "Demandez un enlèvement, comparez les tarifs et annulez en un clic.",
    ],
    "de": [
        "Tarife mehrerer Dienstleister direkt vergleichen, dann Etikett kaufen und drucken.",
        "Alle Sendungen auf einem Bildschirm verfolgen, mit automatischer Aktualisierung.",
        "Adressen speichern und prüfen, mit länderspezifischen Feldern für 197 Länder.",
        "CSV mit Empfängern importieren, Versanddienstleister und Service wählen, dann im Stapel kaufen.",
        "Alle gekauften Etiketten an einem Ort, Erstattung mit einem Klick.",
        "Ausgaben je Dienstleister und Etikettenanzahl auf einen Blick.",
        "Codes des Harmonisierten Zolltarifs live durchsuchen.",
        "Etikettenformat, Etikettengröße und fünfzig Oberflächensprachen.",
        "Abholung anfordern, Abholtarife vergleichen und mit einem Klick stornieren.",
    ],
    "ja": [
        "複数の配送業者の実際の料金を並べて比較し、そのままラベルを購入・印刷。",
        "すべての荷物を1つの画面で追跡。自動更新に対応。",
        "住所を保存して検証。197か国に対応した国別入力項目。",
        "受取人のCSVをインポートし、配送業者とサービスを選んで、一括購入。",
        "購入したラベルをすべて一覧表示。返金もワンクリック。",
        "配送業者ごとの費用とラベル枚数をひと目で把握。",
        "税関書類向けにHTS（統一関税品目表）コードをリアルタイム検索。",
        "ラベル形式、ラベルサイズ、50言語のインターフェース。",
        "配送業者の集荷を依頼し、集荷料金を比較して、ワンクリックでキャンセル。",
    ],
}


# ORDER and each caption list are parallel: CAPTIONS[locale][slot - 1] captions
# ORDER[slot - 1]. Dropping or adding a screenshot means editing both, and a
# list left one entry long would silently shift every caption after it onto the
# wrong image — the same drift c91fff6 fixed on the stage 2 side, which is not
# visible in any count the build prints.
for _locale, _captions in CAPTIONS.items():
    if len(_captions) != len(ORDER):
        raise SystemExit(
            f"{_locale} has {len(_captions)} captions for {len(ORDER)} "
            f"screenshots. ORDER and CAPTIONS must stay parallel."
        )


def asset_file(locale: str, slot: int, slug: str) -> str:
    return f"{locale}_{slot}_{slug}.png"


def read_export(path: Path):
    rows = list(csv.reader(Path(path).read_bytes().decode("utf-8-sig").splitlines(True)))
    # Column 3 is "default", the catch-all Partner Center applies to languages
    # with no value of their own. The export leaves it empty and every listing
    # language is populated explicitly, so it stays empty here too.
    return rows, rows[0], rows[0][4:], {r[0]: r for r in rows[1:]}


def write_csv(rows, target: Path) -> None:
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)


def clear_unused_slots(by_field, width: int) -> None:
    """Keep every slot past the last one in ORDER blank in the written CSV.

    This stops a stale image being *cited* by the package. It does not delete
    anything already live: Partner Center reads a blank image cell as "leave
    unchanged", which is exactly why stage 1 can blank the forty fallback
    languages without touching their screenshots. A populated slot beyond ORDER
    therefore survives until it is removed in the portal.
    """
    for slot in range(len(ORDER) + 1, 31):
        for row in (by_field[f"DesktopScreenshot{slot}"],
                    by_field[f"DesktopScreenshotCaption{slot}"]):
            for col in range(4, width):
                row[col] = ""


def load_release_notes(path: Path | None):
    """Read the per-language release-note catalogue, or None if none is set.

    A missing file is a hard error only when the caller asked for one by
    passing --notes; the built-in default silently no-ops so the screenshot
    refresh still works on a machine that has not staged release notes."""
    if path is None:
        return None
    if not path.exists():
        raise SystemExit(f"release-notes catalogue not found: {path}")
    notes = json.loads(path.read_text(encoding="utf-8"))
    over = {k: len(v) for k, v in notes.items() if len(v) > RELEASE_NOTES_LIMIT}
    if over:
        raise SystemExit(f"release notes exceed {RELEASE_NOTES_LIMIT} chars: {over}")
    return notes


def inject_release_notes(by_field, langs, notes, owns) -> int:
    """Overwrite the ReleaseNotes cell for every language this stage owns.

    `owns(lang) -> bool` picks the columns this half of the staged import is
    responsible for — the same localised/fallback split the screenshots use —
    so stage 1 and stage 2 together cover all 47 and neither blanks a cell the
    other populated. Languages absent from the catalogue keep their export
    value untouched rather than being blanked."""
    if not notes:
        return 0
    row = by_field.get("ReleaseNotes")
    if row is None:
        raise SystemExit("export has no ReleaseNotes row; cannot inject notes")
    written = 0
    for col, lang in enumerate(langs, start=4):
        if not owns(lang):
            continue
        text = notes.get(lang)
        if text is None:
            continue
        row[col] = text
        written += 1
    return written


def build_folder(export: Path, package: str, only_localised: bool, dest: Path | None,
                 notes=None):
    """A folder package: CSV plus the images it cites, paths prefixed with the
    folder name."""
    rows, header, langs, by_field = read_export(export)
    out_dir = ROOT / "store_assets" / package

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    for locale in LOCALISED.values():
        for slot, (source, slug) in enumerate(ORDER, start=1):
            src = SHOTS / locale / f"{source}.png"
            if not src.exists():
                raise SystemExit(f"missing screenshot: {src}")
            shutil.copy2(src, out_dir / asset_file(locale, slot, slug))

    touched = 0
    for slot, (_, slug) in enumerate(ORDER, start=1):
        shot_row = by_field[f"DesktopScreenshot{slot}"]
        caption_row = by_field[f"DesktopScreenshotCaption{slot}"]
        for col, lang in enumerate(langs, start=4):
            if only_localised and lang not in LOCALISED:
                # Blanking an image field is a documented no-op: the language
                # keeps whatever it already has. That is what makes staging
                # safe — the 40 fallback listings are simply not touched, and
                # no asset URL has to sit beside a folder path in one file.
                shot_row[col] = ""
                caption_row[col] = ""
                continue
            locale = LOCALISED.get(lang, FALLBACK)
            shot_row[col] = f"{package}/{asset_file(locale, slot, slug)}"
            caption_row[col] = CAPTIONS[locale][slot - 1]
            touched += 2

    # Release notes follow the same split: in stage 1 (localised only) this
    # stage owns the localised languages; in full mode it owns them all.
    notes_written = inject_release_notes(
        by_field, langs, notes,
        owns=(lambda l: l in LOCALISED) if only_localised else (lambda l: True),
    )

    clear_unused_slots(by_field, len(header))
    target = out_dir / f"{package}.csv"
    write_csv(rows, target)

    # Everything the importer will silently reject, checked here instead —
    # its error page names no field, no language and no reason.
    if "developer.microsoft.com" in target.read_text(encoding="utf-8-sig"):
        raise SystemExit("asset URL survived into the CSV; it will not resolve")
    if len(list(out_dir.glob("*.csv"))) != 1:
        raise SystemExit("the folder must hold exactly one .csv")

    images = sorted(p.name for p in out_dir.glob("*.png"))
    cited = {v for row in rows[1:] if row[0].startswith("DesktopScreenshot")
             and "Caption" not in row[0] for v in row[4:] if v.strip()}
    for ref in sorted(cited):
        root, _, name = ref.partition("/")
        if root != package or name not in images:
            raise SystemExit(f"unresolvable screenshot reference: {ref}")

    print(f"mode           : {'stage 1 (localised only)' if only_localised else 'full'}")
    print(f"listings written: {len(LOCALISED) if only_localised else len(langs)}")
    print(f"images copied  : {len(images)}")
    print(f"cells rewritten: {touched}")
    print(f"release notes  : {notes_written if notes else 'unchanged (no catalogue)'}")
    print(f"folder         : {out_dir}")
    deliver(out_dir, dest, package)


def build_fanout(export: Path, dest: Path | None, notes=None):
    """Stage 2: no upload at all. Copy the English asset URLs that stage 1
    created into the 40 languages that reuse them, and ship a bare CSV.

    This is the pattern that already populated those listings once, and it is
    the cheap half — Partner Center resolves a URL it minted itself instead of
    ingesting 3.8 MB of images across 400-odd asset records."""
    rows, header, langs, by_field = read_export(export)
    en = header.index("en-us")

    missing = [s for s in range(1, len(ORDER) + 1)
               if "developer.microsoft.com" not in by_field[f"DesktopScreenshot{s}"][en]]
    if missing:
        raise SystemExit(
            f"en-us slots {missing} hold no Partner Center URL. Run stage 1 first, "
            "then export again and re-run this."
        )

    touched = 0
    for slot in range(1, len(ORDER) + 1):
        shot_row = by_field[f"DesktopScreenshot{slot}"]
        caption_row = by_field[f"DesktopScreenshotCaption{slot}"]
        url = shot_row[en]
        for col, lang in enumerate(langs, start=4):
            if lang in LOCALISED:
                continue  # keeps its own imagery, passed through untouched
            shot_row[col] = url
            # Take the caption from the SAME slot of the same export the image
            # came from, rather than regenerating it from ORDER.
            #
            # Partner Center does not keep the order a folder import sends. The
            # 1.2.0 stage-1 import came back with slots 4-9 permuted, so
            # ORDER's index no longer described what sits in slot N. Rebuilding
            # the caption from ORDER while copying the image from the export
            # paired the Settings screenshot with the batch caption, and
            # mislabelled every slot from 4 down, in all forty languages.
            #
            # Copying both from the same cell cannot drift: whatever English
            # shows, the other forty show, correctly captioned.
            caption_row[col] = caption_row[en]
            touched += 2

    # Stage 2 owns exactly the languages stage 1 did not: the non-localised
    # forty. Together the two stages set release notes for all 47.
    # Release notes are written for ALL 47, not just the forty this stage owns
    # for images.
    #
    # The stage split exists because of how image fields behave: a blank one is
    # a no-op, and an asset URL cannot sit in the same file as a folder path.
    # Neither applies to text. ReleaseNotes is a plain string that overwrites
    # cleanly, so there is no reason for it to inherit the split — and every
    # reason not to, because stage 1 runs against whichever catalogue was
    # current when the images were shot. Owning notes only here means one file
    # decides the version, instead of the seven localised languages keeping
    # whatever stage 1 happened to write.
    #
    # That is not hypothetical: stage 1 for 1.2.1 was built before the 1.2.1
    # catalogue existed, so the listing would have published 1.2.1 notes in
    # forty languages and 1.2.0 in the seven that matter most, English included.
    notes_written = inject_release_notes(
        by_field, langs, notes, owns=lambda l: True,
    )

    clear_unused_slots(by_field, len(header))
    target = ROOT / "store_assets" / "EasyPost-Store-Listings-IMPORT-v2-stage2.csv"
    write_csv(rows, target)

    print("mode           : stage 2 (URL fan-out, nothing uploaded)")
    print(f"listings written: {len(langs) - len(LOCALISED)} (+ release notes for all {len(langs)})")
    print(f"cells rewritten: {touched}")
    print(f"release notes  : {notes_written if notes else 'unchanged (no catalogue)'}")
    print(f"csv            : {target}")
    if dest:
        shutil.copy2(target, dest / target.name)
        print(f"copied to      : {dest / target.name}")


def deliver(out_dir: Path, dest: Path | None, package: str) -> None:
    if not dest:
        return
    target = dest / package
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(out_dir, target)
    print(f"copied to      : {target}")


def resolve_notes(flags) -> Path | None:
    """Pick the release-note catalogue from the flags.

    --no-notes   → skip injection, pass ReleaseNotes through untouched.
    --notes=PATH → use PATH (hard error if it is missing).
    (neither)    → the built-in default file if it exists, else no-op.
    """
    if "--no-notes" in flags:
        return None
    for flag in flags:
        if flag.startswith("--notes="):
            return Path(flag.split("=", 1)[1])
    return RELEASE_NOTES_JSON if RELEASE_NOTES_JSON.exists() else None


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    export = Path(args[0])
    dest = Path(args[1]) if len(args) > 1 else None
    notes = load_release_notes(resolve_notes(flags))

    if "--stage2" in flags:
        build_fanout(export, dest, notes)
    elif "--stage1" in flags:
        build_folder(export, PACKAGE + "-stage1", True, dest, notes)
    else:
        build_folder(export, PACKAGE, False, dest, notes)


if __name__ == "__main__":
    main()
