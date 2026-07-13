"""Schedule a carrier pickup for one or more purchased shipments."""

from functools import partial

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateTimeEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.services.addresses import list_addresses
from app.services.pickups import buy_pickup, cancel_pickup, create_pickup, list_pickups, save_pickup_locally
from app.services.shipments import list_shipments
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.purchase_confirm import confirm_if_production

_RATE_COLUMNS = ["Carrier", "Service", "Rate", "Currency", ""]
_PICKUP_COLUMNS = ["Address", "Window start", "Window end", "Status", ""]


class PickupsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._current_pickup = None
        self._current_shipment_ids: list[str] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('pickups.title')}</h2>"))
        layout.addWidget(self._build_form_group())
        layout.addWidget(self._build_rates_group())
        layout.addWidget(self._build_scheduled_group(), stretch=1)

        self.refresh_choices()
        self.refresh_scheduled()

    def _build_form_group(self) -> QGroupBox:
        group = QGroupBox(tr("pickups.form_group_title"))
        form = QFormLayout()

        self._address_combo = QComboBox()
        self._shipments_list = QListWidget()
        self._shipments_list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        self._shipments_list.setMaximumHeight(120)

        self._min_datetime = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self._min_datetime.setCalendarPopup(True)
        self._max_datetime = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600 * 4))
        self._max_datetime.setCalendarPopup(True)

        self._instructions_input = QLineEdit()
        self._reference_input = QLineEdit()

        reload_btn = QPushButton(tr("pickups.reload_button"))
        reload_btn.clicked.connect(self.refresh_choices)

        form.addRow(tr("pickups.address_label"), self._address_combo)
        form.addRow(tr("pickups.shipments_label"), self._shipments_list)
        form.addRow(tr("pickups.earliest_time_label"), self._min_datetime)
        form.addRow(tr("pickups.latest_time_label"), self._max_datetime)
        form.addRow(tr("pickups.instructions_label"), self._instructions_input)
        form.addRow(tr("pickups.reference_label"), self._reference_input)
        form.addRow(reload_btn)

        self._request_btn = QPushButton(tr("pickups.request_rates_button"))
        self._request_btn.clicked.connect(self._on_request_clicked)

        group_layout = QVBoxLayout()
        group_layout.addLayout(form)
        group_layout.addWidget(self._request_btn)
        group.setLayout(group_layout)
        return group

    def _build_rates_group(self) -> QGroupBox:
        group = QGroupBox(tr("pickups.rates_group_title"))
        self._rates_table = QTableWidget(0, len(_RATE_COLUMNS))
        self._rates_table.setHorizontalHeaderLabels([
            tr("pickups.rate_col_carrier"),
            tr("pickups.rate_col_service"),
            tr("pickups.rate_col_rate"),
            tr("pickups.rate_col_currency"),
            "",
        ])
        self._rates_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._rates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout()
        layout.addWidget(self._rates_table)
        group.setLayout(layout)
        return group

    def _build_scheduled_group(self) -> QGroupBox:
        group = QGroupBox(tr("pickups.scheduled_group_title"))
        self._scheduled_table = QTableWidget(0, len(_PICKUP_COLUMNS))
        self._scheduled_table.setHorizontalHeaderLabels([
            tr("pickups.pickup_col_address"),
            tr("pickups.pickup_col_window_start"),
            tr("pickups.pickup_col_window_end"),
            tr("pickups.pickup_col_status"),
            "",
        ])
        self._scheduled_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._scheduled_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout()
        layout.addWidget(self._scheduled_table)
        group.setLayout(layout)
        return group

    def refresh_choices(self) -> None:
        self._address_combo.clear()
        for rec in list_addresses():
            self._address_combo.addItem(
                f"{rec.label or rec.name or rec.id} — {rec.city}, {rec.state}", rec.id
            )

        self._shipments_list.clear()
        for rec in list_shipments():
            if not rec.tracking_code:
                continue
            item = QListWidgetItem(f"{rec.tracking_code} — {rec.carrier} {rec.service}")
            item.setData(1000, rec.id)
            self._shipments_list.addItem(item)

    def _on_request_clicked(self) -> None:
        address_id = self._address_combo.currentData()
        selected_items = self._shipments_list.selectedItems()
        if not address_id or not selected_items:
            QMessageBox.warning(
                self, tr("pickups.missing_info_title"), tr("pickups.missing_info_body")
            )
            return

        shipment_ids = [item.data(1000) for item in selected_items]
        params = dict(
            address_id=address_id,
            shipment_ids=shipment_ids,
            min_datetime=self._min_datetime.dateTime().toString("yyyy-MM-ddTHH:mm:ss"),
            max_datetime=self._max_datetime.dateTime().toString("yyyy-MM-ddTHH:mm:ss"),
            instructions=self._instructions_input.text().strip(),
            reference=self._reference_input.text().strip(),
        )

        self._request_btn.setEnabled(False)
        self._current_shipment_ids = shipment_ids
        self._pending_task = run_async(lambda: create_pickup(**params), self)
        self._pending_task.succeeded.connect(self._on_pickup_created)
        self._pending_task.failed.connect(self._on_request_failed)

    def _on_pickup_created(self, pickup) -> None:
        self._request_btn.setEnabled(True)
        self._current_pickup = pickup

        rates = getattr(pickup, "pickup_rates", None) or getattr(pickup, "rates", None) or []
        self._rates_table.setRowCount(len(rates))
        for row, rate in enumerate(rates):
            values = [
                getattr(rate, "carrier", ""),
                getattr(rate, "service", ""),
                getattr(rate, "rate", ""),
                getattr(rate, "currency", ""),
            ]
            for col, value in enumerate(values):
                self._rates_table.setItem(row, col, QTableWidgetItem(value))
            buy_btn = QPushButton(tr("pickups.buy_button"))
            buy_btn.clicked.connect(partial(self._on_buy_clicked, rate))
            self._rates_table.setCellWidget(row, len(_RATE_COLUMNS) - 1, buy_btn)

        if not rates:
            QMessageBox.information(
                self, tr("pickups.requested_title"),
                tr("pickups.no_rates_body"),
            )

    def _on_request_failed(self, exc: Exception) -> None:
        self._request_btn.setEnabled(True)
        QMessageBox.critical(self, tr("common.error"), tr("pickups.request_failed_body", error=exc))

    def _on_buy_clicked(self, rate) -> None:
        if not self._current_pickup:
            return
        carrier = getattr(rate, "carrier", "")
        service = getattr(rate, "service", "")
        if not confirm_if_production(
            self, tr("pickups.confirm_buy_body", carrier=carrier, service=service)
        ):
            return

        pickup_id = self._current_pickup.id
        self._pending_task = run_async(
            lambda: buy_pickup(pickup_id, carrier, service), self
        )
        self._pending_task.succeeded.connect(self._on_pickup_bought)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("pickups.buy_failed_body", error=exc)
            )
        )

    def _on_pickup_bought(self, pickup) -> None:
        save_pickup_locally(pickup, self._current_shipment_ids)
        self.refresh_scheduled()
        QMessageBox.information(self, tr("pickups.booked_title"), tr("pickups.booked_body"))

    def refresh_scheduled(self) -> None:
        records = list_pickups()
        self._scheduled_table.setRowCount(len(records))
        for row, rec in enumerate(records):
            values = [
                rec.address or "",
                rec.min_datetime or "",
                rec.max_datetime or "",
                rec.status or "",
            ]
            for col, value in enumerate(values):
                self._scheduled_table.setItem(row, col, QTableWidgetItem(value))

            if rec.status not in ("canceled", "cancelled"):
                cancel_btn = QPushButton(tr("pickups.cancel_button"))
                cancel_btn.clicked.connect(partial(self._on_cancel_clicked, rec.id))
                self._scheduled_table.setCellWidget(row, len(_PICKUP_COLUMNS) - 1, cancel_btn)

    def _on_cancel_clicked(self, pickup_id: str) -> None:
        if (
            QMessageBox.question(
                self, tr("pickups.cancel_confirm_title"), tr("pickups.cancel_confirm_body")
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._pending_task = run_async(lambda: cancel_pickup(pickup_id), self)
        self._pending_task.succeeded.connect(
            lambda pickup: (save_pickup_locally(pickup, []), self.refresh_scheduled())
        )
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("pickups.cancel_failed_body", error=exc)
            )
        )
