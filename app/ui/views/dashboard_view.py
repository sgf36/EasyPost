"""The page the app opens on: what needs attention, from local records only.

It was a placeholder from the first commit, promising pages that had long since
shipped, and it reached users in all fifty languages like that. What it shows
now comes from app/services/dashboard.py, which reads the local database and
never the network, so the page appears at once and still works offline.
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import MODE_PRODUCTION
from app.i18n import tr
from app.services.dashboard import PROBLEM_WINDOW_DAYS, DashboardSummary, load_summary
from app.services.formatting import (
    display_carrier,
    display_service,
    display_status,
    format_money,
    format_money_map,
)
from app.ui import theme

# Column headings are borrowed from Tracking and History so a column is called
# the same thing on every page, in every language.
_ATTENTION_COLUMN_KEYS = [
    "tracking.column_tracking_code",
    "tracking.column_carrier",
    "tracking.column_status",
]
_RECENT_COLUMN_KEYS = [
    "history.column_tracking_code",
    "history.column_to",
    "history.column_carrier",
    "history.column_service",
    "history.column_rate",
    "history.column_status",
]

# A busy account can collect dozens of problems in a month. Unbounded, the table
# would push the latest shipments off the bottom of the page meant to show both
# at a glance, so past this many rows it scrolls instead.
_ATTENTION_VISIBLE_ROWS = 8

# The cards and headings exist only on this page, so their styling stays with it
# rather than in the application-wide stylesheet.
_STYLE = f"""
QFrame#dashboardCard {{
    background: {theme.PANEL_BG};
    border: 1px solid {theme.BORDER};
    border-radius: 8px;
}}
QLabel#dashboardCaption, QLabel#dashboardMuted {{
    color: {theme.TEXT_MUTED};
}}
QLabel#dashboardFigure {{
    font-size: 22px;
    font-weight: 600;
}}
QLabel#dashboardHeading {{
    font-size: 15px;
    font-weight: 600;
}}
"""


def _label(text: str, object_name: str) -> QLabel:
    """A wrapping label. Every label on this page wraps, because a sentence that
    fits one line in English sets the whole page's minimum width in German, and
    a page wider than its window cuts controls off the right-hand edge."""
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    return label


def _table(column_keys: list[str], *, stretch: tuple[int, ...]) -> QTableWidget:
    table = QTableWidget(0, len(column_keys))
    table.setHorizontalHeaderLabels([tr(key) for key in column_keys])
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    # As on History: columns with a natural width get it, and the ones that read
    # acceptably truncated (an address, a service name, a sentence) take the rest.
    for index in range(len(column_keys)):
        header.setSectionResizeMode(
            index,
            QHeaderView.ResizeMode.Stretch
            if index in stretch
            else QHeaderView.ResizeMode.ResizeToContents,
        )
    # As tall as its rows, so the page reads top to bottom instead of as boxes
    # of empty grid.
    table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return table


def _fill(table: QTableWidget, rows: list[tuple[str, ...]]) -> None:
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(value or ""))


class DashboardView(QWidget):
    # The window owns navigation, so the page asks for another page rather than
    # reaching into the sidebar itself.
    create_shipment_requested = Signal()
    tracking_requested = Signal()
    history_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("dashboard.title")))
        self._scope_label = _label("", "dashboardMuted")
        layout.addWidget(self._scope_label)

        self._empty_panel = self._build_empty_panel()
        layout.addWidget(self._empty_panel)

        self._content = QWidget()
        content = QVBoxLayout(self._content)
        content.setContentsMargins(0, 8, 0, 0)
        content.setSpacing(18)
        content.addLayout(self._build_cards())
        content.addWidget(self._build_attention_section())
        content.addWidget(self._build_recent_section())
        layout.addWidget(self._content)
        layout.addStretch(1)

        self.refresh()

    def _build_empty_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("dashboardCard")
        box = QVBoxLayout(panel)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(10)
        box.addWidget(_label(tr("dashboard.empty_heading"), "dashboardHeading"))
        box.addWidget(_label(tr("dashboard.empty_body"), "dashboardBody"))

        self._empty_create_button = QPushButton(tr("dashboard.create_shipment_button"))
        self._empty_create_button.setObjectName("primary")
        self._empty_create_button.clicked.connect(
            lambda: self.create_shipment_requested.emit()
        )
        row = QHBoxLayout()
        row.addWidget(self._empty_create_button)
        row.addStretch(1)
        box.addLayout(row)
        return panel

    def _build_cards(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        self._open_figure = self._add_card(row, tr("dashboard.card_open_trackers"))
        self._problems_figure = self._add_card(
            row, tr("dashboard.card_problems", days=PROBLEM_WINDOW_DAYS)
        )
        self._refunds_figure = self._add_card(row, tr("dashboard.card_refunds_pending"))
        self._spend_figure = self._add_card(row, tr("dashboard.card_total_spend"))
        return row

    @staticmethod
    def _add_card(row: QHBoxLayout, caption: str) -> QLabel:
        card = QFrame()
        card.setObjectName("dashboardCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.addWidget(_label(caption, "dashboardCaption"))
        figure = _label("", "dashboardFigure")
        box.addWidget(figure)
        box.addStretch(1)
        row.addWidget(card, stretch=1)
        return figure

    def _build_attention_section(self) -> QWidget:
        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(_label(
            tr("dashboard.attention_heading", days=PROBLEM_WINDOW_DAYS), "dashboardHeading"
        ))

        self._attention_table = _table(_ATTENTION_COLUMN_KEYS, stretch=(2,))
        table = self._attention_table
        table.setMaximumHeight(
            table.horizontalHeader().sizeHint().height()
            + _ATTENTION_VISIBLE_ROWS * table.verticalHeader().defaultSectionSize()
            + 2 * table.frameWidth()
        )
        box.addWidget(table)
        self._attention_empty = _label(
            tr("dashboard.attention_empty", days=PROBLEM_WINDOW_DAYS), "dashboardMuted"
        )
        box.addWidget(self._attention_empty)

        open_tracking = QPushButton(tr("dashboard.open_tracking_button"))
        open_tracking.clicked.connect(lambda: self.tracking_requested.emit())
        buttons = QHBoxLayout()
        buttons.addWidget(open_tracking)
        buttons.addStretch(1)
        box.addLayout(buttons)
        return section

    def _build_recent_section(self) -> QWidget:
        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(_label(tr("dashboard.recent_heading"), "dashboardHeading"))

        self._recent_table = _table(_RECENT_COLUMN_KEYS, stretch=(1, 3))
        box.addWidget(self._recent_table)
        self._recent_empty = _label(tr("dashboard.recent_empty"), "dashboardMuted")
        box.addWidget(self._recent_empty)

        create = QPushButton(tr("dashboard.create_shipment_button"))
        create.setObjectName("primary")
        create.clicked.connect(lambda: self.create_shipment_requested.emit())
        open_history = QPushButton(tr("dashboard.open_history_button"))
        open_history.clicked.connect(lambda: self.history_requested.emit())
        buttons = QHBoxLayout()
        buttons.addWidget(create)
        buttons.addWidget(open_history)
        buttons.addStretch(1)
        box.addLayout(buttons)
        return section

    def refresh(self) -> None:
        summary = load_summary()
        # Records never mix across modes, so an empty page straight after a
        # switch has to say which set it is looking at, or it reads as lost data.
        self._scope_label.setText(tr(
            "dashboard.scope_production"
            if summary.mode == MODE_PRODUCTION
            else "dashboard.scope_test"
        ))
        self._empty_panel.setVisible(summary.is_empty)
        self._content.setVisible(not summary.is_empty)
        if summary.is_empty:
            return
        self._render_figures(summary)
        self._render_attention(summary)
        self._render_recent(summary)

    def _render_figures(self, summary: DashboardSummary) -> None:
        self._open_figure.setText(str(summary.open_trackers))
        problems = len(summary.problems)
        self._problems_figure.setText(str(problems))
        # Red only when there is something to act on. A red nought reads as an
        # alarm about nothing.
        self._problems_figure.setStyleSheet(f"color: {theme.DANGER};" if problems else "")
        self._refunds_figure.setText(str(len(summary.pending_refunds)))
        # One figure per currency, never a sum: there is no exchange rate in
        # this application.
        self._spend_figure.setText(format_money_map(summary.spend_by_currency))

    def _render_attention(self, summary: DashboardSummary) -> None:
        rows = [
            (t.tracking_code, display_carrier(t.carrier or ""),
             display_status(t.status, t.status_detail))
            for t in summary.problems
        ]
        problem_rows = len(rows)
        rows += [
            (s.tracking_code, display_carrier(s.carrier or ""),
             tr("dashboard.refund_pending_status"))
            for s in summary.pending_refunds
        ]
        _fill(self._attention_table, rows)
        for row in range(problem_rows):
            self._attention_table.item(row, 2).setForeground(QColor(theme.DANGER))
        self._attention_table.setVisible(bool(rows))
        self._attention_empty.setVisible(not rows)

    def _render_recent(self, summary: DashboardSummary) -> None:
        rows = [
            (
                s.tracking_code,
                s.to_address,
                display_carrier(s.carrier or ""),
                display_service(s.service or ""),
                format_money(s.rate_amount, s.rate_currency) if s.rate_amount else "",
                display_status(s.status),
            )
            for s in summary.recent_shipments
        ]
        _fill(self._recent_table, rows)
        self._recent_table.setVisible(bool(rows))
        self._recent_empty.setVisible(not rows)
