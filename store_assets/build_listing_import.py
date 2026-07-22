"""Build a Partner Center listing-import package from the current export.

Partner Center round-trips listings as a wide CSV: one row per field, one
column per language. The safe way to change a listing is therefore to start
from a fresh *export* and edit only the cells that need to move — anything
left untouched keeps whatever is already live, and nothing can be blanked by
omission.

This script does exactly that for the screenshot block: it rewrites
DesktopScreenshot1-9 and their captions for all 47 listing languages and
leaves every other row byte-identical to the export.

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
"""

import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "store_assets" / "screenshots"
# Folder name matches the CSV inside it, the shape the successful import used.
PACKAGE = "EasyPost-Store-Listings-IMPORT-v2"
OUT_DIR = ROOT / "store_assets" / PACKAGE
CSV_NAME = PACKAGE + ".csv"

# Screenshot order as it will appear on the Store page. Rate shopping leads:
# it is the one image that shows what the product actually does. Settings
# trails, because nobody installs an app for its settings page.
ORDER = [
    ("02_create_shipment", "rate_shopping"),
    ("04_tracking", "tracking"),
    ("03_address_book", "address_book"),
    ("06_batch", "batch_shipments"),
    ("05_history", "history"),
    ("07_reports", "reports"),
    ("08_hts_lookup", "hts_lookup"),
    ("01_dashboard", "dashboard"),
    ("09_settings", "settings"),
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
        "Import a CSV of recipients, then rate and buy in bulk.",
        "Every purchased label in one place, with refunds a click away.",
        "Spend by carrier and label counts at a glance.",
        "Search live Harmonized Tariff Schedule codes for customs paperwork.",
        "Shipping activity at a glance the moment the app opens.",
        "Label format, label size and fifty interface languages.",
    ],
    "zh": [
        "并排比较各承运商的实时费率，随即购买并打印面单。",
        "在一个界面追踪所有包裹，自动刷新。",
        "保存并验证地址，支持 197 个国家的本地化字段。",
        "导入收件人 CSV 文件，批量询价并购买。",
        "所有已购面单集中管理，退款只需一次点击。",
        "一目了然地查看各承运商支出与面单数量。",
        "实时查询协调关税税则（HTS）编码，便于报关。",
        "打开应用即可总览发货动态。",
        "面单格式、面单尺寸，以及 50 种界面语言。",
    ],
    "hi": [
        "कई कैरियर की लाइव दरें साथ-साथ देखें और तुरंत लेबल खरीदें व प्रिंट करें।",
        "सभी पार्सल एक ही स्क्रीन से ट्रैक करें, स्वचालित रिफ़्रेश के साथ।",
        "पते सहेजें और सत्यापित करें, 197 देशों के अनुरूप फ़ील्ड के साथ।",
        "प्राप्तकर्ताओं की CSV फ़ाइल आयात करें, फिर थोक में दर देखें और खरीदें।",
        "खरीदे गए सभी लेबल एक जगह, रिफ़ंड बस एक क्लिक दूर।",
        "कैरियर के अनुसार खर्च और लेबल संख्या एक नज़र में।",
        "सीमा शुल्क दस्तावेज़ों के लिए लाइव HTS टैरिफ़ कोड खोजें।",
        "ऐप खोलते ही शिपिंग गतिविधि एक नज़र में।",
        "लेबल फ़ॉर्मैट, लेबल आकार और पचास इंटरफ़ेस भाषाएँ।",
    ],
    "es": [
        "Compara tarifas reales de transportistas y compra la etiqueta al momento.",
        "Sigue todos los envíos desde una sola pantalla, con actualización automática.",
        "Guarda y verifica direcciones, con campos adaptados a 197 países.",
        "Importa un CSV de destinatarios y cotiza y compra por lotes.",
        "Todas las etiquetas compradas en un solo lugar, con reembolsos a un clic.",
        "Gasto por transportista y recuento de etiquetas de un vistazo.",
        "Busca códigos arancelarios armonizados en directo para la documentación aduanera.",
        "La actividad de envíos a la vista nada más abrir la aplicación.",
        "Formato de etiqueta, tamaño de etiqueta y cincuenta idiomas de interfaz.",
    ],
    "fr": [
        "Comparez les tarifs réels des transporteurs, puis achetez et imprimez l'étiquette.",
        "Suivez chaque colis depuis un seul écran, avec actualisation automatique.",
        "Enregistrez et vérifiez les adresses, avec des champs adaptés à 197 pays.",
        "Importez un CSV de destinataires, puis tarifez et achetez en lot.",
        "Toutes les étiquettes achetées au même endroit, remboursement en un clic.",
        "Dépenses par transporteur et nombre d'étiquettes en un coup d'œil.",
        "Recherchez les codes du tarif douanier harmonisé en direct.",
        "L'activité d'expédition en un coup d'œil dès l'ouverture.",
        "Format d'étiquette, taille d'étiquette et cinquante langues d'interface.",
    ],
    "de": [
        "Tarife mehrerer Dienstleister direkt vergleichen, dann Etikett kaufen und drucken.",
        "Alle Sendungen auf einem Bildschirm verfolgen, mit automatischer Aktualisierung.",
        "Adressen speichern und prüfen, mit länderspezifischen Feldern für 197 Länder.",
        "Eine CSV-Datei mit Empfängern importieren, dann Tarife abrufen und im Stapel kaufen.",
        "Alle gekauften Etiketten an einem Ort, Erstattung mit einem Klick.",
        "Ausgaben je Dienstleister und Etikettenanzahl auf einen Blick.",
        "Codes des Harmonisierten Zolltarifs live durchsuchen.",
        "Die Versandaktivität auf einen Blick, direkt nach dem Start.",
        "Etikettenformat, Etikettengröße und fünfzig Oberflächensprachen.",
    ],
    "ja": [
        "複数の配送業者の実際の料金を並べて比較し、そのままラベルを購入・印刷。",
        "すべての荷物を1つの画面で追跡。自動更新に対応。",
        "住所を保存して検証。197か国に対応した国別入力項目。",
        "宛先のCSVを取り込み、一括で料金確認と購入。",
        "購入したラベルをすべて一覧表示。返金もワンクリック。",
        "配送業者ごとの費用とラベル枚数をひと目で把握。",
        "税関書類向けにHTS（統一関税品目表）コードをリアルタイム検索。",
        "起動直後に配送状況をひと目で確認。",
        "ラベル形式、ラベルサイズ、50言語のインターフェース。",
    ],
}


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
    """Slots past the ninth held nothing in the export and must stay that way,
    otherwise a stale tenth image could survive the import."""
    for slot in range(len(ORDER) + 1, 31):
        for row in (by_field[f"DesktopScreenshot{slot}"],
                    by_field[f"DesktopScreenshotCaption{slot}"]):
            for col in range(4, width):
                row[col] = ""


def build_folder(export: Path, package: str, only_localised: bool, dest: Path | None):
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
    print(f"folder         : {out_dir}")
    deliver(out_dir, dest, package)


def build_fanout(export: Path, dest: Path | None):
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
            caption_row[col] = CAPTIONS[FALLBACK][slot - 1]
            touched += 2

    clear_unused_slots(by_field, len(header))
    target = ROOT / "store_assets" / "EasyPost-Store-Listings-IMPORT-v2-stage2.csv"
    write_csv(rows, target)

    print("mode           : stage 2 (URL fan-out, nothing uploaded)")
    print(f"listings written: {len(langs) - len(LOCALISED)}")
    print(f"cells rewritten: {touched}")
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


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    export = Path(args[0])
    dest = Path(args[1]) if len(args) > 1 else None

    if "--stage2" in flags:
        build_fanout(export, dest)
    elif "--stage1" in flags:
        build_folder(export, PACKAGE + "-stage1", True, dest)
    else:
        build_folder(export, PACKAGE, False, dest)


if __name__ == "__main__":
    main()
