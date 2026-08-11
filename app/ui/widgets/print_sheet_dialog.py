"""Dialog to compose selected purchased labels into a printable sheet PDF.

Given a list of label URLs (from the History or Batch views), it downloads the
label images off the UI thread, then lets the user pick a label-sheet template,
the printer type (laser or inkjet, which sets the printable-area safety), and a
per-printer calibration nudge — with a live preview of page one — before saving
the PDF. The chosen options are remembered for next time.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core import label_sheet
from app.core.settings import load_settings
from app.i18n import tr
from app.services.label_sheets import build_print_sheet, fetch_label_images, remember_defaults
from app.ui.widgets.async_worker import run_async

_PREVIEW_W = 360
_PREVIEW_H = 480


class PrintSheetDialog(QDialog):
    def __init__(self, label_urls: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("print_sheet.title"))
        self._urls = [u for u in label_urls if u]
        self._images: list[bytes] = []
        self._failed: list[str] = []
        self._pending_task = None
        settings = load_settings()

        root = QHBoxLayout(self)

        # --- Left: controls ------------------------------------------------
        form = QFormLayout()
        self._template_combo = QComboBox()
        for t in label_sheet.list_templates():
            self._template_combo.addItem(t.name, t.key)
        self._select_data(self._template_combo, settings.label_sheet_template)

        self._printer_combo = QComboBox()
        self._printer_combo.addItem(tr("print_sheet.printer_laser"), "laser")
        self._printer_combo.addItem(tr("print_sheet.printer_inkjet"), "inkjet")
        self._select_data(self._printer_combo, settings.printer_type)

        self._offx = self._offset_spin(settings.label_offset_x_mm)
        self._offy = self._offset_spin(settings.label_offset_y_mm)

        form.addRow(tr("print_sheet.template_label"), self._template_combo)
        form.addRow(tr("print_sheet.printer_label"), self._printer_combo)
        form.addRow(tr("print_sheet.offset_x_label"), self._offx)
        form.addRow(tr("print_sheet.offset_y_label"), self._offy)

        self._info = QLabel(tr("print_sheet.loading", count=len(self._urls)))
        self._info.setWordWrap(True)

        self._save_btn = QPushButton(tr("print_sheet.save_button"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        close_btn = QPushButton(tr("print_sheet.close_button"))
        close_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(close_btn)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self._info)
        left.addStretch(1)
        left.addLayout(btn_row)

        # --- Right: preview ------------------------------------------------
        preview_group = QGroupBox(tr("print_sheet.preview_group"))
        self._preview = QLabel()
        self._preview.setFixedSize(_PREVIEW_W, _PREVIEW_H)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("background:#f4f4f4;border:1px solid #ccc;")
        pv = QVBoxLayout()
        pv.addWidget(self._preview)
        preview_group.setLayout(pv)

        root.addLayout(left, stretch=1)
        root.addWidget(preview_group)

        for w in (self._template_combo, self._printer_combo):
            w.currentIndexChanged.connect(self._refresh)
        for w in (self._offx, self._offy):
            w.valueChanged.connect(self._refresh)

        self._set_controls_enabled(False)
        self._start_fetch()

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _select_data(combo: QComboBox, data) -> None:
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _offset_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-25.0, 25.0)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setSuffix(" mm")
        spin.setValue(value)
        return spin

    def _set_controls_enabled(self, on: bool) -> None:
        for w in (self._template_combo, self._printer_combo, self._offx, self._offy):
            w.setEnabled(on)

    def _template(self):
        return label_sheet.get_template(self._template_combo.currentData())

    # -- fetch --------------------------------------------------------------
    def _start_fetch(self) -> None:
        urls = list(self._urls)
        self._pending_task = run_async(lambda: fetch_label_images(urls), self)
        self._pending_task.succeeded.connect(self._on_fetched)
        self._pending_task.failed.connect(self._on_fetch_failed)

    def _on_fetched(self, result) -> None:
        self._images, self._failed = result
        if not self._images:
            self._info.setText(tr("print_sheet.none_body"))
            return
        self._set_controls_enabled(True)
        self._save_btn.setEnabled(True)
        self._refresh()

    def _on_fetch_failed(self, exc: Exception) -> None:
        self._info.setText(tr("print_sheet.fetch_failed", error=str(exc)))

    # -- preview / info -----------------------------------------------------
    def _refresh(self) -> None:
        if not self._images:
            return
        template = self._template()
        ptype = self._printer_combo.currentData()
        ox, oy = self._offx.value(), self._offy.value()

        per = label_sheet.positions_per_sheet(
            template, ptype, offset_x_mm=ox, offset_y_mm=oy
        )
        pages = label_sheet.page_count(
            len(self._images), template, ptype, offset_x_mm=ox, offset_y_mm=oy
        )
        info = tr(
            "print_sheet.info", included=len(self._images), per=per, pages=pages
        )
        if self._failed:
            info += "\n" + tr("print_sheet.some_failed", failed=len(self._failed))
        self._info.setText(info)

        try:
            png = label_sheet.preview_png(
                self._images, template, printer_type=ptype, offset_x_mm=ox, offset_y_mm=oy
            )
        except Exception:
            self._preview.clear()
            return
        img = QImage.fromData(png, "PNG")
        pix = QPixmap.fromImage(img).scaled(
            _PREVIEW_W,
            _PREVIEW_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(pix)

    # -- save ---------------------------------------------------------------
    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("print_sheet.save_dialog_title"),
            "print-sheet.pdf",
            tr("print_sheet.pdf_filter"),
        )
        if not path:
            return
        template = self._template()
        ptype = self._printer_combo.currentData()
        ox, oy = self._offx.value(), self._offy.value()
        try:
            result = build_print_sheet(
                self._urls,
                template_key=template.key,
                printer_type=ptype,
                offset_x_mm=ox,
                offset_y_mm=oy,
            )
            with open(path, "wb") as fh:
                fh.write(result.pdf)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, tr("print_sheet.error_title"), str(exc))
            return

        remember_defaults(template.key, ptype, ox, oy)
        body = tr("print_sheet.saved_body", count=result.included, path=path)
        if result.failed:
            body += "\n" + tr("print_sheet.some_failed", failed=len(result.failed))
        QMessageBox.information(self, tr("print_sheet.saved_title"), body)
        self.accept()
