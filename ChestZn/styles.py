"""
Стили ChestZn (styles.py).
Темная и светлая темы оформления.
"""

APP_QSS = """
/* ================================================================
   ТЁМНАЯ ТЕМА — БАЗА
   ================================================================ */
QMainWindow, QDialog {
    background-color: #0f1419;
    color: #e6edf3;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    font-size: 15px;
}

QLabel {
    background: transparent;
    border: none;
    color: #e6edf3;
}

QLabel[heading="true"], QLabel#pageTitle {
    color: #f8fafc;
    font-size: 18px;
    font-weight: 700;
}

QTableWidget QLineEdit,
QTableWidget QSpinBox,
QTableWidget QDoubleSpinBox,
QTableWidget QComboBox {
    background-color: #0f172a;
    color: #e6edf3;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 14px;
}
QTableWidget QLineEdit:focus,
QTableWidget QSpinBox:focus,
QTableWidget QDoubleSpinBox:focus {
    border: 1px solid #3b82f6;
    background-color: #111c2e;
}

QTableWidget QHeaderView::section:vertical {
    background-color: #161c24;
    color: #64748b;
    border-right: 1px solid #2a3542;
    border-bottom: 1px solid #2a3542;
    font-weight: 600;
    font-size: 12px;
}

/* ================================================================
   WARNING BANNER
   ================================================================ */
QFrame#warningBanner {
    background-color: rgba(245, 158, 11, 0.15);
    border-bottom: 1px solid rgba(245, 158, 11, 0.4);
}
QLabel#bannerIcon { color: #fbbf24; font-size: 17px; }
QLabel#bannerLabel { color: #fbbf24; font-size: 13px; font-weight: 500; }
QPushButton#bannerButton {
    background-color: transparent;
    border: 1px solid rgba(245, 158, 11, 0.5);
    border-radius: 6px;
    color: #fbbf24;
    font-size: 12px;
    padding: 4px 12px;
}
QPushButton#bannerButton:hover {
    background-color: rgba(245, 158, 11, 0.2);
    border-color: #fbbf24;
}

/* ================================================================
   HEADER & SIDEBAR
   ================================================================ */
QFrame#header {
    background-color: #161c24;
    border-bottom: 1px solid #2a3542;
}
QLabel#brand {
    font-size: 15px;
    font-weight: 700;
    color: #e6edf3;
}
QLabel#brandRole {
    background-color: rgba(139,92,246,.18);
    color: #c4b5fd;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#meta { color: #8b97a6; font-size: 13px; }
QLabel#okDot { color: #22c55e; font-weight: 700; }

QFrame#sidebar {
    background-color: #161c24;
    border-right: 1px solid #2a3542;
}
QPushButton#navBtn {
    background: transparent;
    border: none;
    color: #8b97a6;
    text-align: left;
    padding: 10px 14px;
    border-radius: 7px;
    font-size: 14px;
    font-weight: 500;
}
QPushButton#navBtn:hover {
    background-color: #1c2530;
    color: #e6edf3;
}
QPushButton#navBtn:checked {
    background-color: #3b82f6;
    color: #ffffff;
    font-weight: 700;
}
QLabel#navSection {
    color: #8b97a6;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
    padding: 16px 14px 6px;
}

/* ================================================================
   TABS & CARDS & TABLES
   ================================================================ */
QTabWidget::pane {
    background-color: #0f1419;
    border: none;
}
QTabWidget::tab-bar { alignment: left; }
QTabBar::tab {
    background: #1c2530;
    color: #8b97a6;
    padding: 9px 20px;
    border: 1px solid #2a3542;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 3px;
    font-size: 14px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #3b82f6;
    color: #fff;
}
QTabBar::tab:hover:!selected {
    background: #232e3c;
    color: #e6edf3;
}

QFrame#card, QFrame#kpi, QFrame#scanbar, QFrame#counter {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
}
QLabel#cardTitle {
    font-size: 12px;
    font-weight: 700;
    color: #8b97a6;
    letter-spacing: 1.2px;
}

QTableWidget, QTreeWidget {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
    gridline-color: #2a3542;
    color: #e6edf3;
    font-size: 14px;
}
QTableWidget::item, QTreeWidget::item {
    padding: 7px 10px;
    border-bottom: 1px solid #232c37;
}
QTableWidget::item:selected, QTreeWidget::item:selected {
    background-color: rgba(59,130,246,.25);
    color: #ffffff;
}
QHeaderView::section {
    background-color: #1b232e;
    color: #94a3b8;
    padding: 9px 10px;
    border: none;
    border-bottom: 1px solid #2a3542;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: .5px;
}

/* ================================================================
   BUTTONS & INPUTS
   ================================================================ */
QPushButton {
    background-color: #1c2530;
    border: 1px solid #2a3542;
    border-radius: 8px;
    padding: 8px 16px;
    color: #e6edf3;
    font-size: 14px;
    font-weight: 600;
}
QPushButton:hover:!disabled {
    background-color: #232e3c;
    border-color: #3b82f6;
}
QPushButton:disabled {
    color: #64748b;
    background-color: #12171e;
    border-color: #1e2631;
}
QPushButton[primary="true"] {
    background-color: #2563eb;
    border-color: #2563eb;
    color: #fff;
}
QPushButton[primary="true"]:hover:!disabled {
    background-color: #1d4ed8;
}

QLineEdit, QComboBox {
    background-color: #1c2530;
    border: 1px solid #2a3542;
    border-radius: 6px;
    padding: 7px 11px;
    color: #e6edf3;
    font-size: 14px;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #3b82f6;
}
QComboBox::drop-down {
    border: none;
    width: 26px;
}
QComboBox QAbstractItemView {
    background: #1c2530;
    border: 1px solid #2a3542;
    selection-background-color: #3b82f6;
    color: #e6edf3;
    font-size: 14px;
}

/* Спинбоксы */
QSpinBox, QDoubleSpinBox {
    background-color: #1c2530;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 5px 8px;
    color: #f8fafc;
    font-size: 14px;
    font-weight: 600;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #3b82f6;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    height: 15px;
    border-left: 1px solid #334155;
    border-bottom: 1px solid #334155;
    background-color: #2563eb;
    border-top-right-radius: 5px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 24px;
    height: 15px;
    border-left: 1px solid #334155;
    background-color: #2563eb;
    border-bottom-right-radius: 5px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #3b82f6;
}

/* Чекбоксы */
QCheckBox {
    color: #e6edf3;
    font-size: 14px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #334155;
    border-radius: 4px;
    background-color: #1c2530;
}
QCheckBox::indicator:hover {
    border-color: #3b82f6;
}
QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #2563eb;
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24'><path fill='%23ffffff' d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z'/></svg>");
}

/* ================================================================
   STARTUP PROBLEMS WINDOW
   ================================================================ */
QDialog#startupWindow { background-color: #0d1117; }
QDialog#startupWindow QLabel#startupTitle { font-size: 18px; font-weight: 700; color: #8b97a6; }
QDialog#startupWindow QLabel#startupTitle[state="ok"]       { color: #22c55e; }
QDialog#startupWindow QLabel#startupTitle[state="warning"]  { color: #f59e0b; }
QDialog#startupWindow QLabel#startupTitle[state="critical"] { color: #ef4444; }
QDialog#startupWindow QLabel#startupSubtitle { color: #94a3b8; font-size: 14px; }
QDialog#startupWindow QFrame#problemCard {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
    padding: 10px;
}
QDialog#startupWindow QLabel#problemIcon[color="ok"]      { color: #22c55e; font-size: 18px; }
QDialog#startupWindow QLabel#problemIcon[color="warning"] { color: #f59e0b; font-size: 18px; }
QDialog#startupWindow QLabel#problemIcon[color="error"]   { color: #ef4444; font-size: 18px; }
QDialog#startupWindow QLabel#problemName { color: #f8fafc; font-size: 15px; font-weight: 700; }
QDialog#startupWindow QLabel#problemMessage[color="ok"]      { color: #22c55e; font-size: 14px; }
QDialog#startupWindow QLabel#problemMessage[color="warning"] { color: #f59e0b; font-size: 14px; }
QDialog#startupWindow QLabel#problemMessage[color="error"]   { color: #ef4444; font-size: 14px; }
QDialog#startupWindow QLabel#problemHint { color: #8b97a6; font-size: 12px; font-family: Consolas, monospace; }
QDialog#startupWindow QPushButton#actionButton {
    background-color: #232e3c;
    border: 1px solid #3b82f6;
    border-radius: 6px;
    color: #93c5fd;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 14px;
}
QDialog#startupWindow QPushButton#continueButton {
    background-color: #2563eb;
    border: none;
    border-radius: 8px;
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
    padding: 10px;
}
QDialog#startupWindow QPushButton#quitButton,
QDialog#startupWindow QPushButton#auxButton,
QDialog#startupWindow QPushButton#restartButton {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 6px;
    color: #94a3b8;
    font-size: 13px;
    font-weight: 600;
    padding: 7px 14px;
}
"""

LIGHT_QSS = """
/* ================================================================
   СВЕТЛАЯ ТЕМА — CLEAN SLATE
   ================================================================ */
QMainWindow, QDialog {
    background-color: #f8fafc;
    color: #0f172a;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    font-size: 15px;
}

QLabel {
    background: transparent;
    border: none;
    color: #0f172a;
}

QLabel[heading="true"], QLabel#pageTitle, QLabel#sectionTitle {
    color: #0f172a;
    font-size: 18px;
    font-weight: 700;
}

QFrame#header {
    background-color: #ffffff;
    border-bottom: 1px solid #e2e8f0;
}
QLabel#brand { font-size: 15px; font-weight: 700; color: #0f172a; }
QLabel#brandRole {
    background-color: #e0e7ff;
    color: #4338ca;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#meta { color: #64748b; font-size: 13px; }
QLabel#okDot { color: #16a34a; font-weight: 700; }

QFrame#sidebar {
    background-color: #ffffff;
    border-right: 1px solid #e2e8f0;
}
QPushButton#navBtn {
    background: transparent;
    border: none;
    color: #475569;
    text-align: left;
    padding: 10px 14px;
    border-radius: 7px;
    font-size: 14px;
    font-weight: 500;
}
QPushButton#navBtn:hover {
    background-color: #f1f5f9;
    color: #0f172a;
}
QPushButton#navBtn:checked {
    background-color: #2563eb;
    color: #ffffff;
    font-weight: 700;
}
QLabel#navSection {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
    padding: 16px 14px 6px;
}

QFrame#card, QFrame#kpi, QFrame#scanbar, QFrame#counter {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
}
QLabel#cardTitle {
    font-size: 12px;
    font-weight: 700;
    color: #64748b;
    letter-spacing: 1.2px;
}

QLineEdit#scanInput {
    background-color: #ffffff;
    border: 2px solid #cbd5e1;
    border-radius: 8px;
    padding: 10px 14px;
    color: #0f172a;
    font-family: 'Consolas', monospace;
    font-size: 15px;
}
QLineEdit#scanInput:focus {
    border-color: #2563eb;
}
QLabel#scanStatus {
    color: #475569;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 12px;
    background: #f1f5f9;
    border-radius: 6px;
}

QTabWidget::pane { background-color: #f8fafc; border: none; }
QTabWidget::tab-bar { alignment: left; }
QTabBar::tab {
    background: #e2e8f0;
    color: #475569;
    padding: 9px 20px;
    border: 1px solid #cbd5e1;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 3px;
    font-size: 14px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #2563eb;
    color: #ffffff;
}

QTableWidget, QTreeWidget {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    gridline-color: #f1f5f9;
    color: #0f172a;
    font-size: 14px;
}
QTableWidget::item, QTreeWidget::item {
    padding: 7px 10px;
    border-bottom: 1px solid #f1f5f9;
    color: #0f172a;
}
QTableWidget::item:selected, QTreeWidget::item:selected {
    background-color: #eff6ff;
    color: #1d4ed8;
}
QHeaderView::section {
    background-color: #f8fafc;
    color: #64748b;
    padding: 9px 10px;
    border: none;
    border-bottom: 1px solid #e2e8f0;
    font-size: 12px;
    font-weight: 700;
}
QTableWidget QHeaderView::section:vertical {
    background-color: #f8fafc;
    color: #64748b;
    border: none;
    border-right: 1px solid #e2e8f0;
    border-bottom: 1px solid #e2e8f0;
    font-weight: 600;
}

QTableWidget QLineEdit,
QTableWidget QSpinBox,
QTableWidget QDoubleSpinBox,
QTableWidget QComboBox {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 14px;
}

QCheckBox {
    color: #0f172a;
    font-size: 14px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    background-color: #ffffff;
}
QCheckBox::indicator:hover {
    border-color: #2563eb;
}
QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #2563eb;
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24'><path fill='%23ffffff' d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z'/></svg>");
}

QPushButton {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 8px 16px;
    color: #1e293b;
    font-size: 14px;
    font-weight: 600;
}
QPushButton:hover:!disabled {
    background-color: #f1f5f9;
    border-color: #94a3b8;
}
QPushButton[primary="true"] {
    background-color: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
}
QPushButton[primary="true"]:hover:!disabled {
    background-color: #1d4ed8;
}

QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 5px 8px;
    color: #0f172a;
    font-size: 14px;
    font-weight: 500;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    height: 15px;
    border-left: 1px solid #cbd5e1;
    border-bottom: 1px solid #cbd5e1;
    background-color: #f1f5f9;
    border-top-right-radius: 5px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 24px;
    height: 15px;
    border-left: 1px solid #cbd5e1;
    background-color: #f1f5f9;
    border-bottom-right-radius: 5px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #e2e8f0;
}

QDialog#startupWindow { background-color: #f8fafc; }
QDialog#startupWindow QScrollArea,
QDialog#startupWindow QScrollArea QWidget,
QDialog#startupWindow QWidget#scrollContent {
    background-color: transparent;
    border: none;
}
QDialog#startupWindow QFrame#problemCard {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
}
QDialog#startupWindow QLabel#problemName { color: #0f172a; font-size: 15px; font-weight: 700; }
QDialog#startupWindow QLabel#problemHint { color: #64748b; font-size: 12px; }
QDialog#startupWindow QPushButton#continueButton { background-color: #2563eb; color: #fff; }
QDialog#startupWindow QPushButton#auxButton,
QDialog#startupWindow QPushButton#restartButton,
QDialog#startupWindow QPushButton#quitButton {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    color: #334155;
}
"""


def get_qss(theme: str = "dark") -> str:
    """Возвращает QSS-стили в зависимости от выбранной темы."""
    if str(theme).lower() == "light":
        return LIGHT_QSS
    return APP_QSS