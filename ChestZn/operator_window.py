"""Экран оператора — минималистичный, без технических полей."""
from typing import Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence, QFont, QBrush, QColor

from logger import get_logger
log = get_logger("operator")

from store import Store, parse_foreign_barcode
from barcode import render_datamatrix
from toast import Toast


def _play_sound(kind: str):
    """Воспроизвести звук успеха/ошибки."""
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        # Простая попытка проиграть системный сигнал
        if kind == "error":
            app.beep()
    except Exception:
        pass


class OperatorWindow(QWidget):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.last_cz_code = None
        self.last_foreign = None
        self.last_result_state = None   # 'ok' | 'error' | 'idle'

        self.toast = Toast(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # ============ HEADER ============
        root.addWidget(self._build_header())

        # ============ SCAN BAR ============
        root.addWidget(self._build_scanbar())

        # ============ RESULT ============
        root.addWidget(self._build_result_card(), stretch=0)

        # ============ COUNTERS ============
        root.addWidget(self._build_counter())

        # ============ MINI-JOURNAL ============
        root.addWidget(self._build_mini_journal(), stretch=1)

        # ============ ACTIONS ============
        root.addWidget(self._build_actions())

        # ============ HOTKEYS ============
        QShortcut(QKeySequence("F2"), self, self.do_print)
        QShortcut(QKeySequence("F5"), self, self._open_manual)
        QShortcut(QKeySequence("F9"), self, self._open_full_journal)
        QShortcut(QKeySequence("Esc"), self, self.reset)

        self._refresh_counter()
        self._refresh_mini_journal()
        self._show_result("idle")

    # ==================================================================
    # HEADER
    # ==================================================================
    def _build_header(self) -> QFrame:
        box = QFrame()
        box.setObjectName("card")
        h = QHBoxLayout(box)
        h.setContentsMargins(18, 10, 18, 10)
        h.setSpacing(20)

        point = self.store.get_point(self.store.current_point) or {}
        point_id = point.get("id", self.store.current_point)
        point_name = point.get("name", "—")

        lbl_point = QLabel(f"Точка:  {point_id} · {point_name}")
        lbl_point.setStyleSheet("color:#e6edf3; font-size:13px; font-weight:500;")
        h.addWidget(lbl_point)

        h.addStretch(1)

        lbl_op = QLabel(f"Оператор:  {self.store.current_operator}")
        lbl_op.setStyleSheet("color:#8b97a6; font-size:13px;")
        h.addWidget(lbl_op)

        return box

    # ==================================================================
    # SCAN BAR
    # ==================================================================
    def _build_scanbar(self) -> QFrame:
        box = QFrame()
        box.setObjectName("scanbar")
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        title = QLabel("СКАНИРУЙТЕ ИНОСТРАННЫЙ ШТРИХКОД")
        title.setStyleSheet(
            "color:#8b97a6; letter-spacing:1.5px; font-size:11px; font-weight:600;"
        )
        v.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(14)

        icon = QLabel("📡")
        icon.setStyleSheet(
            "font-size:22px; padding:6px 10px; "
            "background:#1c2530; border:1px solid #2a3542; border-radius:8px;"
        )
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignCenter)
        row.addWidget(icon)

        self.scan_input = QLineEdit()
        self.scan_input.setObjectName("scanInput")
        self.scan_input.setPlaceholderText(
            "Ожидание сканирования... (Enter для скана)"
        )
        self.scan_input.returnPressed.connect(lambda: self.do_scan(mode="auto"))
        # Крупный шрифт
        f = QFont("Consolas")
        f.setPixelSize(16)
        self.scan_input.setFont(f)
        row.addWidget(self.scan_input, stretch=1)

        # Индикатор статуса
        self.status_dot = QLabel("●")
        self.status_dot.setFixedSize(40, 40)
        self.status_dot.setAlignment(Qt.AlignCenter)
        self.status_dot.setStyleSheet(
            "color:#64748b; font-size:28px;"
        )
        row.addWidget(self.status_dot)

        v.addLayout(row)
        return box

    # ==================================================================
    # RESULT CARD
    # ==================================================================
    def _build_result_card(self) -> QFrame:
        box = QFrame()
        box.setObjectName("card")
        box.setMinimumHeight(180)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        v = QVBoxLayout(box)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(8)

        v.addStretch(1)

        self.result_icon = QLabel("⏳")
        self.result_icon.setAlignment(Qt.AlignCenter)
        self.result_icon.setStyleSheet("font-size:56px;")
        v.addWidget(self.result_icon)

        self.result_title = QLabel("Готово к сканированию")
        self.result_title.setAlignment(Qt.AlignCenter)
        self.result_title.setStyleSheet(
            "color:#8b97a6; font-size:22px; font-weight:600;"
        )
        v.addWidget(self.result_title)

        self.result_subtitle = QLabel("")
        self.result_subtitle.setAlignment(Qt.AlignCenter)
        self.result_subtitle.setStyleSheet(
            "color:#64748b; font-size:13px;"
        )
        self.result_subtitle.setWordWrap(True)
        v.addWidget(self.result_subtitle)

        # Кнопки действия при ошибке
        self.error_actions = QWidget()
        ea = QHBoxLayout(self.error_actions)
        ea.setContentsMargins(0, 8, 0, 0)
        ea.setSpacing(10)
        ea.setAlignment(Qt.AlignCenter)

        self.btn_error_retry = QPushButton("🔄 Повторить")
        self.btn_error_retry.clicked.connect(lambda: self.do_scan(mode="auto"))
        ea.addWidget(self.btn_error_retry)

        self.btn_error_skip = QPushButton("⏭ Пропустить")
        self.btn_error_skip.clicked.connect(self.reset)
        ea.addWidget(self.btn_error_skip)

        ea.addStretch(1)

        self.error_actions.hide()
        v.addWidget(self.error_actions)

        v.addStretch(1)
        return box

    def _show_result(self, state: str, foreign: dict = None,
                     message: str = "", sub: str = ""):
        """
        state: 'idle' | 'ok' | 'error'
        """
        self.last_result_state = state

        if state == "idle":
            self.result_icon.setText("⏳")
            self.result_title.setText("Готово к сканированию")
            self.result_title.setStyleSheet(
                "color:#8b97a6; font-size:22px; font-weight:600;"
            )
            self.result_subtitle.setText("Отсканируйте ШК или нажмите Enter")
            self.error_actions.hide()
            self.status_dot.setStyleSheet("color:#64748b; font-size:28px;")

        elif state == "ok":
            self.result_icon.setText("✅")
            self.result_title.setText("НАПЕЧАТАНО")
            self.result_title.setStyleSheet(
                "color:#22c55e; font-size:32px; font-weight:700; letter-spacing:2px;"
            )

            name = (foreign or {}).get("name", "")
            gtin = (foreign or {}).get("gtin", "")
            sn = (foreign or {}).get("serial", "")

            parts = []
            if name:
                parts.append(name)
            if gtin:
                parts.append(f"GTIN {gtin}" + (f" · {sn}" if sn else ""))
            self.result_subtitle.setText(" · ".join(parts) if parts else sub)

            self.error_actions.hide()
            self.status_dot.setStyleSheet("color:#22c55e; font-size:28px;")
            _play_sound("ok")

        elif state == "error":
            self.result_icon.setText("⛔")
            self.result_title.setText(message.upper() or "ОШИБКА")
            self.result_title.setStyleSheet(
                "color:#ef4444; font-size:28px; font-weight:700; letter-spacing:2px;"
            )
            self.result_subtitle.setText(sub or "")

            self.error_actions.show()
            self.status_dot.setStyleSheet("color:#ef4444; font-size:28px;")
            _play_sound("error")

    # ==================================================================
    # COUNTERS
    # ==================================================================
    def _build_counter(self) -> QFrame:
        box = QFrame()
        box.setObjectName("counter")
        h = QHBoxLayout(box)
        h.setContentsMargins(18, 10, 18, 10)
        h.setSpacing(24)

        self.counter_labels = {}
        for key, text, color in [
            ("scans",  "Сканов",   "#3b82f6"),
            ("prints", "Печатей",  "#22c55e"),
            ("errors", "Ошибок",   "#ef4444"),
            ("limit",  "Лимит",    "#f59e0b"),
        ]:
            wrap = QHBoxLayout()
            wrap.setSpacing(6)

            k = QLabel(text + ":")
            k.setStyleSheet("color:#8b97a6; font-size:12px;")

            v = QLabel("0")
            v.setStyleSheet(
                f"color:{color}; font-family:Consolas,monospace; "
                f"font-size:16px; font-weight:600;"
            )
            self.counter_labels[key] = v

            wrap.addWidget(k)
            wrap.addWidget(v)
            h.addLayout(wrap)

        h.addStretch(1)
        return box

    def _refresh_counter(self):
        c = self.store.counter
        self.counter_labels["scans"].setText(str(c["scans"]))
        self.counter_labels["prints"].setText(str(c["prints"]))
        self.counter_labels["errors"].setText(str(c["errors"]))
        self.counter_labels["limit"].setText(str(c["limit"]))

    # ==================================================================
    # MINI-JOURNAL
    # ==================================================================
    def _build_mini_journal(self) -> QFrame:
        box = QFrame()
        box.setObjectName("card")
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 12, 18, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        ttl = QLabel("ПОСЛЕДНИЕ ОПЕРАЦИИ")
        ttl.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;"
        )
        head.addWidget(ttl)
        head.addStretch(1)

        btn_open = QPushButton("📜  Полный журнал  F9")
        btn_open.setFixedHeight(30)
        btn_open.setMinimumWidth(200)
        btn_open.setStyleSheet("font-size:12px; padding: 4px 12px;")
        btn_open.clicked.connect(self._open_full_journal)
        head.addWidget(btn_open)

        v.addLayout(head)

        self.journal_table = QTableWidget(0, 4)
        self.journal_table.setHorizontalHeaderLabels(
            ["Время", "Результат", "GTIN", "Серия"]
        )
        hh = self.journal_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.journal_table.setColumnWidth(0, 90)
        self.journal_table.setColumnWidth(1, 160)
        self.journal_table.verticalHeader().setVisible(False)
        self.journal_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.journal_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.journal_table.setShowGrid(False)
        self.journal_table.setFocusPolicy(Qt.NoFocus)
        self.journal_table.setStyleSheet("""
            QTableWidget {
                background: transparent;
                border: none;
            }
            QTableWidget::item {
                padding: 6px 4px;
                border-bottom: 1px solid #2a3542;
            }
        """)
        v.addWidget(self.journal_table, stretch=1)

        return box

    def _refresh_mini_journal(self):
        try:
            rows = self.store.get_recent_audit(limit=5)
        except Exception:
            rows = []

        t = self.journal_table
        t.setRowCount(0)

        result_colors = {
            "успех":     "#22c55e",
            "не найден": "#ef4444",
            "ошибка":    "#ef4444",
            "в очередь": "#f59e0b",
            "повтор":    "#f59e0b",
            "нет кодов": "#ef4444",
        }

        for r in rows:
            i = t.rowCount()
            t.insertRow(i)

            time_item = QTableWidgetItem(r.get("time", ""))
            f = time_item.font()
            f.setFamily("Consolas")
            time_item.setFont(f)
            time_item.setForeground(QBrush(QColor("#8b97a6")))
            t.setItem(i, 0, time_item)

            action = r.get("action", "")
            res = r.get("result", "") or "—"
            text = f"{action}" + (f" — {res}" if res and res != "—" else "")
            color = result_colors.get(res, "#e6edf3")
            item_result = QTableWidgetItem(text)
            item_result.setForeground(QBrush(QColor(color)))
            t.setItem(i, 1, item_result)

            gtin_item = QTableWidgetItem(r.get("gtin") or "—")
            f2 = gtin_item.font()
            f2.setFamily("Consolas")
            gtin_item.setFont(f2)
            t.setItem(i, 2, gtin_item)

            sn_item = QTableWidgetItem(r.get("sn") or "—")
            f3 = sn_item.font()
            f3.setFamily("Consolas")
            sn_item.setFont(f3)
            t.setItem(i, 3, sn_item)

        t.resizeRowsToContents()

    # ==================================================================
    # ACTIONS
    # ==================================================================
    def _build_actions(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        self.btn_print = QPushButton("🖨  Повтор печати   F2")
        self.btn_print.setProperty("primary", True)
        self.btn_print.clicked.connect(self.do_print)

        self.btn_manual = QPushButton("✏️  Ручной ввод   F5")
        self.btn_manual.clicked.connect(self._open_manual)

        self.btn_journal = QPushButton("📜  Полный журнал   F9")
        self.btn_journal.clicked.connect(self._open_full_journal)

        self.btn_cancel = QPushButton("✖  Сбросить   Esc")
        self.btn_cancel.clicked.connect(self.reset)

        for b in (self.btn_print, self.btn_manual,
                  self.btn_journal, self.btn_cancel):
            h.addWidget(b, stretch=1)

        self.btn_print.setEnabled(False)

        return wrap

    # ==================================================================
    # ДЕЙСТВИЯ
    # ==================================================================
    def do_scan(self, mode: str = "auto"):
        raw = self.scan_input.text()
        foreign = parse_foreign_barcode(raw)
        self.last_foreign = foreign

        self.store.increment_counter("scans")

        if mode == "err":
            self.store.increment_counter("errors")
            self.store.log("Скан", point=self.store.current_point,
                           operator=self.store.current_operator,
                           gtin=foreign["gtin"], sn=foreign["serial"],
                           result="не найден")
            self._show_result("error",
                              message="Код не найден в ГИС МТ",
                              sub=f"GTIN {foreign['gtin']} · {foreign['serial']}")
            self._refresh_counter()
            self._refresh_mini_journal()
            self.btn_print.setEnabled(False)
            self.scan_input.clear()
            self.scan_input.setFocus()
            return

        # Основной путь — через process_scan
        result = self.store.process_scan(
            foreign_code=raw,
            point=self.store.current_point,
            operator=self.store.current_operator,
        )

        if result.get("status") == "ok":
            cz = result["cz"]
            self.last_cz_code = cz["code"]

            self.store.log("Скан", point=self.store.current_point,
                           operator=self.store.current_operator,
                           gtin=foreign["gtin"], sn=foreign["serial"],
                           result="успех")

            # Печать
            self._auto_print(cz["code"], foreign)

            self._refresh_counter()
            self._refresh_mini_journal()
            self.btn_print.setEnabled(True)
        else:
            self.store.increment_counter("errors")
            self.store.log("Скан", point=self.store.current_point,
                           operator=self.store.current_operator,
                           gtin=foreign["gtin"], sn=foreign["serial"],
                           result="нет кодов")
            self._show_result("error",
                              message=result.get("message", "Ошибка"),
                              sub=f"GTIN {result.get('gtin', '—')}")
            self._refresh_counter()
            self._refresh_mini_journal()
            self.btn_print.setEnabled(False)

        self.scan_input.clear()
        self.scan_input.setFocus()

    def _auto_print(self, code: str, foreign: dict = None):
        from printer import print_code, load_config
        cfg = load_config()

        point_cfg = cfg
        try:
            from printer import get_printer_for_point
            point_cfg = get_printer_for_point(cfg, self.store.current_point)
        except Exception:
            pass

        if not point_cfg.get("printer_name") and not point_cfg.get("printer_ip"):
            self._show_result("error",
                              message="Принтер не настроен",
                              sub="Зайдите в Админ → Принтеры")
            return

        result = print_code(code, point_cfg)

        if result["ok"]:
            self.store.mark_printed(code, point=self.store.current_point)
            self.store.increment_counter("prints")
            self.store.log("Автопечать", point=self.store.current_point,
                           operator=self.store.current_operator,
                           result="успех")
            self._show_result("ok", foreign=foreign)
        else:
            self.store.increment_counter("errors")
            self.store.log("Автопечать", point=self.store.current_point,
                           operator=self.store.current_operator,
                           result="ошибка")
            self._show_result("error",
                              message="Ошибка печати",
                              sub=result.get("info", ""))

        self._refresh_counter()

    def do_print(self):
        """Повтор печати последнего кода."""
        if not self.last_cz_code:
            return

        from printer import print_code, load_config
        cfg = load_config()

        try:
            from printer import get_printer_for_point
            point_cfg = get_printer_for_point(cfg, self.store.current_point)
        except Exception:
            point_cfg = cfg

        result = print_code(self.last_cz_code, point_cfg)

        if result["ok"]:
            self.store.mark_printed(self.last_cz_code,
                                    point=self.store.current_point)
            self.store.increment_counter("prints")
            self.store.log("Повтор печати", point=self.store.current_point,
                           operator=self.store.current_operator,
                           result="успех")
            self._show_result("ok", foreign=self.last_foreign)
        else:
            self.store.increment_counter("errors")
            self._show_result("error",
                              message="Ошибка печати",
                              sub=result.get("info", ""))

        self._refresh_counter()
        self._refresh_mini_journal()

    def _open_manual(self):
        self.toast.show_message("Ручной ввод — в разработке", "warn")

    def _open_full_journal(self):
        """Переключить на вкладку Админ → Журнал."""
        try:
            win = self.window()
            if hasattr(win, "tabs"):
                # Показываем вкладку Админ
                win.tabs.setCurrentIndex(1)
                # Прокручиваем админку на журнал
                if hasattr(win, "admin"):
                    win.admin.switch("journal")
        except Exception as e:
            log.error("Не удалось открыть журнал: %s", e)

    def reset(self):
        self.last_cz_code = None
        self.last_foreign = None
        self.scan_input.clear()
        self.scan_input.setFocus()
        self.btn_print.setEnabled(False)
        self._show_result("idle")