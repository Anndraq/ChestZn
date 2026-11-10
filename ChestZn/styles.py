"""
Централизованные стили ChestZn (styles.py).
Тёмная и светлая темы оформления + мгновенный кэш активной темы в памяти (без чтения config.json с диска на каждый кадр).
"""
import os
import json
from pathlib import Path
from typing import Optional

_CURRENT_THEME_CACHE: Optional[str] = None


def get_current_theme(force_reload: bool = False) -> str:
    """Возвращает текущую тему ('dark' или 'light') из кэша в памяти (чтение с диска — только 1 раз)."""
    global _CURRENT_THEME_CACHE
    if _CURRENT_THEME_CACHE is not None and not force_reload:
        return _CURRENT_THEME_CACHE
    try:
        cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            _CURRENT_THEME_CACHE = "light" if str(data.get("app_theme", "dark")).lower() == "light" else "dark"
            return _CURRENT_THEME_CACHE
    except Exception:
        pass
    _CURRENT_THEME_CACHE = "dark"
    return _CURRENT_THEME_CACHE


def set_current_theme(theme: str) -> str:
    """Обновляет кэш темы в памяти и сохраняет выбор в config.json."""
    global _CURRENT_THEME_CACHE
    norm = "light" if str(theme).lower() == "light" else "dark"
    _CURRENT_THEME_CACHE = norm
    try:
        cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
        cfg = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        cfg["app_theme"] = norm
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return norm


def is_light_theme() -> bool:
    """Быстрая проверка (0 мс, из памяти): активна ли светлая тема."""
    return get_current_theme() == "light"


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

QLabel[heading="true"], QLabel#pageTitle, QLabel#sectionTitle {
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

QTableWidget QTableCornerButton::section,
QTableWidget QHeaderView::section:vertical {
    background-color: #161c24;
    color: #64748b;
    border: none;
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
QLabel#pointBadge {
    background-color: rgba(59,130,246,.18);
    color: #93c5fd;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#pointLabel { color: #8b97a6; font-size: 12px; }
QLabel#meta { color: #8b97a6; font-size: 13px; }
QLabel#okDot { color: #22c55e; font-weight: 700; }
QLabel#headerAvatar {
    background-color: #232e3c;
    color: #e6edf3;
    border: 1px solid #3a4a5c;
    border-radius: 13px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#headerDeviceBadge[state="ok"] {
    background-color: rgba(34, 197, 94, 0.16);
    color: #4ade80;
    border: 1px solid rgba(34, 197, 94, 0.4);
    padding: 2px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#headerDeviceBadge[state="warn"] {
    background-color: rgba(245, 158, 11, 0.18);
    color: #fbbf24;
    border: 1px solid rgba(245, 158, 11, 0.45);
    padding: 2px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#headerDeviceBadge[state="err"] {
    background-color: rgba(239, 68, 68, 0.18);
    color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.45);
    padding: 2px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
}

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
   TABS, CARDS, GROUPBOX & TABLES
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

QFrame#card, QFrame#kpi, QFrame#scanbar, QFrame#counter, QFrame#headerCard, QFrame#batchCard {
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

QGroupBox {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
    margin-top: 10px;
    padding: 14px;
    color: #f8fafc;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #94a3b8;
    font-weight: 700;
}

QTableWidget, QTreeWidget {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
    gridline-color: #2a3542;
    color: #e6edf3;
    font-size: 14px;
    outline: none;
}
QTableWidget::item, QTreeWidget::item {
    padding: 7px 10px;
    border-bottom: 1px solid #232c37;
}
QTableWidget::item:selected, QTreeWidget::item:selected {
    background-color: rgba(59,130,246,.25);
    color: #ffffff;
}
QTreeWidget::branch { background: transparent; }
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
   BUTTONS, INPUTS, MENUS & SCROLLBARS
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
QPushButton[primary="true"], QPushButton#loginButton {
    background-color: #2563eb;
    border-color: #2563eb;
    color: #fff;
}
QPushButton[primary="true"]:hover:!disabled, QPushButton#loginButton:hover:!disabled {
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
    selection-color: #ffffff;
    color: #e6edf3;
    font-size: 14px;
    outline: none;
}

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
    width: 22px;
    height: 15px;
    border-left: 1px solid #334155;
    border-bottom: 1px solid #334155;
    background-color: #2563eb;
    border-top-right-radius: 5px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    height: 15px;
    border-left: 1px solid #334155;
    background-color: #2563eb;
    border-bottom-right-radius: 5px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #3b82f6;
}

QCheckBox {
    color: #e6edf3;
    font-size: 14px;
    spacing: 6px;
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

QMenu {
    background-color: #1c2530;
    color: #e6edf3;
    border: 1px solid #2a3542;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 20px 6px 12px;
    border-radius: 5px;
}
QMenu::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

QScrollBar:vertical {
    background: #141b23;
    width: 8px;
    margin: 2px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #334155;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #475569;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

/* ================================================================
   STARTUP PROBLEMS & MODAL WINDOWS
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
    background-color: #e6eaef;
    color: #1e293b;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    font-size: 15px;
}

QLabel {
    background: transparent;
    border: none;
    color: #1e293b;
}

QLabel[heading="true"], QLabel#pageTitle, QLabel#sectionTitle {
    color: #0f172a;
    font-size: 18px;
    font-weight: 700;
}

QFrame#warningBanner {
    background-color: #fef3c7;
    border-bottom: 1px solid #f59e0b;
}
QLabel#bannerIcon { color: #d97706; font-size: 17px; }
QLabel#bannerLabel { color: #92400e; font-size: 13px; font-weight: 600; }
QPushButton#bannerButton {
    background-color: #ffffff;
    border: 1px solid #f59e0b;
    border-radius: 6px;
    color: #b45309;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 12px;
}
QPushButton#bannerButton:hover {
    background-color: #fde68a;
}

QFrame#header {
    background-color: #f3f5f8;
    border-bottom: 1px solid #cfd6e0;
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
QLabel#pointBadge {
    background-color: #dbeafe;
    color: #1d4ed8;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#pointLabel { color: #475569; font-size: 12px; }
QLabel#meta { color: #64748b; font-size: 13px; }
QLabel#okDot { color: #16a34a; font-weight: 700; }
QLabel#headerAvatar {
    background-color: #e2e8f0;
    color: #1e293b;
    border: 1px solid #cbd5e1;
    border-radius: 13px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#headerDeviceBadge[state="ok"] {
    background-color: #dcfce7;
    color: #15803d;
    border: 1px solid #86efac;
    padding: 2px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#headerDeviceBadge[state="warn"] {
    background-color: #fef3c7;
    color: #b45309;
    border: 1px solid #fcd34d;
    padding: 2px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#headerDeviceBadge[state="err"] {
    background-color: #fee2e2;
    color: #b91c1c;
    border: 1px solid #fca5a5;
    padding: 2px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
}

QFrame#sidebar {
    background-color: #f3f5f8;
    border-right: 1px solid #cfd6e0;
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
    background-color: #e2e8f0;
    color: #0f172a;
}
QPushButton#navBtn:checked {
    background-color: #3b6ea5;
    color: #ffffff;
    font-weight: 700;
}
QLabel#navSection {
    color: #64748b;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
    padding: 16px 14px 6px;
}

QFrame#card, QFrame#kpi, QFrame#scanbar, QFrame#counter, QFrame#headerCard, QFrame#batchCard {
    background-color: #f3f5f8;
    border: 1px solid #cfd6e0;
    border-radius: 10px;
}
QLabel#cardTitle {
    font-size: 12px;
    font-weight: 700;
    color: #64748b;
    letter-spacing: 1.2px;
}

QGroupBox {
    background-color: #f3f5f8;
    border: 1px solid #cfd6e0;
    border-radius: 10px;
    margin-top: 12px;
    padding: 14px;
    color: #2d3748;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #4a5568;
    font-weight: 700;
}

QLineEdit#scanInput {
    background-color: #ebeff4;
    border: 2px solid #cbd5e1;
    border-radius: 8px;
    padding: 10px 14px;
    color: #0f172a;
    font-family: 'Consolas', monospace;
    font-size: 15px;
}
QLineEdit#scanInput:focus {
    border-color: #3b6ea5;
    background-color: #f8fafc;
}
QLabel#scanStatus {
    color: #475569;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 12px;
    background: #e2e8f0;
    border-radius: 6px;
}

QTabWidget::pane {
    background-color: #f3f5f8;
    border: 1px solid #cfd6e0;
    border-radius: 10px;
}
QTabWidget::tab-bar { alignment: left; }
QTabBar::tab {
    background: #dde3ea;
    color: #4a5568;
    padding: 8px 18px;
    border: 1px solid #c4cdd8;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 3px;
    font-size: 14px;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background: #e6eaef;
    color: #1e293b;
}
QTabBar::tab:selected {
    background: #3b6ea5;
    color: #ffffff;
    border-color: #3b6ea5;
    font-weight: 700;
}

QTableWidget, QTreeWidget {
    background-color: #f3f5f8;
    alternate-background-color: #ebeff4;
    border: 1px solid #cfd6e0;
    border-radius: 10px;
    gridline-color: #e2e7ee;
    color: #2d3748;
    font-size: 14px;
    outline: none;
}
QTableWidget::item, QTreeWidget::item {
    padding: 7px 10px;
    border-bottom: 1px solid #e6eaef;
    color: #2d3748;
}
QTableWidget::item:selected, QTreeWidget::item:selected {
    background-color: #d6e4f0;
    color: #1e3a5f;
    font-weight: 600;
}
QTreeWidget::branch { background: transparent; }
QHeaderView::section {
    background-color: #e4e9f0;
    color: #4a5568;
    padding: 9px 10px;
    border: none;
    border-bottom: 1px solid #cbd2dc;
    font-size: 12px;
    font-weight: 700;
}
QTableWidget QTableCornerButton::section,
QTableWidget QHeaderView::section:vertical {
    background-color: #e4e9f0;
    color: #64748b;
    border: none;
    border-right: 1px solid #cbd2dc;
    border-bottom: 1px solid #d8dfe8;
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
    color: #1e293b;
    font-size: 14px;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    background-color: #ffffff;
}
QCheckBox::indicator:hover {
    border-color: #3b6ea5;
}
QCheckBox::indicator:checked {
    background-color: #3b6ea5;
    border-color: #3b6ea5;
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24'><path fill='%23ffffff' d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z'/></svg>");
}

QPushButton {
    background-color: #ebeff4;
    border: 1px solid #c4cdd8;
    border-radius: 8px;
    padding: 7px 14px;
    color: #2d3748;
    font-size: 14px;
    font-weight: 600;
}
QPushButton:hover:!disabled {
    background-color: #dfe5ec;
    border-color: #94a3b8;
    color: #0f172a;
}
QPushButton:disabled {
    color: #94a3b8;
    background-color: #e2e8f0;
    border-color: #cbd5e1;
}
QPushButton[primary="true"], QPushButton#loginButton {
    background-color: #3b6ea5;
    border-color: #2e5988;
    color: #ffffff;
    font-weight: 700;
}
QPushButton[primary="true"]:hover:!disabled, QPushButton#loginButton:hover:!disabled {
    background-color: #2e5988;
}

QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {
    background-color: #ebeff4;
    border: 1px solid #c4cdd8;
    border-radius: 6px;
    padding: 5px 10px;
    color: #1e293b;
    font-size: 14px;
    font-weight: 500;
}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QComboBox:focus {
    background-color: #f8fafc;
    border: 1px solid #3b6ea5;
}
QComboBox QAbstractItemView {
    background-color: #f3f5f8;
    color: #1e293b;
    border: 1px solid #c4cdd8;
    selection-background-color: #d6e4f0;
    selection-color: #1e3a5f;
    outline: none;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    height: 15px;
    border-left: 1px solid #c4cdd8;
    border-bottom: 1px solid #c4cdd8;
    background-color: #e2e8f0;
    border-top-right-radius: 5px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    height: 15px;
    border-left: 1px solid #c4cdd8;
    background-color: #e2e8f0;
    border-bottom-right-radius: 5px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #cbd5e1;
}

QMenu {
    background-color: #f3f5f8;
    color: #1e293b;
    border: 1px solid #c4cdd8;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 20px 6px 12px;
    border-radius: 5px;
}
QMenu::item:selected {
    background-color: #3b6ea5;
    color: #ffffff;
}

QScrollBar:vertical {
    background: #e6eaef;
    width: 8px;
    margin: 2px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #b8c2cf;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #94a3b8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QDialog#startupWindow { background-color: #e6eaef; }
QDialog#startupWindow QScrollArea,
QDialog#startupWindow QScrollArea QWidget,
QDialog#startupWindow QWidget#scrollContent {
    background-color: transparent;
    border: none;
}
QDialog#startupWindow QFrame#problemCard {
    background-color: #f3f5f8;
    border: 1px solid #cfd6e0;
    border-radius: 10px;
}
QDialog#startupWindow QLabel#problemName { color: #0f172a; font-size: 15px; font-weight: 700; }
QDialog#startupWindow QLabel#problemHint { color: #475569; font-size: 12px; }
QDialog#startupWindow QPushButton#continueButton { background-color: #3b6ea5; color: #fff; }
QDialog#startupWindow QPushButton#auxButton,
QDialog#startupWindow QPushButton#restartButton,
QDialog#startupWindow QPushButton#quitButton {
    background-color: #f3f5f8;
    border: 1px solid #cbd5e1;
    color: #334155;
}
"""


def get_qss(theme: Optional[str] = None) -> str:
    """Возвращает QSS-стили в зависимости от выбранной темы и синхронизирует кэш в памяти."""
    global _CURRENT_THEME_CACHE
    if theme is None:
        theme = get_current_theme()
    else:
        _CURRENT_THEME_CACHE = "light" if str(theme).lower() == "light" else "dark"
    if _CURRENT_THEME_CACHE == "light":
        return LIGHT_QSS
    return APP_QSS