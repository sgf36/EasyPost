"""Create a shipment, shop rates, buy a label, and save/open it."""

import re
import webbrowser
from functools import partial

import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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
from app.core.countries import COUNTRIES
from app.core.errors import carrier_messages, format_api_error
from app.core.settings import load_settings, save_settings
from app.i18n import tr
from app.services.addresses import address_choice_label, list_addresses
from app.services.carriers import carrier_display_name
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
from app.ui.theme import TEXT_MUTED
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.chips import badge
from app.ui.widgets.purchase_confirm import confirm_if_production

# Carrier & service | Included | Rate | Delivery | Buy. Rates are shown in a
# QTreeWidget grouped by carrier: the carrier is a top-level (header) row and
# each service is a child under it, so column 0 carries the carrier name on a
# header row and the service name on a child row rather than both sharing one
# flat cell. The "Included" column sits between the service identity and the
# rate, carrying the enhancement badges (tracked / signed / guaranteed).
_RATE_COLUMN_COUNT = 5
_CUSTOMS_ITEM_COLUMN_COUNT = 7

# Label previews are rendered from the image EasyPost returns. PDFs can't be
# painted by Qt without a PDF engine, so those fall back to the open/save
# buttons alone.
_PREVIEWABLE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp")


def _rate_sort_key(rate) -> float:
    """Cheapest first. EasyPost returns rates in no meaningful order and hands
    back `rate` as a string, so anything unparseable sorts to the bottom
    rather than crashing the whole table."""
    try:
        return float(getattr(rate, "rate", None))
    except (TypeError, ValueError):
        return float("inf")


def _delivery_days(rate) -> int | None:
    days = getattr(rate, "delivery_days", None)
    try:
        return int(days)
    except (TypeError, ValueError):
        return None


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


def _fastest_rate_id(rates) -> str | None:
    """Id of the quickest rate, or None when no carrier quoted an estimate
    (common for international and for some regional carriers)."""
    timed = [r for r in rates if _delivery_days(r) is not None]
    if not timed:
        return None
    return min(timed, key=_delivery_days).id


def _format_price(rate) -> str:
    # Royal Mail and other OBA carriers bill the real postage to the account,
    # so the sub-penny figure EasyPost hands back is not the price to show —
    # say it's invoiced rather than a misleading "0.01 GBP".
    if _is_account_billed(rate):
        return tr("create_shipment.billed_to_account")
    amount = getattr(rate, "rate", "") or ""
    currency = getattr(rate, "currency", "") or ""
    return f"{amount} {currency}".strip()


def _format_delivery(rate) -> str:
    """Just the number — the column is already headed "Est. days", so this
    sidesteps plural rules ("1 days") in every one of the 50 locales."""
    days = _delivery_days(rate)
    if days is None:
        return tr("create_shipment.delivery_unknown")
    return str(days)


# Anything below this (in the rate's own currency) is treated as a
# non-purchasable placeholder, not a real quote. Some carriers — notably Royal
# Mail V3 via EasyPost — return their whole service catalogue as rates, including
# services that don't apply to the route, priced at a nominal 0.01 that cannot be
# bought. No real shipping service costs a penny, so these are hidden when
# genuine quotes exist (see _on_rates_received).
_MIN_REAL_RATE = 0.02

# Carriers that invoice postage to the account externally (Royal Mail's OBA
# billing) rather than charging the label price up front. EasyPost's Royal Mail
# v3 integration returns *purchasable* services at a nominal sub-penny rate
# precisely because the true cost is billed to the account, not quoted on the
# rate. So for these carriers a sub-_MIN_REAL_RATE figure means "billed to
# account" and the label genuinely can be bought — it is NOT the non-purchasable
# catalogue placeholder that the same low number means for any other carrier.
_ACCOUNT_BILLED_CARRIERS = {"RoyalMail", "RoyalMailV3"}

# A carrier group with more than this many services starts collapsed (its count
# stays visible on the header), so a 70-service Royal Mail catalogue doesn't
# bury every other carrier; smaller groups — and whichever group holds the
# cheapest rate — start expanded. See _populate_rates_tree.
_MAX_AUTO_EXPAND = 8


def _is_account_billed(rate) -> bool:
    """True for an account-billed carrier rate whose sub-penny figure is a
    "billed to account" marker rather than a real quote (see
    _ACCOUNT_BILLED_CARRIERS). Such rates ARE purchasable despite the low
    number, so they must be told apart from ordinary placeholders."""
    if getattr(rate, "carrier", "") not in _ACCOUNT_BILLED_CARRIERS:
        return False
    try:
        return float(getattr(rate, "rate", None)) < _MIN_REAL_RATE
    except (TypeError, ValueError):
        return False


def _is_placeholder_rate(rate) -> bool:
    """A non-purchasable catalogue placeholder, priced below _MIN_REAL_RATE.
    Account-billed carrier rates (Royal Mail via OBA) sit below that threshold
    too but genuinely can be bought, so they are never hidden as placeholders."""
    if _is_account_billed(rate):
        return False
    try:
        return float(getattr(rate, "rate", None)) < _MIN_REAL_RATE
    except (TypeError, ValueError):
        return False


def _cheapest_rate_id(rates) -> str | None:
    """Id of the cheapest rate, ignoring account-billed rates whose real price
    is unknown (their sub-penny figure isn't a comparable amount). None when
    nothing is priced."""
    priced = [r for r in rates if not _is_account_billed(r)]
    if not priced:
        return None
    return min(priced, key=_rate_sort_key).id


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


# The customs currency map, the declaration shape and the international test
# used to live here, which is precisely why batch shipments never got them: a
# second caller cannot import what is private to a view. They are in
# app/core/customs.py now, and both callers build declarations the same way.


class CreateShipmentView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._pending_packages_task = None
        self._pending_preview_task = None
        self._current_shipment = None
        self._address_by_id = {}
        self._saved_packages = []
        self._predefined_packages = []
        # True when the current rates came from a postal-code-only quote, in
        # which case no rate on screen can actually be bought.
        self._quote_only = False
        # Set while the form rewrites its own inputs (a unit conversion) so that
        # does not read as the user editing the parcel. Must exist before any
        # widget is built, since building them can emit valueChanged.
        self._suspend_rate_invalidation = False

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(QLabel(f"<h2>{tr('create_shipment.title')}</h2>"))
        content_layout.addWidget(self._build_form_group())
        content_layout.addWidget(self._build_customs_group())
        # Get Rates sits below the Customs section. On an international shipment
        # the customs form is visible, so this reads top-to-bottom: package →
        # customs → Get Rates. On a domestic shipment the customs group is
        # hidden and collapses to nothing, so the button simply follows the
        # package form as before.
        content_layout.addWidget(self._get_rates_btn)

        # Rates sit beside the purchased label rather than above it, so the
        # label you just bought is visible without leaving the page — and the
        # other quotes stay on screen next to it.
        results_row = QHBoxLayout()
        results_row.addWidget(self._build_rates_group(), stretch=3)
        results_row.addWidget(self._build_result_group(), stretch=2)
        content_layout.addLayout(results_row)
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
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)

        self.refresh_address_choices()
        self._refresh_saved_packages()
        self._refresh_predefined_packages()

    def _build_form_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.details_group"))
        form = QFormLayout()

        self._from_combo = QComboBox()
        self._to_combo = QComboBox()
        self._from_combo.currentIndexChanged.connect(self._update_customs_visibility)
        self._to_combo.currentIndexChanged.connect(self._update_customs_visibility)
        refresh_btn = QPushButton(tr("create_shipment.reload_button"))
        refresh_btn.clicked.connect(self.refresh_address_choices)

        self._full_address_widget = QWidget()
        addr_row = QHBoxLayout(self._full_address_widget)
        addr_row.setContentsMargins(0, 0, 0, 0)
        addr_row.addWidget(QLabel(tr("create_shipment.from_label")))
        addr_row.addWidget(self._from_combo, stretch=1)
        addr_row.addWidget(QLabel(tr("create_shipment.to_label")))
        addr_row.addWidget(self._to_combo, stretch=1)
        addr_row.addWidget(refresh_btn)

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
        self._package_combo.currentIndexChanged.connect(self._on_package_selected)
        self._save_package_btn = QPushButton(tr("create_shipment.save_package_button"))
        self._save_package_btn.clicked.connect(self._on_save_package_clicked)
        self._delete_package_btn = QPushButton(tr("create_shipment.delete_package_button"))
        self._delete_package_btn.clicked.connect(self._on_delete_package_clicked)
        self._delete_package_btn.setEnabled(False)

        package_row = QHBoxLayout()
        package_row.addWidget(self._package_combo, stretch=1)
        package_row.addWidget(self._save_package_btn)
        package_row.addWidget(self._delete_package_btn)

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

        dims_row = QHBoxLayout()
        dims_row.addWidget(self._length_label)
        dims_row.addWidget(self._length_input)
        dims_row.addWidget(self._width_label)
        dims_row.addWidget(self._width_input)
        dims_row.addWidget(self._height_label)
        dims_row.addWidget(self._height_input)
        dims_row.addWidget(self._weight_label)
        dims_row.addWidget(self._weight_input)
        dims_row.addWidget(self._weight_unit_combo)

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

    def _build_address_mode_row(self) -> QHBoxLayout:
        """Full addresses (can buy a label) vs postal codes only (price check).

        A quick "what would this cost?" doesn't need a saved, verified address
        at either end, which is otherwise a lot of typing before you see a
        single number.
        """
        self._mode_full_radio = QRadioButton(tr("create_shipment.address_mode_full"))
        self._mode_zip_radio = QRadioButton(tr("create_shipment.address_mode_zip"))
        self._mode_full_radio.setChecked(True)
        self._mode_full_radio.toggled.connect(self._on_address_mode_changed)

        row = QHBoxLayout()
        row.addWidget(self._mode_full_radio)
        row.addWidget(self._mode_zip_radio)
        row.addStretch(1)
        return row

    def _build_zip_row(self) -> QWidget:
        self._from_zip_input = QLineEdit()
        self._from_zip_input.setPlaceholderText(tr("create_shipment.zip_placeholder"))
        self._to_zip_input = QLineEdit()
        self._to_zip_input.setPlaceholderText(tr("create_shipment.zip_placeholder"))
        self._from_country_combo = self._country_combo()
        self._to_country_combo = self._country_combo()

        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(tr("create_shipment.from_label")))
        row.addWidget(self._from_zip_input, stretch=1)
        row.addWidget(self._from_country_combo)
        row.addWidget(QLabel(tr("create_shipment.to_label")))
        row.addWidget(self._to_zip_input, stretch=1)
        row.addWidget(self._to_country_combo)
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
        self._suspend_rate_invalidation = False
        self._persist_units()

    def _on_weight_unit_changed(self, *_args) -> None:
        new_unit = self._weight_unit_combo.currentData()
        if not new_unit or new_unit == self._weight_unit:
            return
        canon = units.to_ounces(self._weight_input.value(), self._weight_unit)
        self._weight_unit = new_unit
        wlo, whi, wdec, wstep = units.WEIGHT_SPIN[new_unit]
        # Same weight, different unit — not a different parcel.
        self._suspend_rate_invalidation = True
        self._weight_input.setDecimals(wdec)
        self._weight_input.setRange(wlo, whi)
        self._weight_input.setSingleStep(wstep)
        self._weight_input.setValue(units.from_ounces(canon, new_unit))
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
                tr("create_shipment.customs_item_col_value"),
                tr("create_shipment.customs_item_col_weight"),
                tr("create_shipment.customs_item_col_hts"),
                tr("create_shipment.customs_item_col_origin"),
                "",
            ]
        )
        self._customs_items_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )

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

        weight_spin = self._spin(0.1, 5000, 8)
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

        # Every cell in this table is a widget, which ResizeToContents can't
        # measure — without this the header for a widget-only column (e.g. "HTS
        # number (optional)") clips. Fit each column to header + widget.
        _fit_columns_to_widgets(self._customs_items_table, stretch_col=0)

    def _on_remove_customs_item(self, button: QPushButton) -> None:
        for row in range(self._customs_items_table.rowCount()):
            if self._customs_items_table.cellWidget(row, _CUSTOMS_ITEM_COLUMN_COUNT - 1) is button:
                self._customs_items_table.removeRow(row)
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
        from_rec = self._address_by_id.get(self._from_combo.currentData())
        customs_currency = customs.currency_for(getattr(from_rec, "country", None))

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
            tr("create_shipment.col_enhancements"),
            tr("create_shipment.col_rate"),
            tr("create_shipment.col_est_days"),
            "",
        ]
        # Rates are grouped by carrier under collapsible header rows rather than
        # shown flat: Royal Mail v3 alone returns 70+ services, which used to
        # drown every other carrier in one long list. Each carrier is a
        # top-level row (name + service count) with its services as children.
        self._rates_tree = QTreeWidget()
        self._rates_tree.setColumnCount(_RATE_COLUMN_COUNT)
        self._rates_tree.setHeaderLabels(rate_columns)
        header = self._rates_tree.header()
        # Carrier & service (col 0) absorbs the slack; the compact columns —
        # Included, Rate and Est. days — size to their own content. The Buy
        # column holds a widget, which ResizeToContents can't measure — left to
        # itself it collapses and clips the button, so it's Fixed and sized
        # explicitly in _resize_rates_tree_to_content instead.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_RATE_COLUMN_COUNT - 1, QHeaderView.ResizeMode.Fixed)
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

        # Why a carrier is absent from the table above. EasyPost reports this on
        # the shipment's `messages`, which nothing previously read, so a carrier
        # that declined to quote simply vanished without explanation.
        self._carrier_notes_label = QLabel("")
        self._carrier_notes_label.setWordWrap(True)
        self._carrier_notes_label.setStyleSheet(f"color: {TEXT_MUTED};")
        self._carrier_notes_label.setVisible(False)

        layout = QVBoxLayout()
        layout.addWidget(self._rates_tree)
        layout.addWidget(self._quote_only_note)
        layout.addWidget(self._carrier_notes_label)
        group.setLayout(layout)
        return group

    _CAMEL_SPLIT = re.compile(
        r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])"
    )

    @classmethod
    def _humanize_service(cls, name: str) -> str:
        """Space out a run-together carrier service name so it reads as words.

        EasyPost returns names like ``InternationalBusinessParcelsTracked30kg``;
        splitting at camelCase and letter/digit boundaries gives ``International
        Business Parcels Tracked 30kg``, which is far quicker to scan and lets a
        clipped name break at a sensible point. Names that already contain
        spaces are left untouched. The same spacing turns a carrier code
        (``RoyalMailV3``) into a readable group-header name."""
        if not name or " " in name:
            return name
        return cls._CAMEL_SPLIT.sub(" ", name)

    @classmethod
    def _carrier_display_name(cls, carrier: str) -> str:
        """Group-header label for a carrier code off a rate.

        Rates report the carrier CamelCased ("RoyalMailV3"); the shared lookup
        is case-insensitive and backed by the names EasyPost itself publishes,
        so it resolves either spelling. Only when the carrier is unknown to that
        catalogue — an offline first run, say — does this fall back to
        camel-splitting the code, which reads acceptably for plain acronyms
        (USPS, UPS, DHL) though it does mangle a few ("FedEx" → "Fed Ex")."""
        if not carrier:
            return "—"
        resolved = carrier_display_name(carrier)
        if resolved and resolved != carrier:
            return resolved
        return cls._humanize_service(carrier)

    def _build_rate_service_cell(self, rate, *, cheapest: bool, fastest: bool) -> QWidget:
        """The service cell of a child row: the humanised service name plus any
        cheapest/fastest marker. Unlike the old flat-table identity cell this
        carries no carrier chip — the carrier is the parent (group) row now."""
        cell = QWidget()
        row = QHBoxLayout(cell)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)
        service = self._humanize_service(getattr(rate, "service", "") or "") or "—"
        service_label = QLabel(service)
        # The column clips very long names rather than widening the tree, so
        # carry the full text in a tooltip.
        service_label.setToolTip(service)
        row.addWidget(service_label)
        if cheapest:
            row.addWidget(badge(tr("create_shipment.badge_cheapest")))
        if fastest:
            row.addWidget(badge(tr("create_shipment.badge_fastest"), tone="muted"))
        row.addStretch(1)
        return cell

    def _build_rate_enhancements_cell(self, rate) -> QWidget | None:
        """The "Included" cell: one muted badge per enhancement the service
        advertises (tracked / signed / guaranteed), or None when it has none so
        the caller can leave the cell blank. These describe what the service
        *includes* and are kept apart from the cheapest/fastest ranking markers,
        which stay in the service identity cell."""
        enhancements = _service_enhancements(rate)
        if not enhancements:
            return None
        labels = {
            "tracked": tr("create_shipment.badge_tracked"),
            "signed": tr("create_shipment.badge_signed"),
            "guaranteed": tr("create_shipment.badge_guaranteed"),
        }
        cell = QWidget()
        row = QHBoxLayout(cell)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(4)
        for key in enhancements:
            row.addWidget(badge(labels[key], tone="muted"))
        row.addStretch(1)
        return cell

    def _populate_rates_tree(self, rates, cheapest_id, fastest_id) -> None:
        """Build the carrier-grouped tree: one top-level row per carrier with
        its services as children. Carriers are ordered by their cheapest real
        rate (account-billed-only carriers last); within a carrier the
        real-priced services come first, then the account-billed ones."""
        tree = self._rates_tree
        tree.clear()

        by_carrier: dict[str, list] = {}
        for rate in rates:
            by_carrier.setdefault(getattr(rate, "carrier", "") or "", []).append(rate)

        for carrier in self._order_carriers(by_carrier):
            carrier_rates = by_carrier[carrier]
            # Real-priced services first (cheapest first); then the
            # account-billed services, whose sub-penny figure isn't a comparable
            # price, ordered by name.
            real = sorted(
                (r for r in carrier_rates if not _is_account_billed(r)),
                key=_rate_sort_key,
            )
            billed = sorted(
                (r for r in carrier_rates if _is_account_billed(r)),
                key=lambda r: (getattr(r, "service", "") or "").lower(),
            )
            group_rates = real + billed

            display = self._carrier_display_name(carrier)
            # A carrier header carries the name and a count but is not itself a
            # rate — no enhancements, no price, no est. days, no Buy button.
            parent = QTreeWidgetItem([f"{display} ({len(group_rates)})", "", "", "", ""])
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

    def _order_carriers(self, by_carrier: dict[str, list]) -> list[str]:
        """Carriers with a real (non-account-billed) rate first, ordered by that
        carrier's cheapest real rate; carriers offering only account-billed
        rates (real price unknown) go last, alphabetically."""
        priced: list[tuple[float, str]] = []
        billed_only: list[str] = []
        for carrier, carrier_rates in by_carrier.items():
            real = [r for r in carrier_rates if not _is_account_billed(r)]
            if real:
                priced.append((min(_rate_sort_key(r) for r in real), carrier))
            else:
                billed_only.append(carrier)
        priced.sort(key=lambda pair: (pair[0], pair[1]))
        billed_only.sort()
        return [carrier for _key, carrier in priced] + billed_only

    def _add_rate_child(self, parent, rate, *, cheapest: bool, fastest: bool) -> None:
        tree = self._rates_tree
        # Columns: service cell (0), enhancements (1), rate (2), est days (3),
        # Buy (last). Cols 0/1/last hold widgets, so their text is left blank.
        child = QTreeWidgetItem(parent, ["", "", _format_price(rate), _format_delivery(rate), ""])
        tree.setItemWidget(
            child, 0, self._build_rate_service_cell(rate, cheapest=cheapest, fastest=fastest)
        )
        # Enhancement badges (tracked / signed / guaranteed) — only set a widget
        # when the service has any, so a plain service leaves the cell blank.
        enhancements_cell = self._build_rate_enhancements_cell(rate)
        if enhancements_cell is not None:
            tree.setItemWidget(child, 1, enhancements_cell)

        buy_btn = QPushButton(tr("create_shipment.buy_button"))
        if self._quote_only:
            # A postal-code quote has no deliverable address, so EasyPost would
            # reject the purchase. Disable rather than hide, so the reason is
            # discoverable instead of the button just vanishing.
            buy_btn.setEnabled(False)
            buy_btn.setToolTip(tr("create_shipment.buy_needs_full_address"))
        else:
            buy_btn.clicked.connect(partial(self._on_buy_clicked, rate))
        tree.setItemWidget(child, _RATE_COLUMN_COUNT - 1, buy_btn)

    def _resize_rates_tree_to_content(self, *_args) -> None:
        """Size the tree to show every currently-visible row in full instead of
        scrolling internally — the outer QScrollArea handles overflow.
        Recomputed whenever a carrier group is expanded or collapsed.

        Qt's row-height machinery measures the item delegate, which knows
        nothing about a cell widget, so a service row is measured from the
        service cell and Buy button; a carrier header row (no widgets) falls
        back to the delegate's own text height. The Buy column likewise holds a
        widget Qt won't measure, so it's sized to the widest button here.
        """
        tree = self._rates_tree
        total_height = tree.header().height() + 2 * tree.frameWidth()
        buy_width = 0

        def measure(item) -> None:
            nonlocal total_height, buy_width
            widget_height = 0
            for col in range(tree.columnCount()):
                widget = tree.itemWidget(item, col)
                if widget is not None:
                    widget_height = max(widget_height, widget.sizeHint().height())
            buy = tree.itemWidget(item, _RATE_COLUMN_COUNT - 1)
            if buy is not None:
                buy_width = max(buy_width, buy.sizeHint().width())
            if widget_height == 0:
                # A carrier header has no widgets; size it to its text.
                widget_height = tree.fontMetrics().height()
            # +8 so a row's widget isn't flush against its borders, and so the
            # (slightly generous) total never underestimates and clips a row.
            total_height += widget_height + 8

        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            measure(top)
            if top.isExpanded():
                for j in range(top.childCount()):
                    measure(top.child(j))

        if buy_width:
            tree.setColumnWidth(_RATE_COLUMN_COUNT - 1, buy_width + 16)
        tree.setFixedHeight(total_height + 2)

    def _build_result_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.result_group"))

        # The label itself, drawn in-app. Previously this group only offered
        # "open in browser" / "save as PDF", so you never actually saw what
        # you had just paid for without leaving the app.
        self._label_preview = QLabel(tr("create_shipment.preview_placeholder"))
        self._label_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label_preview.setWordWrap(True)
        self._label_preview.setMinimumHeight(260)
        self._label_preview.setStyleSheet(
            f"color: {TEXT_MUTED}; border: 1px dashed #d9dee5; border-radius: 8px; padding: 8px;"
        )

        self._result_label = QLabel(tr("create_shipment.no_label_yet"))
        self._result_label.setWordWrap(True)

        self._open_label_btn = QPushButton(tr("create_shipment.open_label_button"))
        self._open_label_btn.setEnabled(False)
        self._open_label_btn.clicked.connect(self._on_open_label)

        self._save_label_btn = QPushButton(tr("create_shipment.save_label_button"))
        self._save_label_btn.setEnabled(False)
        self._save_label_btn.clicked.connect(self._on_save_label)

        button_row = QHBoxLayout()
        button_row.addWidget(self._open_label_btn)
        button_row.addWidget(self._save_label_btn)
        button_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._label_preview, stretch=1)
        layout.addWidget(self._result_label)
        layout.addLayout(button_row)
        group.setLayout(layout)
        return group

    def _load_label_preview(self, url: str) -> None:
        """Fetch and draw the purchased label. Qt has no PDF engine, so a PDF
        label falls back to the open/save buttons with a note."""
        if not url.lower().split("?")[0].endswith(_PREVIEWABLE_SUFFIXES):
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
        self._from_combo.clear()
        self._to_combo.clear()
        records = list_addresses()
        self._address_by_id = {rec.id: rec for rec in records}
        for rec in records:
            display = address_choice_label(rec)
            self._from_combo.addItem(display, rec.id)
            self._to_combo.addItem(display, rec.id)
        self._update_customs_visibility()

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
        """
        if self._suspend_rate_invalidation:
            return
        if self._rates_tree.topLevelItemCount() == 0:
            return
        self._rates_tree.clear()
        self._current_shipment = None
        self._quote_only_note.setVisible(False)
        self._resize_rates_tree_to_content()

    def _connect_rate_invalidation(self) -> None:
        """Wire every input that changes what a carrier would quote."""
        for combo in (self._from_combo, self._to_combo, self._package_combo):
            combo.currentIndexChanged.connect(self._invalidate_rates)
        for spin in (
            self._length_input, self._width_input,
            self._height_input, self._weight_input,
        ):
            spin.valueChanged.connect(self._invalidate_rates)

    def _on_get_rates_clicked(self) -> None:
        if self._mode_zip_radio.isChecked():
            self._request_zip_quote()
            return

        from_id = self._from_combo.currentData()
        to_id = self._to_combo.currentData()
        if not from_id or not to_id:
            QMessageBox.warning(
                self,
                tr("create_shipment.missing_addresses_title"),
                tr("create_shipment.missing_addresses_body"),
            )
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

        self._get_rates_btn.setEnabled(False)
        self._get_rates_btn.setText(tr("create_shipment.fetching_rates_button"))

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
        self._pending_task.succeeded.connect(self._on_rates_received)
        self._pending_task.failed.connect(self._on_rates_failed)

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

        self._get_rates_btn.setEnabled(False)
        self._get_rates_btn.setText(tr("create_shipment.fetching_rates_button"))

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
        self._pending_task.succeeded.connect(self._on_rates_received)
        self._pending_task.failed.connect(self._on_rates_failed)

    def _on_rates_received(self, shipment) -> None:
        self._get_rates_btn.setEnabled(True)
        self._get_rates_btn.setText(tr("create_shipment.get_rates_button"))
        self._current_shipment = shipment

        all_rates = sorted(getattr(shipment, "rates", None) or [], key=_rate_sort_key)
        # Drop non-purchasable placeholder rates (e.g. Royal Mail V3 catalogue
        # services that don't apply to the route, priced at 0.01). Account-billed
        # Royal Mail rates sit below the threshold too but ARE buyable, so
        # _is_placeholder_rate keeps them. If filtering would empty the list,
        # fall back to showing everything so a genuine all-low-cost result is
        # never hidden.
        real_rates = [r for r in all_rates if not _is_placeholder_rate(r)]
        rates = real_rates or all_rates
        cheapest_id = _cheapest_rate_id(rates)
        fastest_id = _fastest_rate_id(rates)

        self._quote_only_note.setVisible(self._quote_only)
        self._populate_rates_tree(rates, cheapest_id, fastest_id)

        # Carriers that declined to quote say why here, and nowhere else. The
        # call succeeded, so this is not an error — but without it a carrier
        # just goes missing from the table with no explanation at all.
        notes = carrier_messages(shipment)
        self._carrier_notes_label.setText("\n".join(notes))
        self._carrier_notes_label.setVisible(bool(notes))

        if not rates:
            body = tr("create_shipment.no_rates_body")
            if notes:
                body += "\n\n" + "\n".join(notes)
            QMessageBox.information(
                self, tr("create_shipment.no_rates_title"), body
            )

    def _on_rates_failed(self, exc: Exception) -> None:
        self._get_rates_btn.setEnabled(True)
        self._get_rates_btn.setText(tr("create_shipment.get_rates_button"))
        QMessageBox.critical(
            self, tr("common.error"), tr("create_shipment.get_rates_error_body", error=format_api_error(exc))
        )

    def _on_buy_clicked(self, rate) -> None:
        if self._current_shipment is None:
            return
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
            return

        shipment_id = self._current_shipment.id
        rate_id = rate.id
        self._pending_task = run_async(lambda: buy_shipment(shipment_id, rate_id, insurance), self)
        self._pending_task.succeeded.connect(self._on_bought)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("create_shipment.purchase_error_body", error=format_api_error(exc))
            )
        )

    def _on_bought(self, shipment) -> None:
        self._current_shipment = shipment
        save_shipment_locally(shipment)
        # Buying a label always creates a tracker; recording it here is what
        # puts the shipment on the Tracking page, instead of the user having to
        # paste the tracking number back in by hand.
        track_shipment(shipment)

        postage_label = getattr(shipment, "postage_label", None)
        label_url = getattr(postage_label, "label_url", None) if postage_label else None
        tracking_code = getattr(shipment, "tracking_code", "")

        if label_url:
            self._result_label.setText(
                tr(
                    "create_shipment.purchased_result_text",
                    tracking_code=tracking_code,
                    label_url=label_url,
                )
            )
            self._open_label_btn.setEnabled(True)
            self._save_label_btn.setEnabled(True)
            self._pending_label_url = label_url
            self._load_label_preview(label_url)
        else:
            self._result_label.setText(tr("create_shipment.purchased_no_label"))
            self._label_preview.setText(tr("create_shipment.preview_placeholder"))

        QMessageBox.information(
            self, tr("create_shipment.purchased_title"), tr("create_shipment.purchased_body")
        )

    def _on_open_label(self) -> None:
        if getattr(self, "_pending_label_url", None):
            webbrowser.open(self._pending_label_url)

    def _on_save_label(self) -> None:
        url = getattr(self, "_pending_label_url", None)
        if not url:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("create_shipment.save_label_dialog_title"),
            "label.pdf",
            tr("create_shipment.pdf_filter"),
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
