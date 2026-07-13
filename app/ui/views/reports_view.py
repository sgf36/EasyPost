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
from app.services.reports import (
    label_counts_by_status,
    refund_status_breakdown,
    spend_by_carrier,
    total_labels_purchased,
    total_spend,
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
        spend = spend_by_carrier()
        self._summary_label.setText(
            tr(
                "reports.summary_label",
                total_spend=f"{total_spend():.2f}",
                labels_purchased=total_labels_purchased(),
            )
        )
        self._render_chart(spend)
        self._render_breakdown()

    def _render_chart(self, spend: dict) -> None:
        chart = QChart()
        chart.setTitle(tr("reports.spend_chart_title"))

        bar_set = QBarSet(tr("reports.spend_series_name"))
        categories = list(spend.keys()) or [tr("reports.no_data_label")]
        values = list(spend.values()) or [0]
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

        rows = [(tr("reports.status_row_label", status=k), v) for k, v in statuses.items()]
        rows += [(tr("reports.refund_row_label", status=k), v) for k, v in refunds.items()]

        self._breakdown_table.setRowCount(len(rows))
        for row_idx, (label, count) in enumerate(rows):
            self._breakdown_table.setItem(row_idx, 0, QTableWidgetItem(label))
            self._breakdown_table.setItem(row_idx, 1, QTableWidgetItem(str(count)))
