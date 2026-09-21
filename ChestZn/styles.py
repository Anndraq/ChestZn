"""
Тёмная тема ChestZn.
Все стили приложения в одном файле.
"""

APP_QSS = """
/* ================================================================
   БАЗОВЫЕ ПРАВИЛА
   ================================================================ */
QMainWindow {
    background-color: #0f1419;
}
QDialog {
    background-color: #0f1419;
}

/* QLabel — принудительно прозрачные */
QLabel {
    background: transparent;
    border: none;
}

QMainWindow, QDialog {
    color: #e6edf3;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}


/* ================================================================
   WARNING BANNER (жёлтая полоса сверху главного окна)
   ================================================================ */
QFrame#warningBanner {
    background-color: rgba(245, 158, 11, 0.15);
    border-bottom: 1px solid rgba(245, 158, 11, 0.4);
}
QLabel#bannerIcon {
    color: #fbbf24;
    font-size: 16px;
}
QLabel#bannerLabel {
    color: #fbbf24;
    font-size: 12px;
}
QPushButton#bannerButton {
    background-color: transparent;
    border: 1px solid rgba(245, 158, 11, 0.5);
    border-radius: 6px;
    color: #fbbf24;
    font-size: 11px;
    padding: 4px 12px;
}
QPushButton#bannerButton:hover {
    background-color: rgba(245, 158, 11, 0.2);
    border-color: #fbbf24;
}

/* ================================================================
   HEADER (шапка главного окна)
   ================================================================ */
QFrame#header {
    background-color: #161c24;
    border-bottom: 1px solid #2a3542;
}
QLabel#brand {
    font-size: 14px;
    font-weight: 600;
    color: #e6edf3;
    background: transparent;
}
QLabel#brandRole {
    background-color: rgba(139,92,246,.18);
    color: #c4b5fd;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
}
QLabel#meta {
    color: #8b97a6;
    font-size: 12px;
    background: transparent;
}
QLabel#okDot {
    color: #22c55e;
    font-weight: 600;
    background: transparent;
}


/* ================================================================
   TABS
   ================================================================ */
QTabWidget::pane {
    background-color: #0f1419;
    border: none;
}
QTabWidget::tab-bar { alignment: left; }
QTabBar::tab {
    background: #1c2530;
    color: #8b97a6;
    padding: 8px 18px;
    border: 1px solid #2a3542;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #3b82f6;
    color: #fff;
}
QTabBar::tab:hover:!selected {
    background: #232e3c;
    color: #e6edf3;
}


/* ================================================================
   CARDS (карточки в операторе и админке)
   ================================================================ */
QFrame#card {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
}
QLabel#cardTitle {
    font-size: 11px;
    font-weight: 600;
    color: #8b97a6;
    letter-spacing: 1.5px;
    background: transparent;
}


/* ================================================================
   SCAN BAR (панель сканирования)
   ================================================================ */
QFrame#scanbar {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
}
QLineEdit#scanInput {
    background-color: #1c2530;
    border: 2px solid #2a3542;
    border-radius: 8px;
    padding: 10px 14px;
    color: #e6edf3;
    font-family: 'Consolas', monospace;
    font-size: 14px;
}
QLineEdit#scanInput:focus {
    border-color: #3b82f6;
}
QLabel#scanStatus {
    color: #8b97a6;
    font-size: 13px;
    font-weight: 500;
    padding: 6px 12px;
    background: #1c2530;
    border-radius: 6px;
}
QLabel#scanStatus[state="ok"]   { color: #22c55e; background: rgba(34,197,94,.12); }
QLabel#scanStatus[state="err"]  { color: #ef4444; background: rgba(239,68,68,.12); }
QLabel#scanStatus[state="warn"] { color: #f59e0b; background: rgba(245,158,11,.12); }


/* ================================================================
   DATA FIELDS (поля данных)
   ================================================================ */
QLabel.fieldKey {
    color: #8b97a6;
    background: transparent;
}
QLabel.fieldValue {
    color: #e6edf3;
    font-family: 'Consolas', monospace;
    background: transparent;
}
QLabel.fieldValue[empty="true"] {
    color: #64748b;
    font-style: italic;
    font-family: 'Segoe UI', sans-serif;
}


/* ================================================================
   BADGE (метки статуса)
   ================================================================ */
QLabel#badge {
    font-size: 11px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 4px;
    letter-spacing: .3px;
}
QLabel#badge[state="idle"] { background: rgba(100,116,139,.15); color: #64748b; }
QLabel#badge[state="ok"]   { background: rgba(34,197,94,.15);  color: #22c55e; }
QLabel#badge[state="err"]  { background: rgba(239,68,68,.15);  color: #ef4444; }
QLabel#badge[state="warn"] { background: rgba(245,158,11,.15); color: #f59e0b; }
QLabel#badge[state="info"] { background: rgba(59,130,246,.15); color: #93c5fd; }


/* ================================================================
   BUTTONS (общие кнопки)
   ================================================================ */
QPushButton {
    background-color: #1c2530;
    border: 1px solid #2a3542;
    border-radius: 8px;
    padding: 10px 16px;
    color: #e6edf3;
    font-weight: 500;
}
QPushButton:hover:!disabled {
    background-color: #232e3c;
    border-color: #3a4a5c;
}
QPushButton:disabled {
    color: #64748b;
}
QPushButton[primary="true"] {
    background-color: #3b82f6;
    border-color: #3b82f6;
    color: #fff;
}
QPushButton[primary="true"]:hover:!disabled {
    background-color: #2563eb;
}
QPushButton[danger="true"] {
    color: #fca5a5;
    border-color: rgba(239,68,68,.3);
}
QPushButton[danger="true"]:hover:!disabled {
    background-color: rgba(239,68,68,.1);
}


/* ================================================================
   COUNTER (счётчики оператора)
   ================================================================ */
QFrame#counter {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
}
QLabel.counterKey {
    color: #8b97a6;
    background: transparent;
}
QLabel.counterValue {
    color: #e6edf3;
    font-family: 'Consolas', monospace;
    font-size: 14px;
    background: transparent;
}
QLabel.counterValue[warn="true"] {
    color: #f59e0b;
}


/* ================================================================
   PREVIEW (предпросмотр этикетки)
   ================================================================ */
QFrame#previewBody {
    background-color: #1c2530;
    border: 1px dashed #2a3542;
    border-radius: 8px;
}
QFrame#labelMock {
    background-color: #ffffff;
    border-radius: 6px;
}
QFrame#labelMock QLabel {
    background: transparent;
    border: none;
    color: #000000;
}
QFrame#labelMock QLabel#lmName {
    color: #000000;
    font-weight: 600;
    font-size: 12px;
}
QFrame#labelMock QLabel.lmField {
    color: #000000;
    font-family: Consolas, monospace;
    font-size: 11px;
}


/* ================================================================
   ADMIN SIDEBAR (боковое меню админки)
   ================================================================ */
QFrame#sidebar {
    background-color: #161c24;
    border-right: 1px solid #2a3542;
}
QPushButton#navBtn {
    background: transparent;
    border: none;
    color: #8b97a6;
    text-align: left;
    padding: 9px 14px;
    border-radius: 7px;
    font-size: 13px;
}
QPushButton#navBtn:hover {
    background-color: #1c2530;
    color: #e6edf3;
}
QPushButton#navBtn:checked {
    background-color: #3b82f6;
    color: #ffffff;
    font-weight: 500;
}
QLabel#navSection {
    color: #8b97a6;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    padding: 14px 14px 6px;
    background: transparent;
}


/* ================================================================
   KPI (дашборд админки)
   ================================================================ */
QFrame#kpi {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
}
QLabel.kpiLabel {
    color: #8b97a6;
    font-size: 11px;
    letter-spacing: .5px;
    background: transparent;
}
QLabel.kpiValue {
    font-size: 24px;
    font-weight: 600;
    font-family: 'Consolas', monospace;
    background: transparent;
}
QLabel.kpiValue[color="accent"] { color: #3b82f6; }
QLabel.kpiValue[color="ok"]     { color: #22c55e; }
QLabel.kpiValue[color="warn"]   { color: #f59e0b; }
QLabel.kpiValue[color="err"]    { color: #ef4444; }


/* ================================================================
   TABLES
   ================================================================ */
QTableWidget {
    background-color: #161c24;
    border: 1px solid #2a3542;
    border-radius: 10px;
    gridline-color: #2a3542;
    color: #e6edf3;
}
QTableWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid #2a3542;
}
QTableWidget::item:selected {
    background-color: rgba(59,130,246,.2);
    color: #e6edf3;
}
QHeaderView::section {
    background-color: #161c24;
    color: #8b97a6;
    padding: 9px 10px;
    border: none;
    border-bottom: 1px solid #2a3542;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: .5px;
}


/* ================================================================
   SCROLLBARS
   ================================================================ */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #232e3c;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3a4a5c;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background: #232e3c;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #3a4a5c;
}


/* ================================================================
   INPUTS (LineEdit, ComboBox)
   ================================================================ */
QLineEdit, QComboBox {
    background-color: #1c2530;
    border: 1px solid #2a3542;
    border-radius: 6px;
    padding: 7px 11px;
    color: #e6edf3;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #3b82f6;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background: #1c2530;
    border: 1px solid #2a3542;
    selection-background-color: #3b82f6;
    color: #e6edf3;
}


/* ================================================================
   STARTUP PROBLEMS WINDOW (окно проверки при запуске)
   ================================================================ */
QDialog#startupWindow {
    background-color: #0f1419;
}
QDialog#startupWindow QLabel {
    background: transparent;
    border: none;
}
QDialog#startupWindow QWidget#scrollContent {
    background: transparent;
}

/* Заголовок */
QDialog#startupWindow QLabel#startupTitle {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 2px;
    color: #8b97a6;
}
QDialog#startupWindow QLabel#startupTitle[state="ok"]       { color: #22c55e; }
QDialog#startupWindow QLabel#startupTitle[state="warning"]  { color: #f59e0b; }
QDialog#startupWindow QLabel#startupTitle[state="critical"] { color: #ef4444; }

QDialog#startupWindow QLabel#startupSubtitle {
    color: #8b97a6;
    font-size: 12px;
}

/* Карточки проблем */
QDialog#startupWindow QFrame#problemCard {
    background-color: #1c2530;
    border: 1px solid #2a3542;
    border-radius: 8px;
}
QDialog#startupWindow QLabel#problemIcon {
    font-size: 16px;
}
QDialog#startupWindow QLabel#problemIcon[color="ok"]      { color: #22c55e; }
QDialog#startupWindow QLabel#problemIcon[color="warning"] { color: #f59e0b; }
QDialog#startupWindow QLabel#problemIcon[color="error"]   { color: #ef4444; }

QDialog#startupWindow QLabel#problemName {
    color: #e6edf3;
    font-size: 13px;
    font-weight: 600;
}
QDialog#startupWindow QLabel#problemMessage {
    font-size: 12px;
}
QDialog#startupWindow QLabel#problemMessage[color="ok"]      { color: #22c55e; }
QDialog#startupWindow QLabel#problemMessage[color="warning"] { color: #f59e0b; }
QDialog#startupWindow QLabel#problemMessage[color="error"]   { color: #ef4444; }

QDialog#startupWindow QLabel#problemHint {
    color: #8b97a6;
    font-size: 11px;
    font-family: Consolas, monospace;
}

/* Кнопки */
QDialog#startupWindow QPushButton#actionButton {
    background-color: #232e3c;
    border: 1px solid #2a3542;
    border-radius: 6px;
    color: #93c5fd;
    font-size: 11px;
    padding: 5px 12px;
}
QDialog#startupWindow QPushButton#actionButton:hover {
    background-color: #3b82f6;
    color: #ffffff;
    border-color: #3b82f6;
}

QDialog#startupWindow QPushButton#continueButton {
    background-color: #3b82f6;
    border: none;
    border-radius: 8px;
    color: #ffffff;
    font-size: 13px;
    font-weight: 600;
}
QDialog#startupWindow QPushButton#continueButton:hover {
    background-color: #2563eb;
}
QDialog#startupWindow QPushButton#continueButton:disabled {
    background-color: #2a3542;
    color: #64748b;
}

QDialog#startupWindow QPushButton#quitButton {
    background-color: transparent;
    border: 1px solid #2a3542;
    border-radius: 8px;
    color: #8b97a6;
    font-size: 13px;
}
QDialog#startupWindow QPushButton#quitButton:hover {
    background-color: #1c2530;
    color: #e6edf3;
}

QDialog#startupWindow QPushButton#auxButton {
    background-color: transparent;
    border: 1px solid #2a3542;
    border-radius: 6px;
    color: #8b97a6;
    font-size: 11px;
    padding: 6px 10px;
}
QDialog#startupWindow QPushButton#auxButton:hover {
    background-color: #1c2530;
    color: #e6edf3;
    border-color: #3a4a5c;
}

QDialog#startupWindow QPushButton#restartButton {
    background-color: transparent;
    border: 1px solid #2a3542;
    border-radius: 6px;
    color: #8b97a6;
    font-size: 12px;
}
QDialog#startupWindow QPushButton#restartButton:hover {
    background-color: #1c2530;
    color: #e6edf3;
}
"""