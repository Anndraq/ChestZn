"""
Dotmark — Окно оператора маркировки (operator_window.py)
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
    """Возвращает текущую тему ('dark' или 'light') из config.json."""
    try:
        cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            return data.get("app_theme", "dark")
    except Exception:
        pass
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
        bg_card = "#e8ecf1" if is_lt else "#0f172a"
        border_card = "#c2cbd6" if is_lt else "#1e293b"
        text_main = "#1e293b" if is_lt else "#f8fafc"
        text_sub = "#475569" if is_lt else "#94a3b8"
        bg_subtle = "#dde3eb" if is_lt else "#1e293b"

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ---------------- 1. ШАПКА (HEADER) ----------------
        self.header_card = QFrame()
        self.header_card.setObjectName("headerCard")
        self.header_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header_card = self.header_card
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
        self.lbl_op.setFixedHeight(32)
        self.lbl_op.setStyleSheet(
            "background:#3b6ea5; color:#ffffff; font-size:13px; font-weight:600; "
            "padding:4px 10px; border-radius:6px;"
        )
        h_lay.addWidget(self.lbl_op)

        # Живой индикатор принтера (динамический зеленый / красный)
        self.lbl_printer = QLabel("🖨 Принтер поста: Опрос...")
        self.lbl_printer.setFixedHeight(32)
        self.lbl_printer.setStyleSheet(
            f"background:{bg_card}; color:{text_sub}; font-size:13px; font-weight:600; "
            f"padding:4px 10px; border-radius:6px; border:1px solid {border_card};"
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
        self.batch_card.setObjectName("batchCard")
        batch_card = self.batch_card
        batch_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        batch_card.setStyleSheet(
            f"QFrame#batchCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}"
        )
        b_lay = QHBoxLayout(batch_card)
        b_lay.setContentsMargins(16, 12, 16, 12)
        b_lay.setSpacing(14)
        b_lay.setAlignment(Qt.AlignVCenter)

        # Левый блок: статус-бейдж + название заказа + детали
        batch_info_w = QWidget()
        batch_info_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        batch_info_w.setStyleSheet("background:transparent;")
        batch_info_col = QVBoxLayout(batch_info_w)
        batch_info_col.setContentsMargins(0, 0, 0, 0)
        batch_info_col.setSpacing(5)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.lbl_batch_badge = QLabel("НЕТ ЗАКАЗА")
        self.lbl_batch_badge.setFixedHeight(22)
        self.lbl_batch_badge.setAlignment(Qt.AlignCenter)
        self.lbl_batch_badge.setStyleSheet(
            f"background:{bg_subtle}; color:{text_sub}; border:1px solid {border_card}; "
            "border-radius:6px; font-size:10px; font-weight:700; padding:0 8px; letter-spacing:0.6px;"
        )
        title_row.addWidget(self.lbl_batch_badge, alignment=Qt.AlignVCenter)

        self.lbl_batch_title = QLabel("Заказ / партия не выбрана")
        self.lbl_batch_title.setStyleSheet(f"color:{text_main}; font-size:15px; font-weight:700; background:transparent;")
        title_row.addWidget(self.lbl_batch_title, stretch=1, alignment=Qt.AlignVCenter)
        batch_info_col.addLayout(title_row)

        self.lbl_batch_sub = QLabel("Нажмите «Выбрать заказ», чтобы загрузить марки с сервера")
        self.lbl_batch_sub.setStyleSheet(f"color:{text_sub}; font-size:12px; background:transparent;")
        batch_info_col.addWidget(self.lbl_batch_sub)
        batch_info_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        b_lay.addWidget(batch_info_w, alignment=Qt.AlignVCenter)
        b_lay.addStretch(1)

        # Средний блок: широкая капсула прогресса (видна только когда заказ выбран)
        self.batch_progress_widget = QFrame()
        self.batch_progress_widget.setObjectName("batchProgressBox")
        self.batch_progress_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.batch_progress_widget.setMinimumWidth(360)
        prog_bg = "#e1e7ef" if is_lt else "#131820"
        self.batch_progress_widget.setStyleSheet(
            f"QFrame#batchProgressBox {{ background:{prog_bg}; border:1px solid {border_card}; border-radius:10px; }}"
        )
        self.batch_progress_col = QVBoxLayout(self.batch_progress_widget)
        self.batch_progress_col.setContentsMargins(16, 9, 16, 10)
        self.batch_progress_col.setSpacing(7)

        prog_top = QHBoxLayout()
        prog_top.setContentsMargins(0, 0, 0, 0)
        prog_top.setSpacing(8)
        self.lbl_batch_prog_cap = QLabel("Прогресс партии")
        self.lbl_batch_prog_cap.setStyleSheet(f"color:{text_sub}; font-size:11px; font-weight:600; border:none; background:transparent;")
        prog_top.addWidget(self.lbl_batch_prog_cap)
        prog_top.addStretch(1)

        self.lbl_batch_counter = QLabel("0 / 0 шт. (0%)")
        self.lbl_batch_counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_batch_counter.setStyleSheet(
            "color:#3b6ea5; font-size:12px; font-weight:700; font-family:Consolas,monospace; border:none; background:transparent;"
        )
        prog_top.addWidget(self.lbl_batch_counter)
        self.batch_progress_col.addLayout(prog_top)

        self.batch_progress_bar = QProgressBar()
        self.batch_progress_bar.setRange(0, 100)
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setTextVisible(False)
        self.batch_progress_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.batch_progress_bar.setMinimumWidth(320)
        self.batch_progress_bar.setFixedHeight(10)
        track_col = "#dce3ec" if is_lt else "#1e293b"
        self.batch_progress_bar.setStyleSheet(
            f"QProgressBar {{ background:{track_col}; border-radius:5px; border:none; }} "
            f"QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b6ea5, stop:1 #5a94d6); border-radius:5px; }}"
        )
        self.batch_progress_col.addWidget(self.batch_progress_bar)
        self.batch_progress_widget.setVisible(False)
        b_lay.addWidget(self.batch_progress_widget, stretch=3, alignment=Qt.AlignVCenter)
        b_lay.addStretch(1)

        # Правый блок: сгруппированные кнопки управления заказом
        btn_group = QHBoxLayout()
        btn_group.setContentsMargins(0, 0, 0, 0)
        btn_group.setSpacing(8)

        self.btn_select_batch = QPushButton("📦 Выбрать заказ")
        self.btn_select_batch.setProperty("primary", True)
        self.btn_select_batch.setFixedHeight(36)
        self.btn_select_batch.setCursor(Qt.PointingHandCursor)
        self.btn_select_batch.setStyleSheet(
            "QPushButton { background:#3b6ea5; color:#ffffff; font-weight:700; font-size:13px; "
            "padding:0 16px; border-radius:8px; border:none; } "
            "QPushButton:hover { background:#2e5988; }"
        )
        self.btn_select_batch.clicked.connect(self._open_batch_picker)
        btn_group.addWidget(self.btn_select_batch)

        self.btn_clear_batch = QPushButton("✕ Снять")
        self.btn_clear_batch.setFixedHeight(36)
        self.btn_clear_batch.setToolTip("Снять выбранную партию и очистить активный заказ")
        self.btn_clear_batch.setCursor(Qt.PointingHandCursor)
        self.btn_clear_batch.setStyleSheet(
            f"QPushButton {{ background:{bg_subtle}; color:{text_sub}; border:1px solid {border_card}; "
            f"border-radius:8px; font-weight:600; font-size:12px; padding:0 12px; }} "
            f"QPushButton:hover {{ background:#ef4444; color:#ffffff; border-color:#ef4444; }}"
        )
        self.btn_clear_batch.clicked.connect(self._clear_active_batch)
        self.btn_clear_batch.setVisible(False)
        btn_group.addWidget(self.btn_clear_batch)

        b_lay.addLayout(btn_group)

        root.addWidget(batch_card)

        # ---------------- 3. СТАТУС-БАННЕР И ВВОД ШТРИХКОДА ----------------
        self.status_card = QFrame()
        self.status_card.setObjectName("statusCard")
        self.status_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        self.btn_scan.setMinimumWidth(160)
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
        self.last_card.setObjectName("lastCard")
        self.last_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        last_card = self.last_card
        last_card.setStyleSheet(f"QFrame#lastCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}")
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
        self.kpi_card.setObjectName("kpiCard")
        self.kpi_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        kpi_card = self.kpi_card
        self.kpi_lbls = []
        kpi_card.setStyleSheet(f"QFrame#kpiCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}")
        kc_lay = QHBoxLayout(kpi_card)
        kc_lay.setContentsMargins(18, 12, 18, 12)
        kc_lay.setSpacing(16)

        def _make_kpi(label_text, color):
            box = QVBoxLayout()
            box.setSpacing(2)
            box.setAlignment(Qt.AlignVCenter)
            lbl = QLabel(label_text)
            self.kpi_lbls.append(lbl)
            lbl.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:600; background:transparent;")
            val = QLabel("0")
            val.setStyleSheet(f"color:{color}; font-size:24px; font-weight:800; font-family:Consolas,monospace; background:transparent;")
            box.addStretch(1)
            box.addWidget(lbl)
            box.addWidget(val)
            box.addStretch(1)
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
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().setDefaultSectionSize(34)
        self.history_table.setMinimumHeight(170)
        self.history_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.history_table.setStyleSheet(
            f"QTableWidget {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; color:{text_main}; font-size:13px; }} "
            f"QHeaderView::section {{ background:{bg_subtle}; color:{text_sub}; font-weight:700; border:none; border-bottom:1px solid {border_card}; padding:7px 10px; }}"
        )
        root.addWidget(self.history_table, stretch=1)
        self.apply_theme()

    # ==========================================================================
    # ДИНАМИЧЕСКИЙ СТАТУС ПРИНТЕРА И АВТО-БЛОКИРОВКА
    # ==========================================================================
    def _refresh_header_route(self):
        """Проверяет реальный статус принтера и сканера: если принтер отключён — блокирует сканирование."""
        try:
            is_lt = self._is_light
            ok_bg = "#dfece6" if is_lt else "#11241d"
            ok_fg = "#246e53" if is_lt else "#6ee7b7"
            ok_bd = "#b3d9c9" if is_lt else "#2b5c49"

            err_bg = "#eedee0" if is_lt else "#24171a"
            err_fg = "#9f3a44" if is_lt else "#e5989b"
            err_bd = "#d6b0b5" if is_lt else "#6b343b"

            # 1. Проверка статуса сканера
            live_scanners = []
            try:
                from device_discovery import detect_local_scanners
                live_scanners = detect_local_scanners(connected_only=True, force_refresh=False) or []
            except Exception:
                live_scanners = []

            if hasattr(self, "lbl_op"):
                self.lbl_op.setVisible(True)
                sc_sig = (bool(live_scanners), live_scanners[0].get("name") if live_scanners else "", is_lt)
                if getattr(self, "_last_sc_sig", None) != sc_sig:
                    self._last_sc_sig = sc_sig
                    if live_scanners:
                        sc_name = live_scanners[0].get("name") or "Сканер: Готов"
                        self.lbl_op.setText(f"📡 {sc_name}")
                        self.lbl_op.setStyleSheet(
                            f"background:{ok_bg}; color:{ok_fg}; font-size:13px; font-weight:700; "
                            f"padding:0 12px; border-radius:8px; border:1px solid {ok_bd};"
                        )
                    else:
                        self.lbl_op.setText("Сканер отключён")
                        self.lbl_op.setStyleSheet(
                            f"background:{err_bg}; color:{err_fg}; font-size:13px; font-weight:700; "
                            f"padding:0 12px; border:1px solid {err_bd}; border-radius:8px;"
                        )

            # 2. Проверка статуса принтера
            live = get_currently_connected_printer(force_refresh=False)
            is_conn = bool(live and (live.get("connected") or live.get("online")))
            pr_name = live["name"] if is_conn else ""

            if is_conn:
                self.lbl_printer.setText(f"🖨 {pr_name}")
                self.lbl_printer.setStyleSheet(
                    f"background:{ok_bg}; color:{ok_fg}; font-size:13px; font-weight:700; "
                    f"padding:0 12px; border-radius:8px; border:1px solid {ok_bd};"
                )
                if not self.scan_input.isEnabled():
                    self.scan_input.setEnabled(True)
                    self.scan_input.setPlaceholderText("Ожидание сканирования / ручного ввода...")
                    self._show_status("ready", "🟢 Ожидание сканирования / ручного ввода", sub="Поднесите штрихкод товара к сканеру или введите цифры вручную")
            else:
                self.lbl_printer.setText("Принтер отключён")
                self.lbl_printer.setStyleSheet(
                    f"background:{err_bg}; color:{err_fg}; font-size:13px; font-weight:700; "
                    f"padding:0 12px; border-radius:8px; border:1px solid {err_bd};"
                )
                if self.scan_input.isEnabled():
                    self.scan_input.setEnabled(False)
                    self.scan_input.setPlaceholderText("Сканирование заблокировано: подключите принтер")
                self._show_status(
                    "error",
                    "Принтер не подключён — подключите принтер к ПК для начала работы",
                    sub="Включите питание принтера или подключите USB-кабель к компьютеру"
                )
        except Exception as e:
            log.debug("_refresh_header_route: %s", e)
    def _on_poll_tick(self):
        if not self.isVisible():
            return
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

        import socket
        all_pkgs = sync_mod.list_available_json_packages(self.store, include_outbox=False, auto_query_server=False)
        if not all_pkgs:
            all_pkgs = sync_mod.list_available_json_packages(self.store, include_outbox=True, auto_query_server=False)

        # Убираем дубликаты по (название партии, ШК), отдавая приоритет свежим
        seen_keys = set()
        raw_pkgs = []
        for p in sorted(all_pkgs, key=lambda x: x.get("mtime") or 0, reverse=True):
            key = (str(p.get("batch_title") or p.get("filename")), str(p.get("shk") or ""))
            if key not in seen_keys:
                seen_keys.add(key)
                raw_pkgs.append(p)
        raw_pkgs.sort(key=lambda x: (x.get("batch_title") or "", x.get("shk") or ""))

        try:
            my_pc_name = sync_mod.get_pc_node_name(self.store).strip().lower()
        except Exception:
            my_pc_name = ""
        my_host = socket.gethostname().strip().lower()
        active_title = ((self._active_batch_info or {}).get("title") or (self.store.get_setting("active_batch_name") or "")).strip()

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
                b_title = p.get("batch_title") or p.get("filename")
                it_title = QTableWidgetItem(b_title)
                it_title.setData(Qt.UserRole, p)
                t.setItem(r, 0, it_title)
                t.setItem(r, 1, QTableWidgetItem(p.get("shk") or "—"))
                t.setItem(r, 2, QTableWidgetItem(p.get("gtin") or "—"))
                t.setItem(r, 3, QTableWidgetItem(f"{p.get('total_codes', 0)} шт."))

                claimed_by = str(p.get("claimed_by") or "").strip()
                claimed_low = claimed_by.lower()
                is_claimed_by_me = bool(claimed_by) and (
                    (my_pc_name and my_pc_name in claimed_low)
                    or (my_host and my_host in claimed_low)
                )

                # Если партия помечена нашим ПК, но сейчас не выбрана — это устаревшая пометка, считаем её свободной ("Готов")
                if is_claimed_by_me and b_title != active_title:
                    claimed_by = ""

                if active_title and b_title == active_title:
                    st_text = "Выбрана сейчас"
                    st_color = QColor("#2563eb")
                elif not claimed_by:
                    st_text = "Готов"
                    st_color = QColor("#2e856e")
                else:
                    st_text = f"Выдан ({claimed_by})"
                    st_color = QColor("#dc2626")

                st_item = QTableWidgetItem(st_text)
                st_item.setForeground(QBrush(st_color))
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
            chosen = t.item(idx, 0).data(Qt.UserRole) if t.item(idx, 0) else None
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
        """Очищает привязку активной партии и снимает отметку 'Выдан' в JSON-файле для текущего ПК."""
        prev_info = self._active_batch_info or {}
        prev_title = prev_info.get("title") or (self.store.get_setting("active_batch_name") or "")
        self._active_batch_info = None
        self.store.set_setting("active_batch_name", "")
        self.store.set_setting("active_batch_shk", "")

        try:
            import json
            my_pc = sync_mod.get_pc_node_name(self.store).strip().lower()
            folder = sync_mod.get_sync_folder(self.store)
            for sub in (sync_mod.packages_dir(folder), folder / "master" / "outbox"):
                if not sub.exists():
                    continue
                for jf in sub.glob("*.json"):
                    try:
                        data = json.loads(jf.read_text(encoding="utf-8"))
                        if not isinstance(data, dict):
                            continue
                        claimed = str(data.get("claimed_by") or "").strip().lower()
                        b_title = str(data.get("package_title") or "").strip()
                        if claimed and (my_pc in claimed or (prev_title and b_title == prev_title)):
                            data["claimed_by"] = ""
                            data["claimed_at"] = ""
                            jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass
        except Exception:
            pass

        self._refresh_batch_progress()
        self.toast.show_message("Выбор партии сброшен", "ok")

    def _restore_or_check_active_batch(self):
        saved_name = self.store.get_setting("active_batch_name") or ""
        saved_shk = self.store.get_setting("active_batch_shk") or ""
        if saved_name:
            self._active_batch_info = {"title": saved_name, "shk": saved_shk}
        self._refresh_batch_progress()

    def _refresh_batch_progress(self):
        """Обновляет остатки по текущей партии или показывает, что партия не выбрана."""
        import db

        def _set_empty_state():
            self.lbl_batch_title.setText("Заказ / партия не выбрана")
            self.lbl_batch_sub.setText("Нажмите «Выбрать заказ», чтобы загрузить марки с сервера")
            self.lbl_batch_counter.setText("0 / 0 шт. (0%)")
            self.batch_progress_bar.setValue(0)
            if hasattr(self, "lbl_batch_badge"):
                self.lbl_batch_badge.setText("НЕТ ЗАКАЗА")
                bg_s = "#e6eaef" if self._is_light else "#0f172a"
                fg_s = "#5a6778" if self._is_light else "#8b97a6"
                bd_s = "#cfd6e0" if self._is_light else "#2a3542"
                self.lbl_batch_badge.setStyleSheet(
                    f"background:{bg_s}; color:{fg_s}; border:1px solid {bd_s}; "
                    "border-radius:6px; font-size:10px; font-weight:700; padding:0 8px; letter-spacing:0.6px;"
                )
            if hasattr(self, "btn_select_batch"):
                self.btn_select_batch.setText("📦 Выбрать заказ")
            if hasattr(self, "btn_clear_batch"):
                self.btn_clear_batch.setVisible(False)
            if hasattr(self, "batch_progress_widget"):
                self.batch_progress_widget.setVisible(False)

        if not self._active_batch_info:
            _set_empty_state()
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

        if total_cnt == 0:
            self._active_batch_info = None
            try:
                self.store.set_setting("active_batch_name", "")
                self.store.set_setting("active_batch_shk", "")
            except Exception:
                pass
            _set_empty_state()
            return

        used_cnt = total_cnt - free_cnt
        percent = int((used_cnt / total_cnt) * 100) if total_cnt > 0 else 0

        if hasattr(self, "lbl_batch_badge"):
            self.lbl_batch_badge.setText("● АКТИВНЫЙ ЗАКАЗ")
            if self._is_light:
                self.lbl_batch_badge.setStyleSheet(
                    "background:#dbeafe; color:#1e40af; border:1px solid #93c5fd; "
                    "border-radius:6px; font-size:10px; font-weight:700; padding:0 8px; letter-spacing:0.6px;"
                )
            else:
                self.lbl_batch_badge.setStyleSheet(
                    "background:#172554; color:#60a5fa; border:1px solid #1d4ed8; "
                    "border-radius:6px; font-size:10px; font-weight:700; padding:0 8px; letter-spacing:0.6px;"
                )

        pretty_title = title.replace("_", " ")
        self.lbl_batch_title.setText(pretty_title)
        shk_part = f"ШК: {shk}  ·  " if shk else ""
        self.lbl_batch_sub.setText(
            f"{shk_part}Осталось: {free_cnt} шт.  ·  Обработано: {used_cnt} шт."
        )
        self.lbl_batch_counter.setText(f"{used_cnt} / {total_cnt} шт. ({percent}%)")
        self.batch_progress_bar.setValue(percent)

        if hasattr(self, "btn_select_batch"):
            self.btn_select_batch.setText("📦 Сменить заказ")
        if hasattr(self, "btn_clear_batch"):
            self.btn_clear_batch.setVisible(True)
        if hasattr(self, "batch_progress_widget"):
            self.batch_progress_widget.setVisible(True)
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
        if not live_pr or not (live_pr.get("connected") or live_pr.get("online")):
            self._show_status(
                "error",
                "Принтер не подключён — подключите принтер к ПК для начала работы",
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
        """Встроенный цветовой статус без блокирующих диалогов (мягкая палитра)."""
        is_lt = self._is_light
        if kind == "error":
            err_bg = "#eedee0" if is_lt else "#24171a"
            err_bd = "#d6b0b5" if is_lt else "#6b343b"
            err_fg = "#9f3a44" if is_lt else "#e5989b"
            err_sub = "#b05b63" if is_lt else "#c9888c"
            self.status_card.setStyleSheet(f"QFrame#statusCard {{ background:{err_bg}; border-radius:12px; border:1px solid {err_bd}; }}")
            self.lbl_status.setText(message)
            self.lbl_status.setStyleSheet(f"color:{err_fg}; font-size:17px; font-weight:700; background:transparent;")
            self.lbl_status_sub.setText(sub or "Отсканируйте следующий штрихкод для сброса ошибки")
            self.lbl_status_sub.setStyleSheet(f"color:{err_sub}; font-size:13px; background:transparent;")
        elif kind == "success":
            ok_bg = "#dfece6" if is_lt else "#11241d"
            ok_bd = "#b3d9c9" if is_lt else "#2b5c49"
            ok_fg = "#246e53" if is_lt else "#6ee7b7"
            ok_sub = "#3b8269" if is_lt else "#a7f3d0"
            self.status_card.setStyleSheet(f"QFrame#statusCard {{ background:{ok_bg}; border-radius:12px; border:1px solid {ok_bd}; }}")
            self.lbl_status.setText(message)
            self.lbl_status.setStyleSheet(f"color:{ok_fg}; font-size:17px; font-weight:700; background:transparent;")
            self.lbl_status_sub.setText(sub or "Готово к сканированию следующего товара")
            self.lbl_status_sub.setStyleSheet(f"color:{ok_sub}; font-size:13px; background:transparent;")
        else:
            border_c = "#3b6ea5"
            bg_c = "#e8ecf1" if is_lt else "#161b22"
            text_s = "#5a6778" if is_lt else "#8b97a6"
            self.status_card.setStyleSheet(f"QFrame#statusCard {{ background:{bg_c}; border-radius:12px; border:2px solid {border_c}; }}")
            self.lbl_status.setText(message)
            self.lbl_status.setStyleSheet("color:#3b6ea5; font-size:17px; font-weight:700; background:transparent;")
            self.lbl_status_sub.setText(sub or "Поднесите штрихкод товара к сканеру")
            self.lbl_status_sub.setStyleSheet(f"color:{text_s}; font-size:13px; background:transparent;")
    # ==========================================================================
    # ПОВТОР ПЕЧАТИ (1 / 3 / 5)
    # ==========================================================================
    def _show_reprint_menu(self):
        """Меню повтора последних напечатанных этикеток."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#1e293b; color:#f8fafc; border:1px solid #475569; padding:4px; border-radius:6px; } "
            "QMenu::item { padding:8px 18px; border-radius:4px; font-size:14px; font-weight:600; } "
            "QMenu::item:selected { background:#3b6ea5; }"
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

    def showEvent(self, event):
        super().showEvent(event)
        try:
            cur_lt = (get_current_theme() == "light")
            if cur_lt != self._is_light:
                self.apply_theme("light" if cur_lt else "dark")
        except Exception:
            pass

    def apply_theme(self, theme_name: str = ""):
        """Динамически применяет светлую или тёмную тему ко всем карточкам и элементам окна оператора."""
        is_lt = (theme_name == "light") if theme_name else (get_current_theme() == "light")
        self._is_light = is_lt

        if is_lt:
            bg_main = "#dce2ea"
            bg_card = "#e8ecf1"
            bg_subtle = "#dde3eb"
            border_card = "#c2cbd6"
            text_main = "#1e293b"
            text_sub = "#475569"
            disabled_bg = "#d4dbe4"
            disabled_fg = "#94a3b8"
            disabled_bd = "#cbd5e1"
            reprint_hover = "#cbd5e1"
            clear_bg = "#fef2f2"
            clear_bd = "#fecaca"
        else:
            bg_main = "#0f1419"
            bg_card = "#161b22"
            bg_subtle = "#1c2129"
            border_card = "#30363d"
            text_main = "#e6edf3"
            text_sub = "#8b949e"
            disabled_bg = "#161b22"
            disabled_fg = "#6e7681"
            disabled_bd = "#30363d"
            reprint_hover = "#30363d"
            clear_bg = "#24171a"
            clear_bd = "#6b343b"

        self.setStyleSheet(f"OperatorWindow {{ background:{bg_main}; }}")

        if hasattr(self, "header_card"):
            self.header_card.setStyleSheet(
                f"QFrame#headerCard {{ background:{bg_subtle}; border-radius:10px; border:1px solid {border_card}; padding:4px; }}"
            )
        if hasattr(self, "lbl_point"):
            self.lbl_point.setStyleSheet(f"font-size:15px; font-weight:700; color:{text_main}; background:transparent;")
        if hasattr(self, "lbl_operator_name"):
            self.lbl_operator_name.setStyleSheet(f"color:{text_sub}; font-size:14px; font-weight:500; background:transparent;")

        if hasattr(self, "batch_card"):
            self.batch_card.setStyleSheet(
                f"QFrame#batchCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}"
            )
        if hasattr(self, "lbl_batch_title"):
            self.lbl_batch_title.setStyleSheet(f"color:{text_main}; font-size:15px; font-weight:700; background:transparent;")
        if hasattr(self, "lbl_batch_sub"):
            self.lbl_batch_sub.setStyleSheet(f"color:{text_sub}; font-size:12px; background:transparent;")
        if hasattr(self, "batch_progress_widget"):
            prog_bg = "#e1e7ef" if is_lt else "#131820"
            self.batch_progress_widget.setStyleSheet(
                f"QFrame#batchProgressBox {{ background:{prog_bg}; border:1px solid {border_card}; border-radius:10px; }}"
            )
        if hasattr(self, "lbl_batch_prog_cap"):
            self.lbl_batch_prog_cap.setStyleSheet(f"color:{text_sub}; font-size:11px; font-weight:600; border:none; background:transparent;")
        if hasattr(self, "lbl_batch_counter"):
            counter_col = "#2e5988" if is_lt else "#60a5fa"
            self.lbl_batch_counter.setStyleSheet(
                f"color:{counter_col}; font-size:12px; font-weight:700; font-family:Consolas,monospace; border:none; background:transparent;"
            )
        if hasattr(self, "btn_clear_batch"):
            self.btn_clear_batch.setStyleSheet(
                f"QPushButton {{ background:{bg_subtle}; color:{text_sub}; border:1px solid {border_card}; "
                f"border-radius:8px; font-weight:600; font-size:12px; padding:0 12px; }} "
                f"QPushButton:hover {{ background:#c05b63; color:#ffffff; border-color:#c05b63; }}"
            )
        if hasattr(self, "batch_progress_bar"):
            track_col = "#dce3ec" if is_lt else "#1e293b"
            self.batch_progress_bar.setStyleSheet(
                f"QProgressBar {{ background:{track_col}; border-radius:5px; border:none; }} "
                f"QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b6ea5, stop:1 #5a94d6); border-radius:5px; }}"
            )
        if hasattr(self, "_refresh_header_route"):
            self._refresh_header_route()
        if hasattr(self, "_refresh_batch_progress"):
            self._refresh_batch_progress()

        if hasattr(self, "scan_input"):
            self.scan_input.setStyleSheet(
                f"QLineEdit#scanInput {{ background:{bg_subtle}; color:{text_main}; font-size:16px; font-weight:600; "
                f"padding:8px 14px; border:1px solid {border_card}; border-radius:8px; font-family:Consolas,monospace; }} "
                f"QLineEdit#scanInput:focus {{ border:2px solid #3b6ea5; background:{bg_card}; }} "
                f"QLineEdit#scanInput:disabled {{ background:{disabled_bg}; color:{disabled_fg}; border:1px solid {disabled_bd}; }}"
            )
        if hasattr(self, "btn_reprint"):
            self.btn_reprint.setStyleSheet(
                f"QPushButton {{ background:{bg_subtle}; color:{text_main}; font-size:14px; font-weight:600; "
                f"padding:6px 14px; border-radius:8px; border:1px solid {border_card}; }} "
                f"QPushButton:hover {{ background:{reprint_hover}; }}"
            )

        if hasattr(self, "last_card"):
            self.last_card.setStyleSheet(f"QFrame#lastCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}")
        if hasattr(self, "lc_title"):
            self.lc_title.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:700; letter-spacing:1px; background:transparent;")
        if hasattr(self, "lbl_last_product"):
            self.lbl_last_product.setStyleSheet(f"font-size:15px; font-weight:700; color:{text_main}; background:transparent;")
        if hasattr(self, "lbl_last_details"):
            self.lbl_last_details.setStyleSheet(f"font-family:Consolas,monospace; color:{text_sub}; font-size:13px; background:transparent;")
        if hasattr(self, "lbl_last_dm"):
            self.lbl_last_dm.setStyleSheet("font-family:Consolas,monospace; color:#2e856e; font-size:12px; background:transparent;")

        if hasattr(self, "kpi_card"):
            self.kpi_card.setStyleSheet(f"QFrame#kpiCard {{ background:{bg_card}; border-radius:10px; border:1px solid {border_card}; }}")
        for lbl in getattr(self, "kpi_lbls", []):
            lbl.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:600; background:transparent;")

        if hasattr(self, "hist_title"):
            self.hist_title.setStyleSheet(f"color:{text_sub}; font-size:12px; font-weight:700; letter-spacing:1px; background:transparent;")
        if hasattr(self, "history_table"):
            alt_bg = "#e1e6ed" if is_lt else "#131922"
            self.history_table.setStyleSheet(
                f"QTableWidget {{ background:{bg_card}; alternate-background-color:{alt_bg}; border-radius:10px; border:1px solid {border_card}; color:{text_main}; font-size:13px; }} "
                f"QHeaderView::section {{ background:{bg_subtle}; color:{text_sub}; font-weight:700; border:none; border-bottom:1px solid {border_card}; padding:7px 10px; }}"
            )

        if hasattr(self, "scan_input") and self.scan_input.isEnabled():
            self._show_status(
                "ready",
                self.lbl_status.text() if hasattr(self, "lbl_status") else "🟢 Ожидание сканирования / ручного ввода",
                sub=self.lbl_status_sub.text() if hasattr(self, "lbl_status_sub") else "Поднесите штрихкод товара к сканеру"
            )