"""Cross-platform look-and-feel for the app.

By default Qt renders with each OS's *native* widget style, which means the
same code looks crisp on Windows 11 but dated on macOS (plain list nav, old
table grids, native combo boxes). To get one polished, identical appearance
everywhere we force Qt's built-in ``Fusion`` style, pin an explicit light
palette, choose the platform's native UI font, and layer a stylesheet on top.

Call :func:`apply_theme` once on the ``QApplication`` before creating windows.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# Palette — a light, blue-accented scheme. ACCENT matches the existing
# test-mode banner so the whole app reads as one system.
ACCENT = "#2b6cb0"
ACCENT_HOVER = "#2c5282"
ACCENT_PRESSED = "#274d76"
ACCENT_SOFT = "#e8f0fb"  # selection/hover wash behind accent text
TEXT = "#1a202c"
TEXT_MUTED = "#5a6472"
WINDOW_BG = "#ffffff"
SIDEBAR_BG = "#f5f7fa"
PANEL_BG = "#ffffff"
BORDER = "#d9dee5"
BORDER_STRONG = "#c2c9d2"
INPUT_DISABLED_BG = "#eef1f4"
TABLE_HEADER_BG = "#f0f3f7"
TABLE_ALT_BG = "#f8fafc"


def _ui_font() -> QFont:
    """The native UI font for the current platform, so text looks native
    rather than like Qt's fallback (which also triggers the ``8514oem``
    DirectWrite warning on Windows)."""
    if sys.platform.startswith("win"):
        font = QFont("Segoe UI", 10)
    elif sys.platform == "darwin":
        # The system font; Qt resolves this alias to SF Pro on modern macOS.
        font = QFont(".AppleSystemUIFont", 13)
        if not font.exactMatch():
            font = QFont("Helvetica Neue", 13)
    else:
        font = QFont("Noto Sans", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(WINDOW_BG))
    p.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Base, QColor(PANEL_BG))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(TABLE_ALT_BG))
    p.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Button, QColor(PANEL_BG))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2d3748"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    p.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    # Keep disabled text legible but clearly muted.
    disabled = QColor(TEXT_MUTED)
    for grp in (QPalette.ColorGroup.Disabled,):
        p.setColor(grp, QPalette.ColorRole.Text, disabled)
        p.setColor(grp, QPalette.ColorRole.ButtonText, disabled)
        p.setColor(grp, QPalette.ColorRole.WindowText, disabled)
    return p


# Full stylesheet. Selectors are intentionally specific so colored banners
# (which set their own stylesheet) and content lists are not clobbered.
_STYLESHEET = f"""
/* ---- base ---- */
QWidget {{
    color: {TEXT};
}}
QMainWindow, QDialog {{
    background: {WINDOW_BG};
}}
QToolTip {{
    background: #2d3748;
    color: #ffffff;
    border: none;
    padding: 5px 8px;
    border-radius: 4px;
}}

/* ---- left navigation sidebar ---- */
QListWidget#navSidebar {{
    background: {SIDEBAR_BG};
    border: none;
    border-right: 1px solid {BORDER};
    outline: 0;
    padding: 8px 8px;
}}
QListWidget#navSidebar::item {{
    color: {TEXT};
    padding: 9px 12px;
    margin: 2px 0;
    border-radius: 6px;
}}
QListWidget#navSidebar::item:hover {{
    background: #e9edf3;
}}
QListWidget#navSidebar::item:selected {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
    font-weight: 600;
}}

/* ---- headings (QLabel rich text uses <h2>) ---- */
QLabel {{
    background: transparent;
}}

/* ---- group boxes ---- */
QGroupBox {{
    background: {PANEL_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 14px 12px 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    padding: 0 4px;
    color: {TEXT_MUTED};
}}

/* ---- text inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateEdit {{
    background: {PANEL_BG};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 20px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus, QDateEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {INPUT_DISABLED_BG};
    color: {TEXT_MUTED};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL_BG};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {ACCENT};
    outline: 0;
}}

/* ---- buttons (default) ---- */
QPushButton {{
    background: {PANEL_BG};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 20px;
}}
QPushButton:hover {{
    background: #f2f5f9;
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background: #e6ebf2;
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background: {INPUT_DISABLED_BG};
    border-color: {BORDER};
}}
QPushButton:default {{
    background: {ACCENT};
    color: #ffffff;
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton:default:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton:default:pressed {{
    background: {ACCENT_PRESSED};
}}
/* Opt-in accent buttons via objectName="primary" */
QPushButton#primary {{
    background: {ACCENT};
    color: #ffffff;
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}

/* ---- tables ---- */
QTableWidget, QTableView {{
    background: {PANEL_BG};
    alternate-background-color: {TABLE_ALT_BG};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 8px;
    outline: 0;
}}
QTableWidget::item, QTableView::item {{
    padding: 4px 6px;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QHeaderView::section {{
    background: {TABLE_HEADER_BG};
    color: {TEXT_MUTED};
    padding: 7px 8px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}
QHeaderView::section:last {{
    border-right: none;
}}
QTableCornerButton::section {{
    background: {TABLE_HEADER_BG};
    border: none;
    border-bottom: 1px solid {BORDER};
}}

/* ---- content lists (not the nav) ---- */
QListWidget {{
    background: {PANEL_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    outline: 0;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
}}
QListWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
}}

/* ---- checkboxes ---- */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {PANEL_BG};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}

/* ---- tabs (if any view adds them) ---- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
    font-weight: 600;
}}

/* ---- scrollbars (thin, modern) ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #c4ccd6;
    min-height: 28px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: #a9b3bf; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #c4ccd6;
    min-width: 28px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: #a9b3bf; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- menus & message boxes ---- */
QMenu {{
    background: {PANEL_BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
}}
"""


def apply_theme(app: QApplication) -> None:
    """Force a consistent, polished light theme across all platforms."""
    app.setStyle("Fusion")
    app.setFont(_ui_font())
    app.setPalette(_light_palette())
    app.setStyleSheet(_STYLESHEET)
