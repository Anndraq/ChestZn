"""Экран оператора — скан, данные, DataMatrix, предпросмотр русификатора."""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QLineEdit,
    QPushButton, QGridLayout, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence

from logger import get_logger
log = get_logger("operator")

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

        log.info("Инициализация OperatorWindow (точка=%s)",
                 self.store.current_point)

        self.toast = Toast(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # ============ INFO BAR (точка + оператор + индикатор печати) ============
        root.addWidget(self._build_infobar())

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
        QShortcut(QKeySequence("F5"), self, self.reset)
        QShortcut(QKeySequence("F8"), self, self.reset)
        QShortcut(QKeySequence("Esc"), self, self.reset)

        self.reset()
        self._refresh_counter()
        self._update_print_alert()

        # Таймер: проверять не напечатанные каждые 30 секунд
        self._print_alert_timer = QTimer(self)
        self._print_alert_timer.setInterval(30_000)
        self._print_alert_timer.timeout.connect(self._update_print_alert)
        self._print_alert_timer.start()

        log.info("OperatorWindow готов")

    # ------------------------------------------------------------------
    def _build_infobar(self) -> QFrame:
        """Шапка: текущая точка, оператор, индикатор не напечатанных."""
        box = QFrame()
        box.setObjectName("scanbar")
        h = QHBoxLayout(box)
        h.setContentsMargins(18, 8, 18, 8)
        h.setSpacing(20)

        # Точка
        point_label = QLabel("Точка:")
        point_label.setStyleSheet("color:#8b97a6; font-size:11px; letter-spacing:1px;")
        h.addWidget(point_label)

        self.point_value = QLabel(self.store.current_point)
        self.point_value.setStyleSheet(
            "color:#3b82f6; font-size:14px; font-weight:600; "
            "font-family:Consolas,monospace;"
        )
        h.addWidget(self.point_value)

        # Название точки
        name = self._point_name(self.store.current_point)
        self.point_name_value = QLabel(name)
        self.point_name_value.setStyleSheet("color:#8b97a6; font-size:12px;")
        h.addWidget(self.point_name_value)

        h.addStretch(1)

        # Оператор
        op_label = QLabel("Оператор:")
        op_label.setStyleSheet("color:#8b97a6; font-size:11px; letter-spacing:1px;")
        h.addWidget(op_label)

        self.operator_value = QLabel(self.store.current_operator)
        self.operator_value.setStyleSheet("color:#e6edf3; font-size:12px;")
        h.addWidget(self.operator_value)

        h.addStretch(1)

        # Индикатор не напечатанных
        self.print_alert_label = QLabel("")
        self.print_alert_label.setStyleSheet(
            "color:#f59e0b; font-weight:600; "
            "background:rgba(245,158,11,.12); "
            "padding:4px 10px; border-radius:6px;"
        )
        self.print_alert_label.hide()
        h.addWidget(self.print_alert_label)

        return box

    def _point_name(self, point_id: str) -> str:
        for p in self.store.points:
            if p["id"] == point_id:
                return f"· {p['name']}"
        return ""

    # ------------------------------------------------------------------
    def _build_scanbar(self) -> QFrame:
        box = QFrame(); box.setObjectName("scanbar")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        title = QLabel("СКАНИРУЙТЕ ИНОСТРАННЫЙ ШТРИХКОД")
        title.setStyleSheet(
            "color:#8b97a6; letter-spacing:1.5px; font-size:11px; font-weight:600;"
        )
        layout.addWidget(title)

        row = QHBoxLayout(); row.setSpacing(14)
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
        self.scan_input.returnPressed.connect(self.do_scan)
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

        # ---- Макет этикетки ----
        self.label_mock = QFrame()
        self.label_mock.setObjectName("labelMock")
        self.label_mock.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

        lm = QHBoxLayout(self.label_mock)
        lm.setContentsMargins(16, 16, 16, 16)
        lm.setSpacing(16)
        lm.setAlignment(Qt.AlignTop)

        # DataMatrix — слева
        self.dm_small = QLabel()
        self.dm_small.setFixedSize(120, 120)
        self.dm_small.setStyleSheet("background:transparent;")
        lm.addWidget(self.dm_small, alignment=Qt.AlignTop)

        # Информация — справа
        info = QVBoxLayout()
        info.setSpacing(3)
        info.setAlignment(Qt.AlignTop)

        self.lm_name = QLabel("—")
        self.lm_name.setStyleSheet("color:#000; font-weight:600; font-size:13px;")
        info.addWidget(self.lm_name)

        for lbl_text, holder in [
            ("Модель", "model"), ("Импортёр", "importer"),
            ("GTIN", "gtin"), ("Серия", "serial"), ("Срок годности", "exp"),
        ]:
            row = QLabel(f"{lbl_text}: —")
            row.setStyleSheet(
                "color:#000; font-family:Consolas,monospace; font-size:12px;"
            )
            setattr(self, f"lm_{holder}", row)
            info.addWidget(row)

        info.addWidget(self._lm_line("Кол-во: 1 шт."))

        lm.addLayout(info)

        self.label_mock.hide()
        pb.addWidget(self.label_mock)

        v.addWidget(self.preview_body, stretch=1)
        return card

    def _lm_line(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color:#000; font-family:Consolas,monospace; font-size:12px;"
        )
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
        ]:
            wrap = QHBoxLayout()
            wrap.setSpacing(6)
            k = QLabel(text + ":")
            k.setProperty("class", "counterKey")
            k.setStyleSheet("color:#8b97a6;")
            v = QLabel("0")
            v.setProperty("class", "counterValue")
            v.setStyleSheet(
                "color:#e6edf3; font-family:Consolas,monospace; font-size:14px;"
            )
            if key in ("errors", "queue"):
                v.setStyleSheet(
                    "color:#f59e0b; font-family:Consolas,monospace; font-size:14px;"
                )
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

        self.btn_print  = self._make_button("🖨 Повтор печати   F2", primary=True)
        self.btn_reset  = self._make_button("✖ Сбросить   Esc")

        self.btn_print.clicked.connect(self.do_print)
        self.btn_reset.clicked.connect(self.reset)

        for b in (self.btn_print, self.btn_reset):
            h.addWidget(b, stretch=1)
        return wrap

    def _make_button(self, text: str, primary: bool = False,
                     danger: bool = False) -> QPushButton:
        b = QPushButton(text)
        if primary: b.setProperty("primary", True)
        if danger:  b.setProperty("danger", True)
        return b

    # ------------------------------------------------------------------
    # Helpers
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
            lbl.setStyleSheet(
                "color:#e6edf3; font-family:Consolas,monospace;"
            )

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

    def _update_buttons(self, state: Optional[str]):
        can_print = state == "ok"
        self.btn_print.setEnabled(can_print)
        self.btn_reset.setEnabled(state is not None)

    # ------------------------------------------------------------------
    # Индикатор не напечатанных
    # ------------------------------------------------------------------
    def _update_print_alert(self):
        """Проверить, есть ли не напечатанные коды для этой точки."""
        point = self.store.current_point
        try:
            unprinted = self.store.count_unprinted_for_point(point)
        except Exception as e:
            log.error("Не удалось получить кол-во ненапечатанных: %s", e)
            unprinted = 0

        if unprinted > 0:
            self.print_alert_label.setText(f"⚠ Не напечатано: {unprinted}")
            self.print_alert_label.show()
        else:
            self.print_alert_label.hide()

    # ------------------------------------------------------------------
    # Автопечать (использует принтер своей точки)
    # ------------------------------------------------------------------
    def _auto_print(self, code: str):
        """Автопечать после успешного скана."""
        from printer import print_code, load_config, get_printer_for_point
        cfg = load_config()
        point = self.store.current_point

        # Выбираем принтер для своей точки
        cfg_for_point = get_printer_for_point(cfg, point)

        if not cfg_for_point.get("printer_name") and not cfg_for_point.get("printer_ip"):
            log.warning("Автопечать отменена: принтер для точки %s не настроен", point)
            self.toast.show_message(
                f"Принтер для точки {point} не настроен — открой Админ → Принтер",
                "warn"
            )
            try:
                self.store.log(
                    "Печать невозможна",
                    point=point,
                    operator=self.store.current_operator,
                    result="принтер не настроен",
                )
            except Exception:
                pass
            self._update_print_alert()
            return

        log.info("Автопечать: точка=%s, принтер=%s (%s)",
                 point, cfg_for_point.get("printer_name"),
                 cfg_for_point.get("printer_lang"))

        try:
            result = print_code(code, cfg_for_point)
        except Exception as e:
            result = {"ok": False, "info": f"{type(e).__name__}: {e}"}

        if result["ok"]:
            self.store.mark_printed(code, point=point)
            self.store.increment_counter("prints")
            self._refresh_counter()
            self.store.log("Автопечать", point=point,
                           operator=self.store.current_operator,
                           result="успех")
            self.toast.show_message(f"Напечатано: {result['info']}", "ok")
            log.info("Автопечать успешна: %s", result["info"])
        else:
            # Ошибка печати — сигнал
            self.store.increment_counter("errors")
            self._refresh_counter()
            try:
                self.store.log(
                    "Ошибка печати",
                    point=point,
                    operator=self.store.current_operator,
                    result=f"ошибка: {result['info']}",
                )
            except Exception:
                pass
            self.toast.show_message(
                f"⚠ ПЕЧАТЬ НЕ УДАЛАСЬ: {result['info']}", "err", duration=5000
            )
            log.error("Автопечать не удалась: %s", result["info"])

        # Обновляем индикатор не напечатанных
        self._update_print_alert()

    # ------------------------------------------------------------------
    # Основное действие — скан
    # ------------------------------------------------------------------
    def do_scan(self):
        raw = self.scan_input.text().strip()
        if not raw:
            return

        log.debug("do_scan вызван, raw=%s", raw[:60])
        self.store.increment_counter("scans")

        point = self.store.current_point
        operator = self.store.current_operator

        # Обрабатываем скан через Store
        result = self.store.process_scan(raw, point=point, operator=operator)

        if result["status"] == "ok":
            # Успех
            foreign = result["foreign"]
            cz = result["cz"]
            prev_count = result.get("prev_count", 0)
            overflow = result.get("overflow", False)

            log.info("Честный знак получен: %s (повтор #%s)",
                     cz["code"][:40] + "...", prev_count)

            self.store.log(
                "Скан",
                point=point, operator=operator,
                gtin=foreign["gtin"], sn=foreign["serial"],
                result=("успех (повтор)" if prev_count > 0 else "успех"),
            )

            # Пишем в аудит, если перебор (не блокирует)
            if overflow:
                log.warning(
                    "Перебор GTIN: сканов %s > кодов %s",
                    result.get("gtin_scans"), result.get("gtin_total"),
                )

            self._render_ok(foreign, cz["code"])

            # Инфо: если это повтор — короткий toast
            if prev_count > 0:
                self.toast.show_message(
                    f"Повтор #{prev_count + 1} — другая единица", "warn"
                )
            else:
                self.toast.show_message("Честный знак получен", "ok")

            self._refresh_counter()
            self._update_buttons("ok")
            self._auto_print(cz["code"])
            self._update_print_alert()
            return

        # Ошибки
        err_code = result.get("code")
        foreign = result.get("foreign") or parse_foreign_barcode(raw)

        if err_code == "NO_FREE_CODES":
            log.error("Нет свободных кодов для GTIN=%s на точке %s",
                      result.get("gtin"), point)
            self.store.increment_counter("errors")
            self.store.log("Скан (нет кодов)", point=point, operator=operator,
                           gtin=foreign["gtin"], sn=foreign["serial"],
                           result="нет кодов")
            self._render_no_codes(foreign, result.get("gtin"))
            self.toast.show_message("Нет свободных кодов", "err")
            self._refresh_counter()
            self._update_buttons("error")
            return

        # Неизвестная ошибка
        log.error("Неизвестная ошибка скана: %s", result)
        self.store.increment_counter("errors")
        self._render_err(foreign)
        self._toast_error("Ошибка обработки скана")
        self._refresh_counter()
        self._update_buttons("error")

    # ------------------------------------------------------------------
    # Отрисовка
    # ------------------------------------------------------------------
    def _render_ok(self, foreign: dict, cz_code: str):
        self._set_field("Тип", foreign["type"])
        self._set_field("GTIN", foreign["gtin"])
        self._set_field("Серия", foreign["serial"])
        self._set_field("Партия", foreign["lot"])
        self._set_field("Срок годн.", foreign["exp"])
        self._set_field("Страна", foreign["country"])

        self._set_badge("НАЙДЕН", "ok")
        self._set_field("ЧЗ_Статус", "✅ Найден")
        self._set_field("ЧЗ_Источник", f"пул {self.store.current_point}")
        self._set_field("ЧЗ_Запрошен", datetime.now().strftime("%H:%M:%S"))
        self._set_field("ЧЗ_Код", cz_code)
        self._set_status("✅ Найдено", "ok")

        pix_big = render_datamatrix(cz_code, 120)
        self.dm_big.setPixmap(pix_big)
        pix_small = render_datamatrix(cz_code, 120)
        self.dm_small.setPixmap(pix_small)

        self.preview_empty.hide()
        self.label_mock.show()
        self.lm_name.setText(foreign["name"])
        self.lm_model.setText(f"Модель: {foreign['model']}")
        self.lm_importer.setText(f"Импортёр: {foreign['importer']}")
        self.lm_gtin.setText(f"GTIN: {foreign['gtin']}")
        self.lm_serial.setText(f"Серия: {foreign['serial']}")
        self.lm_exp.setText(f"Срок годности: {foreign['exp']}")

        self.last_cz_code = cz_code

    def _render_no_codes(self, foreign: dict, gtin: str):
        self._set_field("Тип", foreign["type"])
        self._set_field("GTIN", foreign["gtin"])
        self._set_field("Серия", foreign["serial"])
        self._set_field("Партия", foreign["lot"])
        self._set_field("Срок годн.", foreign["exp"])
        self._set_field("Страна", foreign["country"])

        self._set_badge("НЕТ КОДОВ", "err")
        self._set_field("ЧЗ_Статус", "⛔ Нет свободных кодов для этого товара")
        self._set_field("ЧЗ_Код", None)
        self._set_field("ЧЗ_Источник", None)
        self._set_field("ЧЗ_Запрошен", datetime.now().strftime("%H:%M:%S"))
        self._set_status("❌ Нет свободных кодов", "err")

        self.dm_big.clear()
        self.dm_small.clear()

        self.preview_empty.setText(
            f"Нет свободных кодов для GTIN {gtin}\n"
            f"на точке {self.store.current_point}.\n"
            f"Обратитесь к администратору."
        )
        self.preview_empty.show()
        self.label_mock.hide()

        self.last_cz_code = None

    def _render_err(self, foreign: dict):
        self._set_field("Тип", foreign["type"])
        self._set_field("GTIN", foreign["gtin"])
        self._set_field("Серия", foreign["serial"])
        self._set_field("Партия", foreign["lot"])
        self._set_field("Срок годн.", foreign["exp"])
        self._set_field("Страна", foreign["country"])

        self._set_badge("ОШИБКА", "err")
        self._set_field("ЧЗ_Статус", "⛔ Не удалось обработать скан")
        self._set_field("ЧЗ_Код", None)
        self._set_field("ЧЗ_Источник", None)
        self._set_field("ЧЗ_Запрошен", datetime.now().strftime("%H:%M:%S"))
        self._set_status("❌ Ошибка", "err")

        self.dm_big.clear()
        self.dm_small.clear()

        self.preview_empty.setText("Русификатор не сформирован: ошибка обработки")
        self.preview_empty.show()
        self.label_mock.hide()

        self.last_cz_code = None

    def _toast_error(self, text: str):
        self.toast.show_message(text, "err")

    # ------------------------------------------------------------------
    def do_print(self):
        """Повторная печать последнего кода."""
        if not self.last_cz_code:
            log.warning("Повтор печати: нет последнего кода")
            return

        log.info("Повторная печать: %s", self.last_cz_code[:40] + "...")

        from printer import print_code, load_config, get_printer_for_point
        cfg = load_config()
        point = self.store.current_point
        cfg_for_point = get_printer_for_point(cfg, point)

        try:
            result = print_code(self.last_cz_code, cfg_for_point)
        except Exception as e:
            result = {"ok": False, "info": f"{type(e).__name__}: {e}"}

        if result["ok"]:
            self.store.mark_printed(self.last_cz_code, point=point)
            self.store.increment_counter("prints")
            self.store.log("Повтор печати", point=point,
                           operator=self.store.current_operator,
                           result="успех")
            self._refresh_counter()
            self.toast.show_message(f"Напечатано: {result['info']}", "ok")
            log.info("Повторная печать успешна: %s", result["info"])
        else:
            self.store.increment_counter("errors")
            self._refresh_counter()
            try:
                self.store.log(
                    "Ошибка повторной печати",
                    point=point,
                    operator=self.store.current_operator,
                    result=f"ошибка: {result['info']}",
                )
            except Exception:
                pass
            self.toast.show_message(
                f"⚠ ПЕЧАТЬ НЕ УДАЛАСЬ: {result['info']}", "err", duration=5000
            )
            log.error("Повторная печать не удалась: %s", result["info"])

        self._update_print_alert()

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