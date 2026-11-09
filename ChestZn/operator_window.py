"""
Окно оператора (operator_window.py).
Полная поддержка тёмной и светлой темы через CSS-селекторы.
Инлайн-хардкод тёмного цвета убран.
"""
from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QLineEdit, QPushButton, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal

from store import Store
from toast import Toast


class OperatorWindow(QWidget):
    shift_report_requested = Signal()

    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.toast = Toast(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # 1. Верхний блок: Пост и принтер
        top_card = QFrame()
        top_card.setObjectName("card")
        tc_layout = QHBoxLayout(top_card)
        tc_layout.setContentsMargins(16, 12, 16, 12)
        tc_layout.setSpacing(16)

        self.lbl_point = QLabel("📍 Пост: —")
        self.lbl_point.setStyleSheet("font-size: 15px; font-weight: 700;")
        tc_layout.addWidget(self.lbl_point)

        self.printer_status_badge = QLabel("⚪ Проверка принтера...")
        self.printer_status_badge.setObjectName("badge")
        self.printer_status_badge.setProperty("state", "idle")
        tc_layout.addWidget(self.printer_status_badge)

        tc_layout.addStretch(1)

        btn_report = QPushButton("📊 Отправить отчёт за смену")
        btn_report.clicked.connect(self._on_shift_report)
        tc_layout.addWidget(btn_report)

        root.addWidget(top_card)

        # 2. Карточка текущего заказа
        order_card = QFrame()
        order_card.setObjectName("card")
        oc_layout = QVBoxLayout(order_card)
        oc_layout.setContentsMargins(16, 12, 16, 12)
        oc_layout.setSpacing(8)

        oc_top = QHBoxLayout()
        btn_change_order = QPushButton("🔄 Сменить заказ")
        btn_change_order.setProperty("primary", True)
        btn_change_order.clicked.connect(self._on_change_order)
        oc_top.addWidget(btn_change_order)

        self.lbl_order_title = QLabel("Текущий заказ: Основной поток маркировки")
        self.lbl_order_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        oc_top.addWidget(self.lbl_order_title)
        oc_top.addStretch(1)

        self.lbl_order_stats = QLabel("0 / 0 шт. (0%)")
        self.lbl_order_stats.setStyleSheet("font-weight: 700; font-family: Consolas;")
        oc_top.addWidget(self.lbl_order_stats)
        oc_layout.addLayout(oc_top)

        self.order_progress = QProgressBar()
        self.order_progress.setFixedHeight(6)
        self.order_progress.setTextVisible(False)
        self.order_progress.setRange(0, 100)
        self.order_progress.setValue(0)
        oc_layout.addWidget(self.order_progress)

        root.addWidget(order_card)

        # 3. Панель сканирования
        scan_card = QFrame()
        scan_card.setObjectName("scanbar")
        sc_layout = QVBoxLayout(scan_card)
        sc_layout.setContentsMargins(16, 14, 16, 14)
        sc_layout.setSpacing(10)

        self.lbl_scan_status = QLabel("🟢 Ожидание сканирования / ручного ввода")
        self.lbl_scan_status.setAlignment(Qt.AlignCenter)
        self.lbl_scan_status.setStyleSheet("font-size: 15px; font-weight: 700;")
        sc_layout.addWidget(self.lbl_scan_status)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self.scan_input = QLineEdit()
        self.scan_input.setObjectName("scanInput")
        self.scan_input.setPlaceholderText("Ожидание сканирования / ручного ввода...")
        self.scan_input.returnPressed.connect(self._on_scan_entered)
        input_row.addWidget(self.scan_input, stretch=1)

        btn_reprint = QPushButton("🔁 Повтор печати")
        btn_reprint.clicked.connect(self._on_reprint)
        input_row.addWidget(btn_reprint)
        sc_layout.addLayout(input_row)

        root.addWidget(scan_card)

        # 4. Средний блок: последняя наклейка + счётчики
        mid_row = QHBoxLayout()
        mid_row.setSpacing(14)

        # Последняя этикетка
        last_card = QFrame()
        last_card.setObjectName("card")
        lc_layout = QVBoxLayout(last_card)
        lc_layout.setContentsMargins(14, 12, 14, 12)
        lc_layout.setSpacing(6)

        lc_title = QLabel("ПОСЛЕДНЯЯ НАПЕЧАТАННАЯ НАКЛЕЙКА")
        lc_title.setObjectName("cardTitle")
        lc_layout.addWidget(lc_title)

        self.lbl_last_product = QLabel("Пока нет напечатанных этикеток")
        self.lbl_last_product.setStyleSheet("font-size: 15px; font-weight: 700;")
        lc_layout.addWidget(self.lbl_last_product)

        self.lbl_last_meta = QLabel("ШК: —  |  Код ЧЗ: —")
        self.lbl_last_meta.setObjectName("fieldValue")
        lc_layout.addWidget(self.lbl_last_meta)
        mid_row.addWidget(last_card, stretch=2)

        # Блоки счётчиков
        self.kpi_widgets = {}
        for key, title, val_color in [
            ("prints", "Напечатано", "#10b981"),
            ("scans", "Сканов", "#3b82f6"),
            ("errors", "Ошибок", "#ef4444"),
        ]:
            c_box = QFrame()
            c_box.setObjectName("counter")
            c_box.setFixedWidth(130)
            cb_layout = QVBoxLayout(c_box)
            cb_layout.setContentsMargins(12, 10, 12, 10)
            cb_layout.setSpacing(4)

            lbl_t = QLabel(title)
            lbl_t.setObjectName("cardTitle")
            cb_layout.addWidget(lbl_t)

            lbl_v = QLabel("0")
            lbl_v.setStyleSheet(f"font-size: 26px; font-weight: 800; font-family: Consolas; color: {val_color};")
            cb_layout.addWidget(lbl_v)

            self.kpi_widgets[key] = lbl_v
            mid_row.addWidget(c_box)

        root.addLayout(mid_row)

        # 5. Нижняя таблица: последние действия
        sec_title = QLabel("ПОСЛЕДНИЕ ДЕЙСТВИЯ")
        sec_title.setObjectName("cardTitle")
        root.addWidget(sec_title)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["Время", "Штрихкод", "Серийный номер", "Результат", "Точка"])
        hh = self.history_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        self.history_table.setColumnWidth(0, 110)
        self.history_table.setColumnWidth(1, 160)
        self.history_table.setColumnWidth(3, 140)
        self.history_table.setColumnWidth(4, 90)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setShowGrid(False)
        root.addWidget(self.history_table, stretch=1)

        self.refresh_ui()

    def refresh_ui(self):
        pt = self.store.current_point or "T-01"
        pt_obj = self.store.get_point(pt)
        p_name = pt_obj.get("name", "Основная линия") if pt_obj else "Основная линия"
        self.lbl_point.setText(f"📍 Пост: {pt} — {p_name}")

        cnt = self.store.counter
        self.kpi_widgets["prints"].setText(str(cnt.get("prints", 0)))
        self.kpi_widgets["scans"].setText(str(cnt.get("scans", 0)))
        self.kpi_widgets["errors"].setText(str(cnt.get("errors", 0)))

        self.update_printer_badge()

    def update_printer_badge(self):
        try:
            from device_discovery import check_printer_status
            from printer import load_config, get_printer_for_point

            cfg = load_config()
            pt_cfg = get_printer_for_point(cfg, self.store.current_point) or cfg
            pr_name = pt_cfg.get("printer_name", "")

            st = check_printer_status(pr_name)
            if st.get("connected"):
                self.printer_status_badge.setText(f"🟢 Принтер готов: {pr_name}")
                self.printer_status_badge.setProperty("state", "ok")
            else:
                self.printer_status_badge.setText(f"🔴 Принтер отключён: {pr_name or 'Не выбран'}")
                self.printer_status_badge.setProperty("state", "err")
        except Exception:
            self.printer_status_badge.setText("🟡 Статус принтера: неизвестно")
            self.printer_status_badge.setProperty("state", "warn")

        self.printer_status_badge.style().unpolish(self.printer_status_badge)
        self.printer_status_badge.style().polish(self.printer_status_badge)

    def _on_scan_entered(self):
        text = self.scan_input.text().strip()
        if not text:
            return
        self.scan_input.clear()
        self.toast.show_message(f"Обработан скан: {text}", "ok")

    def _on_reprint(self):
        self.toast.show_message("Повторная печать выполнена", "ok")

    def _on_change_order(self):
        self.toast.show_message("Смена заказа доступна в админ-панели", "warn")

    def _on_shift_report(self):
        self.shift_report_requested.emit()