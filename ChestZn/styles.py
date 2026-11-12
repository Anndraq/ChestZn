"""
Централизованные стили Dotmark (styles.py).
Тёмная и приглушённая мягкая светлая темы оформления + мгновенный кэш активной темы в памяти.
"""
import os
import json
from pathlib import Path
from typing import Optional

_CURRENT_THEME_CACHE: Optional[str] = None


def get_current_theme(force_reload: bool = False) -> str:
    """Возвращает текущую тему ('dark' или 'light') из кэша в памяти (чтение с диска – только 1 раз)."""
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
/* ============================================================================
   ТЁМНАЯ ТЕМА — БАЗА
   ============================================================================ */
QMainWindow, QDialog, QStackedWidget, QStackedWidget > QWidget {
    background-color: #0f1419;
    color: #e6edf3;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    font-size: 15px;
}

QFrame#header {
    background-color: #161b22;
    border-bottom: 1px solid #30363d;
}

QFrame#warningBanner {
    background-color: #241e12;
    border-bottom: 1px solid #4a3b1c;
}

QLabel#bannerIcon, QLabel#bannerLabel {
    background: transparent;
    color: #f3d078;
    font-size: 13px;
    font-weight: 600;
}

QPushButton#bannerButton {
    background-color: #2c2619;
    border: 1px solid #6b5428;
    border-radius: 6px;
    color: #f5deb3;
    font-size: 13px;
    font-weight: 600;
    padding: 4px 14px;
    min-height: 20px;
}
QPushButton#bannerButton:hover {
    background-color: #3d3320;
    border-color: #9c7a38;
    color: #ffffff;
}
QPushButton#bannerButton:pressed {
    background-color: #211c12;
}

QFrame#sidebar {
    background-color: #131820;
    border-right: 1px solid #262c36;
}

QLabel#navSection {
    color: #7d8590;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 10px 10px 4px 10px;
    background: transparent;
}

QPushButton#navBtn {
    background-color: transparent;
    color: #b1bac4;
    border: none;
    border-radius: 8px;
    padding: 9px 12px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#navBtn:hover:!checked {
    background-color: #1f2631;
    color: #f0f6fc;
}
QPushButton#navBtn:checked {
    background-color: #2563eb;
    color: #ffffff;
    font-weight: 700;
}

QTabWidget::pane {
    border: 1px solid #30363d;
    border-radius: 10px;
    background-color: #0f1419;
}
QTabBar::tab {
    background-color: #161b22;
    color: #8b949e;
    border: 1px solid #30363d;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 16px;
    margin-right: 4px;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background-color: #1c2129;
    color: #e6edf3;
}
QTabBar::tab:selected {
    background-color: #2563eb;
    color: #ffffff;
    border-color: #2563eb;
}

QFrame#card {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
}

QFrame#card_interactive {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
}
QFrame#card_interactive:hover {
    border-color: #58a6ff;
    background-color: #1c2129;
}

QLabel {
    color: #e6edf3;
    font-size: 14px;
}

QLabel#title {
    font-size: 22px;
    font-weight: 700;
    color: #ffffff;
}

QLabel#subtitle {
    font-size: 14px;
    color: #8b949e;
}

QLabel#badge {
    background-color: #1f6beb;
    color: #ffffff;
    font-size: 12px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 10px;
}

QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    color: #e6edf3;
    padding: 10px 14px;
    font-size: 14px;
    selection-background-color: #1f6beb;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 2px solid #58a6ff;
    background-color: #161b22;
}

QComboBox {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    color: #e6edf3;
    padding: 8px 14px;
    font-size: 14px;
}
QComboBox:focus {
    border: 2px solid #58a6ff;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #161b22;
    border: 1px solid #30363d;
    color: #e6edf3;
    selection-background-color: #1f6beb;
    selection-color: #ffffff;
}

QPushButton {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    color: #c9d1d9;
    font-size: 14px;
    font-weight: 600;
    padding: 10px 20px;
}
QPushButton:hover {
    background-color: #30363d;
    border-color: #8b949e;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #161b22;
}

QPushButton#btn_primary {
    background-color: #238636;
    border: 1px solid #2ea043;
    color: #ffffff;
}
QPushButton#btn_primary:hover {
    background-color: #2ea043;
}
QPushButton#btn_primary:pressed {
    background-color: #1a7f37;
}

QPushButton[primary=true], QPushButton#btn_accent {
    background-color: #1f6beb;
    border: 1px solid #388bfd;
    color: #ffffff;
}
QPushButton[primary=true]:hover, QPushButton#btn_accent:hover {
    background-color: #388bfd;
}

QPushButton#btn_danger {
    background-color: #da3633;
    border: 1px solid #f85149;
    color: #ffffff;
}
QPushButton#btn_danger:hover {
    background-color: #f85149;
}

QTableWidget, QTableView {
    background-color: #0d1117;
    alternate-background-color: #131922;
    border: 1px solid #30363d;
    border-radius: 8px;
    gridline-color: #21262d;
    color: #e6edf3;
    font-size: 13px;
    selection-background-color: #1f6beb;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #161b22;
    color: #8b949e;
    font-weight: 600;
    font-size: 13px;
    padding: 8px;
    border: none;
    border-bottom: 2px solid #30363d;
}


QScrollArea {
    background-color: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}


QFrame#kpi {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
}

QCheckBox {
    color: #e6edf3;
    spacing: 8px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #475569;
    background-color: #0d1117;
}
QCheckBox::indicator:checked {
    background-color: #3b6ea5;
    border-color: #60a5fa;
}

QTableWidget::item, QTableView::item {
    padding: 6px 10px;
    border-bottom: 1px solid #21262d;
}

QScrollBar:vertical {
    background-color: #0d1117;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background-color: #30363d;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #484f58;
}
"""


LIGHT_QSS = """
/* ============================================================================
   СВЕТЛАЯ ТЕМА — ПРИГЛУШЁННАЯ, КОМФОРТНАЯ ДЛЯ ГЛАЗ (SOFT NORDIC STONE)
   ============================================================================ */
QMainWindow, QDialog, QStackedWidget, QStackedWidget > QWidget {
    background-color: #dce2ea;
    color: #1e293b;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    font-size: 15px;
}

QFrame#header {
    background-color: #d4dbe4;
    border-bottom: 1px solid #bec8d4;
}

QFrame#sidebar {
    background-color: #d2d9e3;
    border-right: 1px solid #bec8d4;
}

QLabel#navSection {
    color: #5a6778;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 10px 10px 4px 10px;
}

QPushButton#navBtn {
    background-color: transparent;
    color: #334155;
    border: none;
    border-radius: 8px;
    padding: 9px 12px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#navBtn:hover:!checked {
    background-color: #c5cedb;
    color: #0f172a;
}
QPushButton#navBtn:checked {
    background-color: #3b6ea5;
    color: #f1f5f9;
    font-weight: 700;
}

QFrame#card {
    background-color: #e8ecf1;
    border: 1px solid #c2cbd6;
    border-radius: 12px;
}

QFrame#card_interactive {
    background-color: #e8ecf1;
    border: 1px solid #c2cbd6;
    border-radius: 12px;
}
QFrame#card_interactive:hover {
    border-color: #3b6ea5;
    background-color: #e0e6ee;
}

QLabel {
    color: #1e293b;
    font-size: 14px;
}

QLabel#title {
    font-size: 22px;
    font-weight: 700;
    color: #0f172a;
}

QLabel#subtitle {
    font-size: 14px;
    color: #475569;
}

QLabel#badge {
    background-color: #3b6ea5;
    color: #f1f5f9;
    font-size: 12px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 10px;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background-color: #e3e8ee;
    border: 1px solid #bcc6d2;
    border-radius: 8px;
    color: #1e293b;
    padding: 8px 12px;
    font-size: 14px;
    selection-background-color: #3b6ea5;
    selection-color: #ffffff;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 2px solid #3b6ea5;
    background-color: #edf1f6;
}

QComboBox {
    background-color: #e3e8ee;
    border: 1px solid #bcc6d2;
    border-radius: 8px;
    color: #1e293b;
    padding: 7px 12px;
    font-size: 14px;
}
QComboBox:focus {
    border: 2px solid #3b6ea5;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #e8ecf1;
    border: 1px solid #bcc6d2;
    color: #1e293b;
    selection-background-color: #cad8e8;
    selection-color: #0f172a;
}

QPushButton {
    background-color: #e3e8ee;
    border: 1px solid #bcc6d2;
    border-radius: 8px;
    color: #1e293b;
    font-size: 14px;
    font-weight: 600;
    padding: 8px 16px;
}
QPushButton:hover {
    background-color: #d4dce6;
    border-color: #9ba8b8;
    color: #0f172a;
}
QPushButton:pressed {
    background-color: #c8d2de;
}

QPushButton[primary="true"], QPushButton#btn_accent {
    background-color: #3b6ea5;
    border: 1px solid #2e5988;
    color: #f1f5f9;
}
QPushButton[primary="true"]:hover, QPushButton#btn_accent:hover {
    background-color: #2e5988;
}

QPushButton#btn_primary {
    background-color: #246e53;
    border: 1px solid #1d5943;
    color: #f1f5f9;
}
QPushButton#btn_primary:hover {
    background-color: #1d5943;
}

QPushButton#btn_danger {
    background-color: #b84a52;
    border: 1px solid #9f3a44;
    color: #f1f5f9;
}
QPushButton#btn_danger:hover {
    background-color: #9f3a44;
}

QTabWidget::pane {
    border: 1px solid #c2cbd6;
    border-radius: 10px;
    background-color: #dce2ea;
}
QTabBar::tab {
    background-color: #cfd6e0;
    color: #475569;
    border: 1px solid #bcc6d2;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 16px;
    margin-right: 4px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background-color: #3b6ea5;
    color: #f1f5f9;
    border-color: #3b6ea5;
}

QTableWidget, QTableView {
    background-color: #e8ecf1;
    alternate-background-color: #e1e6ed;
    border: 1px solid #c2cbd6;
    border-radius: 8px;
    gridline-color: #d4dce6;
    color: #1e293b;
    font-size: 13px;
    selection-background-color: #cad8e8;
    selection-color: #0f172a;
}
QHeaderView::section {
    background-color: #dde3eb;
    color: #475569;
    font-weight: 700;
    font-size: 13px;
    padding: 8px;
    border: none;
    border-bottom: 1px solid #c2cbd6;
}

QGroupBox {
    background-color: #e8ecf1;
    border: 1px solid #c2cbd6;
    border-radius: 10px;
    margin-top: 12px;
    padding: 14px;
    color: #1e293b;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #475569;
}


QScrollArea {
    background-color: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}


QFrame#kpi {
    background-color: #e8ecf1;
    border: 1px solid #c2cbd6;
    border-radius: 10px;
}

QFrame#warningBanner {
    background-color: #efe7d6;
    border-bottom: 1px solid #d6c5a3;
}

QLabel#bannerIcon, QLabel#bannerLabel {
    background: transparent;
    color: #5c4314;
    font-size: 13px;
    font-weight: 600;
}

QPushButton#bannerButton {
    background-color: #e4d8be;
    border: 1px solid #c2ae84;
    border-radius: 6px;
    color: #3f2d0c;
    font-size: 13px;
    font-weight: 600;
    padding: 4px 14px;
    min-height: 20px;
}
QPushButton#bannerButton:hover {
    background-color: #d8c8a6;
    border-color: #a68f60;
    color: #1f1504;
}
QPushButton#bannerButton:pressed {
    background-color: #cbb892;
}

QCheckBox {
    color: #1e293b;
    spacing: 8px;
    font-size: 13px;
    font-weight: 500;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #94a3b8;
    background-color: #e8ecf1;
}
QCheckBox::indicator:hover {
    border-color: #3b6ea5;
}
QCheckBox::indicator:checked {
    background-color: #3b6ea5;
    border-color: #2e5988;
}

QTableWidget::item, QTableView::item {
    padding: 6px 10px;
    border-bottom: 1px solid #d4dce6;
    color: #1e293b;
}
QTableWidget::item:selected, QTableView::item:selected {
    background-color: #cad8e8;
    color: #0f172a;
}

QSpinBox, QDoubleSpinBox {
    padding-right: 22px;
    min-height: 24px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 18px;
    border: none;
    background: transparent;
}

QScrollBar:vertical {
    background-color: #dce2ea;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background-color: #b4bfcc;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #94a3b8;
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
