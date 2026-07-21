"""Create a shipment, shop rates, buy a label, and save/open it."""

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
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.countries import COUNTRIES
from app.core.errors import format_api_error
from app.i18n import tr
from app.services.addresses import list_addresses
from app.services.packages import (
    delete_saved_package,
    list_predefined_packages,
    list_saved_packages,
    save_package,
)
from app.services.shipments import (
    buy_shipment,
    create_rate_quote,
    create_shipment,
    save_shipment_locally,
)
from app.ui.theme import TEXT_MUTED
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.chips import badge, carrier_chip
from app.ui.widgets.purchase_confirm import confirm_if_production

# Carrier & service | Rate | Delivery | Buy. Carrier and service share one
# cell (chip plus name) rather than taking a column each — the same amount of
# information in less width, which keeps the table readable now that it sits
# beside the label preview.
_RATE_COLUMN_COUNT = 4
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


def _fastest_rate_id(rates) -> str | None:
    """Id of the quickest rate, or None when no carrier quoted an estimate
    (common for international and for some regional carriers)."""
    timed = [r for r in rates if _delivery_days(r) is not None]
    if not timed:
        return None
    return min(timed, key=_delivery_days).id


def _format_price(rate) -> str:
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

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(QLabel(f"<h2>{tr('create_shipment.title')}</h2>"))
        content_layout.addWidget(self._build_form_group())
        content_layout.addWidget(self._build_customs_group())

        # Rates sit beside the purchased label rather than above it, so the
        # label you just bought is visible without leaving the page — and the
        # other quotes stay on screen next to it.
        results_row = QHBoxLayout()
        results_row.addWidget(self._build_rates_group(), stretch=3)
        results_row.addWidget(self._build_result_group(), stretch=2)
        content_layout.addLayout(results_row)
        content_layout.addStretch(1)

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

        self._length_input = self._spin(1, 1000, 6)
        self._width_input = self._spin(1, 1000, 6)
        self._height_input = self._spin(1, 1000, 6)
        self._weight_input = self._spin(0.1, 5000, 16)
        self._reference_input = QLineEdit()

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

        dims_row = QHBoxLayout()
        dims_row.addWidget(QLabel(tr("create_shipment.length_label")))
        dims_row.addWidget(self._length_input)
        dims_row.addWidget(QLabel(tr("create_shipment.width_label")))
        dims_row.addWidget(self._width_input)
        dims_row.addWidget(QLabel(tr("create_shipment.height_label")))
        dims_row.addWidget(self._height_input)
        dims_row.addWidget(QLabel(tr("create_shipment.weight_label")))
        dims_row.addWidget(self._weight_input)

        form.addRow(mode_row)
        form.addRow(self._full_address_widget)
        form.addRow(self._zip_widget)
        form.addRow(tr("create_shipment.package_label"), package_row)
        form.addRow(dims_row)
        self._reference_row_label = QLabel(tr("create_shipment.reference_field"))
        form.addRow(self._reference_row_label, self._reference_input)

        self._get_rates_btn = QPushButton(tr("create_shipment.get_rates_button"))
        self._get_rates_btn.clicked.connect(self._on_get_rates_clicked)

        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        group_layout.addWidget(self._get_rates_btn)
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
            self._length_input.setValue(pkg.length or 1)
            self._width_input.setValue(pkg.width or 1)
            self._height_input.setValue(pkg.height or 1)
            self._weight_input.setValue(pkg.weight)

    def _on_save_package_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, tr("create_shipment.save_package_dialog_title"), tr("create_shipment.save_package_dialog_label"))
        name = name.strip()
        if not ok or not name:
            return
        save_package(
            name,
            self._length_input.value(),
            self._width_input.value(),
            self._height_input.value(),
            self._weight_input.value(),
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

    def _on_remove_customs_item(self, button: QPushButton) -> None:
        for row in range(self._customs_items_table.rowCount()):
            if self._customs_items_table.cellWidget(row, _CUSTOMS_ITEM_COLUMN_COUNT - 1) is button:
                self._customs_items_table.removeRow(row)
                return

    def _is_international(self) -> bool:
        from_rec = self._address_by_id.get(self._from_combo.currentData())
        to_rec = self._address_by_id.get(self._to_combo.currentData())
        if not from_rec or not to_rec or not from_rec.country or not to_rec.country:
            return False
        return from_rec.country.upper() != to_rec.country.upper()

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
        contents_type = self._contents_type_combo.currentData()
        contents_explanation = self._contents_explanation_input.text().strip()
        if contents_type == "other" and not contents_explanation:
            raise ValueError("missing_contents_explanation")

        signer = self._customs_signer_input.text().strip()
        if not signer or not self._customs_certify_checkbox.isChecked():
            raise ValueError("missing_signer_or_certify")

        restriction_type = self._restriction_type_combo.currentData()
        restriction_comments = self._restriction_comments_input.text().strip()
        if restriction_type != "none" and not restriction_comments:
            raise ValueError("missing_restriction_comments")

        items = []
        for row in range(self._customs_items_table.rowCount()):
            description = self._customs_items_table.cellWidget(row, 0).text().strip()
            origin_combo = self._customs_items_table.cellWidget(row, 5)
            origin_country = origin_combo.currentData()
            if not description or not origin_country:
                raise ValueError("incomplete_customs_item")
            items.append(
                {
                    "description": description,
                    "quantity": self._customs_items_table.cellWidget(row, 1).value(),
                    "value": self._customs_items_table.cellWidget(row, 2).value(),
                    "weight": self._customs_items_table.cellWidget(row, 3).value(),
                    "hs_tariff_number": self._customs_items_table.cellWidget(row, 4).text().strip() or None,
                    "origin_country": origin_country,
                    "currency": "USD",
                }
            )
        if not items:
            raise ValueError("no_customs_items")

        customs_info = {
            "contents_type": contents_type,
            "restriction_type": restriction_type,
            "non_delivery_option": self._non_delivery_combo.currentData(),
            "customs_certify": True,
            "customs_signer": signer,
            "customs_items": items,
            # Exemption citation for the (typical) case of an export value under
            # $2,500 — the threshold above which a real EEI filing is required.
            # Without some eel_pfc value, some carriers reject the label outright.
            "eel_pfc": "NOEEI 30.37(a)",
        }
        if contents_type == "other":
            customs_info["contents_explanation"] = contents_explanation
        if restriction_type != "none":
            customs_info["restriction_comments"] = restriction_comments
        return customs_info

    def _build_rates_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.rates_group"))
        rate_columns = [
            tr("create_shipment.col_carrier_service"),
            tr("create_shipment.col_rate"),
            tr("create_shipment.col_est_days"),
            "",
        ]
        self._rates_table = QTableWidget(0, _RATE_COLUMN_COUNT)
        self._rates_table.setHorizontalHeaderLabels(rate_columns)
        header = self._rates_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Column 0 holds a cell widget, and ResizeToContents measures the
        # delegate rather than the widget — it would truncate longer service
        # names. Width is set from the widgets themselves once rows are built.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._rates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._rates_table.verticalHeader().setVisible(False)
        # Sized to fit every row (see _resize_rates_table_to_content) instead
        # of scrolling internally — the outer QScrollArea handles overflow.
        self._rates_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._quote_only_note = QLabel(tr("create_shipment.zip_mode_note"))
        self._quote_only_note.setWordWrap(True)
        self._quote_only_note.setStyleSheet(f"color: {TEXT_MUTED};")
        self._quote_only_note.setVisible(False)

        layout = QVBoxLayout()
        layout.addWidget(self._rates_table)
        layout.addWidget(self._quote_only_note)
        group.setLayout(layout)
        return group

    def _build_rate_identity_cell(self, rate, *, cheapest: bool, fastest: bool) -> QWidget:
        """One cell holding the carrier chip, the service name, and any
        cheapest/fastest marker — the column that used to be three."""
        cell = QWidget()
        row = QHBoxLayout(cell)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)
        row.addWidget(carrier_chip(getattr(rate, "carrier", "")))
        row.addWidget(QLabel(getattr(rate, "service", "") or "—"))
        if cheapest:
            row.addWidget(badge(tr("create_shipment.badge_cheapest")))
        if fastest:
            row.addWidget(badge(tr("create_shipment.badge_fastest"), tone="muted"))
        row.addStretch(1)
        return cell

    def _resize_rates_table_to_content(self) -> None:
        """Size rows, the identity column, and the table itself around the
        cell *widgets*.

        Qt's resizeRowsToContents/ResizeToContents measure the item delegate,
        which knows nothing about a cell widget — left to itself the table
        clips the carrier chip vertically and truncates longer service names
        horizontally.
        """
        table = self._rates_table
        table.resizeRowsToContents()

        identity_width = 0
        total_height = table.horizontalHeader().height() + 2 * table.frameWidth()
        for row in range(table.rowCount()):
            widget_height = 0
            for col in range(table.columnCount()):
                widget = table.cellWidget(row, col)
                if widget is None:
                    continue
                hint = widget.sizeHint()
                widget_height = max(widget_height, hint.height())
                if col == 0:
                    identity_width = max(identity_width, hint.width())
            # +6 so the Buy button isn't flush against the row borders.
            if widget_height + 6 > table.rowHeight(row):
                table.setRowHeight(row, widget_height + 6)
            total_height += table.rowHeight(row)

        if identity_width:
            table.setColumnWidth(0, identity_width + 12)
        table.setFixedHeight(total_height + 2)

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
            display = f"{rec.label or rec.name or rec.id} — {rec.city}, {rec.state}"
            self._from_combo.addItem(display, rec.id)
            self._to_combo.addItem(display, rec.id)
        self._update_customs_visibility()

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
            weight=self._weight_input.value(),
            reference=self._reference_input.text().strip(),
            customs_info=customs_info,
        )
        if isinstance(package_data, tuple) and package_data[0] == "predefined":
            params["predefined_package"] = package_data[1].name
        else:
            params["length"] = self._length_input.value()
            params["width"] = self._width_input.value()
            params["height"] = self._height_input.value()
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
            weight=self._weight_input.value(),
        )
        package_data = self._package_combo.currentData()
        if isinstance(package_data, tuple) and package_data[0] == "predefined":
            params["predefined_package"] = package_data[1].name
        else:
            params["length"] = self._length_input.value()
            params["width"] = self._width_input.value()
            params["height"] = self._height_input.value()

        self._quote_only = True
        self._pending_task = run_async(lambda: create_rate_quote(**params), self)
        self._pending_task.succeeded.connect(self._on_rates_received)
        self._pending_task.failed.connect(self._on_rates_failed)

    def _on_rates_received(self, shipment) -> None:
        self._get_rates_btn.setEnabled(True)
        self._get_rates_btn.setText(tr("create_shipment.get_rates_button"))
        self._current_shipment = shipment

        rates = sorted(getattr(shipment, "rates", None) or [], key=_rate_sort_key)
        cheapest_id = rates[0].id if rates else None
        fastest_id = _fastest_rate_id(rates)

        self._quote_only_note.setVisible(self._quote_only)
        self._rates_table.setRowCount(len(rates))
        for row, rate in enumerate(rates):
            self._rates_table.setCellWidget(
                row,
                0,
                self._build_rate_identity_cell(
                    rate,
                    cheapest=rate.id == cheapest_id,
                    fastest=rate.id == fastest_id,
                ),
            )
            self._rates_table.setItem(row, 1, QTableWidgetItem(_format_price(rate)))
            self._rates_table.setItem(row, 2, QTableWidgetItem(_format_delivery(rate)))

            buy_btn = QPushButton(tr("create_shipment.buy_button"))
            if self._quote_only:
                # A postal-code quote has no deliverable address, so EasyPost
                # would reject the purchase. Disable rather than hide, so the
                # reason is discoverable instead of the button just vanishing.
                buy_btn.setEnabled(False)
                buy_btn.setToolTip(tr("create_shipment.buy_needs_full_address"))
            else:
                buy_btn.clicked.connect(partial(self._on_buy_clicked, rate))
            self._rates_table.setCellWidget(row, _RATE_COLUMN_COUNT - 1, buy_btn)

        self._resize_rates_table_to_content()

        if not rates:
            QMessageBox.information(
                self, tr("create_shipment.no_rates_title"), tr("create_shipment.no_rates_body")
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
        if not confirm_if_production(self, description):
            return

        shipment_id = self._current_shipment.id
        rate_id = rate.id
        self._pending_task = run_async(lambda: buy_shipment(shipment_id, rate_id), self)
        self._pending_task.succeeded.connect(self._on_bought)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("create_shipment.purchase_error_body", error=format_api_error(exc))
            )
        )

    def _on_bought(self, shipment) -> None:
        self._current_shipment = shipment
        save_shipment_locally(shipment)

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
