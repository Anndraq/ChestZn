"""
ChestZn — Окно оператора маркировки (operator_window.py)
Разработано по принципам дизайн-системы Контур.Маркировки:
1. Синхронизация прямо на экране оператора (кнопка «Выбрать заказ / партию кодов» с поиском и компактной карточкой прогресса).
2. Никаких блокирующих всплывающих окон QMessageBox при ошибках сканирования (крупный цветной статус со звуком, сбрасывающийся при следующем скане).
3. Статус «Ожидание сканирования / ручного ввода» с цветовой индикацией готовности.
4. Повтор печати: кнопка с выпадающим списком (1 последняя / 3 последних / 5 последних).
5. Удалён лимит смены и кнопка «Полный журнал» (вместо неё — компактная лента последних 5 напечатанных марок).
6. Живой мониторинг оборудования: блокировка сканирования при отключённом принтере и мгновенная авто-разблокировка при подключении.
7. Полная адаптивность под Светлую и Тёмную темы (базовый шрифт 15px, контрастные карточки без тёмных пятен в светлой теме).
"""

import os
import re
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QProgressBar,
    QDialog, QMessageBox, QApplication, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, Slot, QSize
from PySide6.QtGui import QFont, QColor, QBrush, QAction, QCursor

from store import Store
from toast import Toast
from logger import get_logger
from barcode import render_datamatrix, parse_datamatrix_gs1, encode_gs1_datamatrix_svg
import sync as sync_mod
from printer import (
    get_printer_for_point, load_config, print_code,
    detect_language, default_printer
)
from device_discovery import get_currently_connected_printer

log = get_logger("operator")


def get_current_theme() -> str:
    """Возвращает текущую тему ('dark' или 'light') из кэша в памяти styles.py (0 мс)."""
    try:
        from styles import get_current_theme as _get_cached_theme
        return _get_cached_theme()
    except Exception:
        return "dark"


class OperatorWindow(QWidget):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.toast = Toast(self)
        self._last_printed_codes: List[Dict[str, Any]] = []
        self._active_batch_info: Optional[Dict[str, Any]] = None
        self._is_light = (get_current_theme() == "light")

        self._init_ui()

        # Инициализируем многопоточное ядро рабочих мест
        try:
            from workstation_engine import WorkstationEngine
            self.engine = WorkstationEngine(store_instance=self.store, parent=self)
            self.engine.start_all()
        except Exception as e:
            log.warning("WorkstationEngine инициализирован с предупреждением: %s", e)
            self.engine = None

        # Таймер периодического обновления статуса принтера и остатка партии (каждые 3 сек)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(3000)
        self._poll_timer.timeout.connect(self._on_poll_tick)
        self._poll_timer.start()

        # Первичное заполнение
        self._refresh_header_route()
        self._refresh_counter()
        self._restore_or_check_active_batch()

    # ==========================================================================
    # ИНТЕРФЕЙС (КОМПОНОВКА ПО СТАНДАРТУ КОНТУР.МАРКИРОВКИ)
    # ==========================================================================
    def _init_ui(self):
        is_lt = self._is_light
        bg_win = "#e6eaef" if is_lt else "#0b0f14"
        self.setStyleSheet(f"OperatorWindow {{ background-color: {bg_win}; }}")
        bg_card = "#f3f5f8" if is_lt else "#0f172a"
        border_card = "#cfd6e0" if is_lt else "#1e293b"
        text_main = "#2d3748" if is_lt else "#f8fafc"
        text_sub = "#5a6778" if is_lt else "#94a3b8"
        bg_subtle = "#e6eaef" if is_lt else "#1e293b"

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ---------------- 1. ШАПКА (HEADER) ----------------
        self.header_card = QFrame()
        header_card = self.header_card
        header_card.setObjectName("headerCard")
        header_card.setStyleSheet(
            f"QFrame#headerCard {{ background:{bg_subtle}; border-radius:10px; "
            f"border:1px solid {border_card}; padding:4px; }}"
        )
        h_lay = QHBoxLayout(header_card)
        h_lay.setContentsMargins(14, 8, 14, 8)
        h_lay.setSpacing(12)

        # Имя точки
        cur_pt = self.store.current_point or "T-01"
        pt_obj = self.store.get_point(cur_pt) or {}
        self.lbl_point = QLabel(f"📍 Пост: {cur_pt} · {pt_obj.get('name', 'Линия упаковки')}")
        self.lbl_point.setStyleSheet(f"font-size:15px; font-weight:700; color:{text_main};")
        h_lay.addWidget(self.lbl_point)

        # Индикатор сканера
        self.lbl_op = QLabel("📡 Сканер: Готов")
        self.lbl_op.setFixedHeight(28)
        self.lbl_op.setAlignment(Qt.AlignCenter)
        self.lbl_op.setStyleSheet(
            "background:#3b6ea5; color:#ffffff; font-size:13px; font-weight:600; "
            "padding:4px 12px; border-radius:6px;"
        )
        h_lay.addWidget(self.lbl_op)

        # Живой индикатор принтера (динамический зеленый / красный)
        self.lbl_printer = QLabel("🖨 Принтер поста: Опрос...")
        self.lbl_printer.setFixedHeight(28)
        self.lbl_printer.setAlignment(Qt.AlignCenter)
        self.lbl_printer.setStyleSheet(
            f"background:{bg_card}; color:{text_sub}; font-size:13px; font-weight:600; "
            f"padding:4px 12px; border-radius:6px; border:1px solid {border_card};"
        )
        h_lay.addWidget(self.lbl_printer)

        h_lay.addStretch(1)

        # Оператор смены
        self.lbl_operator_name = QLabel("👤 Оператор")
        self.lbl_operator_name.setStyleSheet(f"color:{text_sub}; font-size:14px; font-weight:500;")
        h_lay.addWidget(self.lbl_operator_name)

        root.addWidget(header_card)

        # ---------------- 2. ПАНЕЛЬ ВЫБОРА И ПРОГРЕССА ПАРТИИ (СИНХРОНИЗАЦИЯ) ----------------
        self.batch_card = QFrame()
        batch_card = self.batch_card
        batch_card.setObjectName("batchCard")
        batch_card.setStyleSheet(
            f"QFrame#batchCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}"
        )
        b_lay = QHBoxLayout(batch_card)
        b_lay.setContentsMargins(16, 12, 16, 12)
        b_lay.setSpacing(14)

        self.btn_select_batch = QPushButton("📦 Выбрать заказ / партию с сервера...")
        self.btn_select_batch.setProperty("primary", True)
        self.btn_select_batch.setMinimumHeight(40)
        self.btn_select_batch.setCursor(Qt.PointingHandCursor)
        self.btn_select_batch.setStyleSheet(
            "QPushButton { background:#3b6ea5; color:#ffffff; font-weight:700; font-size:14px; "
            "padding:6px 16px; border-radius:8px; } "
            "QPushButton:hover { background:#2e5988; }"
        )
        self.btn_select_batch.clicked.connect(self._open_batch_picker)
        b_lay.addWidget(self.btn_select_batch)

        batch_info_col = QVBoxLayout()
        batch_info_col.setSpacing(3)
        self.lbl_batch_title = QLabel("Партия кодов не выбрана. Нажмите кнопку слева для загрузки партии с сервера.")
        self.lbl_batch_title.setStyleSheet(f"color:{text_main}; font-size:15px; font-weight:700;")
        batch_info_col.addWidget(self.lbl_batch_title)

        self.lbl_batch_sub = QLabel("Все коды (ШК и ДМ) подтягиваются с центрального сервера через Мастер-ПК.")
        self.lbl_batch_sub.setStyleSheet(f"color:{text_sub}; font-size:13px;")
        batch_info_col.addWidget(self.lbl_batch_sub)
        b_lay.addLayout(batch_info_col, stretch=1)

        # Кнопка сброса/смены партии
        self.btn_clear_batch = QPushButton("✕")
        self.btn_clear_batch.setFixedSize(30, 30)
        self.btn_clear_batch.setToolTip("Очистить выбранную партию")
        self.btn_clear_batch.setCursor(Qt.PointingHandCursor)
        self.btn_clear_batch.setStyleSheet(
            f"QPushButton {{ background:{bg_subtle}; color:{text_sub}; border:1px solid {border_card}; "
            f"border-radius:6px; font-weight:700; }} QPushButton:hover {{ background:#ef4444; color:#ffffff; }}"
        )
        self.btn_clear_batch.clicked.connect(self._clear_active_batch)
        b_lay.addWidget(self.btn_clear_batch)

        # Прогресс-бар остатка кодов в партии
        self.batch_progress_col = QVBoxLayout()
        self.batch_progress_col.setSpacing(3)
        self.lbl_batch_counter = QLabel("0 / 0 шт.")
        self.lbl_batch_counter.setAlignment(Qt.AlignRight)
        self.lbl_batch_counter.setStyleSheet(
            "color:#3b6ea5; font-size:14px; font-weight:700; font-family:Consolas,monospace;"
        )
        self.batch_progress_col.addWidget(self.lbl_batch_counter)

        self.batch_progress_bar = QProgressBar()
        self.batch_progress_bar.setRange(0, 100)
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setTextVisible(False)
        self.batch_progress_bar.setFixedWidth(160)
        self.batch_progress_bar.setFixedHeight(8)
        self.batch_progress_bar.setStyleSheet(
            f"QProgressBar {{ background:{bg_subtle}; border-radius:4px; border:none; }} "
            f"QProgressBar::chunk {{ background:#3b6ea5; border-radius:4px; }}"
        )
        self.batch_progress_col.addWidget(self.batch_progress_bar)
        b_lay.addLayout(self.batch_progress_col)

        root.addWidget(batch_card)

        # ---------------- 3. СТАТУС-БАННЕР И ВВОД ШТРИХКОДА ----------------
        self.status_card = QFrame()
        self.status_card.setObjectName("statusCard")
        self.status_card.setStyleSheet(
            f"QFrame#statusCard {{ background:{bg_card}; border-radius:12px; border:2px solid #3b6ea5; }}"
        )
        st_lay = QVBoxLayout(self.status_card)
        st_lay.setContentsMargins(20, 16, 20, 16)
        st_lay.setSpacing(10)

        # Крупная статусная плашка (Ожидание сканирования / Ошибка без модальных окон)
        self.lbl_status = QLabel("🟢 Ожидание сканирования / ручного ввода")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color:#3b6ea5; font-size:18px; font-weight:800; letter-spacing:0.5px;")
        st_lay.addWidget(self.lbl_status)

        self.lbl_status_sub = QLabel("Поднесите штрихкод товара к сканеру или введите цифры вручную")
        self.lbl_status_sub.setAlignment(Qt.AlignCenter)
        self.lbl_status_sub.setStyleSheet(f"color:{text_sub}; font-size:13px;")
        st_lay.addWidget(self.lbl_status_sub)

        # Поле ввода штрихкода
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self.scan_input = QLineEdit()
        self.scan_input.setObjectName("scanInput")
        self.scan_input.setPlaceholderText("Ожидание сканирования / ручного ввода...")
        self.scan_input.setMinimumHeight(46)
        self.scan_input.setStyleSheet(
            f"QLineEdit#scanInput {{ background:{bg_subtle}; color:{text_main}; font-size:16px; font-weight:600; "
            f"padding:8px 14px; border:1px solid {border_card}; border-radius:8px; font-family:Consolas,monospace; }} "
            f"QLineEdit#scanInput:focus {{ border:2px solid #3b6ea5; background:{bg_card}; }} "
            f"QLineEdit#scanInput:disabled {{ background:#e2e8f0; color:#94a3b8; border:1px solid #cbd5e1; }}"
        )
        self.scan_input.returnPressed.connect(lambda: self.do_scan(mode="manual"))
        input_row.addWidget(self.scan_input, stretch=1)

        self.btn_scan = QPushButton("🖨 Напечатать")
        self.btn_scan.setProperty("primary", True)
        self.btn_scan.setMinimumHeight(46)
        self.btn_scan.setFixedWidth(140)
        self.btn_scan.setStyleSheet(
            "QPushButton { background:#2e856e; color:#ffffff; font-size:15px; font-weight:700; border-radius:8px; } "
            "QPushButton:hover { background:#246e53; }"
        )
        self.btn_scan.clicked.connect(lambda: self.do_scan(mode="manual"))
        input_row.addWidget(self.btn_scan)

        # КНОПКА ПОВТОРА ПЕЧАТИ С ВЫПАДАЮЩИМ СПИСКОМ (1 / 3 / 5)
        self.btn_reprint = QPushButton("🔄 Повтор печати ▾")
        self.btn_reprint.setMinimumHeight(46)
        self.btn_reprint.setFixedWidth(170)
        self.btn_reprint.setStyleSheet(
            f"QPushButton {{ background:{bg_subtle}; color:{text_main}; font-size:14px; font-weight:600; "
            f"padding:6px 14px; border-radius:8px; border:1px solid {border_card}; }} "
            f"QPushButton:hover {{ background:#cbd5e1; }}"
        )
        self.btn_reprint.clicked.connect(self._show_reprint_menu)
        input_row.addWidget(self.btn_reprint)

        st_lay.addLayout(input_row)
        root.addWidget(self.status_card)

        # ---------------- 4. КАРТОЧКА ПОСЛЕДНЕГО ТОВАРА И СЧЁТЧИКИ ----------------
        mid_row = QHBoxLayout()
        mid_row.setSpacing(12)

        # Карточка последней напечатанной позиции
        self.last_card = QFrame()
        last_card = self.last_card
        last_card.setStyleSheet(f"background:{bg_card}; border-radius:10px; border:1px solid {border_card};")
        lc_lay = QVBoxLayout(last_card)
        lc_lay.setContentsMargins(14, 12, 14, 12)
        lc_lay.setSpacing(6)

        self.lc_title = QLabel("ПОСЛЕДНЯЯ НАПЕЧАТАННАЯ ЭТИКЕТКА")
        lc_title = self.lc_title
        lc_title.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:700; letter-spacing:1px;")
        lc_lay.addWidget(lc_title)

        self.lbl_last_product = QLabel("— пока нет напечатанных кодов —")
        self.lbl_last_product.setStyleSheet(f"font-size:15px; font-weight:700; color:{text_main};")
        lc_lay.addWidget(self.lbl_last_product)

        self.lbl_last_details = QLabel("ШК: —  |  GTIN ЧЗ: —  |  SN: —")
        self.lbl_last_details.setStyleSheet(f"font-family:Consolas,monospace; color:{text_sub}; font-size:13px;")
        lc_lay.addWidget(self.lbl_last_details)

        self.lbl_last_dm = QLabel("DataMatrix: —")
        self.lbl_last_dm.setStyleSheet("font-family:Consolas,monospace; color:#2e856e; font-size:12px;")
        self.lbl_last_dm.setWordWrap(True)
        lc_lay.addWidget(self.lbl_last_dm)

        mid_row.addWidget(last_card, stretch=2)

        # Показатели смены (БЕЗ лимита!)
        self.kpi_card = QFrame()
        kpi_card = self.kpi_card
        kpi_card.setStyleSheet(f"background:{bg_card}; border-radius:10px; border:1px solid {border_card};")
        kc_lay = QHBoxLayout(kpi_card)
        kc_lay.setContentsMargins(14, 10, 14, 10)
        kc_lay.setSpacing(16)

        self._kpi_title_labels = []
        def _make_kpi(label_text, color):
            box = QVBoxLayout()
            box.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:600;")
            self._kpi_title_labels.append(lbl)
            val = QLabel("0")
            val.setStyleSheet(f"color:{color}; font-size:24px; font-weight:800; font-family:Consolas,monospace;")
            box.addWidget(lbl)
            box.addWidget(val)
            kc_lay.addLayout(box)
            return val

        self.kpi_prints = _make_kpi("Напечатано", "#2e856e")
        self.kpi_scans = _make_kpi("Сканов", "#3b6ea5")
        self.kpi_errors = _make_kpi("Ошибок", "#c04d53")

        mid_row.addWidget(kpi_card, stretch=1)
        root.addLayout(mid_row)

        # ---------------- 5. ЛЕНТА ПОСЛЕДНИХ 5 НАПЕЧАТАННЫХ МАРОК ----------------
        hist_head = QHBoxLayout()
        self.hist_title = QLabel("ИСТОРИЯ ПОСЛЕДНИХ НАПЕЧАТАННЫХ МАРОК СМЕНЫ")
        hist_title = self.hist_title
        hist_title.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:700; letter-spacing:1px;")
        hist_head.addWidget(hist_title)
        hist_head.addStretch(1)
        root.addLayout(hist_head)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels([
            "Время", "Штрихкод (ШК)", "GTIN ЧЗ", "Серийный номер (SN)", "Статус"
        ])
        h_hdr = self.history_table.horizontalHeader()
        h_hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h_hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h_hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h_hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        h_hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setShowGrid(False)
        self.history_table.setMaximumHeight(150)
        self.history_table.setStyleSheet(
            f"QTableWidget {{ background:{bg_card}; border-radius:8px; border:1px solid {border_card}; color:{text_main}; font-size:13px; }} "
            f"QHeaderView::section {{ background:{bg_subtle}; color:{text_sub}; font-weight:600; border:none; padding:4px 8px; }}"
        )
        root.addWidget(self.history_table)
        self.apply_theme()

    # ==========================================================================
    # ДИНАМИЧЕСКИЙ СТАТУС ПРИНТЕРА И АВТО-БЛОКИРОВКА
    # ==========================================================================
    def _refresh_header_route(self):
        """Проверяет реальный статус принтера: если отключён — блокирует сканирование."""
        if hasattr(self, "lbl_op"):
            try:
                from device_discovery import detect_local_scanners
                scs = detect_local_scanners(connected_only=True, force_refresh=False)
                if scs:
                    sc_name = scs[0].get("name", "Сканер ШК")
                    self.lbl_op.setText(f"🟢 📡 {sc_name}")
                    if self._is_light:
                        self.lbl_op.setStyleSheet(
                            "background:#dcfce7; color:#246e53; font-size:13px; font-weight:700; "
                            "padding:4px 12px; border-radius:6px; border:1px solid #2e856e;"
                        )
                    else:
                        self.lbl_op.setStyleSheet(
                            "background:#0f291e; color:#4ade80; font-size:13px; font-weight:700; "
                            "padding:4px 12px; border-radius:6px; border:1px solid #2e856e;"
                        )
                else:
                    self.lbl_op.setText("🔴 📡 Сканер отключён / не отвечает")
                    if self._is_light:
                        self.lbl_op.setStyleSheet(
                            "background:#fee2e2; color:#b91c1c; font-size:13px; font-weight:700; "
                            "padding:4px 12px; border-radius:6px; border:1px solid #ef4444;"
                        )
                    else:
                        self.lbl_op.setStyleSheet(
                            "background:#2a1215; color:#f87171; font-size:13px; font-weight:700; "
                            "padding:4px 12px; border-radius:6px; border:1px solid #c04d53;"
                        )
            except Exception:
                self.lbl_op.setText("🔴 📡 Сканер не отвечает")
        try:
            live = get_currently_connected_printer(force_refresh=False)
            is_conn = bool(live and live.get("connected"))
            pr_name = live["name"] if is_conn else ""

            if is_conn:
                self.lbl_printer.setText(f"🟢 {pr_name}")
                if self._is_light:
                    self.lbl_printer.setStyleSheet(
                        "background:#dcfce7; color:#246e53; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #2e856e;"
                    )
                else:
                    self.lbl_printer.setStyleSheet(
                        "background:#0f291e; color:#4ade80; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #2e856e;"
                    )
                if not self.scan_input.isEnabled():
                    self.scan_input.setEnabled(True)
                    self.scan_input.setPlaceholderText("Ожидание сканирования / ручного ввода...")
                    self._show_status("ready", "🟢 Ожидание сканирования / ручного ввода", sub="Поднесите штрихкод товара к сканеру или введите цифры вручную")
            else:
                self.lbl_printer.setText("🔴 Принтер отключён")
                if self._is_light:
                    self.lbl_printer.setStyleSheet(
                        "background:#fee2e2; color:#b91c1c; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #ef4444;"
                    )
                else:
                    self.lbl_printer.setStyleSheet(
                        "background:#2a1215; color:#f87171; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #c04d53;"
                    )
                if self.scan_input.isEnabled():
                    self.scan_input.setEnabled(False)
                    self.scan_input.setPlaceholderText("🔴 Сканирование заблокировано: подключите принтер")
                self._show_status(
                    "error",
                    "🔴 Принтер не подключён. Подключите принтер к ПК для начала работы",
                    sub="Включите питание принтера или подключите USB-кабель к компьютеру"
                )
        except Exception as e:
            log.debug("_refresh_header_route: %s", e)

    def set_scanner_status(self, is_connected: bool, scanner_name: str = ""):
        """Установить актуальный статус сканера из фонового мониторинга MainWindow."""
        if hasattr(self, "lbl_op"):
            if is_connected:
                title = f"🟢 📡 {scanner_name or 'Сканер ШК'}"
                self.lbl_op.setText(title)
                if getattr(self, "_is_light", False):
                    self.lbl_op.setStyleSheet(
                        "background:#dcfce7; color:#246e53; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #2e856e;"
                    )
                else:
                    self.lbl_op.setStyleSheet(
                        "background:#0f291e; color:#4ade80; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #2e856e;"
                    )
            else:
                self.lbl_op.setText("🔴 📡 Сканер отключён / не отвечает")
                if getattr(self, "_is_light", False):
                    self.lbl_op.setStyleSheet(
                        "background:#fee2e2; color:#b91c1c; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #ef4444;"
                    )
                else:
                    self.lbl_op.setStyleSheet(
                        "background:#2a1215; color:#f87171; font-size:13px; font-weight:700; "
                        "padding:4px 12px; border-radius:6px; border:1px solid #c04d53;"
                    )

    def _on_poll_tick(self):
        self._refresh_header_route()
        self._refresh_batch_progress()

    # ==========================================================================
    # СИНХРОНИЗАЦИЯ И ВЫБОР ПАРТИИ ПРЯМО В ОКНЕ ОПЕРАТОРА
    # ==========================================================================
    def _open_batch_picker(self):
        """Открывает диалог выбора партии кодов с сервера (через Мастер-ПК) с живым поиском."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Выбор партии / заказа с сервера")
        dlg.resize(880, 520)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        hdr = QLabel("Выберите заказ с сервера для текущего рабочего места")
        hdr.setStyleSheet("font-size:16px; font-weight:700;")
        lay.addWidget(hdr)

        # Поле живого поиска по заказам
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("🔍 Поиск по названию заказа, товару или ШК...")
        search_edit.setMinimumHeight(36)
        lay.addWidget(search_edit)

        t = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(["Имя заказа / партии", "ШК товара", "GTIN ЧЗ", "Кодов в партии", "Статус"])
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(t, stretch=1)

        raw_pkgs = sync_mod.list_available_json_packages(self.store, include_outbox=True, auto_query_server=False)

        def _populate_table():
            q = search_edit.text().strip().lower()
            t.setRowCount(0)
            pkgs = raw_pkgs
            if q:
                pkgs = [
                    p for p in raw_pkgs
                    if q in (p.get("batch_title") or "").lower()
                    or q in (p.get("shk") or "").lower()
                    or q in (p.get("gtin") or "").lower()
                ]
            for p in pkgs:
                r = t.rowCount(); t.insertRow(r)
                t.setItem(r, 0, QTableWidgetItem(p.get("batch_title") or p.get("filename")))
                t.setItem(r, 1, QTableWidgetItem(p.get("shk") or "—"))
                t.setItem(r, 2, QTableWidgetItem(p.get("gtin") or "—"))
                t.setItem(r, 3, QTableWidgetItem(f"{p.get('total_codes', 0)} шт."))
                st_text = "Готов" if not p.get("claimed_by") else f"Выдан ({p['claimed_by']})"
                st_item = QTableWidgetItem(st_text)
                st_item.setForeground(QBrush(QColor("#2e856e" if not p.get("claimed_by") else "#f59e0b")))
                t.setItem(r, 4, st_item)
            if t.rowCount() > 0:
                t.selectRow(0)

        search_edit.textChanged.connect(lambda _: _populate_table())
        _populate_table()

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_cancel)

        btn_apply = QPushButton("Загрузить выбранную партию")
        btn_apply.setProperty("primary", True)
        btn_apply.setStyleSheet("background:#3b6ea5; color:#ffffff; font-weight:700; padding:6px 16px; border-radius:6px;")
        btn_row.addWidget(btn_apply)
        lay.addLayout(btn_row)

        def _on_apply():
            sel = t.selectionModel().selectedRows()
            if not sel:
                return
            idx = sel[0].row()
            title = t.item(idx, 0).text() if t.item(idx, 0) else ""
            chosen = next((p for p in raw_pkgs if (p.get("batch_title") or p.get("filename")) == title), None)
            if not chosen:
                return
            pt = self.store.current_point or "T-01"
            res = sync_mod.claim_and_import_json_package(self.store, Path(chosen["path"]), point_id=pt)
            if res.get("ok"):
                self.toast.show_message(f"Партия «{chosen['batch_title']}» успешно загружена!", "ok")
                self._active_batch_info = {
                    "title": chosen["batch_title"],
                    "shk": chosen["shk"],
                    "gtin": chosen["gtin"],
                    "path": chosen["path"],
                }
                self.store.set_setting("active_batch_name", chosen["batch_title"])
                self.store.set_setting("active_batch_shk", chosen["shk"])
                self._refresh_batch_progress()
                dlg.accept()
            else:
                QMessageBox.warning(dlg, "Ошибка", res.get("info", "Не удалось загрузить партию"))

        btn_apply.clicked.connect(_on_apply)
        t.doubleClicked.connect(lambda _: _on_apply())
        dlg.exec()

    def _clear_active_batch(self):
        """Очищает привязку активной партии."""
        self._active_batch_info = None
        self.store.set_setting("active_batch_name", "")
        self.store.set_setting("active_batch_shk", "")
        self._refresh_batch_progress()
        self.toast.show_message("Выбор партии сброшен", "ok")

    def _restore_or_check_active_batch(self):
        saved_name = self.store.get_setting("active_batch_name") or ""
        saved_shk = self.store.get_setting("active_batch_shk") or ""
        if saved_name:
            self._active_batch_info = {"title": saved_name, "shk": saved_shk}
        self._refresh_batch_progress()

    def _refresh_batch_progress(self):
        """Обновляет остатки по текущей партии."""
        import db
        if not self._active_batch_info:
            try:
                with db.get_conn() as conn:
                    r = conn.execute(
                        "SELECT import_batch, gtin, COUNT(*) as cnt, "
                        "SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) as free "
                        "FROM pool_codes GROUP BY import_batch ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    if r and r["import_batch"]:
                        self._active_batch_info = {
                            "title": r["import_batch"],
                            "gtin": r["gtin"],
                            "shk": "",
                        }
            except Exception:
                pass

        if not self._active_batch_info:
            self.lbl_batch_title.setText("Партия кодов не выбрана. Нажмите «Выбрать заказ», чтобы загрузить коды с сервера.")
            self.lbl_batch_sub.setText("Без выбранной партии печать марок не производится.")
            self.lbl_batch_counter.setText("0 / 0 шт.")
            self.batch_progress_bar.setValue(0)
            return

        title = self._active_batch_info.get("title", "Партия")
        shk = self._active_batch_info.get("shk", "")
        free_cnt = 0
        total_cnt = 0

        try:
            with db.get_conn() as conn:
                r = conn.execute(
                    "SELECT COUNT(*) AS total, SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free "
                    "FROM pool_codes WHERE import_batch = ?",
                    (title,)
                ).fetchone()
                if r:
                    total_cnt = r["total"] or 0
                    free_cnt = r["free"] or 0
        except Exception:
            pass

        used_cnt = total_cnt - free_cnt
        percent = int((used_cnt / total_cnt) * 100) if total_cnt > 0 else 0

        shk_label = f"  ·  ШК: {shk}" if shk else ""
        self.lbl_batch_title.setText(f"Активная партия: {title}{shk_label}")
        self.lbl_batch_sub.setText(f"Обработано {used_cnt} из {total_cnt} шт.  ·  Осталось свободных марок: {free_cnt} шт.")
        self.lbl_batch_counter.setText(f"{used_cnt} / {total_cnt} шт. ({percent}%)")
        self.batch_progress_bar.setValue(percent)

    # ==========================================================================
    # СКАНИРОВАНИЕ И ОБРАБОТКА КОДОВ (БЕЗ МОДАЛЬНЫХ ОКОН ОШИБОК)
    # ==========================================================================
    def do_scan(self, mode="auto"):
        """Обрабатывает отсканированный штрихкод без всплывающих окон ошибок."""
        raw_barcode = self.scan_input.text().strip()
        self.scan_input.clear()
        if not raw_barcode:
            return

        # 1. Проверка принтера
        live_pr = get_currently_connected_printer(force_refresh=False)
        if not live_pr or not live_pr.get("connected"):
            self._show_status(
                "error",
                "🔴 Принтер не подключён. Подключите принтер к ПК для начала работы",
                sub="Включите питание принтера или подключите USB-кабель к компьютеру"
            )
            QApplication.beep()
            return

        # 2. Поиск привязки ШК -> ДМ
        try:
            from workstation_engine import resolve_and_claim_cz_code
            res = resolve_and_claim_cz_code(
                raw_barcode,
                store=self.store,
                point=self.store.current_point or "T-01",
                claim=True
            )
        except Exception as e:
            res = {"ok": False, "info": str(e), "error_code": "INTERNAL_ERROR"}

        if not res.get("ok"):
            err_code = res.get("error_code")
            if err_code == "UNKNOWN_BARCODE":
                msg = f"🔴 Неизвестный штрихкод: {raw_barcode}"
                sub = "Товар не найден в текущем заказе или отсутствует привязка ШК ↔ GTIN"
            elif err_code == "NO_FREE_CODES":
                msg = "🔴 В партии закончились свободные коды маркировки!"
                sub = "Загрузите новую партию с сервера кнопкой «Выбрать заказ»"
            else:
                msg = f"🔴 Ошибка: {res.get('info', 'Сбой сканирования')}"
                sub = "Повторите сканирование упаковки"

            self._show_status("error", msg, sub=sub)
            QApplication.beep()
            self._record_kpi(is_error=True)
            return

        # 3. Печать кода маркировки
        cz_code = res.get("code")
        gtin_val = res.get("gtin", "")
        sn_val = res.get("sn", "")

        cfg_print = get_printer_for_point(load_config(), self.store.current_point or "T-01")
        print_res = print_code(cz_code, cfg_print)

        if not print_res.get("ok"):
            self._show_status(
                "error",
                f"🔴 Сбой печати: {print_res.get('info')}",
                sub="Проверьте бумагу и состояние принтера"
            )
            QApplication.beep()
            self._record_kpi(is_error=True)
            return

        # 4. Успех: красивый зелёный баннер и обновление истории
        self._show_status(
            "success",
            f"🟢 Успешно напечатано: ШК {raw_barcode} → GTIN {gtin_val}",
            sub=f"Серийный номер: {sn_val}  ·  Код ЧЗ отправлен на принтер"
        )

        item_data = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "barcode": raw_barcode,
            "gtin": gtin_val,
            "sn": sn_val,
            "code": cz_code,
            "status": "Напечатано",
        }
        self._last_printed_codes.insert(0, item_data)
        self._last_printed_codes = self._last_printed_codes[:10]

        # Обновляем карточку последнего товара
        self.lbl_last_product.setText(f"ШК {raw_barcode} (Заказ: {self._active_batch_info.get('title', '—') if self._active_batch_info else '—'})")
        self.lbl_last_details.setText(f"ШК: {raw_barcode}  |  GTIN: {gtin_val}  |  SN: {sn_val}")
        short_dm = cz_code[:38] + "..." if len(cz_code) > 38 else cz_code
        self.lbl_last_dm.setText(f"DataMatrix: {short_dm}")

        self._add_history_row(item_data)
        self._record_kpi(is_scan=True, is_print=True)
        self._refresh_batch_progress()

    def _show_status(self, kind: str, message: str, sub: str = ""):
        """Встроенный цветовой статус без блокирующих диалогов (адаптивный под светлую/тёмную тему)."""
        self._last_status_kind = kind
        self._last_status_msg = message
        self._last_status_sub = sub
        is_lt = self._is_light

        if kind == "error":
            bg_err = "#f9ebeb" if is_lt else "#2a1215"
            bd_err = "#dca7a9" if is_lt else "#c04d53"
            fg_err = "#9b2c2c" if is_lt else "#f87171"
            sub_err = "#742a2a" if is_lt else "#fca5a5"
            self.status_card.setStyleSheet(f"QFrame#statusCard {{ background:{bg_err}; border-radius:12px; border:2px solid {bd_err}; }}")
            self.lbl_status.setText(message)
            self.lbl_status.setStyleSheet(f"color:{fg_err}; font-size:18px; font-weight:800; background:transparent; border:none;")
            self.lbl_status_sub.setText(sub or "Отсканируйте следующий штрихкод для сброса ошибки")
            self.lbl_status_sub.setStyleSheet(f"color:{sub_err}; font-size:13px; background:transparent; border:none;")
        elif kind == "success":
            bg_ok = "#e6f2ed" if is_lt else "#092e20"
            bd_ok = "#9cc5b3" if is_lt else "#10b981"
            fg_ok = "#276749" if is_lt else "#34d399"
            sub_ok = "#22543d" if is_lt else "#a7f3d0"
            self.status_card.setStyleSheet(f"QFrame#statusCard {{ background:{bg_ok}; border-radius:12px; border:2px solid {bd_ok}; }}")
            self.lbl_status.setText(message)
            self.lbl_status.setStyleSheet(f"color:{fg_ok}; font-size:18px; font-weight:800; background:transparent; border:none;")
            self.lbl_status_sub.setText(sub or "Готово к сканированию следующего товара")
            self.lbl_status_sub.setStyleSheet(f"color:{sub_ok}; font-size:13px; background:transparent; border:none;")
        else:
            bg_c = "#f8fafc" if is_lt else "#0f172a"
            bd_c = "#7dd3fc" if is_lt else "#3b6ea5"
            text_s = "#64748b" if is_lt else "#94a3b8"
            self.status_card.setStyleSheet(f"QFrame#statusCard {{ background:{bg_c}; border-radius:12px; border:2px solid {bd_c}; }}")
            self.lbl_status.setText(message)
            self.lbl_status.setStyleSheet("color:#3b6ea5; font-size:18px; font-weight:800; background:transparent; border:none;")
            self.lbl_status_sub.setText(sub or "Поднесите штрихкод товара к сканеру")
            self.lbl_status_sub.setStyleSheet(f"color:{text_s}; font-size:13px; background:transparent; border:none;")

    # ==========================================================================
    # ПОВТОР ПЕЧАТИ (1 / 3 / 5)
    # ==========================================================================
    def _show_reprint_menu(self):
        """Меню повтора последних напечатанных этикеток."""
        menu = QMenu(self)
        if self._is_light:
            menu.setStyleSheet(
                "QMenu { background:#ffffff; color:#0f172a; border:1px solid #cbd5e1; padding:4px; border-radius:6px; } "
                "QMenu::item { padding:8px 18px; border-radius:4px; font-size:14px; font-weight:600; } "
                "QMenu::item:selected { background:#3b6ea5; color:#ffffff; }"
            )
        else:
            menu.setStyleSheet(
                "QMenu { background:#1e293b; color:#f8fafc; border:1px solid #475569; padding:4px; border-radius:6px; } "
                "QMenu::item { padding:8px 18px; border-radius:4px; font-size:14px; font-weight:600; } "
                "QMenu::item:selected { background:#3b6ea5; color:#ffffff; }"
            )
        act1 = menu.addAction("🖨 1 последняя этикетка")
        act3 = menu.addAction("🖨 3 последних этикетки")
        act5 = menu.addAction("🖨 5 последних этикеток")

        act1.triggered.connect(lambda: self._reprint_last_n(1))
        act3.triggered.connect(lambda: self._reprint_last_n(3))
        act5.triggered.connect(lambda: self._reprint_last_n(5))

        menu.exec(QCursor.pos())

    def _reprint_last_n(self, count: int):
        """Повторно отправляет на принтер последние N этикеток."""
        if not self._last_printed_codes:
            self.toast.show_message("История печати пуста", "warn")
            return

        to_print = self._last_printed_codes[:count]
        cfg_print = get_printer_for_point(load_config(), self.store.current_point or "T-01")

        reprinted = 0
        for item in reversed(to_print):
            code_str = item.get("code")
            if code_str:
                res = print_code(code_str, cfg_print)
                if res.get("ok"):
                    reprinted += 1

        self.toast.show_message(f"Повторно напечатано: {reprinted} из {len(to_print)} шт.", "ok")
        self._record_kpi(is_print=True, print_count=reprinted)

    # ==========================================================================
    # ИСТОРИЯ И СЧЁТЧИКИ
    # ==========================================================================
    def _add_history_row(self, item: Dict[str, Any]):
        t = self.history_table
        r = 0
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(item.get("time", "")))
        t.setItem(r, 1, QTableWidgetItem(item.get("barcode", "")))
        t.setItem(r, 2, QTableWidgetItem(item.get("gtin", "")))
        t.setItem(r, 3, QTableWidgetItem(item.get("sn", "")))
        st = QTableWidgetItem(item.get("status", "Успех"))
        st.setForeground(QBrush(QColor("#2e856e")))
        t.setItem(r, 4, st)
        if t.rowCount() > 5:
            t.removeRow(t.rowCount() - 1)

    def _record_kpi(self, is_scan=False, is_print=False, is_error=False, print_count=1):
        c = self.store.counter
        if is_scan:
            c["scans"] = c.get("scans", 0) + 1
        if is_print:
            c["prints"] = c.get("prints", 0) + print_count
        if is_error:
            c["errors"] = c.get("errors", 0) + 1
        self._refresh_counter()

    def _refresh_counter(self):
        c = self.store.counter
        self.kpi_prints.setText(str(c.get("prints", 0)))
        self.kpi_scans.setText(str(c.get("scans", 0)))
        self.kpi_errors.setText(str(c.get("errors", 0)))

    def apply_theme(self, theme_name: Optional[str] = None):
        """Мгновенно перекрашивает все карточки, тексты и таблицы окна оператора под активную тему."""
        t_name = theme_name or get_current_theme()
        self._is_light = (t_name == "light")
        is_lt = self._is_light

        bg_card = "#f3f5f8" if is_lt else "#0f172a"
        border_card = "#cfd6e0" if is_lt else "#1e293b"
        text_main = "#2d3748" if is_lt else "#f8fafc"
        text_sub = "#5a6778" if is_lt else "#94a3b8"
        bg_subtle = "#e6eaef" if is_lt else "#1e293b"

        if hasattr(self, "header_card"):
            self.header_card.setStyleSheet(
                f"QFrame#headerCard {{ background:{bg_card}; border-radius:10px; "
                f"border:1px solid {border_card}; padding:4px; }}"
            )
        if hasattr(self, "lbl_point"):
            self.lbl_point.setStyleSheet(f"font-size:15px; font-weight:700; color:{text_main}; background:transparent; border:none;")
        if hasattr(self, "lbl_operator_name"):
            self.lbl_operator_name.setStyleSheet(f"color:{text_sub}; font-size:14px; font-weight:500; background:transparent; border:none;")

        if hasattr(self, "batch_card"):
            self.batch_card.setStyleSheet(
                f"QFrame#batchCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}"
            )
        if hasattr(self, "lbl_batch_title"):
            self.lbl_batch_title.setStyleSheet(f"color:{text_main}; font-size:15px; font-weight:700; background:transparent; border:none;")
        if hasattr(self, "lbl_batch_sub"):
            self.lbl_batch_sub.setStyleSheet(f"color:{text_sub}; font-size:13px; background:transparent; border:none;")
        if hasattr(self, "btn_clear_batch"):
            self.btn_clear_batch.setStyleSheet(
                f"QPushButton {{ background:{bg_subtle}; color:{text_sub}; border:1px solid {border_card}; "
                f"border-radius:6px; font-weight:700; }} QPushButton:hover {{ background:#ef4444; color:#ffffff; }}"
            )
        if hasattr(self, "batch_progress_bar"):
            self.batch_progress_bar.setStyleSheet(
                f"QProgressBar {{ background:{bg_subtle}; border-radius:4px; border:1px solid {border_card}; }} "
                f"QProgressBar::chunk {{ background:#3b6ea5; border-radius:3px; }}"
            )

        if hasattr(self, "scan_input"):
            dis_bg = "#f1f5f9" if is_lt else "#1e293b"
            dis_fg = "#64748b" if is_lt else "#94a3b8"
            self.scan_input.setStyleSheet(
                f"QLineEdit#scanInput {{ background:{bg_subtle}; color:{text_main}; font-size:16px; font-weight:600; "
                f"padding:8px 14px; border:1px solid {border_card}; border-radius:8px; font-family:Consolas,monospace; }} "
                f"QLineEdit#scanInput:focus {{ border:2px solid #3b6ea5; background:{bg_card}; }} "
                f"QLineEdit#scanInput:disabled {{ background:{dis_bg}; color:{dis_fg}; border:1px solid {border_card}; }}"
            )

        if hasattr(self, "btn_reprint"):
            hov_bg = "#e2e8f0" if is_lt else "#334155"
            self.btn_reprint.setStyleSheet(
                f"QPushButton {{ background:{bg_subtle}; color:{text_main}; font-size:14px; font-weight:600; "
                f"padding:6px 14px; border-radius:8px; border:1px solid {border_card}; }} "
                f"QPushButton:hover {{ background:{hov_bg}; }}"
            )

        if hasattr(self, "last_card"):
            self.last_card.setStyleSheet(f"QFrame {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}")
        if hasattr(self, "lc_title"):
            self.lc_title.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:700; letter-spacing:1px; background:transparent; border:none;")
        if hasattr(self, "lbl_last_product"):
            self.lbl_last_product.setStyleSheet(f"font-size:15px; font-weight:700; color:{text_main}; background:transparent; border:none;")
        if hasattr(self, "lbl_last_details"):
            self.lbl_last_details.setStyleSheet(f"font-family:Consolas,monospace; color:{text_sub}; font-size:13px; background:transparent; border:none;")
        if hasattr(self, "lbl_last_dm"):
            self.lbl_last_dm.setStyleSheet("font-family:Consolas,monospace; color:#2e856e; font-size:12px; background:transparent; border:none;")

        if hasattr(self, "kpi_card"):
            self.kpi_card.setStyleSheet(f"QFrame {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}")
        for k_lbl in getattr(self, "_kpi_title_labels", []):
            k_lbl.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:600; background:transparent; border:none;")
        if hasattr(self, "kpi_prints"):
            self.kpi_prints.setStyleSheet("color:#2e856e; font-size:24px; font-weight:800; font-family:Consolas,monospace; background:transparent; border:none;")
        if hasattr(self, "kpi_scans"):
            self.kpi_scans.setStyleSheet("color:#3b6ea5; font-size:24px; font-weight:800; font-family:Consolas,monospace; background:transparent; border:none;")
        if hasattr(self, "kpi_errors"):
            self.kpi_errors.setStyleSheet("color:#c04d53; font-size:24px; font-weight:800; font-family:Consolas,monospace; background:transparent; border:none;")

        if hasattr(self, "hist_title"):
            self.hist_title.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:700; letter-spacing:1px; background:transparent; border:none;")
        if hasattr(self, "history_table"):
            self.history_table.setStyleSheet(
                f"QTableWidget {{ background:{bg_card}; border-radius:8px; border:1px solid {border_card}; color:{text_main}; font-size:13px; }} "
                f"QHeaderView::section {{ background:{bg_subtle}; color:{text_sub}; font-weight:600; border:none; border-bottom:1px solid {border_card}; padding:6px 8px; }}"
            )

        # Перекрашиваем текущий статус-баннер и плашку принтера
        kind = getattr(self, "_last_status_kind", "ready")
        msg = getattr(self, "_last_status_msg", self.lbl_status.text() if hasattr(self, "lbl_status") else "")
        sub = getattr(self, "_last_status_sub", self.lbl_status_sub.text() if hasattr(self, "lbl_status_sub") else "")
        self._show_status(kind, msg, sub)
        self._refresh_header_route()

    def showEvent(self, event):
        """Мгновенно синхронизирует тему (только при её изменении) и статус оборудования при открытии вкладки оператора."""
        super().showEvent(event)
        cur_is_light = (get_current_theme() == "light")
        if getattr(self, "_is_light", None) != cur_is_light:
            self.apply_theme("light" if cur_is_light else "dark")
        else:
            self._refresh_header_route()
        if hasattr(self, "scan_input") and self.scan_input.isEnabled():
            self.scan_input.setFocus()