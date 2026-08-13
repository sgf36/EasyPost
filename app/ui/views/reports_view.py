"""Reporting dashboard: spend by carrier, label counts, refund breakdown."""

from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.services.formatting import display_carrier, display_status, format_money_map
from app.services.reports import (
    label_counts_by_status,
    primary_currency,
    refund_status_breakdown,
    spend_by_carrier,
    total_labels_purchased,
    total_spend_by_currency,
)


class ReportsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h2>{tr('reports.title')}</h2>"))

        self._summary_label = QLabel()
        layout.addWidget(self._summary_label)

        charts_row = QHBoxLayout()
        self._chart_view = QChartView()
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chart_view.setMinimumHeight(300)
        charts_row.addWidget(self._chart_view, stretch=2)

        self._breakdown_group = QGroupBox(tr("reports.breakdown_group_title"))
        self._breakdown_table = QTableWidget(0, 2)
        self._breakdown_table.setHorizontalHeaderLabels([
            tr("reports.col_category"),
            tr("reports.col_count"),
        ])
        breakdown_layout = QVBoxLayout()
        breakdown_layout.addWidget(self._breakdown_table)
        self._breakdown_group.setLayout(breakdown_layout)
        charts_row.addWidget(self._breakdown_group, stretch=1)

        layout.addLayout(charts_row, stretch=1)

        refresh_btn = QPushButton(tr("reports.refresh_button"))
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        self.refresh()

    def refresh(self) -> None:
        # Every figure carries its currency. Summing across them produced
        # "12.25" for 3.85 GBP plus 8.40 USD, and that number reached both
        # store listings before anyone read it.
        self._summary_label.setText(
            tr(
                "reports.summary_label",
                total_spend=format_money_map(total_spend_by_currency()),
                labels_purchased=total_labels_purchased(),
            )
        )
        self._render_chart(spend_by_carrier())
        self._render_breakdown()

    def _render_chart(self, spend: dict[str, dict[str, float]]) -> None:
        """One currency per chart, named in its own title.

        A bar chart has one axis and an axis has one unit, so pounds and
        dollars cannot share it. Rather than plot incomparable bars the chart
        shows the currency most of the spend is in and says which; the rest is
        still in the summary line above.
        """
        currency = primary_currency()
        chart = QChart()
        title = tr("reports.spend_chart_title")
        chart.setTitle(f"{title} ({currency})" if currency else title)

        bar_set = QBarSet(tr("reports.spend_series_name"))
        # Carriers are stored as the API returns them ("RoyalMailV3"); the axis
        # was labelling its bars with that raw code.
        per_carrier = {
            display_carrier(carrier): by_ccy.get(currency, 0.0)
            for carrier, by_ccy in spend.items()
            if by_ccy.get(currency)
        }
        categories = list(per_carrier) or [tr("reports.no_data_label")]
        values = list(per_carrier.values()) or [0]
        for value in values:
            bar_set.append(value)

        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setRange(0, max(values) * 1.2 if max(values) > 0 else 1)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)

        chart.legend().setVisible(False)
        self._chart_view.setChart(chart)

    def _render_breakdown(self) -> None:
        statuses = label_counts_by_status()
        refunds = refund_status_breakdown()

        # "Status: purchased" was as raw as the tracking table's in_transit.
        rows = [
            (tr("reports.status_row_label", status=display_status(k)), v)
            for k, v in statuses.items()
        ]
        rows += [
            (tr("reports.refund_row_label", status=display_status(k)), v)
            for k, v in refunds.items()
        ]

        self._breakdown_table.setRowCount(len(rows))
        for row_idx, (label, count) in enumerate(rows):
            self._breakdown_table.setItem(row_idx, 0, QTableWidgetItem(label))
            self._breakdown_table.setItem(row_idx, 1, QTableWidgetItem(str(count)))
