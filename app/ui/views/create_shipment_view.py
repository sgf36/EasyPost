"""Create a shipment, shop rates, buy a label, and save/open it."""

import webbrowser
from functools import partial

import requests
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.services.addresses import list_addresses
from app.services.shipments import buy_shipment, create_shipment, save_shipment_locally
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.purchase_confirm import confirm_if_production

_RATE_COLUMN_COUNT = 6


class CreateShipmentView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._current_shipment = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('create_shipment.title')}</h2>"))
        layout.addWidget(self._build_form_group())
        layout.addWidget(self._build_rates_group(), stretch=1)
        layout.addWidget(self._build_result_group())

        self.refresh_address_choices()

    def _build_form_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.details_group"))
        form = QFormLayout()

        self._from_combo = QComboBox()
        self._to_combo = QComboBox()
        refresh_btn = QPushButton(tr("create_shipment.reload_button"))
        refresh_btn.clicked.connect(self.refresh_address_choices)

        addr_row = QHBoxLayout()
        addr_row.addWidget(QLabel(tr("create_shipment.from_label")))
        addr_row.addWidget(self._from_combo, stretch=1)
        addr_row.addWidget(QLabel(tr("create_shipment.to_label")))
        addr_row.addWidget(self._to_combo, stretch=1)
        addr_row.addWidget(refresh_btn)

        self._length_input = self._spin(1, 1000, 6)
        self._width_input = self._spin(1, 1000, 6)
        self._height_input = self._spin(1, 1000, 6)
        self._weight_input = self._spin(0.1, 5000, 16)
        self._reference_input = QLineEdit()

        dims_row = QHBoxLayout()
        dims_row.addWidget(QLabel(tr("create_shipment.length_label")))
        dims_row.addWidget(self._length_input)
        dims_row.addWidget(QLabel(tr("create_shipment.width_label")))
        dims_row.addWidget(self._width_input)
        dims_row.addWidget(QLabel(tr("create_shipment.height_label")))
        dims_row.addWidget(self._height_input)
        dims_row.addWidget(QLabel(tr("create_shipment.weight_label")))
        dims_row.addWidget(self._weight_input)

        form.addRow(addr_row)
        form.addRow(dims_row)
        form.addRow(tr("create_shipment.reference_field"), self._reference_input)

        self._get_rates_btn = QPushButton(tr("create_shipment.get_rates_button"))
        self._get_rates_btn.clicked.connect(self._on_get_rates_clicked)

        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        group_layout.addWidget(self._get_rates_btn)
        group.setLayout(group_layout)
        return group

    @staticmethod
    def _spin(minimum: float, maximum: float, default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(default)
        return spin

    def _build_rates_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.rates_group"))
        rate_columns = [
            tr("create_shipment.col_carrier"),
            tr("create_shipment.col_service"),
            tr("create_shipment.col_rate"),
            tr("create_shipment.col_currency"),
            tr("create_shipment.col_est_days"),
            "",
        ]
        self._rates_table = QTableWidget(0, _RATE_COLUMN_COUNT)
        self._rates_table.setHorizontalHeaderLabels(rate_columns)
        self._rates_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._rates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout()
        layout.addWidget(self._rates_table)
        group.setLayout(layout)
        return group

    def _build_result_group(self) -> QGroupBox:
        group = QGroupBox(tr("create_shipment.result_group"))
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
        layout.addWidget(self._result_label)
        layout.addLayout(button_row)
        group.setLayout(layout)
        return group

    def refresh_address_choices(self) -> None:
        self._from_combo.clear()
        self._to_combo.clear()
        for rec in list_addresses():
            display = f"{rec.label or rec.name or rec.id} — {rec.city}, {rec.state}"
            self._from_combo.addItem(display, rec.id)
            self._to_combo.addItem(display, rec.id)

    def _on_get_rates_clicked(self) -> None:
        from_id = self._from_combo.currentData()
        to_id = self._to_combo.currentData()
        if not from_id or not to_id:
            QMessageBox.warning(
                self,
                tr("create_shipment.missing_addresses_title"),
                tr("create_shipment.missing_addresses_body"),
            )
            return

        self._get_rates_btn.setEnabled(False)
        self._get_rates_btn.setText(tr("create_shipment.fetching_rates_button"))

        params = dict(
            to_address_id=to_id,
            from_address_id=from_id,
            length=self._length_input.value(),
            width=self._width_input.value(),
            height=self._height_input.value(),
            weight=self._weight_input.value(),
            reference=self._reference_input.text().strip(),
        )
        self._pending_task = run_async(lambda: create_shipment(**params), self)
        self._pending_task.succeeded.connect(self._on_rates_received)
        self._pending_task.failed.connect(self._on_rates_failed)

    def _on_rates_received(self, shipment) -> None:
        self._get_rates_btn.setEnabled(True)
        self._get_rates_btn.setText(tr("create_shipment.get_rates_button"))
        self._current_shipment = shipment

        rates = getattr(shipment, "rates", None) or []
        self._rates_table.setRowCount(len(rates))
        for row, rate in enumerate(rates):
            values = [
                getattr(rate, "carrier", ""),
                getattr(rate, "service", ""),
                getattr(rate, "rate", ""),
                getattr(rate, "currency", ""),
                str(getattr(rate, "delivery_days", "") or ""),
            ]
            for col, value in enumerate(values):
                self._rates_table.setItem(row, col, QTableWidgetItem(value))

            buy_btn = QPushButton(tr("create_shipment.buy_button"))
            buy_btn.clicked.connect(partial(self._on_buy_clicked, rate))
            self._rates_table.setCellWidget(row, _RATE_COLUMN_COUNT - 1, buy_btn)

        if not rates:
            QMessageBox.information(
                self, tr("create_shipment.no_rates_title"), tr("create_shipment.no_rates_body")
            )

    def _on_rates_failed(self, exc: Exception) -> None:
        self._get_rates_btn.setEnabled(True)
        self._get_rates_btn.setText(tr("create_shipment.get_rates_button"))
        QMessageBox.critical(
            self, tr("common.error"), tr("create_shipment.get_rates_error_body", error=exc)
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
                self, tr("common.error"), tr("create_shipment.purchase_error_body", error=exc)
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
        else:
            self._result_label.setText(tr("create_shipment.purchased_no_label"))

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
                self, tr("common.error"), tr("create_shipment.save_label_error_body", error=exc)
            )
