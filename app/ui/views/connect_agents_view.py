"""Connect AI agents over MCP, and approve anything they want to spend.

Two halves. The top connects MCP clients; the bottom is the approval queue,
which is the only place a purchase an agent requested can actually happen.

In Store builds the whole feature is unavailable (see app/config.py:
MCP_SUPPORTED) and this page says so rather than silently hiding.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import MCP_SUPPORTED
from app.core import mcp_approvals, mcp_clients
from app.core.settings import load_settings, save_settings
from app.i18n import tr
from app.services.mcp_runner import execute_approved
from app.ui.theme import TEXT_MUTED
from app.ui.widgets.async_worker import run_async


class ConnectAgentsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_task = None
        self._client_rows: list[tuple] = []

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(QLabel(f"<h2>{tr('connect_agents.title')}</h2>"))

        if not MCP_SUPPORTED:
            layout.addWidget(self._build_unsupported_notice())
            layout.addStretch(1)
        else:
            layout.addWidget(self._build_enable_group())
            layout.addWidget(self._build_clients_group())
            layout.addWidget(self._build_manual_group())
            layout.addWidget(self._build_approvals_group())
            layout.addStretch(1)

            # The queue is written by a separate process, so poll rather than
            # relying on a signal that cannot cross the process boundary.
            self._timer = QTimer(self)
            self._timer.setInterval(4000)
            self._timer.timeout.connect(self.refresh_approvals)
            self._timer.start()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        if MCP_SUPPORTED:
            self.refresh()

    # ------------------------------------------------------------ store build

    def _build_unsupported_notice(self) -> QGroupBox:
        group = QGroupBox(tr("connect_agents.unsupported_title"))
        body = QLabel(tr("connect_agents.unsupported_body"))
        body.setWordWrap(True)
        link = QLabel(tr("connect_agents.unsupported_link"))
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addWidget(body)
        layout.addWidget(link)
        group.setLayout(layout)
        return group

    # ---------------------------------------------------------------- enable

    def _build_enable_group(self) -> QGroupBox:
        group = QGroupBox(tr("connect_agents.enable_group"))
        settings = load_settings()

        self._enabled_check = QCheckBox(tr("connect_agents.enable_label"))
        self._enabled_check.setChecked(settings.mcp_enabled)
        self._enabled_check.toggled.connect(self._on_settings_changed)

        self._spending_check = QCheckBox(tr("connect_agents.allow_spending_label"))
        self._spending_check.setChecked(settings.mcp_allow_spending)
        self._spending_check.toggled.connect(self._on_settings_changed)

        self._max_purchase = QDoubleSpinBox()
        self._max_purchase.setRange(0, 100000)
        self._max_purchase.setDecimals(2)
        self._max_purchase.setValue(settings.mcp_max_purchase)
        self._max_purchase.valueChanged.connect(self._on_settings_changed)

        self._daily_limit = QDoubleSpinBox()
        self._daily_limit.setRange(0, 1000000)
        self._daily_limit.setDecimals(2)
        self._daily_limit.setValue(settings.mcp_daily_limit)
        self._daily_limit.valueChanged.connect(self._on_settings_changed)

        limits = QFormLayout()
        limits.addRow(tr("connect_agents.max_purchase_label"), self._max_purchase)
        limits.addRow(tr("connect_agents.daily_limit_label"), self._daily_limit)

        explain = QLabel(tr("connect_agents.safety_explainer"))
        explain.setWordWrap(True)
        explain.setStyleSheet(f"color: {TEXT_MUTED};")

        layout = QVBoxLayout()
        layout.addWidget(self._enabled_check)
        layout.addWidget(self._spending_check)
        layout.addLayout(limits)
        layout.addWidget(explain)
        group.setLayout(layout)
        return group

    def _on_settings_changed(self, *_args) -> None:
        settings = load_settings()
        settings.mcp_enabled = self._enabled_check.isChecked()
        settings.mcp_allow_spending = self._spending_check.isChecked()
        settings.mcp_max_purchase = self._max_purchase.value()
        settings.mcp_daily_limit = self._daily_limit.value()
        save_settings(settings)
        self._spending_check.setEnabled(settings.mcp_enabled)

    # --------------------------------------------------------------- clients

    def _build_clients_group(self) -> QGroupBox:
        group = QGroupBox(tr("connect_agents.clients_group"))
        self._clients_layout = QVBoxLayout()
        intro = QLabel(tr("connect_agents.clients_intro"))
        intro.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addWidget(intro)
        layout.addLayout(self._clients_layout)
        group.setLayout(layout)
        return group

    def _populate_clients(self) -> None:
        while self._clients_layout.count():
            item = self._clients_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._client_rows = []

        detected = mcp_clients.detect()
        if not detected:
            none_found = QLabel(tr("connect_agents.no_clients"))
            none_found.setWordWrap(True)
            self._clients_layout.addWidget(none_found)
            return

        for client in detected:
            row = QWidget()
            hbox = QHBoxLayout(row)
            hbox.setContentsMargins(0, 4, 0, 4)

            configured = mcp_clients.is_configured(client)
            status = tr("connect_agents.client_connected") if configured else tr("connect_agents.client_not_connected")
            label = QLabel(f"<b>{client.label}</b> — {status}<br>"
                           f"<span style='color:{TEXT_MUTED};font-size:11px'>{client.config_path}</span>")
            label.setWordWrap(True)

            button = QPushButton(
                tr("connect_agents.disconnect_button") if configured else tr("connect_agents.connect_button")
            )
            button.clicked.connect(
                lambda _checked=False, c=client, was=configured: self._on_toggle_client(c, was)
            )

            hbox.addWidget(label, stretch=1)
            hbox.addWidget(button)
            self._clients_layout.addWidget(row)
            self._client_rows.append((client, button))

    def _on_toggle_client(self, client, was_configured: bool) -> None:
        if was_configured:
            ok, message = mcp_clients.uninstall(client)
        else:
            # Writing into another application's file is worth a beat of
            # deliberation, and the exact path is part of the question.
            confirm = QMessageBox.question(
                self,
                tr("connect_agents.confirm_write_title"),
                tr("connect_agents.confirm_write_body", client=client.label, path=str(client.config_path)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            ok, message = mcp_clients.install(client)

        QMessageBox.information(
            self,
            tr("connect_agents.result_title") if ok else tr("common.error"),
            message,
        )
        self._populate_clients()

    # ---------------------------------------------------------------- manual

    def _build_manual_group(self) -> QGroupBox:
        group = QGroupBox(tr("connect_agents.manual_group"))
        intro = QLabel(tr("connect_agents.manual_intro"))
        intro.setWordWrap(True)

        self._snippet = QPlainTextEdit()
        self._snippet.setReadOnly(True)
        self._snippet.setPlainText(mcp_clients.config_snippet())
        self._snippet.setFixedHeight(140)

        copy_btn = QPushButton(tr("connect_agents.copy_button"))
        copy_btn.clicked.connect(self._on_copy)
        save_btn = QPushButton(tr("connect_agents.save_md_button"))
        save_btn.clicked.connect(self._on_save_md)

        buttons = QHBoxLayout()
        buttons.addWidget(copy_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(intro)
        layout.addWidget(self._snippet)
        layout.addLayout(buttons)
        group.setLayout(layout)
        return group

    def _on_copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._snippet.toPlainText())

    def _on_save_md(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("connect_agents.save_md_dialog"), "MCP-SETUP.md",
            tr("connect_agents.md_filter"),
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(mcp_clients.setup_markdown())
            QMessageBox.information(self, tr("connect_agents.result_title"),
                                    tr("connect_agents.saved_md", path=path))
        except OSError as exc:
            QMessageBox.critical(self, tr("common.error"), str(exc))

    # -------------------------------------------------------------- approvals

    def _build_approvals_group(self) -> QGroupBox:
        group = QGroupBox(tr("connect_agents.approvals_group"))
        intro = QLabel(tr("connect_agents.approvals_intro"))
        intro.setWordWrap(True)
        self._approvals_layout = QVBoxLayout()
        layout = QVBoxLayout()
        layout.addWidget(intro)
        layout.addLayout(self._approvals_layout)
        group.setLayout(layout)
        return group

    def refresh_approvals(self) -> None:
        while self._approvals_layout.count():
            item = self._approvals_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            pending = mcp_approvals.list_pending()
        except Exception:  # noqa: BLE001 - never let the poller break the page
            pending = []

        if not pending:
            self._approvals_layout.addWidget(QLabel(tr("connect_agents.no_approvals")))
            return

        for request in pending:
            self._approvals_layout.addWidget(self._build_approval_card(request))

    def _build_approval_card(self, request) -> QWidget:
        card = QGroupBox(tr("connect_agents.approval_card_title", action=request.action))
        summary = request.summary or {}

        # Every value here came from EasyPost via mcp_verify, not from the
        # agent. That is the whole point of the dialog.
        lines = [
            f"<b>{tr('connect_agents.field_carrier')}:</b> {summary.get('carrier', '—')} "
            f"{summary.get('service', '')}",
            f"<b>{tr('connect_agents.field_amount')}:</b> {summary.get('price', '—')} "
            f"{summary.get('currency', '')}",
            f"<b>{tr('connect_agents.field_to')}:</b> {summary.get('to', '—')}",
            f"<b>{tr('connect_agents.field_from')}:</b> {summary.get('from', '—')}",
            f"<b>{tr('connect_agents.field_mode')}:</b> {summary.get('mode', '—')}",
        ]
        detail = QLabel("<br>".join(lines))
        detail.setWordWrap(True)

        provenance = QLabel(tr("connect_agents.verified_note"))
        provenance.setWordWrap(True)
        provenance.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")

        approve = QPushButton(tr("connect_agents.approve_button"))
        approve.setObjectName("primary")
        approve.clicked.connect(lambda _c=False, r=request: self._on_approve(r))
        reject = QPushButton(tr("connect_agents.reject_button"))
        reject.clicked.connect(lambda _c=False, r=request: self._on_reject(r))

        buttons = QHBoxLayout()
        buttons.addWidget(approve)
        buttons.addWidget(reject)
        buttons.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(detail)
        layout.addWidget(provenance)
        layout.addLayout(buttons)
        card.setLayout(layout)
        return card

    def _on_approve(self, request) -> None:
        confirm = QMessageBox.question(
            self,
            tr("connect_agents.confirm_spend_title"),
            tr("connect_agents.confirm_spend_body",
               amount=request.summary.get("price", "?"),
               currency=request.summary.get("currency", ""),
               carrier=request.summary.get("carrier", "?"),
               mode=request.summary.get("mode", "?")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._pending_task = run_async(lambda r=request: execute_approved(r.id), self)
        self._pending_task.succeeded.connect(lambda _r: self.refresh_approvals())
        self._pending_task.failed.connect(
            lambda exc: QMessageBox.critical(self, tr("common.error"), str(exc))
        )

    def _on_reject(self, request) -> None:
        mcp_approvals.set_status(request.id, "rejected")
        self.refresh_approvals()

    # ----------------------------------------------------------------- public

    def refresh(self) -> None:
        if not MCP_SUPPORTED:
            return
        self._populate_clients()
        self._snippet.setPlainText(mcp_clients.config_snippet())
        self.refresh_approvals()
        self._spending_check.setEnabled(self._enabled_check.isChecked())
