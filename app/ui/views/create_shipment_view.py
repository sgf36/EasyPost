"""Create a shipment, shop rates, buy a label, and save/open it."""

from functools import partial

import requests
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import customs, units
from app.core.client import client_manager
from app.core.countries import COUNTRIES
from app.core.errors import carrier_messages, format_api_error
from app.core.settings import load_settings, save_settings
from app.i18n import tr
from app.services.addresses import address_choice_label, list_addresses
from app.services.carriers import carriers_present_in, for_carrier
from app.services.formatting import display_carrier, humanize_code
from app.services import rates as rate_rules
from app.services.insurance import INSURANCE_MAX_USD
from app.services.packages import (
    delete_saved_package,
    list_predefined_packages,
    list_saved_packages,
    save_package,
)
from app.services.tracking import track_shipment
from app.services.shipments import (
    buy_shipment,
    create_rate_quote,
    create_shipment,
    save_shipment_locally,
)
from app.core.review_prompt import mark_session_friction, note_successful_shipment
from app.ui.open_file import open_label
from app.ui.theme import TEXT_MUTED
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.carrier_combo import CarrierCombo
from app.ui.widgets.chips import badge
from app.ui.widgets.print_sheet_dialog import PrintSheetDialog
from app.ui.widgets.purchase_confirm import confirm_if_production
from app.ui.widgets.review_nudge import schedule_review_prompt

# Carrier & service | Rate | Est. days | Buy. Rates are shown in a QTreeWidget
# grouped by carrier: the carrier is a top-level (header) row and each service
# is a child under it, so column 0 carries the carrier name on a header row and
# the service name on a child row rather than both sharing one flat cell.
#
# There used to be an "Included" column between the name and the rate, holding
# the tracked / signed / guaranteed badges. Those badges now sit on a line under
# the service name with cheapest / fastest (see _build_rate_service_cell). A
# column of its own competed with the name for width, and at the default window
# either the name or the badges had to be cut; under the name neither is.
_RATE_COLUMN_COUNT = 4
_RATE_COL_SERVICE, _RATE_COL_PRICE, _RATE_COL_DAYS, _RATE_COL_BUY = range(_RATE_COLUMN_COUNT)
_CUSTOMS_ITEM_COLUMN_COUNT = 7

# Label previews are rendered from the image EasyPost returns. PDFs can't be
# painted by Qt without a PDF engine, so those fall back to the open/save
# buttons alone.
_PREVIEWABLE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp")

# The label formats EasyPost produces (app/core/label_options.py), keyed by the
# suffix of the label URL, with label_file_type as the fallback. Checked live in
# test mode for all four: the URL suffix and file type match the format asked for.
_LABEL_FILE_FORMATS = {"png": "PNG", "pdf": "PDF", "zpl": "ZPL", "epl2": "EPL2"}
_LABEL_FILE_TYPES = {
    "image/png": "png", "application/pdf": "pdf",
    "application/zpl": "zpl", "application/x-epl2": "epl2",
}


def _narrowable_combo() -> QComboBox:
    """A combo whose longest entry does not set the page's minimum width.

    An address choice reads "Label — street, city, state", and Qt sizes a combo
    to its longest entry by default, so one long saved address widened the whole
    page. The full text is still shown in the open list.
    """
    combo = QComboBox()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(16)
    return combo


def _is_previewable(url: str) -> bool:
    return (url or "").lower().split("?")[0].endswith(_PREVIEWABLE_SUFFIXES)


def _label_file_extension(url: str, file_type: str | None = None) -> str | None:
    """The extension of the file a label URL actually serves, or None.

    The bytes are written exactly as downloaded, so the name must say what they
    are. Offering "label.pdf" for the default PNG label produced a file no PDF
    reader would open, at the moment a new customer first bought something.
    """
    path = (url or "").split("?")[0].lower()
    for ext in _LABEL_FILE_FORMATS:
        if path.endswith("." + ext):
            return ext
    return _LABEL_FILE_TYPES.get((file_type or "").lower())


def _service_enhancements(rate) -> list[str]:
    """Which included features a rate's service advertises, as a subset of
    ["tracked", "signed", "guaranteed"], in that fixed order.

    Read from the service name (case-insensitive) plus EasyPost's
    delivery_date_guaranteed flag — "signature" in a name counts as signed
    because carriers name the age-verified variants "…Signature…" rather than
    "…SignedFor…". Purely descriptive and never raises: a rate with no service
    name simply yields an empty list.
    """
    name = (getattr(rate, "service", "") or "").lower()
    enhancements: list[str] = []
    if "tracked" in name:
        enhancements.append("tracked")
    if "signed" in name or "signature" in name:
        enhancements.append("signed")
    if getattr(rate, "delivery_date_guaranteed", False) or "guaranteed" in name:
        enhancements.append("guaranteed")
    return enhancements


def _format_price(rate) -> str:
    # Royal Mail and other OBA carriers bill the real postage to the account,
    # so the sub-penny figure EasyPost hands back is not the price to show —
    # say it's invoiced rather than a misleading "0.01 GBP".
    if rate_rules.is_account_billed(rate):
        return tr("create_shipment.billed_to_account")
    amount = getattr(rate, "rate", "") or ""
    currency = getattr(rate, "currency", "") or ""
    return f"{amount} {currency}".strip()


#: Shown in the Est. days column when a carrier gave no estimate. The words
#: ("Not quoted") are the cell's tooltip; in the cell they were a single 170px
#: word in Tamil, in a column of one- and two-digit numbers, taken out of the
#: service name's width.
_NO_ESTIMATE = "—"


def _format_delivery(rate) -> str:
    """Just the number — the column is already headed "Est. days", so this
    sidesteps plural rules ("1 days") in every one of the 50 locales."""
    days = rate_rules.delivery_days(rate)
    if days is None:
        return _NO_ESTIMATE
    return str(days)


# What a rate's figure means (a real price, a "billed to account" marker or a
# placeholder that cannot be bought) and which rates may be ranked against each
# other are decided in app/services/rates.py. They used to be private copies
# here, which is why the agent purchase path never applied them.

# A carrier group with more than this many services starts collapsed (its count
# stays visible on the header), so a 70-service Royal Mail catalogue doesn't
# bury every other carrier; smaller groups — and whichever group holds the
# cheapest rate — start expanded. See _populate_rates_tree.
_MAX_AUTO_EXPAND = 8


def _size_widget_column(table, col: int, *, padding: int = 16) -> None:
    """Widen `col` to fit its widest cell *widget*. Qt's ResizeToContents
    measures the item delegate, not widgets set via setCellWidget, so a column
    holding only a widget (e.g. the Buy button) otherwise collapses and clips.
    The column must be in Interactive/Fixed mode for this to take effect."""
    width = 0
    for row in range(table.rowCount()):
        widget = table.cellWidget(row, col)
        if widget is not None:
            width = max(width, widget.sizeHint().width())
    if width:
        table.setColumnWidth(col, width + padding)


#: The object names inside a rate's first cell, so the column sizing can
#: measure the name and the badge line themselves rather than the cell's hint.
_SERVICE_NAME = "rateServiceName"
_SERVICE_BADGES = "rateServiceBadges"


class _ServiceCell(QWidget):
    """A rate's first cell, whose height Qt can ask for and get a true answer.

    QTreeView sizes a row from each cell widget's sizeHint, and a word-wrapping
    label's hint is a height for a width Qt guesses, not the width the column
    has. Rows came out 10 to 40px taller than their text, with the badges
    stretched to fill the gap. _fit_rate_rows measures the height for the real
    width and pins it here.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fitted_height = 0

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.fitted_height:
            hint.setHeight(self.fitted_height)
        return hint


def _cell_depth_indent(tree, item) -> int:
    """The horizontal space the tree takes before an item's column-0 cell."""
    depth = 0
    parent = item.parent()
    while parent is not None:
        depth += 1
        parent = parent.parent()
    return tree.indentation() * (depth + (1 if tree.rootIsDecorated() else 0))


def _natural_cell_width(tree, item) -> int:
    """What column 0 needs to show this row's name on one line.

    Measured from the name label's own text, never from the cell widget's size
    hint: a word-wrapping label reports a guessed width, and the delegate that
    Qt's own sizing consults knows nothing about cell widgets at all. The old
    sizing measured ``item.text(0)``, which is empty on every service row
    because the name lives in a widget, so column 0 was sized to the carrier
    headers alone and handed whatever the other columns left: 28px in German,
    0px in Tamil.
    """
    widget = tree.itemWidget(item, _RATE_COL_SERVICE)
    if widget is None:
        width = tree.fontMetrics().horizontalAdvance(item.text(0))
    else:
        # The badges' padding comes from the stylesheet, which is applied only
        # at polish; measured before that, chips came out narrower than drawn.
        widget.ensurePolished()
        label = widget.findChild(QLabel, _SERVICE_NAME)
        if label is None:
            width = widget.sizeHint().width()
        else:
            margins = widget.layout().contentsMargins()
            name = label.fontMetrics().horizontalAdvance(label.text()) + 4
            # Badges sit on their own line under the name, so they widen the
            # column only when that line is wider than the name itself.
            badges = widget.findChild(QWidget, _SERVICE_BADGES)
            extra = badges.sizeHint().width() if badges is not None else 0
            width = max(name, extra) + margins.left() + margins.right()
    return width + _cell_depth_indent(tree, item)


def _rate_rows(tree, *, visible_only: bool = False):
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        yield top
        if visible_only and not top.isExpanded():
            continue
        for j in range(top.childCount()):
            yield top.child(j)


def _fit_rate_columns(tree, *, padding: int = 12) -> None:
    """Give the carrier-and-service column the width its names actually need.

    Carrier and service names are never translated -- they are the carrier's own
    product names -- so column 0 needs the same width in every language. What
    varies is everything competing with it: in German and Tamil "Billed to
    account" and "Est. days" run two to three times longer. The priority is
    therefore fixed here rather than left to Qt:

        service name  >  rate and days figures  >  header words

    A clipped header is a word the reader can infer from the column beneath it
    (and it is one hover away), so headers elide. A clipped service name is a
    false statement about what the button beside it buys.

    The rate and days columns are sized to their figures, column 0 takes what
    the longest name on screen needs, and anything left over first widens the
    headers that were cut short and then goes to column 0. Only the rows on
    screen are measured: Royal Mail's collapsed catalogue holds a 63-character
    service name that would otherwise take the room from every header while
    nobody can see it. Expanding a group re-runs this.

    When even a name cannot fit on one line (the 880px minimum window) column 0
    takes everything and the name wraps; _fit_rate_rows makes the row tall
    enough, so the whole name is still there to read.
    """
    header = tree.header()
    viewport = tree.viewport().width()
    if viewport <= 0:                  # not laid out yet; nothing to divide up
        return

    header.setTextElideMode(Qt.TextElideMode.ElideRight)
    compact_cols = (_RATE_COL_PRICE, _RATE_COL_DAYS)
    for col in (_RATE_COL_SERVICE,) + compact_cols:
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)

    needed = padding
    for item in _rate_rows(tree, visible_only=True):
        needed = max(needed, _natural_cell_width(tree, item) + padding)

    # sizeHintForColumn measures the items only, so a long header no longer
    # drags these two wider than the figures they hold.
    compact = {c: tree.sizeHintForColumn(c) + padding for c in compact_cols}
    buy = header.sectionSize(_RATE_COL_BUY)

    # Still contested: "Billed to account" is two long words in Tamil. The price
    # wraps (between words, never inside one) before the name gives up width.
    shortfall = needed + sum(compact.values()) + buy - viewport
    if shortfall > 0:
        floor = padding + max(
            (
                tree.fontMetrics().horizontalAdvance(word)
                for item in _rate_rows(tree, visible_only=True)
                for word in item.text(_RATE_COL_PRICE).split()
            ),
            default=0,
        )
        compact[_RATE_COL_PRICE] = max(floor, compact[_RATE_COL_PRICE] - shortfall)

    spare = viewport - sum(compact.values()) - buy - needed
    for col in compact_cols:
        if spare <= 0:
            break
        extra = min(max(header.sectionSizeFromContents(col).width() - compact[col], 0), spare)
        compact[col] += extra
        spare -= extra

    header.resizeSection(_RATE_COL_SERVICE, max(viewport - sum(compact.values()) - buy, 0))
    for col, width in compact.items():
        header.resizeSection(col, width)


def _buy_button(tree, item) -> QPushButton | None:
    """The Buy button on a rate row, or None on a carrier header."""
    holder = tree.itemWidget(item, _RATE_COL_BUY)
    return holder.findChild(QPushButton) if holder is not None else None


def _wrapped_text_height(tree, text: str, width: int) -> int:
    rect = tree.fontMetrics().boundingRect(
        QRect(0, 0, max(width, 1), 100000), int(Qt.TextFlag.TextWordWrap), text
    )
    return rect.height()


def _fit_rate_rows(tree, *, gap: int = 6, padding: int = 12) -> int:
    """Make each row as tall as its cell widgets need, and return the total.

    Qt sizes a row from the item delegate, which cannot see cell widgets, so a
    wrapped service name was cut to its first line and the tree's own height
    estimate disagreed with the rows it drew (the 900px of blank space inside
    the tree in the audit). Every row's height is set here instead, from the
    width its name actually has, and the tree is sized to exactly their sum.
    """
    total = 0
    column0 = tree.columnWidth(_RATE_COL_SERVICE)
    for item in _rate_rows(tree, visible_only=True):
        height = 0
        for col in range(tree.columnCount()):
            widget = tree.itemWidget(item, col)
            if widget is None:
                continue
            if col == _RATE_COL_SERVICE and widget.hasHeightForWidth():
                width = max(column0 - _cell_depth_indent(tree, item), 1)
                fitted = widget.heightForWidth(width)
                if isinstance(widget, _ServiceCell):
                    widget.fitted_height = fitted
                    widget.updateGeometry()
                height = max(height, fitted)
            else:
                height = max(height, widget.sizeHint().height())
        # The price may wrap too (see _fit_rate_columns), and the delegate's own
        # size hint does not know the column width it will be drawn in.
        price = item.text(_RATE_COL_PRICE)
        if price:
            height = max(height, _wrapped_text_height(
                tree, price, tree.columnWidth(_RATE_COL_PRICE) - padding) + 8)
        if height == 0:
            # A carrier header has no widgets; size it to its text.
            height = tree.fontMetrics().height()
        height += gap
        # On every column: the row is as tall as its tallest size hint, and the
        # delegate's hint for a word-wrapping text cell guesses at a width it
        # does not know, which left gaps of 10 to 40px under single-line rows.
        # The width half of the hint is the text's single-line width, because
        # sizeHintForColumn reads it back when the columns are next divided.
        for col in range(tree.columnCount()):
            text_width = tree.fontMetrics().horizontalAdvance(item.text(col)) + 8
            item.setSizeHint(col, QSize(text_width, height))
        total += height
    # The view caches each row's height from its last layout, and a changed
    # size hint does not clear that cache: rows measured while column 0 was
    # narrower kept their old, taller height.
    tree.doItemsLayout()
    return total


def _fit_columns_to_widgets(table, *, stretch_col: int = 0, padding: int = 20) -> None:
    """Size each non-stretch column to fit the wider of its header text and its
    widest cell widget. Used for tables whose cells are all widgets (spin boxes,
    combos, buttons) with headers like "HTS number (optional)" that Qt's
    ResizeToContents would clip because it ignores the widgets. One column
    stretches to absorb the remaining width."""
    fm = table.horizontalHeader().fontMetrics()
    for col in range(table.columnCount()):
        if col == stretch_col:
            continue
        item = table.horizontalHeaderItem(col)
        width = (fm.horizontalAdvance(item.text()) if item else 0) + 28
        for row in range(table.rowCount()):
            widget = table.cellWidget(row, col)
            if widget is not None:
                width = max(width, widget.sizeHint().width() + padding)
        table.setColumnWidth(col, width)


def _message_carriers(shipment) -> list[str]:
    """The carrier codes named in a shipment's messages, in order, once each.

    Each of these reported a problem with this particular shipment, so its
    group, if it quoted anything at all, should not lead the table.
    """
    found: list[str] = []
    for entry in getattr(shipment, "messages", None) or []:
        if isinstance(entry, dict):
            carrier = entry.get("carrier")
        else:
            carrier = getattr(entry, "carrier", None)
        if carrier and str(carrier) not in found:
            found.append(str(carrier))
    return found


# The customs currency map, the declaration shape and the international test
# used to live here, which is precisely why batch shipments never got them: a
# second caller cannot import what is private to a view. They are in
# app/core/customs.py now, and both callers build declarations the same way.


class CreateShipmentView(QWidget):
    # "Track parcel" on a bought label: the window owns navigation, so the view
    # only asks for the Tracking page.
    tracking_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._pending_packages_task = None
        self._pending_preview_task = None
        self._current_shipment = None
        self._address_by_id = {}
        self._saved_packages = []
        self._predefined_packages = []
        # The rates the last rating returned, held so the carrier filter can
        # re-draw the tree without re-rating. The cheapest/fastest badges are
        # computed once over the WHOLE set and kept: they answer "cheapest of
        # everything quoted", and recomputing them per filtered view would let
        # filtering to one carrier crown that carrier's cheapest service as the
        # cheapest full stop.
        self._rates: list = []
        self._cheapest_id = None
        self._fastest_id = None
        # The carrier implied by a chosen predefined package, applied to the
        # filter once a rating comes back. Empty means the user has not implied
        # one, not that they chose "all carriers".
        self._preferred_carrier = ""
        # True when the current rates came from a postal-code-only quote, in
        # which case no rate on screen can actually be bought.
        self._quote_only = False
        # Set while the form rewrites its own inputs (a unit conversion) so that
        # does not read as the user editing the parcel. Must exist before any
        # widget is built, since building them can emit valueChanged.
        self._suspend_rate_invalidation = False
        # Bumped by every rating request and every edit that invalidates rates.
        # A reply is installed only if the generation it was requested under is
        # still current: otherwise it describes a parcel, address or declaration
        # the form no longer holds, and its Buy buttons would buy that instead.
        self._rates_generation = 0
        self._rating_in_flight = False
        # Buy stays disabled from the click until the purchase settles.
        # EasyPost refuses a second buy on the same shipment, so a double click
        # showed "purchase failed" beside a label that had in fact been bought.
        self._purchase_in_flight = False
        # The shipment on screen has been bought, so none of its other rates can
        # be bought either: EasyPost answers "Postage already exists".
        self._label_bought = False

        content = QWidget()
        content_layout = QVBoxLayout(content)
        # The page must fit its window, not the other way round. Anything that
        # sets a minimum width wider than the page makes the whole page scroll
        # sideways, and the rates table, which fills the page width, then has
        # its Buy column off screen. The Tamil title alone asked for 730px.
        title = QLabel(f"<h2>{tr('create_shipment.title')}</h2>")
        title.setWordWrap(True)
        content_layout.addWidget(title)
        content_layout.addWidget(self._build_form_group())
        content_layout.addWidget(self._build_customs_group())
        # Get Rates sits below the Customs section. On an international shipment
        # the customs form is visible, so this reads top-to-bottom: package →
        # customs → Get Rates. On a domestic shipment the customs group is
        # hidden and collapses to nothing, so the button simply follows the
        # package form as before.
        content_layout.addWidget(self._get_rates_btn)

        # The purchased label sits ABOVE the rates, full width, and stays hidden
        # until something is bought. It used to share a row with the rates, and
        # both mistakes followed from that: the rates got three fifths of the
        # page, which left no room for a service name at the default 1100px
        # window, and the panel grew to the height of an 85-row rates tree, so
        # the label was drawn 2,300px down the page where nobody was looking.
        content_layout.addWidget(self._build_result_group())
        content_layout.addWidget(self._build_rates_group())
        content_layout.addStretch(1)

        # Connected only once every input exists, and after the initial values
        # have been set, so start-up does not clear an empty rates table.
        self._connect_rate_invalidation()

        # The Rates table auto-sizes to show every service option in full
        # (see _on_rates_received) rather than scrolling internally, so this
        # outer scroll area is what handles overflow when a route returns
        # many rates plus the customs section — one natural scrollbar for
        # the whole page instead of a cramped nested one on the table.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        # Held so a purchase can bring its result into view (see _reveal_result).
        self._scroll = scroll
        self._content = content
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)

        self.refresh_address_choices()
        self._refresh_saved_packages()
        self._refresh_predefined_packages()

    def _build_form_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.details_group"))
        form = QFormLayout()
        # A label beside a field that cannot fit moves above it instead of
        # widening the page.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._from_combo = _narrowable_combo()
        self._to_combo = _narrowable_combo()
        # Shown while no recipient is chosen, which is how a new shipment starts
        # (see refresh_address_choices).
        self._to_combo.setPlaceholderText(tr("create_shipment.to_placeholder"))
        self._from_combo.currentIndexChanged.connect(self._update_customs_visibility)
        self._to_combo.currentIndexChanged.connect(self._update_customs_visibility)
        refresh_btn = QPushButton(tr("create_shipment.reload_button"))
        refresh_btn.clicked.connect(self.refresh_address_choices)

        # From and To on lines of their own. Side by side, with the reload
        # button, they needed 1,146px in Tamil.
        self._full_address_widget = QWidget()
        addr_grid = QGridLayout(self._full_address_widget)
        addr_grid.setContentsMargins(0, 0, 0, 0)
        addr_grid.addWidget(QLabel(tr("create_shipment.from_label")), 0, 0)
        addr_grid.addWidget(self._from_combo, 0, 1)
        addr_grid.addWidget(QLabel(tr("create_shipment.to_label")), 1, 0)
        addr_grid.addWidget(self._to_combo, 1, 1)
        addr_grid.addWidget(refresh_btn, 2, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        addr_grid.setColumnStretch(1, 1)

        mode_row = self._build_address_mode_row()
        self._zip_widget = self._build_zip_row()
        self._zip_widget.setVisible(False)

        # Measurement system, remembered across sessions. Everything is
        # normalised to inches/ounces for EasyPost via app/core/units.py.
        _saved = load_settings()
        self._unit_system = _saved.unit_system if _saved.unit_system in units.DIM_UNIT else "imperial"
        _wu = units.WEIGHT_UNITS[self._unit_system]
        self._weight_unit = _saved.weight_unit if _saved.weight_unit in _wu else _wu[0]

        # Minimum 0, not 1: a document/letter is under 1 inch (or cm) thick, and
        # forcing every dimension to >= 1 made the parcel read as a box — which
        # stopped carriers without a predefined letter package from quoting
        # letter/document services. The carrier still validates the final values.
        # Ranges, decimals and the unit shown are set per unit by _apply_units().
        self._length_input = self._spin(0, 1000, 6)
        self._width_input = self._spin(0, 1000, 6)
        self._height_input = self._spin(0, 1000, 6)
        self._weight_input = self._spin(0.1, 5000, 16)
        self._reference_input = QLineEdit()

        # Metric/Imperial toggle (dimensions: cm|in) and the weight-unit
        # selector (kg|g in metric, oz|lb in imperial).
        self._system_combo = QComboBox()
        self._system_combo.addItem(tr("create_shipment.units_metric"), "metric")
        self._system_combo.addItem(tr("create_shipment.units_imperial"), "imperial")
        self._system_combo.setCurrentIndex(self._system_combo.findData(self._unit_system))
        self._system_combo.currentIndexChanged.connect(self._on_system_changed)
        self._weight_unit_combo = QComboBox()
        self._weight_unit_combo.currentIndexChanged.connect(self._on_weight_unit_changed)

        self._package_combo = QComboBox()
        # Wide enough for its own entries. Left to size itself it settled around
        # the width of a short label and clipped "Custom dimensions" to
        # "Custom di" — which is how it reached a published store screenshot.
        # AdjustToContents keeps it honest as carrier packages are loaded, and
        # the minimum stops it collapsing before any are.
        self._package_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._package_combo.setMinimumContentsLength(24)
        self._package_combo.currentIndexChanged.connect(self._on_package_selected)
        self._save_package_btn = QPushButton(tr("create_shipment.save_package_button"))
        self._save_package_btn.clicked.connect(self._on_save_package_clicked)
        self._delete_package_btn = QPushButton(tr("create_shipment.delete_package_button"))
        self._delete_package_btn.clicked.connect(self._on_delete_package_clicked)
        self._delete_package_btn.setEnabled(False)

        # The buttons go under the combo rather than beside it, which was 642px
        # of minimum width in Tamil before the field's label was counted.
        package_row = QVBoxLayout()
        package_row.addWidget(self._package_combo)
        package_buttons = QHBoxLayout()
        package_buttons.addWidget(self._save_package_btn)
        package_buttons.addWidget(self._delete_package_btn)
        package_buttons.addStretch(1)
        package_row.addLayout(package_buttons)

        # Labels carry the active dimension unit (e.g. "L (cm)"); _apply_units
        # sets their text. The weight unit is shown by the combo beside it.
        self._length_label = QLabel()
        self._width_label = QLabel()
        self._height_label = QLabel()
        self._weight_label = QLabel(tr("create_shipment.weight_label"))

        units_row = QHBoxLayout()
        units_row.addWidget(QLabel(tr("create_shipment.units_label")))
        units_row.addWidget(self._system_combo)
        units_row.addStretch(1)

        # Two pairs to a line: all four in one row did not fit the minimum
        # window in the longer languages.
        dims_row = QGridLayout()
        dims_row.addWidget(self._length_label, 0, 0)
        dims_row.addWidget(self._length_input, 0, 1)
        dims_row.addWidget(self._width_label, 0, 2)
        dims_row.addWidget(self._width_input, 0, 3)
        dims_row.addWidget(self._height_label, 1, 0)
        dims_row.addWidget(self._height_input, 1, 1)
        dims_row.addWidget(self._weight_label, 1, 2)
        weight_box = QHBoxLayout()
        weight_box.addWidget(self._weight_input)
        weight_box.addWidget(self._weight_unit_combo)
        dims_row.addLayout(weight_box, 1, 3)
        dims_row.setColumnStretch(4, 1)

        form.addRow(mode_row)
        form.addRow(self._full_address_widget)
        form.addRow(self._zip_widget)
        form.addRow(tr("create_shipment.package_label"), package_row)
        form.addRow(units_row)
        form.addRow(dims_row)
        # Set unit labels, spin ranges/decimals, the weight-unit combo and the
        # starting values to match the loaded measurement system.
        self._apply_units(initial=True)
        self._reference_row_label = QLabel(tr("create_shipment.reference_field"))
        form.addRow(self._reference_row_label, self._reference_input)

        # Signature on delivery drives EasyPost's delivery_confirmation option,
        # which changes the services carriers quote (SIGNATURE → Royal Mail's
        # SignedFor set, ADULT_SIGNATURE → the age-verification set). It lives
        # here with the other parcel/options inputs, just above Get Rates, so
        # it's chosen before rates are fetched. userData is the raw option value
        # (None means "don't send the option at all").
        self._signature_combo = QComboBox()
        self._signature_combo.addItem(tr("create_shipment.signature_none"), None)
        self._signature_combo.addItem(tr("create_shipment.signature_signature"), "SIGNATURE")
        self._signature_combo.addItem(tr("create_shipment.signature_adult"), "ADULT_SIGNATURE")
        self._signature_combo.currentIndexChanged.connect(self._on_signature_changed)
        form.addRow(tr("create_shipment.signature_label"), self._signature_combo)

        # Optional declared value to insure the parcel for. Unlike signature it
        # does not change the quoted rates, so it is read at Buy time and passed
        # to EasyPost's purchase call (see _on_buy_clicked). 0 means no cover.
        #
        # Capped at EasyPost's real ceiling and prefixed in dollars, because the
        # amount is always USD however the shipment is priced. The old
        # 1,000,000 maximum let a user enter a figure the API would refuse, and
        # the refusal arrived only after they had confirmed spending money.
        self._insurance_input = QDoubleSpinBox()
        self._insurance_input.setDecimals(2)
        self._insurance_input.setMaximum(INSURANCE_MAX_USD)
        self._insurance_input.setPrefix("$ ")
        self._insurance_input.setSpecialValueText(tr("create_shipment.insurance_none"))
        self._insurance_input.setToolTip(tr("create_shipment.insurance_tooltip"))
        form.addRow(tr("create_shipment.insurance_label"), self._insurance_input)

        self._get_rates_btn = QPushButton(tr("create_shipment.get_rates_button"))
        self._get_rates_btn.clicked.connect(self._on_get_rates_clicked)

        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        # The Get Rates button is intentionally NOT added here. It is placed at
        # page level, after the Customs section (see the content assembly in
        # __init__), so an international shipment prompts for customs details
        # above the button rather than below it. The button is created here so
        # the reference to it exists before that assembly runs.
        group.setLayout(group_layout)
        return group

    def _build_address_mode_row(self) -> QVBoxLayout:
        """Full addresses (can buy a label) vs postal codes only (price check).

        A quick "what would this cost?" doesn't need a saved, verified address
        at either end, which is otherwise a lot of typing before you see a
        single number.
        """
        self._mode_full_radio = QRadioButton(tr("create_shipment.address_mode_full"))
        self._mode_zip_radio = QRadioButton(tr("create_shipment.address_mode_zip"))
        self._mode_full_radio.setChecked(True)
        self._mode_full_radio.toggled.connect(self._on_address_mode_changed)

        # One above the other: side by side they were 776px in Tamil.
        row = QVBoxLayout()
        row.addWidget(self._mode_full_radio)
        row.addWidget(self._mode_zip_radio)
        return row

    def _build_zip_row(self) -> QWidget:
        self._from_zip_input = QLineEdit()
        self._from_zip_input.setPlaceholderText(tr("create_shipment.zip_placeholder"))
        self._to_zip_input = QLineEdit()
        self._to_zip_input.setPlaceholderText(tr("create_shipment.zip_placeholder"))
        self._from_country_combo = self._country_combo()
        self._to_country_combo = self._country_combo()

        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(QLabel(tr("create_shipment.from_label")), 0, 0)
        grid.addWidget(self._from_zip_input, 0, 1)
        grid.addWidget(self._from_country_combo, 0, 2)
        grid.addWidget(QLabel(tr("create_shipment.to_label")), 1, 0)
        grid.addWidget(self._to_zip_input, 1, 1)
        grid.addWidget(self._to_country_combo, 1, 2)
        grid.setColumnStretch(1, 1)
        return widget

    @staticmethod
    def _country_combo() -> QComboBox:
        combo = QComboBox()
        for code, name in COUNTRIES:
            combo.addItem(f"{code} — {name}", code)
        index = combo.findData("US")
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _on_address_mode_changed(self) -> None:
        full = self._mode_full_radio.isChecked()
        self._full_address_widget.setVisible(full)
        self._zip_widget.setVisible(not full)
        # A reference and a customs declaration only mean something on a real
        # shipment; neither applies to a throwaway price check.
        self._reference_input.setVisible(full)
        self._reference_row_label.setVisible(full)
        self._update_customs_visibility()

    @staticmethod
    def _spin(minimum: float, maximum: float, default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(default)
        return spin

    # --- Measurement units -------------------------------------------------
    # EasyPost is always given inches/ounces; the widgets hold whatever the user
    # picked and these helpers convert. See app/core/units.py.

    def _dim_unit(self) -> str:
        return units.DIM_UNIT[self._unit_system]

    def _apply_units(self, initial: bool = False) -> None:
        """Point the labels, spin ranges/decimals and the weight-unit combo at
        the active system. With initial=True, also seed default values."""
        dim_unit = self._dim_unit()
        self._length_label.setText(tr("create_shipment.length_label", unit=dim_unit))
        self._width_label.setText(tr("create_shipment.width_label", unit=dim_unit))
        self._height_label.setText(tr("create_shipment.height_label", unit=dim_unit))
        lo, hi, dec, step = units.DIM_SPIN[dim_unit]
        for spin in (self._length_input, self._width_input, self._height_input):
            spin.setDecimals(dec)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            if initial:
                spin.setValue(units.DIM_DEFAULT[dim_unit])
        self._rebuild_weight_unit_combo()
        wlo, whi, wdec, wstep = units.WEIGHT_SPIN[self._weight_unit]
        self._weight_input.setDecimals(wdec)
        self._weight_input.setRange(wlo, whi)
        self._weight_input.setSingleStep(wstep)
        if initial:
            self._weight_input.setValue(units.WEIGHT_DEFAULT[self._weight_unit])

    def _rebuild_weight_unit_combo(self) -> None:
        combo = self._weight_unit_combo
        combo.blockSignals(True)
        combo.clear()
        for unit_code in units.WEIGHT_UNITS[self._unit_system]:
            combo.addItem(unit_code, unit_code)
        idx = combo.findData(self._weight_unit)
        if idx < 0:
            idx = 0
            self._weight_unit = combo.itemData(0)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _on_system_changed(self, *_args) -> None:
        new_system = self._system_combo.currentData()
        if not new_system or new_system == self._unit_system:
            return
        old_dim = units.DIM_UNIT[self._unit_system]
        # Re-displaying the same parcel in different units does not change the
        # parcel, so quoted rates stay valid across the switch even though every
        # spin box is about to be rewritten.
        self._suspend_rate_invalidation = True
        old_weight_unit = self._weight_unit
        # Preserve the physical parcel across the switch: read canonical in/oz,
        # then re-display in the new units.
        dim_canon = [
            units.to_inches(s.value(), old_dim)
            for s in (self._length_input, self._width_input, self._height_input)
        ]
        weight_canon = units.to_ounces(self._weight_input.value(), old_weight_unit)
        self._unit_system = new_system
        self._weight_unit = units.WEIGHT_UNITS[new_system][0]
        self._apply_units(initial=False)
        new_dim = self._dim_unit()
        for spin, canon in zip(
            (self._length_input, self._width_input, self._height_input), dim_canon
        ):
            spin.setValue(units.from_inches(canon, new_dim))
        self._weight_input.setValue(units.from_ounces(weight_canon, self._weight_unit))
        self._convert_customs_item_weights(old_weight_unit)
        self._suspend_rate_invalidation = False
        self._persist_units()

    def _on_weight_unit_changed(self, *_args) -> None:
        new_unit = self._weight_unit_combo.currentData()
        if not new_unit or new_unit == self._weight_unit:
            return
        canon = units.to_ounces(self._weight_input.value(), self._weight_unit)
        old_unit = self._weight_unit
        self._weight_unit = new_unit
        wlo, whi, wdec, wstep = units.WEIGHT_SPIN[new_unit]
        # Same weight, different unit — not a different parcel.
        self._suspend_rate_invalidation = True
        self._weight_input.setDecimals(wdec)
        self._weight_input.setRange(wlo, whi)
        self._weight_input.setSingleStep(wstep)
        self._weight_input.setValue(units.from_ounces(canon, new_unit))
        self._convert_customs_item_weights(old_unit)
        self._suspend_rate_invalidation = False
        self._persist_units()

    def _persist_units(self) -> None:
        settings = load_settings()
        settings.unit_system = self._unit_system
        settings.weight_unit = self._weight_unit
        save_settings(settings)

    def _length_in(self) -> float:
        return round(units.to_inches(self._length_input.value(), self._dim_unit()), 3)

    def _width_in(self) -> float:
        return round(units.to_inches(self._width_input.value(), self._dim_unit()), 3)

    def _height_in(self) -> float:
        return round(units.to_inches(self._height_input.value(), self._dim_unit()), 3)

    def _weight_oz(self) -> float:
        return round(units.to_ounces(self._weight_input.value(), self._weight_unit), 3)

    def _refresh_saved_packages(self) -> None:
        self._saved_packages = list_saved_packages()
        self._populate_package_combo()

    def _refresh_predefined_packages(self) -> None:
        self._pending_packages_task = run_async(list_predefined_packages, self)
        self._pending_packages_task.succeeded.connect(self._on_predefined_packages_loaded)
        # A failed live fetch isn't worth interrupting the user over here —
        # packages.list_predefined_packages() already falls back to
        # whatever's cached, so "failed" only means both live and cache
        # came up empty; the combo just won't offer carrier packages yet.

    def _on_predefined_packages_loaded(self, packages) -> None:
        self._predefined_packages = packages
        self._populate_package_combo()

    def _populate_package_combo(self) -> None:
        combo = self._package_combo
        previous_data = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr("create_shipment.package_custom_option"), None)

        if self._saved_packages:
            combo.insertSeparator(combo.count())
            for pkg in self._saved_packages:
                combo.addItem(
                    tr(
                        "create_shipment.package_saved_option",
                        name=pkg.name,
                        length=pkg.length,
                        width=pkg.width,
                        height=pkg.height,
                        weight=pkg.weight,
                    ),
                    ("saved", pkg),
                )

        by_carrier: dict[str, list] = {}
        for pkg in self._predefined_packages:
            by_carrier.setdefault(pkg.carrier, []).append(pkg)
        for carrier in sorted(by_carrier):
            combo.insertSeparator(combo.count())
            combo.addItem(f"— {carrier.upper()} —")
            combo.model().item(combo.count() - 1).setEnabled(False)
            for pkg in sorted(by_carrier[carrier], key=lambda p: p.name):
                label = pkg.name if not pkg.dimensions else f"{pkg.name} ({pkg.dimensions})"
                combo.addItem(f"    {label}", ("predefined", pkg))

        # Re-select whatever was active before the repopulate (e.g. after
        # deleting one saved package, or after the live fetch finishes)
        # rather than silently resetting the user back to "Custom".
        restored = False
        if isinstance(previous_data, tuple) and previous_data[0] == "saved":
            for i in range(combo.count()):
                data = combo.itemData(i)
                if isinstance(data, tuple) and data[0] == "saved" and data[1].id == previous_data[1].id:
                    combo.setCurrentIndex(i)
                    restored = True
                    break
        if not restored:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self._on_package_selected()

    def _on_package_selected(self, *_args) -> None:
        data = self._package_combo.currentData()
        is_predefined = isinstance(data, tuple) and data[0] == "predefined"
        is_saved = isinstance(data, tuple) and data[0] == "saved"

        # A carrier's own predefined package names that carrier, so the rates
        # filter follows it — a user who picks a Royal Mail box is asking about
        # Royal Mail. Recorded rather than applied: the filter is populated from
        # rates, which do not exist yet, and choosing a package clears the table
        # anyway (see _invalidate_rates).
        self._preferred_carrier = data[1].carrier if is_predefined else ""

        self._length_input.setEnabled(not is_predefined)
        self._width_input.setEnabled(not is_predefined)
        self._height_input.setEnabled(not is_predefined)
        self._delete_package_btn.setEnabled(is_saved)

        if is_saved:
            pkg = data[1]
            dim_unit = self._dim_unit()
            # Saved packages are stored canonically (inches/ounces); display them
            # in the active units. `is not None`, not `or`: 0 is a legitimate
            # (thin/letter) dimension now that the minimum is 0.
            self._length_input.setValue(units.from_inches(pkg.length if pkg.length is not None else 1, dim_unit))
            self._width_input.setValue(units.from_inches(pkg.width if pkg.width is not None else 1, dim_unit))
            self._height_input.setValue(units.from_inches(pkg.height if pkg.height is not None else 1, dim_unit))
            self._weight_input.setValue(units.from_ounces(pkg.weight, self._weight_unit))

    def _on_save_package_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, tr("create_shipment.save_package_dialog_title"), tr("create_shipment.save_package_dialog_label"))
        name = name.strip()
        if not ok or not name:
            return
        save_package(
            name,
            self._length_in(),
            self._width_in(),
            self._height_in(),
            self._weight_oz(),
        )
        self._refresh_saved_packages()
        # Select the package just saved rather than leaving the combo on
        # whatever it happened to show before (usually "Custom").
        for i in range(self._package_combo.count()):
            data = self._package_combo.itemData(i)
            if isinstance(data, tuple) and data[0] == "saved" and data[1].name == name:
                self._package_combo.setCurrentIndex(i)
                break

    def _on_delete_package_clicked(self) -> None:
        data = self._package_combo.currentData()
        if not isinstance(data, tuple) or data[0] != "saved":
            return
        pkg = data[1]
        if (
            QMessageBox.question(
                self,
                tr("create_shipment.delete_package_confirm_title"),
                tr("create_shipment.delete_package_confirm_body", name=pkg.name),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        delete_saved_package(pkg.id)
        self._refresh_saved_packages()

    def _build_customs_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.customs_group_title"))
        self._customs_group = group

        intro = QLabel(tr("create_shipment.customs_intro"))
        intro.setWordWrap(True)

        self._contents_type_combo = QComboBox()
        for value, key in (
            ("merchandise", "create_shipment.contents_type_merchandise"),
            ("documents", "create_shipment.contents_type_documents"),
            ("gift", "create_shipment.contents_type_gift"),
            ("sample", "create_shipment.contents_type_sample"),
            ("returned_goods", "create_shipment.contents_type_returned_goods"),
            ("other", "create_shipment.contents_type_other"),
        ):
            self._contents_type_combo.addItem(tr(key), value)
        self._contents_type_combo.currentIndexChanged.connect(self._update_contents_explanation_enabled)

        self._contents_explanation_input = QLineEdit()
        self._update_contents_explanation_enabled()

        self._restriction_type_combo = QComboBox()
        for value, key in (
            ("none", "create_shipment.restriction_none"),
            ("other", "create_shipment.restriction_other"),
            ("quarantine", "create_shipment.restriction_quarantine"),
            ("sanitary_phytosanitary_inspection", "create_shipment.restriction_sanitary"),
        ):
            self._restriction_type_combo.addItem(tr(key), value)
        self._restriction_type_combo.currentIndexChanged.connect(self._update_restriction_comments_enabled)

        self._restriction_comments_input = QLineEdit()
        self._update_restriction_comments_enabled()

        self._non_delivery_combo = QComboBox()
        for value, key in (
            ("return", "create_shipment.non_delivery_return"),
            ("abandon", "create_shipment.non_delivery_abandon"),
        ):
            self._non_delivery_combo.addItem(tr(key), value)

        self._customs_signer_input = QLineEdit()
        self._customs_certify_checkbox = QCheckBox(tr("create_shipment.customs_certify_checkbox"))

        form = QFormLayout()
        form.addRow(tr("create_shipment.contents_type_label"), self._contents_type_combo)
        form.addRow(tr("create_shipment.contents_explanation_label"), self._contents_explanation_input)
        form.addRow(tr("create_shipment.restriction_type_label"), self._restriction_type_combo)
        form.addRow(tr("create_shipment.restriction_comments_label"), self._restriction_comments_input)
        form.addRow(tr("create_shipment.non_delivery_label"), self._non_delivery_combo)
        form.addRow(tr("create_shipment.customs_signer_label"), self._customs_signer_input)
        form.addRow(self._customs_certify_checkbox)

        items_group = QGroupBox(tr("create_shipment.customs_items_group_title"))
        self._customs_items_table = QTableWidget(0, _CUSTOMS_ITEM_COLUMN_COUNT)
        self._customs_items_table.setHorizontalHeaderLabels(
            [
                tr("create_shipment.customs_item_col_description"),
                tr("create_shipment.customs_item_col_quantity"),
                "",  # value and weight: set by _update_customs_item_headers
                "",
                tr("create_shipment.customs_item_col_hts"),
                tr("create_shipment.customs_item_col_origin"),
                "",
            ]
        )
        self._customs_items_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._update_customs_item_headers()

        add_item_btn = QPushButton(tr("create_shipment.add_customs_item_button"))
        add_item_btn.clicked.connect(self._on_add_customs_item)
        hts_hint = QLabel(tr("create_shipment.customs_hts_hint"))

        items_layout = QVBoxLayout()
        items_layout.addWidget(self._customs_items_table)
        items_row = QHBoxLayout()
        items_row.addWidget(add_item_btn)
        items_row.addWidget(hts_hint, stretch=1)
        items_layout.addLayout(items_row)
        items_group.setLayout(items_layout)

        layout = QVBoxLayout()
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(items_group)
        group.setLayout(layout)

        self._on_add_customs_item()
        group.setVisible(False)
        return group

    def _update_contents_explanation_enabled(self) -> None:
        self._contents_explanation_input.setEnabled(
            self._contents_type_combo.currentData() == "other"
        )

    def _update_restriction_comments_enabled(self) -> None:
        self._restriction_comments_input.setEnabled(
            self._restriction_type_combo.currentData() != "none"
        )

    def _on_add_customs_item(self) -> None:
        row = self._customs_items_table.rowCount()
        self._customs_items_table.insertRow(row)
        self._customs_items_table.setCellWidget(row, 0, QLineEdit())

        qty_spin = QSpinBox()
        qty_spin.setRange(1, 10000)
        qty_spin.setValue(1)
        self._customs_items_table.setCellWidget(row, 1, qty_spin)

        value_spin = self._spin(0.01, 100000, 10)
        self._customs_items_table.setCellWidget(row, 2, value_spin)

        # Entered in the parcel's weight unit, which the column header names.
        weight_spin = QDoubleSpinBox()
        self._configure_customs_weight_spin(weight_spin)
        weight_spin.setValue(units.from_ounces(8, self._weight_unit))
        self._customs_items_table.setCellWidget(row, 3, weight_spin)

        self._customs_items_table.setCellWidget(row, 4, QLineEdit())

        origin_combo = QComboBox()
        for code, name in COUNTRIES:
            origin_combo.addItem(f"{name} ({code})", code)
        from_rec = self._address_by_id.get(self._from_combo.currentData())
        if from_rec and from_rec.country:
            idx = origin_combo.findData(from_rec.country.upper())
            if idx >= 0:
                origin_combo.setCurrentIndex(idx)
        self._customs_items_table.setCellWidget(row, 5, origin_combo)

        remove_btn = QPushButton(tr("create_shipment.remove_customs_item_button"))
        remove_btn.clicked.connect(partial(self._on_remove_customs_item, remove_btn))
        self._customs_items_table.setCellWidget(row, _CUSTOMS_ITEM_COLUMN_COUNT - 1, remove_btn)

        # The declaration is attached when the shipment is created, so a rate
        # quoted before an item changed would buy a label carrying the old one.
        # Wired per row because rows are added after the form is connected.
        self._customs_items_table.cellWidget(row, 0).textChanged.connect(self._invalidate_rates)
        qty_spin.valueChanged.connect(self._invalidate_rates)
        value_spin.valueChanged.connect(self._invalidate_rates)
        weight_spin.valueChanged.connect(self._invalidate_rates)
        self._customs_items_table.cellWidget(row, 4).textChanged.connect(self._invalidate_rates)
        origin_combo.currentIndexChanged.connect(self._invalidate_rates)
        self._invalidate_rates()

        # Every cell in this table is a widget, which ResizeToContents can't
        # measure — without this the header for a widget-only column (e.g. "HTS
        # number (optional)") clips. Fit each column to header + widget.
        _fit_columns_to_widgets(self._customs_items_table, stretch_col=0)

    def _on_remove_customs_item(self, button: QPushButton) -> None:
        for row in range(self._customs_items_table.rowCount()):
            if self._customs_items_table.cellWidget(row, _CUSTOMS_ITEM_COLUMN_COUNT - 1) is button:
                self._customs_items_table.removeRow(row)
                self._invalidate_rates()
                return

    def _is_international(self) -> bool:
        from_rec = self._address_by_id.get(self._from_combo.currentData())
        to_rec = self._address_by_id.get(self._to_combo.currentData())
        return customs.is_international(
            getattr(from_rec, "country", None), getattr(to_rec, "country", None)
        )

    def _update_customs_visibility(self) -> None:
        # Postal-code quotes never carry a customs declaration — nothing can
        # be bought from them, so there is nothing to declare.
        zip_mode = getattr(self, "_mode_zip_radio", None) is not None and self._mode_zip_radio.isChecked()
        self._customs_group.setVisible(not zip_mode and self._is_international())
        self._resync_customs_item_origins()
        # The declared currency follows the sender, so its header does too.
        self._update_customs_item_headers()

    def _customs_currency(self) -> str:
        """The currency a declared value is stated in. Read by both the column
        header and the declaration, so the two cannot disagree."""
        from_rec = self._address_by_id.get(self._from_combo.currentData())
        return customs.currency_for(getattr(from_rec, "country", None))

    def _update_customs_item_headers(self) -> None:
        """Name the currency and unit the declaration will actually carry.

        The headers were fixed at "Value (USD)" and "Weight (oz)" while the
        declaration used the sender's currency and the form's weight unit, so a
        metric UK user's "8" under "Weight (oz)" was declared as 8 kg, in pounds
        sterling. The currency is the sender's by design (a London sender typing
        10 means ten pounds) and the weight unit is the one the parcel is being
        entered in, so the headers follow them rather than the other way round.
        Both are line totals: EasyPost defines value and weight as the unit
        amount times the quantity.
        """
        table = getattr(self, "_customs_items_table", None)
        if table is None:
            return
        table.horizontalHeaderItem(2).setText(
            tr("create_shipment.customs_item_col_value", currency=self._customs_currency())
        )
        table.horizontalHeaderItem(3).setText(
            tr("create_shipment.customs_item_col_weight", unit=self._weight_unit)
        )
        _fit_columns_to_widgets(table, stretch_col=0)

    def _configure_customs_weight_spin(self, spin: QDoubleSpinBox) -> None:
        lo, hi, dec, step = units.WEIGHT_SPIN[self._weight_unit]
        spin.setDecimals(dec)
        spin.setRange(lo, hi)
        spin.setSingleStep(step)

    def _convert_customs_item_weights(self, old_unit: str) -> None:
        """Re-express every item weight in the new unit, as the parcel weight
        is. Relabelling the column without converting would silently turn a
        declared 8 oz into 8 g. Callers suspend rate invalidation, because the
        items weigh the same."""
        table = getattr(self, "_customs_items_table", None)
        if table is None:
            return
        for row in range(table.rowCount()):
            spin = table.cellWidget(row, 3)
            if spin is None:
                continue
            ounces = units.to_ounces(spin.value(), old_unit)
            self._configure_customs_weight_spin(spin)
            spin.setValue(units.from_ounces(ounces, self._weight_unit))
        self._update_customs_item_headers()

    def _resync_customs_item_origins(self) -> None:
        """Keeps each customs item row's origin-country default in step with
        the selected "from" address. Needed because the first item row is
        seeded when the view is built, before any address is selected, so
        it would otherwise default to whichever country sorts first.
        """
        from_rec = self._address_by_id.get(self._from_combo.currentData())
        if not from_rec or not from_rec.country:
            return
        for row in range(self._customs_items_table.rowCount()):
            origin_combo = self._customs_items_table.cellWidget(row, 5)
            if origin_combo is None:
                continue
            idx = origin_combo.findData(from_rec.country.upper())
            if idx >= 0:
                origin_combo.setCurrentIndex(idx)

    def _collect_customs_info(self) -> dict:
        """Builds the customs_info payload from the form. Raises ValueError
        if a required field is missing — the caller shows a single generic
        validation message rather than pinpointing the exact field, since
        the form has no per-field inline error display.
        """
        signer = self._customs_signer_input.text().strip()
        # The tick box is this view's own consent gate, so it is checked here;
        # everything else the declaration needs is validated by the shared
        # builder, which raises the same identifiers.
        if not self._customs_certify_checkbox.isChecked():
            raise ValueError("missing_signer_or_certify")

        # The declared value's currency, taken from the origin country rather
        # than hard-coded to USD. A London sender entering "10" means ten
        # pounds; declaring that as ten dollars misstates the value on a customs
        # form.
        customs_currency = self._customs_currency()

        items = []
        for row in range(self._customs_items_table.rowCount()):
            description = self._customs_items_table.cellWidget(row, 0).text().strip()
            origin_combo = self._customs_items_table.cellWidget(row, 5)
            origin_country = origin_combo.currentData()
            if not description or not origin_country:
                raise ValueError("incomplete_customs_item")
            items.append(customs.customs_item(
                description=description,
                quantity=self._customs_items_table.cellWidget(row, 1).value(),
                value=self._customs_items_table.cellWidget(row, 2).value(),
                weight_oz=units.to_ounces(
                    self._customs_items_table.cellWidget(row, 3).value(),
                    self._weight_unit,
                ),
                origin_country=origin_country,
                currency=customs_currency,
                hs_tariff_number=self._customs_items_table.cellWidget(row, 4).text().strip(),
            ))

        return customs.build_customs_info(
            items,
            customs_signer=signer,
            contents_type=self._contents_type_combo.currentData(),
            restriction_type=self._restriction_type_combo.currentData(),
            non_delivery_option=self._non_delivery_combo.currentData(),
            contents_explanation=self._contents_explanation_input.text().strip(),
            restriction_comments=self._restriction_comments_input.text().strip(),
        )

    def _build_rates_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.rates_group"))
        rate_columns = [
            tr("create_shipment.col_carrier_service"),
            tr("create_shipment.col_rate"),
            tr("create_shipment.col_est_days"),
            "",
        ]
        # Rates are grouped by carrier under collapsible header rows rather than
        # shown flat: Royal Mail v3 alone returns 70+ services, which used to
        # drown every other carrier in one long list. Each carrier is a
        # top-level row (name + service count) with its services as children.
        # Narrowing the tree to one carrier. It defaults to "All carriers" and
        # is populated from the rates that actually came back, never from the
        # catalogue: a filter offering a carrier that quoted nothing would empty
        # the table and look like a failed rating.
        #
        # Deliberately a filter the user can see and clear rather than a silent
        # narrowing. A rate this app hides is a service the user cannot buy
        # through it at all, which is exactly how Royal Mail's whole
        # account-billed catalogue once disappeared (see _is_placeholder_rate),
        # so the count of what is hidden is stated in _carrier_filter_note
        # rather than left for the user to notice.
        self._carrier_filter = CarrierCombo(include_all=True)
        self._carrier_filter.carrier_changed.connect(lambda _: self._render_rates())
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.addWidget(QLabel(tr("create_shipment.carrier_filter_label")))
        filter_row.addWidget(self._carrier_filter)
        filter_row.addStretch(1)
        self._carrier_filter_row = QWidget()
        self._carrier_filter_row.setLayout(filter_row)
        self._carrier_filter_row.setVisible(False)

        self._carrier_filter_note = QLabel("")
        self._carrier_filter_note.setWordWrap(True)
        self._carrier_filter_note.setStyleSheet(f"color: {TEXT_MUTED};")
        self._carrier_filter_note.setVisible(False)

        self._rates_tree = QTreeWidget()
        self._rates_tree.setColumnCount(_RATE_COLUMN_COUNT)
        self._rates_tree.setHeaderLabels(rate_columns)
        # Headers elide rather than take width from the service name, so the
        # whole word stays one hover away.
        for col, text in enumerate(rate_columns):
            if text:
                self._rates_tree.headerItem().setToolTip(col, text)
        # Row heights set explicitly per item (see _fit_rate_rows) only take
        # effect when rows are not all assumed to match the first.
        self._rates_tree.setUniformRowHeights(False)
        # Lets a price wrap between words when the service name needs the room.
        self._rates_tree.setWordWrap(True)
        # The columns are divided up from the tree's width, and that width
        # changes with the window. Sizing once, when rates arrived, left a tree
        # laid out for 1100px inside an 880px window.
        self._rates_tree_width = -1
        self._rates_tree.installEventFilter(self)
        header = self._rates_tree.header()
        # The text columns are divided explicitly in _fit_rate_columns, which
        # runs on every rebuild and resize. Stretch on column 0 plus
        # ResizeToContents on the rest looks right and is not: ResizeToContents
        # measures the HEADER as well as the data, so a language with long
        # header words took the width out of the service name — the one column
        # that must never be cut. The Buy column holds a widget Qt won't measure
        # at all, so it stays Fixed and is sized to the widest button in
        # _resize_rates_tree_to_content.
        for col in (_RATE_COL_SERVICE, _RATE_COL_PRICE, _RATE_COL_DAYS):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(_RATE_COL_BUY, QHeaderView.ResizeMode.Fixed)
        # Don't let Qt stretch the last (Buy) section — it's sized to the button,
        # and a stretched final column would swallow col 0's slack.
        header.setStretchLastSection(False)
        self._rates_tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        # A long service name clips at the (stretched) column edge rather than
        # forcing the tree wider; the full name is humanised and shown, and the
        # service cell carries a tooltip with the untruncated text.
        self._rates_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Sized to fit every visible row (see _resize_rates_tree_to_content)
        # instead of scrolling internally — the outer QScrollArea handles
        # overflow. Expanding or collapsing a carrier changes the total height,
        # so re-measure on those signals.
        self._rates_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rates_tree.itemExpanded.connect(self._resize_rates_tree_to_content)
        self._rates_tree.itemCollapsed.connect(self._resize_rates_tree_to_content)

        self._quote_only_note = QLabel(tr("create_shipment.zip_mode_note"))
        self._quote_only_note.setWordWrap(True)
        self._quote_only_note.setStyleSheet(f"color: {TEXT_MUTED};")
        self._quote_only_note.setVisible(False)

        # Which currency Cheapest and Fastest were judged in, stated only when
        # the rates span more than one: otherwise a cheaper-looking figure in
        # another currency without the badge reads as a mistake.
        self._currency_note = QLabel("")
        self._currency_note.setWordWrap(True)
        self._currency_note.setStyleSheet(f"color: {TEXT_MUTED};")
        self._currency_note.setVisible(False)

        # Why a carrier is absent from the table above. EasyPost reports this on
        # the shipment's `messages`, which nothing previously read, so a carrier
        # that declined to quote simply vanished without explanation. The raw
        # text is carrier configuration jargon ("credentials.client_id:
        # Required") that reads as the app being broken, so a plain summary
        # naming the carriers comes first and the raw lines wait behind a toggle.
        self._carrier_notes_summary = QLabel("")
        self._carrier_notes_summary.setWordWrap(True)
        self._carrier_notes_summary.setStyleSheet(f"color: {TEXT_MUTED};")
        self._carrier_notes_toggle = QPushButton(tr("create_shipment.carrier_notes_show"))
        self._carrier_notes_toggle.setCheckable(True)
        self._carrier_notes_toggle.toggled.connect(self._on_carrier_notes_toggled)
        notes_row = QHBoxLayout()
        notes_row.setContentsMargins(0, 0, 0, 0)
        notes_row.addWidget(self._carrier_notes_summary, stretch=1)
        notes_row.addWidget(self._carrier_notes_toggle, alignment=Qt.AlignmentFlag.AlignTop)
        self._carrier_notes_row = QWidget()
        self._carrier_notes_row.setLayout(notes_row)
        self._carrier_notes_row.setVisible(False)
        self._carrier_notes_label = QLabel("")
        self._carrier_notes_label.setWordWrap(True)
        self._carrier_notes_label.setStyleSheet(f"color: {TEXT_MUTED};")
        self._carrier_notes_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._carrier_notes_label.setVisible(False)

        layout = QVBoxLayout()
        layout.addWidget(self._carrier_filter_row)
        layout.addWidget(self._rates_tree)
        layout.addWidget(self._carrier_filter_note)
        layout.addWidget(self._currency_note)
        layout.addWidget(self._quote_only_note)
        layout.addWidget(self._carrier_notes_row)
        layout.addWidget(self._carrier_notes_label)
        # Packs everything to the top, so any slack never shows as gaps between
        # the filter, the tree and its notes.
        layout.addStretch(1)
        group.setLayout(layout)
        return group


    # These two used to be implemented here, and that was the bug. Every other
    # view read the raw API field instead, so Create Shipment showed "FedEx
    # Ground" while Pickups showed FEDEX_GROUND, History showed RoyalMailV3 and
    # Tracking showed in_transit — all of them on published store screenshots.
    # The implementation now lives in app/services/formatting.py and these are
    # thin aliases so existing callers here keep working.
    @staticmethod
    def _humanize_service(name: str) -> str:
        return humanize_code(name)

    @staticmethod
    def _carrier_display_name(carrier: str) -> str:
        return display_carrier(carrier, blank="—")

    def _build_rate_service_cell(self, rate, *, cheapest: bool, fastest: bool) -> QWidget:
        """The service cell of a child row: the humanised service name, and on a
        line beneath it any cheapest / fastest marker followed by what the
        service includes (tracked / signed / guaranteed).

        The markers used to sit beside the name and the "included" badges in a
        column of their own, and each competed with the name for the same width;
        at the default window both lost ("Royal", "Che", "Guarante"). On their
        own line a badge never truncates and never costs the name a character.
        The name word-wraps as a last resort, when the window is too narrow for
        it on one line, and the row is then made tall enough (_fit_rate_rows).
        """
        cell = _ServiceCell()
        column = QVBoxLayout(cell)
        column.setContentsMargins(6, 4, 6, 4)
        column.setSpacing(3)
        # Stretches either side keep the name and badges together, centred like
        # the price and the Buy button, when the row is taller than they are.
        column.addStretch(1)
        service = self._humanize_service(getattr(rate, "service", "") or "") or "—"
        service_label = QLabel(service)
        service_label.setObjectName(_SERVICE_NAME)
        service_label.setWordWrap(True)
        service_label.setToolTip(service)
        column.addWidget(service_label)

        chips = []
        if cheapest:
            chips.append(badge(tr("create_shipment.badge_cheapest")))
        if fastest:
            chips.append(badge(tr("create_shipment.badge_fastest"), tone="muted"))
        labels = {
            "tracked": tr("create_shipment.badge_tracked"),
            "signed": tr("create_shipment.badge_signed"),
            "guaranteed": tr("create_shipment.badge_guaranteed"),
        }
        chips += [badge(labels[key], tone="muted") for key in _service_enhancements(rate)]
        if chips:
            badges = QWidget()
            badges.setObjectName(_SERVICE_BADGES)
            row = QHBoxLayout(badges)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            for chip in chips:
                row.addWidget(chip)
            row.addStretch(1)
            column.addWidget(badges)
        column.addStretch(1)
        return cell

    def _populate_rates_tree(self, rates, cheapest_id, fastest_id) -> None:
        """Build the carrier-grouped tree: one top-level row per carrier with
        its services as children, in the order rate_rules.order_carriers gives
        (carriers that can quote this route in its currency first). Within a
        carrier the real-priced services come first, cheapest first, then the
        account-billed ones by name."""
        tree = self._rates_tree
        tree.clear()

        by_carrier: dict[str, list] = {}
        for rate in rates:
            by_carrier.setdefault(getattr(rate, "carrier", "") or "", []).append(rate)

        order = rate_rules.order_carriers(
            rates,
            currency=getattr(self, "_rate_currency", None),
            declined=getattr(self, "_declined_carriers", ()),
        )
        for carrier in order:
            carrier_rates = by_carrier[carrier]
            real = sorted(
                (r for r in carrier_rates if not rate_rules.is_account_billed(r)),
                key=rate_rules.sort_key,
            )
            billed = sorted(
                (r for r in carrier_rates if rate_rules.is_account_billed(r)),
                key=lambda r: (getattr(r, "service", "") or "").lower(),
            )
            group_rates = real + billed

            display = self._carrier_display_name(carrier)
            # A carrier header carries the name and a count but is not itself a
            # rate — no enhancements, no price, no est. days, no Buy button.
            parent = QTreeWidgetItem([f"{display} ({len(group_rates)})", "", "", ""])
            tree.addTopLevelItem(parent)

            has_cheapest = False
            for rate in group_rates:
                is_cheapest = rate.id == cheapest_id
                has_cheapest = has_cheapest or is_cheapest
                self._add_rate_child(parent, rate, cheapest=is_cheapest, fastest=rate.id == fastest_id)

            # Expand a group by default when it's small enough to scan at a
            # glance, or when it holds the overall cheapest rate; otherwise a
            # big catalogue (e.g. Royal Mail's 70+ services) starts collapsed
            # behind its count so it doesn't bury the rest.
            parent.setExpanded(len(group_rates) <= _MAX_AUTO_EXPAND or has_cheapest)

        self._resize_rates_tree_to_content()

    def _render_rates(self) -> None:
        """Draw the held rates through the carrier filter.

        Kept apart from :meth:`_on_rates_received` so changing the filter
        re-draws without re-rating: a second rating call would cost a round trip
        and could come back with different prices, so the table would change
        under a user who only asked to look at one carrier.
        """
        carrier = self._carrier_filter.current_carrier()
        shown = for_carrier(self._rates, carrier) if carrier else list(self._rates)
        hidden = len(self._rates) - len(shown)
        self._populate_rates_tree(shown, self._cheapest_id, self._fastest_id)
        if carrier and hidden:
            self._carrier_filter_note.setText(
                tr(
                    "create_shipment.carrier_filter_note",
                    carrier=self._carrier_display_name(carrier),
                    hidden=hidden,
                )
            )
        else:
            self._carrier_filter_note.setText("")
        self._carrier_filter_note.setVisible(bool(self._carrier_filter_note.text()))

    def _add_rate_child(self, parent, rate, *, cheapest: bool, fastest: bool) -> None:
        tree = self._rates_tree
        # Columns: service cell (0), rate (1), est days (2), Buy (last). The
        # service and Buy columns hold widgets, so their text is left blank.
        child = QTreeWidgetItem(parent, ["", _format_price(rate), _format_delivery(rate), ""])
        if rate_rules.delivery_days(rate) is None:
            child.setToolTip(_RATE_COL_DAYS, tr("create_shipment.delivery_unknown"))
        tree.setItemWidget(
            child, _RATE_COL_SERVICE,
            self._build_rate_service_cell(rate, cheapest=cheapest, fastest=fastest),
        )

        buy_btn = QPushButton(tr("create_shipment.buy_button"))
        if self._quote_only:
            # A postal-code quote has no deliverable address, so EasyPost would
            # reject the purchase. Disable rather than hide, so the reason is
            # discoverable instead of the button just vanishing.
            buy_btn.setEnabled(False)
            buy_btn.setToolTip(tr("create_shipment.buy_needs_full_address"))
        else:
            buy_btn.clicked.connect(partial(self._on_buy_clicked, rate))
            # A redraw during a purchase (the carrier filter) must not hand back
            # live buttons.
            self._apply_buy_button_state(buy_btn)
        # Wrapped so the button keeps its own height, vertically centred, in a
        # row made taller by a badge line; given the cell directly, Qt stretched
        # it to the full row.
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 4, 0)
        holder_layout.addWidget(buy_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        tree.setItemWidget(child, _RATE_COL_BUY, holder)

    def _apply_buy_button_state(self, button: QPushButton) -> None:
        if self._label_bought:
            button.setEnabled(False)
            button.setToolTip(tr("create_shipment.buy_already_bought"))
        else:
            button.setEnabled(not self._purchase_in_flight)
            button.setToolTip("")

    def _refresh_buy_buttons(self) -> None:
        if self._quote_only:
            return
        tree = self._rates_tree
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            for j in range(top.childCount()):
                button = _buy_button(tree, top.child(j))
                if button is not None:
                    self._apply_buy_button_state(button)

    def _resize_rates_tree_to_content(self, *_args) -> None:
        """Size the tree to show every currently-visible row in full instead of
        scrolling internally — the outer QScrollArea handles overflow.
        Recomputed whenever a carrier group is expanded or collapsed, and when
        the tree's width changes (see eventFilter).

        The Buy column holds a widget Qt won't measure, so it's sized to the
        widest button here. Then the text columns are divided around it, and
        only then can the rows be measured, because a wrapped service name is
        as tall as its column is narrow.
        """
        tree = self._rates_tree
        buy_width = 0
        for item in _rate_rows(tree):
            buy = _buy_button(tree, item)
            if buy is not None:
                buy_width = max(buy_width, buy.sizeHint().width())
        if buy_width:
            tree.setColumnWidth(_RATE_COL_BUY, buy_width + 20)
        _fit_rate_columns(tree)
        rows = _fit_rate_rows(tree)
        tree.setFixedHeight(tree.header().sizeHint().height() + rows + 2 * tree.frameWidth() + 2)

    def eventFilter(self, watched, event) -> bool:
        tree = getattr(self, "_rates_tree", None)
        if (
            watched is tree
            and event.type() == QEvent.Type.Resize
            and tree.width() != self._rates_tree_width
        ):
            # Width only: setFixedHeight in the resize below is itself a resize,
            # and re-running on it would loop.
            # Deferred: an event filter runs before the tree's own resize
            # handling, so the viewport still has its old width at this point.
            self._rates_tree_width = tree.width()
            if tree.topLevelItemCount():
                QTimer.singleShot(0, self, self._resize_rates_tree_to_content)
        return super().eventFilter(watched, event)

    def _build_result_group(self) -> QGroupBox:
        """The bought label: preview beside the tracking number and what to do
        next. Hidden until a purchase succeeds, then scrolled to (_reveal_result).

        This replaces a "Label purchased successfully" dialog that said nothing
        the page should not have shown, and a raw S3 address nobody can use.
        """
        group = QGroupBox(tr("create_shipment.result_group"))
        self._result_group = group

        # The label itself, drawn in-app, at a fixed 4x6 shape and pinned to the
        # top, so it is on screen as soon as the panel is.
        self._label_preview = QLabel(tr("create_shipment.preview_placeholder"))
        self._label_preview.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self._label_preview.setWordWrap(True)
        self._label_preview.setFixedSize(240, 360)
        self._label_preview.setStyleSheet(
            f"color: {TEXT_MUTED}; border: 1px dashed #d9dee5; border-radius: 8px; padding: 8px;"
        )

        self._result_heading = QLabel(f"<h3>{tr('create_shipment.purchased_body')}</h3>")
        self._result_label = QLabel("")
        self._result_label.setWordWrap(True)
        # A tracking number is something people copy into an email.
        self._result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._print_label_btn = QPushButton(tr("create_shipment.print_label_button"))
        self._print_label_btn.clicked.connect(self._on_print_label)
        self._save_label_btn = QPushButton(tr("create_shipment.save_label_button"))
        self._save_label_btn.setEnabled(False)
        self._save_label_btn.clicked.connect(self._on_save_label)
        self._open_label_btn = QPushButton(tr("create_shipment.open_label_button"))
        self._open_label_btn.setEnabled(False)
        self._open_label_btn.clicked.connect(self._on_open_label)
        self._track_btn = QPushButton(tr("create_shipment.track_button"))
        self._track_btn.clicked.connect(self.tracking_requested.emit)

        details = QVBoxLayout()
        details.addWidget(self._result_heading)
        details.addWidget(self._result_label)
        for button in (self._print_label_btn, self._save_label_btn,
                       self._open_label_btn, self._track_btn):
            row = QHBoxLayout()
            row.addWidget(button)
            row.addStretch(1)
            details.addLayout(row)
        details.addStretch(1)

        layout = QHBoxLayout()
        layout.addWidget(self._label_preview, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(details, stretch=1)
        group.setLayout(layout)
        group.setVisible(False)
        return group

    def _reveal_result(self) -> None:
        """Scroll the page so the result panel's top is at the top of the view.

        The Buy button that was just clicked can be thousands of pixels down an
        85-row tree. Repeated one turn of the event loop later, because the panel
        has only just been shown and its position settles once the layout runs.
        """
        def scroll() -> None:
            self._content.layout().activate()
            top = self._result_group.mapTo(self._content, QPoint(0, 0)).y()
            self._scroll.verticalScrollBar().setValue(max(0, top - 8))

        scroll()
        QTimer.singleShot(0, self, scroll)

    def _load_label_preview(self, url: str) -> None:
        """Fetch and draw the purchased label. Qt has no PDF engine, so a PDF
        label falls back to the open/save buttons with a note."""
        if not _is_previewable(url):
            self._label_preview.setText(tr("create_shipment.preview_unavailable"))
            return

        self._label_preview.setText(tr("create_shipment.preview_loading"))
        self._pending_preview_task = run_async(
            lambda: requests.get(url, timeout=30).content, self
        )
        self._pending_preview_task.succeeded.connect(self._on_preview_loaded)
        self._pending_preview_task.failed.connect(
            lambda _exc: self._label_preview.setText(tr("create_shipment.preview_failed"))
        )

    def _on_preview_loaded(self, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self._label_preview.setText(tr("create_shipment.preview_failed"))
            return
        self._label_preview.setPixmap(
            pixmap.scaled(
                self._label_preview.width() - 16,
                self._label_preview.height() - 16,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def refresh_address_choices(self) -> None:
        """Reload the saved addresses into From and To.

        From starts on the first address, which list_addresses orders
        favourites first, so it is the sender a user has marked as theirs. To
        starts EMPTY. Both used to start on that same first address, so the first
        quote a new customer saw was for posting a parcel to themselves, with
        live Buy buttons; guessing "the other address" instead would still be a
        guess, and a wrong guess is a label addressed to the wrong person. An
        empty To costs one click and cannot be bought from by accident.

        This runs every time the page is shown, so whatever the user already
        chose is kept when it still exists. Rebuilding the lists used to reset
        both to the first address on every visit, and throw the rates away.
        """
        previous_from = self._from_combo.currentData()
        previous_to = self._to_combo.currentData()
        records = list_addresses()
        self._address_by_id = {rec.id: rec for rec in records}
        for combo in (self._from_combo, self._to_combo):
            combo.blockSignals(True)
            combo.clear()
            for rec in records:
                combo.addItem(address_choice_label(rec), rec.id)
        self._from_combo.setCurrentIndex(
            max(self._from_combo.findData(previous_from), 0) if records else -1
        )
        # findData returns -1 for nothing chosen, which is the empty placeholder.
        self._to_combo.setCurrentIndex(self._to_combo.findData(previous_to))
        for combo in (self._from_combo, self._to_combo):
            combo.blockSignals(False)
        self._update_customs_visibility()
        if (self._from_combo.currentData(), self._to_combo.currentData()) != (
            previous_from, previous_to
        ):
            self._invalidate_rates()

    def _on_signature_changed(self, *_args) -> None:
        """Signature level changes which services carriers quote, so any rates
        already on screen are stale the moment it changes."""
        self._invalidate_rates()

    def _invalidate_rates(self, *_args) -> None:
        """Discard quoted rates that no longer describe what would be bought.

        A rate belongs to the shipment it was quoted for — a specific parcel,
        between specific addresses. Change any of those and the prices on screen
        are for something else, yet each row still carries a live Buy button
        wired to the old shipment. Leaving them visible invites buying a label
        for the parcel the user has just finished editing away from.

        Clearing back to the empty state (dropping the quote-only note and the
        shipment the rates could be bought from) makes the user re-run Get Rates,
        which is the only way to get prices that match the form.

        Declared insurance is deliberately NOT wired to this: it is applied at
        purchase time and does not change what carriers quote.

        A rating still in flight is voided too, although the table is empty
        while it runs. The early return on an empty table used to skip exactly
        that case, so the reply arrived afterwards and installed live Buy
        buttons for the parcel as it was when Get Rates was clicked.
        """
        if self._suspend_rate_invalidation:
            return
        self._rates_generation += 1
        self._current_shipment = None
        self._label_bought = False
        if self._rating_in_flight:
            self._end_rating()
        # Customs rows are wired as they are built, before the rates table
        # exists, and then there is nothing on screen to clear.
        tree = getattr(self, "_rates_tree", None)
        if tree is None or tree.topLevelItemCount() == 0:
            return
        self._rates_tree.clear()
        self._rates = []
        self._quote_only_note.setVisible(False)
        self._carrier_filter_row.setVisible(False)
        self._carrier_filter_note.setVisible(False)
        self._resize_rates_tree_to_content()

    def _connect_rate_invalidation(self) -> None:
        """Wire every input that changes what a carrier would quote, or what the
        shipment behind the rates carries. Customs item cells are wired per row
        in _on_add_customs_item."""
        for combo in (
            self._from_combo, self._to_combo, self._package_combo,
            self._from_country_combo, self._to_country_combo,
            self._contents_type_combo, self._restriction_type_combo,
            self._non_delivery_combo,
        ):
            combo.currentIndexChanged.connect(self._invalidate_rates)
        for spin in (
            self._length_input, self._width_input,
            self._height_input, self._weight_input,
        ):
            spin.valueChanged.connect(self._invalidate_rates)
        # The reference and the declaration are fixed when the shipment is
        # created, so a label bought from earlier rates would carry the old ones.
        for line in (
            self._reference_input, self._from_zip_input, self._to_zip_input,
            self._contents_explanation_input, self._restriction_comments_input,
            self._customs_signer_input,
        ):
            line.textChanged.connect(self._invalidate_rates)
        self._customs_certify_checkbox.toggled.connect(self._invalidate_rates)
        self._mode_full_radio.toggled.connect(self._invalidate_rates)

    def _begin_rating(self) -> int:
        """Start a rating request and return the generation its reply must
        match. Starting one supersedes any request still in flight."""
        self._rates_generation += 1
        self._rating_in_flight = True
        # A new quote is a new shipment. The last label stays in History; left
        # here it would sit above rates it has nothing to do with.
        self._result_group.setVisible(False)
        self._get_rates_btn.setEnabled(False)
        self._get_rates_btn.setText(tr("create_shipment.fetching_rates_button"))
        return self._rates_generation

    def _end_rating(self) -> None:
        self._rating_in_flight = False
        self._get_rates_btn.setEnabled(True)
        self._get_rates_btn.setText(tr("create_shipment.get_rates_button"))

    def _connect_rating_reply(self, task, generation: int) -> None:
        task.succeeded.connect(partial(self._deliver_rating, generation, self._on_rates_received))
        task.failed.connect(partial(self._deliver_rating, generation, self._on_rates_failed))

    def _deliver_rating(self, generation: int, handler, payload) -> None:
        # A superseded reply is dropped whole, errors included: an error about a
        # request the user has already edited away from is only noise.
        if generation != self._rates_generation:
            return
        handler(payload)

    def _on_get_rates_clicked(self) -> None:
        if self._mode_zip_radio.isChecked():
            self._request_zip_quote()
            return

        from_id = self._from_combo.currentData()
        to_id = self._to_combo.currentData()
        if self._from_combo.count() < 2 or not from_id:
            QMessageBox.warning(
                self,
                tr("create_shipment.missing_addresses_title"),
                tr("create_shipment.missing_addresses_body"),
            )
            return
        if not to_id:
            # There are addresses to choose from; the user has not chosen one.
            # "Verify at least two addresses first" would send them to the
            # Address Book for nothing.
            QMessageBox.warning(
                self,
                tr("create_shipment.missing_addresses_title"),
                tr("create_shipment.missing_recipient_body"),
            )
            self._to_combo.setFocus()
            return
        if from_id == to_id:
            QMessageBox.warning(
                self,
                tr("create_shipment.same_address_title"),
                tr("create_shipment.same_address_body"),
            )
            self._to_combo.setFocus()
            return

        customs_info = None
        if self._is_international():
            from_rec = self._address_by_id.get(from_id)
            to_rec = self._address_by_id.get(to_id)
            if not (from_rec.name or from_rec.company or "").strip() or not (
                to_rec.name or to_rec.company or ""
            ).strip():
                # Carriers require a name or company on both addresses for the
                # customs declaration. EasyPost's error for this is buried in
                # a generic 400 ("malformed syntax") unless the detailed
                # errors list is surfaced — see app/core/errors.py.
                QMessageBox.warning(
                    self,
                    tr("create_shipment.missing_name_title"),
                    tr("create_shipment.missing_name_body"),
                )
                return
            if not (from_rec.phone or "").strip() or not (to_rec.phone or "").strip():
                # Carriers require a phone number on both addresses for an
                # international label. USPS reports a useless generic 400 when
                # it's missing rather than a clear validation error (DHL/FedEx
                # do report it clearly) — catch it here instead of letting the
                # user hit that opaque error at buy time.
                QMessageBox.warning(
                    self,
                    tr("create_shipment.missing_phone_title"),
                    tr("create_shipment.missing_phone_body"),
                )
                return
            try:
                customs_info = self._collect_customs_info()
            except ValueError:
                QMessageBox.warning(
                    self,
                    tr("create_shipment.customs_validation_title"),
                    tr("create_shipment.customs_validation_body"),
                )
                return

        generation = self._begin_rating()

        package_data = self._package_combo.currentData()
        params = dict(
            to_address_id=to_id,
            from_address_id=from_id,
            weight=self._weight_oz(),
            reference=self._reference_input.text().strip(),
            customs_info=customs_info,
            delivery_confirmation=self._signature_combo.currentData(),
        )
        if isinstance(package_data, tuple) and package_data[0] == "predefined":
            params["predefined_package"] = package_data[1].name
        else:
            params["length"] = self._length_in()
            params["width"] = self._width_in()
            params["height"] = self._height_in()
        self._quote_only = False
        self._pending_task = run_async(lambda: create_shipment(**params), self)
        self._connect_rating_reply(self._pending_task, generation)

    def _request_zip_quote(self) -> None:
        from_zip = self._from_zip_input.text().strip()
        to_zip = self._to_zip_input.text().strip()
        if not from_zip or not to_zip:
            QMessageBox.warning(
                self,
                tr("create_shipment.missing_zip_title"),
                tr("create_shipment.missing_zip_body"),
            )
            return

        generation = self._begin_rating()

        params = dict(
            from_postal_code=from_zip,
            to_postal_code=to_zip,
            from_country=self._from_country_combo.currentData(),
            to_country=self._to_country_combo.currentData(),
            weight=self._weight_oz(),
            delivery_confirmation=self._signature_combo.currentData(),
        )
        package_data = self._package_combo.currentData()
        if isinstance(package_data, tuple) and package_data[0] == "predefined":
            params["predefined_package"] = package_data[1].name
        else:
            params["length"] = self._length_in()
            params["width"] = self._width_in()
            params["height"] = self._height_in()

        self._quote_only = True
        self._pending_task = run_async(lambda: create_rate_quote(**params), self)
        self._connect_rating_reply(self._pending_task, generation)

    def _on_rates_received(self, shipment) -> None:
        self._end_rating()
        self._current_shipment = shipment
        self._label_bought = False

        all_rates = sorted(getattr(shipment, "rates", None) or [], key=rate_rules.sort_key)
        # Drop non-purchasable placeholder rates (e.g. Royal Mail V3 catalogue
        # services that don't apply to the route, priced at 0.01). Account-billed
        # Royal Mail rates sit below the threshold too but ARE buyable, so
        # is_placeholder_rate keeps them. If filtering would empty the list,
        # fall back to showing everything so a genuine all-low-cost result is
        # never hidden.
        real_rates = [r for r in all_rates if not rate_rules.is_placeholder_rate(r)]
        rates = real_rates or all_rates
        self._rates = rates
        # Badges and carrier order are judged in one currency, the sender's when
        # anything is quoted in it (see rate_rules.comparison_currency).
        self._rate_currency = rate_rules.comparison_currency(
            rates, preferred=self._sender_currency()
        )
        self._declined_carriers = _message_carriers(shipment)
        self._cheapest_id = rate_rules.cheapest_rate_id(rates, currency=self._rate_currency)
        self._fastest_id = rate_rules.fastest_rate_id(rates, currency=self._rate_currency)
        currencies = {
            (getattr(r, "currency", "") or "").upper()
            for r in rates if rate_rules.is_priced(r)
        }
        if self._rate_currency and len(currencies) > 1:
            self._currency_note.setText(
                tr("create_shipment.badges_currency_note", currency=self._rate_currency)
            )
        else:
            self._currency_note.setText("")
        self._currency_note.setVisible(bool(self._currency_note.text()))

        self._quote_only_note.setVisible(self._quote_only)
        # Offer only the carriers that quoted, then honour the carrier implied
        # by a chosen predefined package. Preference is applied here rather than
        # when the package is picked because the filter has nothing to select
        # from until a rating has come back.
        self._carrier_filter.blockSignals(True)
        self._carrier_filter.set_carriers(carriers_present_in(rates))
        self._carrier_filter.set_current_carrier(self._preferred_carrier)
        self._carrier_filter.blockSignals(False)
        # One carrier is nothing to filter, and a control with a single choice
        # reads as a broken one.
        self._carrier_filter_row.setVisible(self._carrier_filter.count() > 2)
        self._render_rates()

        # Carriers that declined to quote say why here, and nowhere else. The
        # call succeeded, so this is not an error — but without it a carrier
        # just goes missing from the table with no explanation at all.
        notes = carrier_messages(shipment)
        self._show_carrier_notes(notes)

        if not rates:
            body = tr("create_shipment.no_rates_body")
            if notes:
                body += "\n\n" + "\n".join(notes)
            QMessageBox.information(
                self, tr("create_shipment.no_rates_title"), body
            )

    def _sender_currency(self) -> str | None:
        """The sender's own currency, or None when the country is not one the
        customs table knows (then the majority of quotes decides)."""
        if self._mode_zip_radio.isChecked():
            country = self._from_country_combo.currentData()
        else:
            record = self._address_by_id.get(self._from_combo.currentData())
            country = getattr(record, "country", None)
        return customs.CURRENCY_BY_COUNTRY.get((country or "").upper())

    def _show_carrier_notes(self, notes: list[str]) -> None:
        carriers: list[str] = []
        for carrier in getattr(self, "_declined_carriers", ()):
            name = self._carrier_display_name(carrier)
            if name not in carriers:
                carriers.append(name)
        if notes and carriers:
            self._carrier_notes_summary.setText(
                tr("create_shipment.carrier_notes_summary", carriers=", ".join(carriers))
            )
        else:
            self._carrier_notes_summary.setText("")
        self._carrier_notes_label.setText("\n".join(notes))
        # With no carrier named there is nothing to summarise, so the notes
        # themselves are the only explanation and are shown as they are.
        summarised = bool(self._carrier_notes_summary.text())
        self._carrier_notes_row.setVisible(summarised)
        self._carrier_notes_toggle.blockSignals(True)
        self._carrier_notes_toggle.setChecked(False)
        self._carrier_notes_toggle.setText(tr("create_shipment.carrier_notes_show"))
        self._carrier_notes_toggle.blockSignals(False)
        self._carrier_notes_label.setVisible(bool(notes) and not summarised)

    def _on_carrier_notes_toggled(self, shown: bool) -> None:
        self._carrier_notes_label.setVisible(shown)
        self._carrier_notes_toggle.setText(
            tr("create_shipment.carrier_notes_hide" if shown else "create_shipment.carrier_notes_show")
        )

    def _on_rates_failed(self, exc: Exception) -> None:
        self._end_rating()
        QMessageBox.critical(
            self, tr("common.error"), tr("create_shipment.get_rates_error_body", error=format_api_error(exc))
        )

    def _on_buy_clicked(self, rate) -> None:
        if self._current_shipment is None or self._purchase_in_flight or self._label_bought:
            return
        # Before the confirmation rather than after it: test mode has none, and a
        # second click would otherwise start a second purchase.
        self._set_purchase_in_flight(True)
        description = tr(
            "create_shipment.buy_confirm_description",
            carrier=getattr(rate, "carrier", ""),
            service=getattr(rate, "service", ""),
            rate=getattr(rate, "rate", ""),
            currency=getattr(rate, "currency", ""),
        )
        # A declared insurance value is a real added charge, so surface it in
        # the same confirmation that guards a production purchase.
        insured_value = self._insurance_input.value()
        insurance = f"{insured_value:.2f}" if insured_value > 0 else None
        if insurance:
            description += "\n" + tr("create_shipment.insured_note", amount=insurance)
        if not confirm_if_production(self, description):
            self._set_purchase_in_flight(False)
            return

        shipment_id = self._current_shipment.id
        rate_id = rate.id
        # The key that buys the label decides where it is filed, and the mode
        # banner can be flipped before the reply arrives.
        mode = client_manager.active_mode
        self._pending_task = run_async(lambda: buy_shipment(shipment_id, rate_id, insurance), self)
        self._pending_task.succeeded.connect(partial(self._on_bought, mode=mode))
        self._pending_task.failed.connect(self._on_buy_failed)

    def _set_purchase_in_flight(self, in_flight: bool) -> None:
        self._purchase_in_flight = in_flight
        self._refresh_buy_buttons()

    def _on_buy_failed(self, exc) -> None:
        self._set_purchase_in_flight(False)
        # A failed purchase sours the session: no review prompt afterwards,
        # however well a later label goes.
        mark_session_friction()
        QMessageBox.critical(
            self, tr("common.error"), tr("create_shipment.purchase_error_body", error=format_api_error(exc))
        )

    def _on_bought(self, shipment, mode: str | None = None) -> None:
        self._current_shipment = shipment
        self._label_bought = True
        self._set_purchase_in_flight(False)
        save_shipment_locally(shipment, mode)
        # Buying a label always creates a tracker; recording it here is what
        # puts the shipment on the Tracking page, instead of the user having to
        # paste the tracking number back in by hand.
        track_shipment(shipment, mode)

        postage_label = getattr(shipment, "postage_label", None)
        label_url = getattr(postage_label, "label_url", None) if postage_label else None
        tracking_code = getattr(shipment, "tracking_code", "") or ""

        self._result_label.setText(
            tr("create_shipment.tracking_number", tracking_code=tracking_code)
            if tracking_code else ""
        )
        self._result_label.setVisible(bool(tracking_code))
        self._track_btn.setVisible(bool(tracking_code))
        if label_url:
            self._open_label_btn.setEnabled(True)
            self._save_label_btn.setEnabled(True)
            self._pending_label_url = label_url
            self._pending_label_file_type = getattr(postage_label, "label_file_type", None)
            # The print sheet lays out label images; a PDF or thermal-printer
            # label is opened or saved instead.
            self._print_label_btn.setVisible(_is_previewable(label_url))
            self._load_label_preview(label_url)
        else:
            self._pending_label_url = None
            self._open_label_btn.setEnabled(False)
            self._save_label_btn.setEnabled(False)
            self._print_label_btn.setVisible(False)
            self._label_preview.setText(tr("create_shipment.purchased_no_label"))

        # In place of a modal "Label purchased successfully": the panel says the
        # same thing, and closing a dialog left the customer looking at the Buy
        # button they had just pressed rather than at the label.
        self._result_group.setVisible(True)
        self._reveal_result()

        # A bought label is the moment of satisfaction, so it is where the review
        # prompt belongs. Both calls are no-ops on builds with no storefront, and
        # every other gate is applied inside them.
        note_successful_shipment()
        schedule_review_prompt(self)

    def _on_print_label(self) -> None:
        url = getattr(self, "_pending_label_url", None)
        if url:
            PrintSheetDialog([url], self).exec()

    def _on_open_label(self) -> None:
        if getattr(self, "_pending_label_url", None):
            open_label(self._pending_label_url)

    def _on_save_label(self) -> None:
        url = getattr(self, "_pending_label_url", None)
        if not url:
            return
        ext = _label_file_extension(url, getattr(self, "_pending_label_file_type", None))
        if ext:
            name = f"label.{ext}"
            file_filter = tr(
                "create_shipment.label_file_filter", format=_LABEL_FILE_FORMATS[ext], ext=ext
            )
        else:
            # A format this app does not know: no filter is better than a wrong one.
            name, file_filter = "label", ""
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("create_shipment.save_label_dialog_title"),
            name,
            file_filter,
        )
        if not path:
            return
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            with open(path, "wb") as f:
                f.write(response.content)
            QMessageBox.information(
                self, tr("create_shipment.saved_title"), tr("create_shipment.saved_body", path=path)
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self, tr("common.error"), tr("create_shipment.save_label_error_body", error=format_api_error(exc))
            )
