"""Batch shipments: import a CSV of recipients, validate, then bulk rate/buy."""

import webbrowser

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.services.addresses import list_addresses
from app.services.batches import (
    buy_batch,
    create_batch,
    generate_batch_label,
    parse_csv,
    retrieve_batch,
    save_batch_locally,
    write_csv_template,
)
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.purchase_confirm import confirm_if_production


class BatchView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._csv_path = None
        self._parsed_rows = []
        self._current_batch = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('batch_shipments.title')}</h2>"))
        layout.addWidget(self._build_import_group())
        layout.addWidget(self._build_preview_group(), stretch=1)
        layout.addWidget(self._build_batch_group())

        self.refresh_address_choices()

    def _build_import_group(self) -> QGroupBox:
        group = QGroupBox(tr("batch_shipments.import_group_title"))
        row = QHBoxLayout()

        self._from_combo = QComboBox()
        template_btn = QPushButton(tr("batch_shipments.download_template_button"))
        template_btn.clicked.connect(self._on_download_template)
        browse_btn = QPushButton(tr("batch_shipments.choose_csv_button"))
        browse_btn.clicked.connect(self._on_browse_csv)

        row.addWidget(QLabel(tr("batch_shipments.ship_from_label")))
        row.addWidget(self._from_combo, stretch=1)
        row.addWidget(template_btn)
        row.addWidget(browse_btn)
        group.setLayout(row)
        return group

    def _build_preview_group(self) -> QGroupBox:
        group = QGroupBox(tr("batch_shipments.preview_group_title"))
        self._preview_table = QTableWidget(0, 4)
        self._preview_table.setHorizontalHeaderLabels([
            tr("batch_shipments.col_line"),
            tr("batch_shipments.col_to"),
            tr("batch_shipments.col_parcel"),
            tr("batch_shipments.col_errors"),
        ])
        self._preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._summary_label = QLabel(tr("batch_shipments.no_csv_loaded"))

        layout = QVBoxLayout()
        layout.addWidget(self._summary_label)
        layout.addWidget(self._preview_table)
        group.setLayout(layout)
        return group

    def _build_batch_group(self) -> QGroupBox:
        group = QGroupBox(tr("batch_shipments.batch_group_title"))
        self._create_batch_btn = QPushButton(tr("batch_shipments.create_batch_button"))
        self._create_batch_btn.setEnabled(False)
        self._create_batch_btn.clicked.connect(self._on_create_batch)

        self._refresh_status_btn = QPushButton(tr("batch_shipments.refresh_status_button"))
        self._refresh_status_btn.setEnabled(False)
        self._refresh_status_btn.clicked.connect(self._on_refresh_status)

        self._buy_batch_btn = QPushButton(tr("batch_shipments.buy_batch_button"))
        self._buy_batch_btn.setEnabled(False)
        self._buy_batch_btn.clicked.connect(self._on_buy_batch)

        self._generate_labels_btn = QPushButton(tr("batch_shipments.generate_labels_button"))
        self._generate_labels_btn.setEnabled(False)
        self._generate_labels_btn.clicked.connect(self._on_generate_labels)

        self._status_label = QLabel(tr("batch_shipments.no_batch_label"))
        self._status_label.setWordWrap(True)

        row = QHBoxLayout()
        row.addWidget(self._create_batch_btn)
        row.addWidget(self._refresh_status_btn)
        row.addWidget(self._buy_batch_btn)
        row.addWidget(self._generate_labels_btn)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._status_label)
        group.setLayout(layout)
        return group

    def refresh_address_choices(self) -> None:
        self._from_combo.clear()
        for rec in list_addresses():
            self._from_combo.addItem(
                f"{rec.label or rec.name or rec.id} — {rec.city}, {rec.state}", rec.id
            )

    def _on_download_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("batch_shipments.save_template_dialog_title"),
            "batch_template.csv",
            tr("batch_shipments.csv_filter"),
        )
        if path:
            write_csv_template(path)
            QMessageBox.information(
                self, tr("batch_shipments.saved_title"), tr("batch_shipments.saved_body", path=path)
            )

    def _on_browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("batch_shipments.choose_csv_dialog_title"), "", tr("batch_shipments.csv_filter")
        )
        if not path:
            return
        try:
            self._parsed_rows = parse_csv(path)
            self._csv_path = path
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("batch_shipments.invalid_csv_title"), str(exc))
            return

        valid_count = sum(1 for r in self._parsed_rows if r.is_valid)
        self._summary_label.setText(
            tr(
                "batch_shipments.rows_loaded_summary",
                total=len(self._parsed_rows),
                valid=valid_count,
                invalid=len(self._parsed_rows) - valid_count,
            )
        )

        self._preview_table.setRowCount(len(self._parsed_rows))
        for row_idx, row in enumerate(self._parsed_rows):
            to_summary = f"{row.fields.get('to_name', '')}, {row.fields.get('to_city', '')}"
            parcel_summary = (
                f"{row.fields.get('length','')}x{row.fields.get('width','')}"
                f"x{row.fields.get('height','')} / {row.fields.get('weight','')}oz"
            )
            self._preview_table.setItem(row_idx, 0, QTableWidgetItem(str(row.line_number)))
            self._preview_table.setItem(row_idx, 1, QTableWidgetItem(to_summary))
            self._preview_table.setItem(row_idx, 2, QTableWidgetItem(parcel_summary))
            self._preview_table.setItem(row_idx, 3, QTableWidgetItem("; ".join(row.errors)))

        self._create_batch_btn.setEnabled(valid_count > 0)

    def _on_create_batch(self) -> None:
        from_id = self._from_combo.currentData()
        if not from_id:
            QMessageBox.warning(
                self, tr("batch_shipments.missing_address_title"), tr("batch_shipments.missing_address_body")
            )
            return

        self._create_batch_btn.setEnabled(False)
        self._pending_task = run_async(
            lambda: create_batch(from_id, self._parsed_rows), self
        )
        self._pending_task.succeeded.connect(self._on_batch_created)
        self._pending_task.failed.connect(
            lambda exc: (
                self._create_batch_btn.setEnabled(True),
                QMessageBox.critical(
                    self, tr("common.error"), tr("batch_shipments.create_failed_body", error=exc)
                ),
            )
        )

    def _on_batch_created(self, batch) -> None:
        self._create_batch_btn.setEnabled(True)
        self._current_batch = batch
        save_batch_locally(batch, self._csv_path or "")
        self._refresh_status_btn.setEnabled(True)
        self._update_status_label(batch)

    def _on_refresh_status(self) -> None:
        if not self._current_batch:
            return
        batch_id = self._current_batch.id
        self._pending_task = run_async(lambda: retrieve_batch(batch_id), self)
        self._pending_task.succeeded.connect(self._on_status_refreshed)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("batch_shipments.refresh_failed_body", error=exc)
            )
        )

    def _on_status_refreshed(self, batch) -> None:
        self._current_batch = batch
        save_batch_locally(batch, self._csv_path or "")
        self._update_status_label(batch)

    def _update_status_label(self, batch) -> None:
        state = getattr(batch, "state", None) or getattr(batch, "status", None)
        num_shipments = getattr(batch, "num_shipments", "?")
        self._status_label.setText(
            tr(
                "batch_shipments.status_label",
                batch_id=batch.id,
                state=state,
                num_shipments=num_shipments,
            )
        )

        self._buy_batch_btn.setEnabled(state in ("created",))
        self._generate_labels_btn.setEnabled(state in ("purchased", "label_generating"))

        label_url = getattr(batch, "label_url", None)
        if label_url:
            self._pending_label_url = label_url
            if (
                QMessageBox.question(
                    self, tr("batch_shipments.labels_ready_title"), tr("batch_shipments.labels_ready_body")
                )
                == QMessageBox.StandardButton.Yes
            ):
                webbrowser.open(label_url)

    def _on_buy_batch(self) -> None:
        if not self._current_batch:
            return
        if not confirm_if_production(
            self, tr("batch_shipments.confirm_buy_body")
        ):
            return
        batch_id = self._current_batch.id
        self._buy_batch_btn.setEnabled(False)
        self._pending_task = run_async(lambda: buy_batch(batch_id), self)
        self._pending_task.succeeded.connect(self._on_batch_bought)
        self._pending_task.failed.connect(
            lambda exc: (
                self._buy_batch_btn.setEnabled(True),
                QMessageBox.critical(
                    self, tr("common.error"), tr("batch_shipments.buy_failed_body", error=exc)
                ),
            )
        )

    def _on_batch_bought(self, batch) -> None:
        self._current_batch = batch
        save_batch_locally(batch, self._csv_path or "")
        self._update_status_label(batch)
        QMessageBox.information(
            self, tr("batch_shipments.purchased_title"), tr("batch_shipments.purchased_body")
        )

    def _on_generate_labels(self) -> None:
        if not self._current_batch:
            return
        batch_id = self._current_batch.id
        self._pending_task = run_async(lambda: generate_batch_label(batch_id), self)
        self._pending_task.succeeded.connect(self._on_batch_bought)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("batch_shipments.generate_labels_failed_body", error=exc)
            )
        )
