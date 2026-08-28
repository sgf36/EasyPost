"""Batch shipments: import a CSV of recipients, validate, then bulk buy.

Note "bulk buy", not "bulk rate": EasyPost does not rate a batch. Batch
shipments come back with ``rates: []`` and ``selected_rate: None``, and
``batch.buy`` takes no body, so there is nothing to choose at purchase time. The
carrier and service are declared up front through the ServicePicker instead.
"""

import webbrowser
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
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

from app.core.customs import is_international
from app.core.errors import format_api_error
from app.core.review_prompt import mark_session_friction, note_successful_shipment
from app.core.webhook_manager import webhook_manager
from app.i18n import tr
from app.services.addresses import address_choice_label, list_addresses
from app.services.batches import (
    batch_failure_messages,
    batch_label_urls,
    bought_shipment_ids,
    buy_batch,
    create_batch,
    generate_batch_label,
    parse_import,
    quoted_services,
    rate_representative_row,
    retrieve_batch,
    revalidate,
    save_batch_locally,
    record_batch_shipments,
    write_csv_template,
    write_xlsx_template,
)
from app.services.label_sheets import build_combined_labels
from app.services.packages import predefined_package_choices
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.print_sheet_dialog import PrintSheetDialog
from app.ui.widgets.purchase_confirm import confirm_if_production
from app.ui.widgets.review_nudge import schedule_review_prompt
from app.ui.widgets.service_picker import ServicePicker

# States EasyPost is still working through. Batch creation and purchase are both
# asynchronous, so the app polls until the state settles rather than leaving the
# user to press Refresh and guess.
TRANSITIONAL_STATES = {"creating", "purchasing", "label_generating"}
POLL_INTERVAL_MS = 3000


class BatchView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._poll_task = None
        self._labels_task = None
        self._label_urls = []
        self._csv_path = None
        self._parsed_rows = []
        self._current_batch = None
        # The combined-label prompt must fire once, not on every poll tick.
        self._label_prompt_shown = False

        self._service_picker = ServicePicker(self)
        self._service_picker.changed.connect(self._update_create_enabled)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_once)

        # Instant refresh when a batch event is pushed, where the user has the
        # webhook feature enabled. Polling stays on regardless — the tunnel is
        # optional and can fail — so this only shortens the wait.
        webhook_manager.batch_updated.connect(self._on_batch_event)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('batch_shipments.title')}</h2>"))
        layout.addWidget(self._build_import_group())
        layout.addWidget(self._build_preview_group(), stretch=1)
        layout.addWidget(self._build_customs_group())
        layout.addWidget(self._service_picker)
        layout.addWidget(self._build_batch_group())

        self._from_combo.currentIndexChanged.connect(self._on_from_address_changed)
        self.refresh_address_choices()
        self._service_picker.load_catalogue()

    @staticmethod
    def _save_template_filter() -> str:
        # Extensions stay literal; only the label words are translated.
        return (
            f"{tr('batch_shipments.filter_excel')} (*.xlsx);;"
            f"{tr('batch_shipments.filter_csv')} (*.csv)"
        )

    @staticmethod
    def _import_filter() -> str:
        return (
            f"{tr('batch_shipments.filter_spreadsheets')} (*.xlsx *.csv);;"
            f"{tr('batch_shipments.filter_excel')} (*.xlsx);;"
            f"{tr('batch_shipments.filter_csv')} (*.csv)"
        )

    def _build_import_group(self) -> QGroupBox:
        group = QGroupBox(tr("batch_shipments.import_group_title"))
        row = QHBoxLayout()

        self._from_combo = QComboBox()
        # A combo will not shrink below the width of its longest address unless
        # told it may, so in German — where "Vorlage herunterladen" and
        # "Datei wählen…" are half again as long as their English labels — the
        # row overflowed and cut the second button off the edge of the window.
        # The address is the elastic part here; the buttons are not.
        self._from_combo.setMinimumContentsLength(12)
        self._from_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
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

        # Rating belongs here rather than beside Create batch: it is a question
        # about the parcels just imported, and its answer narrows the carrier
        # and service list below. It cannot be folded into Create batch, which
        # is the opposite order — a batch is never rated, so its carrier and
        # service must already be chosen before it is created.
        self._get_rates_btn = QPushButton(tr("create_shipment.get_rates_button"))
        self._get_rates_btn.setEnabled(False)
        self._get_rates_btn.clicked.connect(self._on_get_rates)

        button_row = QHBoxLayout()
        button_row.addWidget(self._summary_label, stretch=1)
        button_row.addWidget(self._get_rates_btn)

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(self._preview_table)
        group.setLayout(layout)
        return group

    def _on_get_rates(self) -> None:
        """Rate the first valid row to see what this route actually supports."""
        from_id = self._from_combo.currentData()
        row = next((r for r in self._parsed_rows if r.is_valid), None)
        if not from_id or row is None:
            return

        self._get_rates_btn.setEnabled(False)
        self._rates_task = run_async(
            lambda: rate_representative_row(
                from_id,
                row,
                from_country=self._from_country(),
                declaration=self._declaration(),
            ),
            self,
        )
        self._rates_task.succeeded.connect(
            lambda shipment: self._on_rate_preview_received(shipment, row.line_number)
        )
        self._rates_task.failed.connect(
            lambda exc: (
                self._get_rates_btn.setEnabled(True),
                QMessageBox.critical(
                    self, tr("common.error"),
                    tr("batch_shipments.create_failed_body", error=format_api_error(exc)),
                ),
            )
        )

    def _on_rate_preview_received(self, shipment, line_number: int) -> None:
        self._get_rates_btn.setEnabled(True)
        quotes = quoted_services(shipment)
        if not quotes:
            # Worth saying out loud. No rates for the representative parcel
            # means no service will carry it, and creating the batch anyway
            # produces one that cannot be bought.
            QMessageBox.information(
                self,
                tr("create_shipment.no_rates_title"),
                tr("create_shipment.no_rates_body"),
            )
            return
        self._service_picker.set_quoted_services(quotes, line_number)

    def _build_customs_group(self) -> QGroupBox:
        """Declaration-level customs fields, shared by every row in the batch.

        Deliberately built from the existing ``create_shipment.*`` strings
        rather than new ones: it is the same declaration, asked for in the same
        words, and reusing them means this needs no new translation in any of
        the fifty locales.

        Per-item detail — description, value, tariff code — is per row and comes
        from the spreadsheet, since it differs for every parcel. What is asked
        for here is only what is the same for all of them.
        """
        group = QGroupBox(tr("create_shipment.customs_group_title"))
        self._customs_group = group
        group.setVisible(False)

        self._contents_type_combo = QComboBox()
        for value, key in (
            ("merchandise", "create_shipment.contents_type_merchandise"),
            ("documents", "create_shipment.contents_type_documents"),
            ("gift", "create_shipment.contents_type_gift"),
            ("sample", "create_shipment.contents_type_sample"),
            ("returned_goods", "create_shipment.contents_type_returned_goods"),
        ):
            self._contents_type_combo.addItem(tr(key), value)

        self._non_delivery_combo = QComboBox()
        for value, key in (
            ("return", "create_shipment.non_delivery_return"),
            ("abandon", "create_shipment.non_delivery_abandon"),
        ):
            self._non_delivery_combo.addItem(tr(key), value)

        self._customs_signer_input = QLineEdit()
        self._customs_certify_checkbox = QCheckBox(tr("create_shipment.customs_certify_checkbox"))
        self._customs_signer_input.textChanged.connect(self._update_create_enabled)
        self._customs_certify_checkbox.stateChanged.connect(self._update_create_enabled)

        form = QFormLayout()
        # A QLabel that does not wrap reports its whole single line as its
        # minimum width, and a QScrollArea with setWidgetResizable honours that
        # — so this one sentence set the minimum width of the entire Batch page.
        # In English it fitted; in German it is half again as long, and the page
        # grew a horizontal scrollbar that pushed "Datei wählen…" off the edge.
        customs_intro = QLabel(tr("create_shipment.customs_intro"))
        customs_intro.setWordWrap(True)
        form.addRow(customs_intro)
        form.addRow(tr("create_shipment.contents_type_label"), self._contents_type_combo)
        form.addRow(tr("create_shipment.non_delivery_label"), self._non_delivery_combo)
        form.addRow(tr("create_shipment.customs_signer_label"), self._customs_signer_input)
        form.addRow(self._customs_certify_checkbox)
        group.setLayout(form)
        return group

    def _declaration(self) -> dict | None:
        """The batch-level half of the declaration, or None when not needed."""
        if not self._has_international_rows():
            return None
        return {
            "customs_signer": self._customs_signer_input.text().strip(),
            "contents_type": self._contents_type_combo.currentData(),
            "non_delivery_option": self._non_delivery_combo.currentData(),
        }

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

        self._export_sheet_btn = QPushButton(tr("batch_shipments.export_sheet_button"))
        self._export_sheet_btn.setEnabled(False)
        self._export_sheet_btn.clicked.connect(self._on_export_sheet)

        self._status_label = QLabel(tr("batch_shipments.no_batch_label"))
        self._status_label.setWordWrap(True)

        # Five buttons on one row is an English-only layout. Their German
        # labels — "Batch erstellen (Preise abrufen)", "Alle Sendungen im Batch
        # kaufen", "Kombinierte Etiketten erzeugen" — need 1320 points side by
        # side, against roughly 1245 available in a 1440-point window. That one
        # row set the minimum width of the whole page and gave it a horizontal
        # scrollbar, which is why controls two sections *above* it were being
        # cut off the right-hand edge.
        #
        # A grid wraps instead: one row where there is space, three-plus-two
        # where there is not, in any language.
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for index, button in enumerate((
            self._create_batch_btn,
            self._refresh_status_btn,
            self._buy_batch_btn,
            self._generate_labels_btn,
            self._export_sheet_btn,
        )):
            row.addWidget(button, index // 3, index % 3)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._status_label)
        group.setLayout(layout)
        return group

    def refresh_address_choices(self) -> None:
        self._from_combo.clear()
        self._address_by_id = {}
        for rec in list_addresses():
            self._address_by_id[rec.id] = rec
            self._from_combo.addItem(
                address_choice_label(rec), rec.id
            )
        self._on_from_address_changed()

    def _from_country(self) -> str | None:
        rec = self._address_by_id.get(self._from_combo.currentData())
        return getattr(rec, "country", None)

    def _has_international_rows(self) -> bool:
        country = self._from_country()
        return any(
            is_international(country, r.fields.get("to_country"))
            for r in self._parsed_rows
        )

    def _on_from_address_changed(self) -> None:
        """Re-check the loaded rows against the new sender.

        Switching sender can turn a whole file international on its own, and
        leaving a stale "5 valid, 0 with errors" on screen is how an
        undeclarable batch reached a live account.
        """
        if self._parsed_rows:
            self._parsed_rows = revalidate(self._parsed_rows, self._from_country())
            self._render_preview()
        self._customs_group.setVisible(self._has_international_rows())
        self._update_create_enabled()

    def _on_download_template(self) -> None:
        # Excel default: only a workbook can carry the package dropdown. CSV
        # stays available for anyone who prefers it (same columns, no dropdown).
        path, selected = QFileDialog.getSaveFileName(
            self,
            tr("batch_shipments.save_template_dialog_title"),
            "batch_template.xlsx",
            self._save_template_filter(),
        )
        if not path:
            return

        is_xlsx = path.lower().endswith(".xlsx") or "xlsx" in (selected or "").lower()
        if not is_xlsx:
            write_csv_template(path)
            QMessageBox.information(
                self, tr("batch_shipments.saved_title"), tr("batch_shipments.saved_body", path=path)
            )
            return

        # Fetch the carrier package list off the UI thread, then write the
        # workbook — the fetch may hit the network (falls back to cache).
        self._pending_task = run_async(
            lambda: (write_xlsx_template(path, predefined_package_choices()), path)[1], self
        )
        self._pending_task.succeeded.connect(
            lambda saved: QMessageBox.information(
                self, tr("batch_shipments.saved_title"), tr("batch_shipments.saved_body", path=saved)
            )
        )
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("batch_shipments.saved_failed_body", error=str(exc))
            )
        )

    def _on_browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("batch_shipments.choose_csv_dialog_title"), "", self._import_filter()
        )
        if not path:
            return
        try:
            self._parsed_rows = parse_import(path, self._from_country())
            self._csv_path = path
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("batch_shipments.invalid_csv_title"), str(exc))
            return

        self._customs_group.setVisible(self._has_international_rows())
        self._render_preview()

    def _render_preview(self) -> None:
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
            package = row.fields.get("predefined_package", "")
            if package:
                parcel_summary = f"{package} / {row.fields.get('weight','')}oz"
            else:
                parcel_summary = (
                    f"{row.fields.get('length','')}x{row.fields.get('width','')}"
                    f"x{row.fields.get('height','')} / {row.fields.get('weight','')}oz"
                )
            self._preview_table.setItem(row_idx, 0, QTableWidgetItem(str(row.line_number)))
            self._preview_table.setItem(row_idx, 1, QTableWidgetItem(to_summary))
            self._preview_table.setItem(row_idx, 2, QTableWidgetItem(parcel_summary))
            self._preview_table.setItem(row_idx, 3, QTableWidgetItem("; ".join(row.errors)))

        self._valid_row_count = valid_count
        self._update_create_enabled()

    def _update_create_enabled(self) -> None:
        """A batch can only be created once there is something to ship AND a
        service to ship it by — without the latter the batch is created and then
        cannot be bought at all, so the button stays disabled rather than
        producing a dead batch."""
        rows_ready = getattr(self, "_valid_row_count", 0) > 0
        # An international batch also needs its declaration signed. Without it
        # the batch is created and every label fails at purchase, so the button
        # stays disabled rather than producing a batch that cannot be bought —
        # the same reasoning as the service picker above.
        declaration = self._declaration()
        customs_ready = declaration is None or (
            bool(declaration["customs_signer"]) and self._customs_certify_checkbox.isChecked()
        )
        self._create_batch_btn.setEnabled(
            rows_ready and self._service_picker.is_complete() and customs_ready
        )
        # Rating needs a sender and a valid row, and an international one needs
        # its declaration too — the rated shipment carries the same customs_info
        # the batch will, or it would answer a different question.
        self._get_rates_btn.setEnabled(
            rows_ready and bool(self._from_combo.currentData()) and customs_ready
        )

    def _on_create_batch(self) -> None:
        from_id = self._from_combo.currentData()
        if not from_id:
            QMessageBox.warning(
                self, tr("batch_shipments.missing_address_title"), tr("batch_shipments.missing_address_body")
            )
            return

        selection = self._service_picker.selection()
        if selection is None:
            QMessageBox.warning(
                self,
                tr("batch_shipments.missing_service_title"),
                tr("batch_shipments.missing_service_body"),
            )
            return

        self._selection = selection
        self._create_batch_btn.setEnabled(False)
        self._label_prompt_shown = False
        self._label_urls = []
        self._export_sheet_btn.setEnabled(False)
        self._pending_task = run_async(
            lambda: create_batch(
                from_id,
                self._parsed_rows,
                carrier=selection.carrier,
                service=selection.service,
                delivery_confirmation=selection.delivery_confirmation,
                insurance=selection.insurance,
                from_country=self._from_country(),
                declaration=self._declaration(),
            ),
            self,
        )
        self._pending_task.succeeded.connect(self._on_batch_created)
        self._pending_task.failed.connect(
            lambda exc: (
                self._update_create_enabled(),
                QMessageBox.critical(
                    self, tr("common.error"), tr("batch_shipments.create_failed_body", error=format_api_error(exc))
                ),
            )
        )

    def _on_batch_created(self, batch) -> None:
        self._update_create_enabled()
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
                self, tr("common.error"), tr("batch_shipments.refresh_failed_body", error=format_api_error(exc))
            )
        )

    def _on_status_refreshed(self, batch) -> None:
        self._current_batch = batch
        save_batch_locally(batch, self._csv_path or "")
        self._update_status_label(batch)

    # -- polling -------------------------------------------------------------

    def _poll_once(self) -> None:
        """One background refresh per timer tick. Skipped while a previous poll
        is still in flight, so a slow response cannot stack up requests."""
        if not self._current_batch or self._poll_task is not None:
            return
        batch_id = self._current_batch.id
        self._poll_task = run_async(lambda: retrieve_batch(batch_id), self)
        self._poll_task.succeeded.connect(self._on_poll_result)
        # A transient network blip should not kill the poll loop or throw a
        # modal at the user; the next tick simply tries again.
        self._poll_task.failed.connect(lambda _exc: setattr(self, "_poll_task", None))

    def _on_poll_result(self, batch) -> None:
        self._poll_task = None
        self._on_status_refreshed(batch)

    def _on_batch_event(self, batch_id: str) -> None:
        """A pushed batch event for the batch currently on screen."""
        if self._current_batch and batch_id == self._current_batch.id:
            self._poll_once()

    def _sync_polling(self, state) -> None:
        if state in TRANSITIONAL_STATES:
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        elif self._poll_timer.isActive():
            self._poll_timer.stop()

    def _update_status_label(self, batch) -> None:
        state = getattr(batch, "state", None) or getattr(batch, "status", None)
        num_shipments = getattr(batch, "num_shipments", "?")
        text = tr(
            "batch_shipments.status_label",
            batch_id=batch.id,
            state=state,
            num_shipments=num_shipments,
        )
        # Per-shipment failures are reported inside the batch, never raised, so
        # without this a failed purchase looks like a state change and nothing
        # more. This is the message that explains *why* nothing was bought.
        failures = batch_failure_messages(batch)
        if failures:
            text += "\n" + "\n".join(failures)
        self._status_label.setText(text)

        self._sync_polling(state)

        self._buy_batch_btn.setEnabled(state in ("created",))
        # Enabled on `label_generated`, not `label_generating`: the latter means
        # EasyPost is still building the combined PDF, and asking for it again
        # mid-generation just restarts the wait.
        self._generate_labels_btn.setEnabled(state in ("purchased", "label_generated"))
        # Once bought, each shipment carries its own label — offer the print
        # sheet, though the URLs have to be fetched before it can be enabled.
        self._fetch_label_urls(batch)

        label_url = getattr(batch, "label_url", None)
        if label_url and not self._label_prompt_shown:
            self._label_prompt_shown = True
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
                # A failed bulk purchase sours the session; no review prompt after it.
                mark_session_friction(),
                QMessageBox.critical(
                    self, tr("common.error"), tr("batch_shipments.buy_failed_body", error=format_api_error(exc))
                ),
            )
        )

    def _on_batch_bought(self, batch) -> None:
        self._current_batch = batch
        save_batch_locally(batch, self._csv_path or "")
        self._update_status_label(batch)

        failures = batch_failure_messages(batch)
        if failures:
            # Purchase is asynchronous and reports per-shipment failures in the
            # batch body rather than raising, so "submitted" alone would be a
            # misleading thing to tell the user here.
            # Some shipments failed even though the call succeeded. That is
            # friction whatever the rest of the batch did, so it suppresses the
            # review prompt for the session.
            mark_session_friction()
            QMessageBox.warning(
                self,
                tr("batch_shipments.purchase_problems_title"),
                tr("batch_shipments.purchase_problems_body", details="\n".join(failures)),
            )
            return

        # Always record the shipments so a bulk purchase appears in History
        # like any other; the auto-track choice governs only the trackers.
        # Best effort: the labels are already bought, so failing to record
        # them locally must not be reported as a failed purchase.
        track = bool(getattr(self, "_selection", None) and self._selection.auto_track)
        # History refreshes when navigated to, as it does after a single
        # purchase, so there is nothing to signal here.
        self._pending_task = run_async(
            lambda: record_batch_shipments(batch, track=track), self
        )

        QMessageBox.information(
            self, tr("batch_shipments.purchased_title"), tr("batch_shipments.purchased_body")
        )

        # A clean bulk purchase is the strongest satisfaction moment the app has.
        # No-ops on builds with no storefront; every other gate is applied inside.
        note_successful_shipment()
        schedule_review_prompt(self)

    def _fetch_label_urls(self, batch) -> None:
        """Collect the per-shipment label URLs in the background.

        This is one request per shipment — the batch's own shipment entries are
        stubs with no postage label on them — so it must not run on the UI
        thread, and it only runs once the batch reports something bought."""
        if self._label_urls or not bought_shipment_ids(batch):
            return
        self._labels_task = run_async(lambda: batch_label_urls(batch), self)
        self._labels_task.succeeded.connect(self._on_label_urls)

    def _on_label_urls(self, urls) -> None:
        self._label_urls = list(urls or [])
        self._export_sheet_btn.setEnabled(bool(self._label_urls))

    def _on_export_sheet(self) -> None:
        urls = self._label_urls
        if not urls:
            QMessageBox.information(
                self,
                tr("batch_shipments.export_sheet_title"),
                tr("batch_shipments.export_no_labels"),
            )
            return
        PrintSheetDialog(urls, self).exec()

    def _on_generate_labels(self) -> None:
        """Combine the batch's labels into one PDF, one label per page.

        Composed locally from the per-shipment labels rather than through
        EasyPost's ``batch.label()`` merge, which corrupts raster labels: a
        real five-shipment Royal Mail batch came back as landscape US Letter
        pages each drawing the label twice, the address block covering the 1D
        barcode. See ``label_sheet.compose_label_pages``.

        The hosted merge is still the fallback for PDF/ZPL labels, which the
        local compositor cannot open.
        """
        if not self._current_batch:
            return
        if not self._label_urls:
            self._generate_labels_remote()
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("batch_shipments.generate_labels_button"),
            "combined-labels.pdf",
            tr("print_sheet.pdf_filter"),
        )
        if not path:
            return

        urls = list(self._label_urls)
        self._pending_task = run_async(lambda: build_combined_labels(urls), self)
        self._pending_task.succeeded.connect(lambda result: self._on_labels_combined(result, path))
        # A non-raster label set raises ValueError rather than returning
        # nothing, which is the signal to let EasyPost do the merge instead.
        self._pending_task.failed.connect(lambda _exc: self._generate_labels_remote())

    def _on_labels_combined(self, result, path: str) -> None:
        self._pending_task = None
        try:
            with open(path, "wb") as handle:
                handle.write(result.pdf)
        except OSError as exc:
            QMessageBox.critical(
                self,
                tr("common.error"),
                tr("batch_shipments.generate_labels_failed_body", error=str(exc)),
            )
            return
        if result.failed:
            QMessageBox.warning(
                self,
                tr("batch_shipments.export_sheet_title"),
                tr("print_sheet.some_failed", failed=len(result.failed)),
            )
        webbrowser.open(Path(path).as_uri())

    def _generate_labels_remote(self) -> None:
        """Ask EasyPost to merge the labels — the fallback path."""
        if not self._current_batch:
            return
        batch_id = self._current_batch.id
        self._pending_task = run_async(lambda: generate_batch_label(batch_id), self)
        self._pending_task.succeeded.connect(self._on_batch_bought)
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(
                self, tr("common.error"), tr("batch_shipments.generate_labels_failed_body", error=format_api_error(exc))
            )
        )
