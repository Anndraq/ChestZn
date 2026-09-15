"""Экран оператора — скан, данные, DataMatrix, предпросмотр русификатора."""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QLineEdit,
    QPushButton, QGridLayout, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence

from store import Store, parse_foreign_barcode
from barcode import render_datamatrix
from toast import Toast
from datetime import datetime



def field_pair(key: str, value_id_holder: dict) -> tuple:
    """Вернуть пару QLabel (ключ, значение) для формы."""
    k = QLabel(key)
    k.setProperty("class", "fieldKey")
    k.setStyleSheet("color:#8b97a6;")

    v = QLabel("—")
    v.setProperty("class", "fieldValue")
    v.setStyleSheet("color:#64748b; font-style:italic;")
    v.setWordWrap(True)
    value_id_holder[key] = v
    return k, v


class OperatorWindow(QWidget):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.last_cz_code = None
        self.fields = {}

        self.toast = Toast(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # ============ OFFLINE BANNER ============
        self.offline_banner = QLabel(
            "⚠ НЕТ СВЯЗИ С СЕРВЕРОМ — РАБОТА В ОФЛАЙН-РЕЖИМЕ"
        )
        self.offline_banner.setAlignment(Qt.AlignCenter)
        self.offline_banner.setStyleSheet("""
            background-color: rgba(245,158,11,.15);
            border: 1px solid rgba(245,158,11,.4);
            color: #fbbf24;
            padding: 8px;
            border-radius: 8px;
            font-size: 12px;
        """)
        self.offline_banner.hide()
        root.addWidget(self.offline_banner)

        # ============ SCAN BAR ============
        root.addWidget(self._build_scanbar())

        # ============ DATA GRID ============
        root.addWidget(self._build_data_grid())

        # ============ PREVIEW ============
        root.addWidget(self._build_preview(), stretch=1)

        # ============ COUNTER ============
        root.addWidget(self._build_counter())

        # ============ ACTIONS ============
        root.addWidget(self._build_actions())

        # ============ HOTKEYS ============
        QShortcut(QKeySequence("F2"), self, self.do_print)
        QShortcut(QKeySequence("F5"), self, lambda: self.do_scan(mode="err"))
        QShortcut(QKeySequence("F8"), self, self.reset)
        QShortcut(QKeySequence("Esc"), self, self.reset)

        self.reset()

    # ------------------------------------------------------------------
    def _build_scanbar(self) -> QFrame:
        box = QFrame(); box.setObjectName("scanbar")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        title = QLabel("СКАНИРУЙТЕ ИНОСТРАННЫЙ ШТРИХКОД")
        title.setStyleSheet("color:#8b97a6; letter-spacing:1.5px; font-size:11px; font-weight:600;")
        layout.addWidget(title)

        row = QHBoxLayout(); row.setSpacing(14)
        icon = QLabel("📡")
        icon.setStyleSheet("font-size:22px; padding:6px 10px; "
                           "background:#1c2530; border:1px solid #2a3542; border-radius:8px;")
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignCenter)
        row.addWidget(icon)

        self.scan_input = QLineEdit()
        self.scan_input.setObjectName("scanInput")
        self.scan_input.setPlaceholderText("Ожидание сканирования... (Enter для скана)")
        self.scan_input.returnPressed.connect(lambda: self.do_scan(mode="auto"))
        row.addWidget(self.scan_input, stretch=1)

        self.scan_status = QLabel("Ожидание")
        self.scan_status.setObjectName("scanStatus")
        self.scan_status.setAlignment(Qt.AlignCenter)
        self.scan_status.setMinimumWidth(160)
        row.addWidget(self.scan_status)

        layout.addLayout(row)
        return box

    # ------------------------------------------------------------------
    def _build_data_grid(self) -> QWidget:
        wrap = QWidget()
        grid = QHBoxLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)

        # Иностранный ШК
        foreign_card = self._make_card("ИНОСТРАННЫЙ ШК")
        fgrid = QGridLayout(); fgrid.setSpacing(5)
        for i, key in enumerate(["Тип", "GTIN", "Серия", "Партия", "Срок годн.", "Страна"]):
            k, v = field_pair(key, self.fields)
            fgrid.addWidget(k, i, 0)
            fgrid.addWidget(v, i, 1)
        foreign_card.layout().addLayout(fgrid)

        # Честный знак
        cz_card = self._make_card("ЧЕСТНЫЙ ЗНАК", with_badge=True)

        cz_content = QHBoxLayout(); cz_content.setSpacing(14)

        # DataMatrix (большой)
        self.dm_big = QLabel()
        self.dm_big.setFixedSize(120, 120)
        self.dm_big.setStyleSheet("background:#fff; border-radius:6px;")
        self.dm_big.setAlignment(Qt.AlignCenter)
        cz_content.addWidget(self.dm_big)

        # Поля ЧЗ
        cz_fields = QGridLayout(); cz_fields.setSpacing(5)
        for i, key in enumerate(["Статус", "Код", "Источник", "Запрошен"]):
            k, v = field_pair("ЧЗ_" + key, self.fields)
            cz_fields.addWidget(k, i, 0)
            cz_fields.addWidget(v, i, 1)
        cz_content.addLayout(cz_fields, stretch=1)

        cz_card.layout().addLayout(cz_content)

        grid.addWidget(foreign_card, stretch=1)
        grid.addWidget(cz_card, stretch=1)
        return wrap

    def _make_card(self, title: str, with_badge: bool = False) -> QFrame:
        card = QFrame(); card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(10)

        head = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("cardTitle")
        head.addWidget(lbl)
        head.addStretch(1)

        if with_badge:
            self.cz_badge = QLabel("ОЖИДАНИЕ")
            self.cz_badge.setObjectName("badge")
            self.cz_badge.setProperty("state", "idle")
            head.addWidget(self.cz_badge)

        v.addLayout(head)
        return card

    # ------------------------------------------------------------------
    def _build_preview(self) -> QFrame:
        card = QFrame(); card.setObjectName("card")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(10)

        title = QLabel("РУСИФИКАТОР — ПРЕДПРОСМОТР")
        title.setObjectName("cardTitle")
        v.addWidget(title)

        self.preview_body = QFrame(); self.preview_body.setObjectName("previewBody")
        pb = QHBoxLayout(self.preview_body)
        pb.setAlignment(Qt.AlignCenter)

        self.preview_empty = QLabel("Русификатор будет сформирован после получения ЧЗ")
        self.preview_empty.setStyleSheet("color:#8b97a6; font-style:italic;")
        pb.addWidget(self.preview_empty)

        self.label_mock = QFrame(); self.label_mock.setObjectName("labelMock")
        lm = QHBoxLayout(self.label_mock)
        lm.setContentsMargins(14, 14, 14, 14)
        lm.setSpacing(14)

        self.dm_small = QLabel()
        self.dm_small.setFixedSize(90, 90)
        self.dm_small.setStyleSheet("background:transparent;")
        lm.addWidget(self.dm_small)

        info = QVBoxLayout(); info.setSpacing(2)
        self.lm_name = QLabel("—")
        self.lm_name.setStyleSheet("color:#000; font-weight:600; font-size:12px;")
        info.addWidget(self.lm_name)
        for lbl_text, holder in [
            ("Модель", "model"), ("Импортёр", "importer"),
            ("GTIN", "gtin"), ("Серия", "serial"), ("Срок годности", "exp"),
        ]:
            row = QLabel(f"{lbl_text}: —")
            row.setStyleSheet("color:#000; font-family:Consolas,monospace; font-size:11px;")
            setattr(self, f"lm_{holder}", row)
            info.addWidget(row)
        info.addWidget(self._lm_line("Кол-во: 1 шт."))
        info.addStretch(1)
        lm.addLayout(info)
        lm.addStretch(1)

        self.label_mock.hide()
        pb.addWidget(self.label_mock)

        v.addWidget(self.preview_body, stretch=1)
        return card

    def _lm_line(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#000; font-family:Consolas,monospace; font-size:11px;")
        return lbl

    # ------------------------------------------------------------------
    def _build_counter(self) -> QFrame:
        box = QFrame(); box.setObjectName("counter")
        h = QHBoxLayout(box)
        h.setContentsMargins(18, 10, 18, 10)
        h.setSpacing(24)

        self.counter_labels = {}
        for key, text in [
            ("scans", "Сканов"), ("prints", "Печатей"),
            ("errors", "Ошибок"), ("queue", "В очереди"),
            ("limit", "Остаток лимита"),
        ]:
            wrap = QHBoxLayout()
            wrap.setSpacing(6)
            k = QLabel(text + ":")
            k.setProperty("class", "counterKey")
            k.setStyleSheet("color:#8b97a6;")
            v = QLabel("0")
            v.setProperty("class", "counterValue")
            v.setStyleSheet("color:#e6edf3; font-family:Consolas,monospace; font-size:14px;")
            if key in ("errors", "queue"):
                v.setStyleSheet("color:#f59e0b; font-family:Consolas,monospace; font-size:14px;")
            self.counter_labels[key] = v
            wrap.addWidget(k); wrap.addWidget(v)
            h.addLayout(wrap)

        h.addStretch(1)
        return box

    # ------------------------------------------------------------------
    def _build_actions(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        self.btn_print  = self._make_button("🖨 Печать   F2", primary=True)
        self.btn_retry  = self._make_button("🔄 Повтор   F5")
        self.btn_manual = self._make_button("✏️ Вручную   F6")
        self.btn_skip   = self._make_button("⏭ Пропустить   F8", danger=True)
        self.btn_cancel = self._make_button("✖ Сбросить   Esc")

        self.btn_print.clicked.connect(self.do_print)
        self.btn_retry.clicked.connect(lambda: self.do_scan(mode="err"))
        self.btn_manual.clicked.connect(
            lambda: self.toast.show_message("Ручной ввод — в разработке", "warn")
        )
        self.btn_skip.clicked.connect(self.reset)
        self.btn_cancel.clicked.connect(self.reset)

        for b in (self.btn_print, self.btn_retry, self.btn_manual,
                  self.btn_skip, self.btn_cancel):
            h.addWidget(b, stretch=1)
        return wrap

    def _make_button(self, text: str, primary: bool = False, danger: bool = False) -> QPushButton:
        b = QPushButton(text)
        if primary: b.setProperty("primary", True)
        if danger:  b.setProperty("danger", True)
        return b

    # ------------------------------------------------------------------
    # Helpers для обновления полей
    # ------------------------------------------------------------------
    def _set_field(self, key: str, value):
        lbl = self.fields.get(key)
        if not lbl:
            return
        if value in (None, ""):
            lbl.setText("—")
            lbl.setStyleSheet("color:#64748b; font-style:italic;")
        else:
            lbl.setText(str(value))
            lbl.setStyleSheet("color:#e6edf3; font-family:Consolas,monospace;")

    def _set_status(self, text: str, kind: str = ""):
        self.scan_status.setText(text)
        self.scan_status.setProperty("state", kind)
        self.scan_status.style().unpolish(self.scan_status)
        self.scan_status.style().polish(self.scan_status)

    def _set_badge(self, text: str, kind: str):
        self.cz_badge.setText(text)
        self.cz_badge.setProperty("state", kind)
        self.cz_badge.style().unpolish(self.cz_badge)
        self.cz_badge.style().polish(self.cz_badge)

    def _refresh_counter(self):
        c = self.store.counter
        self.counter_labels["scans"].setText(str(c["scans"]))
        self.counter_labels["prints"].setText(str(c["prints"]))
        self.counter_labels["errors"].setText(str(c["errors"]))
        self.counter_labels["queue"].setText(str(len(self.store.queue)))
        self.counter_labels["limit"].setText(str(c["limit"]))

    def _update_buttons(self, state: Optional[str]):
        can_print = state in ("ok", "offline")
        can_retry = state == "error"
        self.btn_print.setEnabled(can_print)
        self.btn_retry.setEnabled(can_retry)
        self.btn_manual.setEnabled(can_retry)
        self.btn_skip.setEnabled(bool(state))

    # ------------------------------------------------------------------
    # Основные действия
    # ------------------------------------------------------------------
    def do_scan(self, mode: str = "auto"):
        raw = self.scan_input.text()
        foreign = parse_foreign_barcode(raw)
        self.store.counter["scans"] += 1

        # offline-режим
        if mode == "offline" or self.store.integrations["gis_mt"]["status"] != "ok":
            cz = self.store.take_code(foreign["gtin"])
            self.store.queue.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "gtin": foreign["gtin"], "sn": foreign["serial"],
                "point": "T-01", "operator": "Иванов И.И.", "status": "pending",
            })
            self.store.log("Скан (офлайн)", point="T-01", operator="Иванов И.И.",
                           gtin=foreign["gtin"], sn=foreign["serial"], result="в очередь")
            self._render_ok(foreign, cz["code"], state="offline")
            self.toast.show_message("Сохранено в очередь синхронизации", "warn")
            self._refresh_counter()
            self._update_buttons("offline")
            return

        if mode == "err":
            self.store.counter["errors"] += 1
            self.store.log("Скан", point="T-01", operator="Иванов И.И.",
                           gtin=foreign["gtin"], sn=foreign["serial"], result="не найден")
            self._render_err(foreign)
            self.toast.show_message("Код не найден в ГИС МТ", "err")
            self._refresh_counter()
            self._update_buttons("error")
            return

        # Нормальный путь
        gtin_cz = self.store.gtin_map.get(foreign["gtin"], foreign["gtin"])
        cz = self.store.take_code(gtin_cz)
        if not cz:
            self.store.counter["errors"] += 1
            self.toast.show_message("Пул кодов пуст", "err")
            self._refresh_counter()
            return
        cz["used"] = True
        cz["point"] = "T-01"
        self.store.log("Скан", point="T-01", operator="Иванов И.И.",
                       gtin=foreign["gtin"], sn=foreign["serial"], result="успех")
        self._render_ok(foreign, cz["code"], state="ok")
        self.toast.show_message("Честный знак получен", "ok")
        self._refresh_counter()
        self._update_buttons("ok")

    def _render_ok(self, foreign: dict, cz_code: str, state: str = "ok"):
        # Иностранный ШК
        self._set_field("Тип", foreign["type"])
        self._set_field("GTIN", foreign["gtin"])
        self._set_field("Серия", foreign["serial"])
        self._set_field("Партия", foreign["lot"])
        self._set_field("Срок годн.", foreign["exp"])
        self._set_field("Страна", foreign["country"])

        # ЧЗ
        if state == "offline":
            self._set_badge("ОТЛОЖЕН", "warn")
            self._set_field("ЧЗ_Статус", "🟡 Будет запрошен при восстановлении связи")
            self._set_field("ЧЗ_Источник", "из локального кэша")
            self._set_field("ЧЗ_Запрошен", datetime.now().strftime("%H:%M:%S"))
            self._set_status("🟡 Офлайн: код в очереди", "warn")
        else:
            self._set_badge("НАЙДЕН", "ok")
            self._set_field("ЧЗ_Статус", "✅ Найден в ГИС МТ")
            self._set_field("ЧЗ_Источник", "ГИС МТ / API (заглушка)")
            self._set_field("ЧЗ_Запрошен", datetime.now().strftime("%H:%M:%S"))
            self._set_status("✅ Найдено", "ok")
        self._set_field("ЧЗ_Код", cz_code)

        # DataMatrix
        pix_big = render_datamatrix(cz_code, 120)
        self.dm_big.setPixmap(pix_big)
        pix_small = render_datamatrix(cz_code, 90)
        self.dm_small.setPixmap(pix_small)

        # Предпросмотр русификатора
        self.preview_empty.hide()
        self.label_mock.show()
        self.lm_name.setText(foreign["name"])
        self.lm_model.setText(f"Модель: {foreign['model']}")
        self.lm_importer.setText(f"Импортёр: {foreign['importer']}")
        self.lm_gtin.setText(f"GTIN: {foreign['gtin']}")
        self.lm_serial.setText(f"Серия: {foreign['serial']}")
        self.lm_exp.setText(f"Срок годности: {foreign['exp']}")

        self.last_cz_code = cz_code

    def _render_err(self, foreign: dict):
        self._set_field("Тип", foreign["type"])
        self._set_field("GTIN", foreign["gtin"])
        self._set_field("Серия", foreign["serial"])
        self._set_field("Партия", foreign["lot"])
        self._set_field("Срок годн.", foreign["exp"])
        self._set_field("Страна", foreign["country"])

        self._set_badge("НЕ НАЙДЕН", "err")
        self._set_field("ЧЗ_Статус", "⛔ Код не найден в ГИС МТ")
        self._set_field("ЧЗ_Код", None)
        self._set_field("ЧЗ_Источник", None)
        self._set_field("ЧЗ_Запрошен", datetime.now().strftime("%H:%M:%S"))
        self._set_status("❌ Код не найден", "err")

        self.dm_big.clear()
        self.dm_small.clear()

        self.preview_empty.setText("Русификатор не сформирован: нет Честного знака")
        self.preview_empty.show()
        self.label_mock.hide()

        self.last_cz_code = None

    def do_print(self):
        if not self.last_cz_code:
            return
        for gtin, codes in self.store.pool.items():
            for c in codes:
                if c["code"] == self.last_cz_code:
                    c["printed"] = True
                    c["printed_at"] = datetime.now().strftime("%H:%M:%S")
                    break
        self.store.counter["prints"] += 1
        self.store.log("Печать", point="T-01", operator="Иванов И.И.",
                       result="успех")
        self._refresh_counter()
        self.toast.show_message("Этикетка отправлена на принтер", "ok")

    def reset(self):
        self.last_cz_code = None
        for key in list(self.fields.keys()):
            self._set_field(key, None)
        self._set_badge("ОЖИДАНИЕ", "idle")
        self._set_status("Ожидание", "")
        self.dm_big.clear()
        self.dm_small.clear()
        self.preview_empty.setText("Русификатор будет сформирован после получения ЧЗ")
        self.preview_empty.show()
        self.label_mock.hide()
        self.scan_input.clear()
        self.scan_input.setFocus()
        self._update_buttons(None)

    # ------------------------------------------------------------------
    def set_offline_banner(self, visible: bool):
        self.offline_banner.setVisible(visible)