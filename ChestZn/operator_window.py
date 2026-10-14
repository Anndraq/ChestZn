"""
Экран оператора ChestZn (operator_window.py) — промышленный интерфейс для поста упаковки.
Оптимизирован для считывания статуса с расстояния 1.5–2 метров («режим светофора»),
защищён от растягивания длинными кодами GS1 DataMatrix (80–120+ символов)
и гарантирует постоянный фокус ввода для аппаратных сканеров ШК.
"""
import re
from typing import Optional, Dict, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSizePolicy, QInputDialog, QApplication
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence, QFont, QBrush, QColor

from logger import get_logger
log = get_logger("operator")

from store import Store, parse_foreign_barcode
from barcode import render_datamatrix
from toast import Toast
from workstation_engine import WorkstationEngine, resolve_bound_printer_for_scan


def _play_sound(kind: str):
    """Воспроизвести звуковой сигнал при ошибке или успешном скане."""
    try:
        app = QApplication.instance()
        if app is None:
            return
        if kind == "error":
            app.beep()
            QTimer.singleShot(140, app.beep)
    except Exception:
        pass


def _clean_display_code(raw_val: Optional[str]) -> str:
    """
    Убирает внутренний префикс __UNKNOWN_SHK__: и управляющие символы GS (\\x1d)
    для аккуратного отображения на экране оператора.
    """
    if not raw_val:
        return "—"
    s = str(raw_val).strip()
    if s.startswith("__UNKNOWN_SHK__:"):
        s = s.split(":", 1)[1].strip()
    s = s.replace("\x1d", " [GS] ")
    return s or "—"


def _elide_middle(text: str, max_len: int = 44) -> str:
    """
    Безопасно сокращает длинные строки (например, 120-символьный DataMatrix с криптохвостом),
    сохраняя начало (GTIN + серийный номер) и конец строки, чтобы окно никогда не растягивалось вширь.
    """
    s = _clean_display_code(text)
    if len(s) <= max_len:
        return s
    head = max(24, int(max_len * 0.62))
    tail = max(10, max_len - head - 5)
    return f"{s[:head]} … {s[-tail:]} ({len(s)} симв.)"


def _format_cz_breakdown(cz_code: Optional[str], foreign: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """
    Разбирает длинный код ЧЗ (01<GTIN>21<SN>\\x1d91<KEY>\\x1d92<CRYPTO>)
    на удобочитаемые поля для крупных плашек на экране оператора.
    """
    foreign = foreign or {}
    gtin = _clean_display_code(foreign.get("gtin") or "")
    sn = _clean_display_code(foreign.get("serial") or foreign.get("sn") or "")
    shk = _clean_display_code(foreign.get("shk") or foreign.get("raw") or "")
    name = str(foreign.get("name") or "").strip()

    raw_cz = str(cz_code or "").strip()
    if raw_cz:
        m = re.match(r"^01(\d{14})21([^\x1d\r\n]{1,24})", raw_cz)
        if m:
            if not gtin or gtin == "—":
                gtin = m.group(1)
            if not sn or sn == "—":
                sn = m.group(2)

    if name.startswith("Неизвестный ШК"):
        name = ""

    return {
        "name": name or "Товар промаркирован",
        "shk": _elide_middle(shk, 32) if shk != "—" else "—",
        "gtin": gtin if len(gtin) <= 18 else _elide_middle(gtin, 18),
        "sn": sn if len(sn) <= 22 else _elide_middle(sn, 22),
        "cz_short": _elide_middle(raw_cz, 52) if raw_cz else "—",
    }


class OperatorWindow(QWidget):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.last_cz_code: Optional[str] = None
        self.last_foreign: Optional[Dict[str, Any]] = None
        self.last_result_state: str = "idle"  # 'idle' | 'ok' | 'error'

        self.toast = Toast(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        # =========== 1. ШАПКА ПОСТА УПАКОВКИ ===========
        root.addWidget(self._build_header())

        # =========== 2. ПОЛЕ СКАНИРОВАНИЯ ===========
        root.addWidget(self._build_scanbar())

        # =========== 3. ГЛАВНОЕ ТАБЛО РЕЗУЛЬТАТА («СВЕТОФОР 2 МЕТРА») ===========
        root.addWidget(self._build_result_card(), stretch=0)

        # =========== 4. КРУПНЫЕ СЧЁТЧИКИ СМЕНЫ ===========
        root.addWidget(self._build_counter())

        # =========== 5. МИНИ-ЖУРНАЛ ПОСЛЕДНИХ ОПЕРАЦИЙ ===========
        root.addWidget(self._build_mini_journal(), stretch=1)

        # =========== 6. ПАНЕЛЬ БЫСТРЫХ КНОПОК (F2 / F5 / F9 / Esc) ===========
        root.addWidget(self._build_actions())

        # =========== ГОРЯЧИЕ КЛАВИШИ ===========
        QShortcut(QKeySequence("F2"), self, self.do_print)
        QShortcut(QKeySequence("F5"), self, self._open_manual)
        QShortcut(QKeySequence("F9"), self, self._open_full_journal)
        QShortcut(QKeySequence("Esc"), self, self.reset)

        # =========== ДВИЖОК РАБОЧИХ ПАР (1 ПК = N сканеров = N принтеров) ===========
        self.engine = WorkstationEngine(store_instance=self.store, parent=self)
        self.engine.pair_updated.connect(self._on_engine_pair_updated)
        self.engine.start_all()

        self._refresh_header_route()
        self._refresh_counter()
        self._refresh_mini_journal()
        self._show_result("idle")

        # Устанавливаем фокус в поле сканирования сразу после инициализации окна
        QTimer.singleShot(50, self._restore_scan_focus)

    def closeEvent(self, event):
        """Остановка всех потоков оборудования при закрытии окна."""
        if hasattr(self, "engine"):
            self.engine.stop_all()
        super().closeEvent(event)

    def showEvent(self, event):
        """Автоматический возврат фокуса в поле сканирования при показе вкладки оператора."""
        super().showEvent(event)
        self._refresh_header_route()
        QTimer.singleShot(30, self._restore_scan_focus)

    def mousePressEvent(self, event):
        """При клике по пустому месту окна возвращаем фокус в поле сканирования."""
        super().mousePressEvent(event)
        QTimer.singleShot(10, self._restore_scan_focus)

    def _restore_scan_focus(self):
        if hasattr(self, "scan_input") and self.scan_input is not None and self.isVisible():
            self.scan_input.setFocus()

    # =========================================================
    # 1. HEADER (Точка, Маршрут «Сканер -> Принтер», Оператор)
    # =========================================================
    def _build_header(self) -> QFrame:
        box = QFrame()
        box.setObjectName("card")
        box.setStyleSheet(
            "QFrame#card { background:#151d26; border:1px solid #263240; border-radius:10px; }"
        )
        h = QHBoxLayout(box)
        h.setContentsMargins(18, 10, 18, 10)
        h.setSpacing(16)

        point = self.store.get_point(self.store.current_point) or {}
        point_id = point.get("id", self.store.current_point)
        point_name = point.get("name", "Линия упаковки")

        self.lbl_point = QLabel(f"📍 Точка: {point_id} · {point_name}")
        self.lbl_point.setStyleSheet("font-weight:700; font-size:15px; color:#f1f5f9;")
        h.addWidget(self.lbl_point)

        h.addStretch(1)

        # Индикатор привязанного принтера (1 сканер = 1 принтер)
        self.lbl_route = QLabel("🖨 Принтер: проверка привязки...")
        self.lbl_route.setStyleSheet(
            "background:#1e293b; color:#38bdf8; font-family:Consolas,monospace; "
            "font-size:13px; font-weight:600; padding:5px 12px; "
            "border:1px solid #334155; border-radius:6px;"
        )
        h.addWidget(self.lbl_route)

        h.addStretch(1)

        self.lbl_op = QLabel(f"👤 Оператор: {self.store.current_operator}")
        self.lbl_op.setStyleSheet("color:#94a3b8; font-size:14px; font-weight:600;")
        h.addWidget(self.lbl_op)

        return box

    def _refresh_header_route(self):
        """Обновляет плашку активного маршрута «Сканер -> Принтер» в шапке окна."""
        if not hasattr(self, "lbl_route"):
            return
        try:
            bound_printer = resolve_bound_printer_for_scan()
            if bound_printer:
                self.lbl_route.setText(f"🖨 Принтер поста: {bound_printer}")
                self.lbl_route.setStyleSheet(
                    "background:#0f291e; color:#4ade80; font-family:Consolas,monospace; "
                    "font-size:13px; font-weight:700; padding:5px 12px; "
                    "border:1px solid #166534; border-radius:6px;"
                )
            else:
                self.lbl_route.setText("⚠ Принтер не привязан (Админ -> Устройства)")
                self.lbl_route.setStyleSheet(
                    "background:#3b2110; color:#fbbf24; font-family:Consolas,monospace; "
                    "font-size:13px; font-weight:700; padding:5px 12px; "
                    "border:1px solid #92400e; border-radius:6px;"
                )
        except Exception:
            pass

    def _on_engine_pair_updated(self, pair_id: int, data: dict):
        self._refresh_header_route()
        self._refresh_counter()
        self._refresh_mini_journal()

    # =========================================================
    # 2. SCAN BAR (Поле ввода сканера с защитой фокуса)
    # =========================================================
    def _build_scanbar(self) -> QFrame:
        box = QFrame()
        box.setObjectName("scanbar")
        box.setStyleSheet(
            "QFrame#scanbar { background:#151d26; border:1px solid #2a3646; border-radius:10px; }"
        )
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 12, 18, 12)
        v.setSpacing(6)

        top_row = QHBoxLayout()
        title = QLabel("СКАНИРОВАНИЕ ШТРИХКОДА ТОВАРА (EAN-13 / GTIN-14 / GS1)")
        title.setStyleSheet(
            "color:#94a3b8; letter-spacing:1.2px; font-size:12px; font-weight:700;"
        )
        top_row.addWidget(title)
        top_row.addStretch(1)

        self.focus_badge = QLabel("● СКАНЕР АКТИВЕН")
        self.focus_badge.setStyleSheet(
            "color:#22c55e; font-size:12px; font-weight:700; letter-spacing:0.8px;"
        )
        top_row.addWidget(self.focus_badge)
        v.addLayout(top_row)

        row = QHBoxLayout()
        row.setSpacing(12)

        icon = QLabel("📡")
        icon.setStyleSheet(
            "font-size:22px; padding:6px 10px; "
            "background:#1e293b; border:1px solid #334155; border-radius:8px;"
        )
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignCenter)
        row.addWidget(icon)

        self.scan_input = QLineEdit()
        self.scan_input.setObjectName("scanInput")
        self.scan_input.setMinimumHeight(48)
        self.scan_input.setPlaceholderText(
            "Отсканируйте штрихкод товара сканером или введите код (Enter)..."
        )
        self.scan_input.returnPressed.connect(lambda: self.do_scan(mode="auto"))
        f = QFont("Consolas")
        f.setPixelSize(19)
        f.setBold(True)
        self.scan_input.setFont(f)
        self.scan_input.setStyleSheet(
            "QLineEdit#scanInput {"
            "  background:#0f172a; color:#f8fafc; border:2px solid #3b82f6;"
            "  border-radius:8px; padding:6px 14px;"
            "}"
            "QLineEdit#scanInput:focus {"
            "  border:2px solid #22c55e; background:#091326;"
            "}"
        )
        row.addWidget(self.scan_input, stretch=1)

        # Крупный цветовой индикатор состояния
        self.status_dot = QLabel("●")
        self.status_dot.setFixedSize(44, 48)
        self.status_dot.setAlignment(Qt.AlignCenter)
        self.status_dot.setStyleSheet("color:#64748b; font-size:30px;")
        row.addWidget(self.status_dot)

        v.addLayout(row)
        return box

    # =========================================================
    # 3. RESULT CARD («СВЕТОФОР 2 МЕТРА» + ЗАЩИТА ОТ ДЛИННЫХ КОДОВ ЧЗ)
    # =========================================================
    def _build_result_card(self) -> QFrame:
        self.result_card = QFrame()
        self.result_card.setObjectName("resultCard")
        self.result_card.setMinimumHeight(230)
        self.result_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QHBoxLayout(self.result_card)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(22)

        # Левая колонка: превью DataMatrix (показывается при успешной печати)
        self.dm_preview_box = QFrame()
        self.dm_preview_box.setFixedSize(132, 150)
        self.dm_preview_box.setStyleSheet(
            "background:#ffffff; border:2px solid #cbd5e1; border-radius:10px;"
        )
        dm_layout = QVBoxLayout(self.dm_preview_box)
        dm_layout.setContentsMargins(8, 8, 8, 6)
        dm_layout.setSpacing(4)

        self.dm_image_label = QLabel()
        self.dm_image_label.setFixedSize(112, 112)
        self.dm_image_label.setAlignment(Qt.AlignCenter)
        dm_layout.addWidget(self.dm_image_label, alignment=Qt.AlignCenter)

        self.dm_caption_label = QLabel("ЧЗ DataMatrix")
        self.dm_caption_label.setAlignment(Qt.AlignCenter)
        self.dm_caption_label.setStyleSheet(
            "color:#0f172a; font-size:11px; font-weight:700; background:transparent; border:none;"
        )
        dm_layout.addWidget(self.dm_caption_label)
        self.dm_preview_box.hide()
        outer.addWidget(self.dm_preview_box)

        # Центральная колонка: крупный статус для чтения с 2 метров + плашки GTIN / Серия / ШК
        v = QVBoxLayout()
        v.setSpacing(8)
        v.addStretch(1)

        top_status = QHBoxLayout()
        top_status.setSpacing(14)
        top_status.setAlignment(Qt.AlignCenter)

        self.result_icon = QLabel("⏳")
        self.result_icon.setAlignment(Qt.AlignCenter)
        self.result_icon.setStyleSheet("font-size:48px; background:transparent; border:none;")
        top_status.addWidget(self.result_icon)

        self.result_title = QLabel("ГОТОВО К СКАНИРОВАНИЮ")
        self.result_title.setAlignment(Qt.AlignCenter)
        self.result_title.setWordWrap(True)
        self.result_title.setStyleSheet(
            "color:#e2e8f0; font-size:32px; font-weight:800; letter-spacing:1.5px; background:transparent; border:none;"
        )
        top_status.addWidget(self.result_title)
        v.addLayout(top_status)

        # Крупный подзаголовок (причина ошибки или наименование товара + GTIN + SN)
        self.result_subtitle = QLabel("Отсканируйте штрихкод товара — система сверит его с базой Честного Знака")
        self.result_subtitle.setAlignment(Qt.AlignCenter)
        self.result_subtitle.setWordWrap(True)
        self.result_subtitle.setStyleSheet(
            "color:#cbd5e1; font-size:18px; font-weight:600; background:transparent; border:none;"
        )
        v.addWidget(self.result_subtitle)

        # Моноширинная плашка для безопасного показа длинных кодов ЧЗ / ШК (без растягивания окна)
        self.result_code_badge = QLabel("")
        self.result_code_badge.setAlignment(Qt.AlignCenter)
        self.result_code_badge.setWordWrap(True)
        self.result_code_badge.setStyleSheet(
            "background:rgba(15, 23, 42, 0.75); color:#f8fafc; "
            "font-family:Consolas,monospace; font-size:16px; font-weight:700; "
            "padding:8px 16px; border:1px solid rgba(148, 163, 184, 0.35); border-radius:8px;"
        )
        self.result_code_badge.hide()
        v.addWidget(self.result_code_badge)

        # Кнопки быстрых действий при ошибке
        self.error_actions = QWidget()
        self.error_actions.setStyleSheet("background:transparent; border:none;")
        ea = QHBoxLayout(self.error_actions)
        ea.setContentsMargins(0, 6, 0, 0)
        ea.setSpacing(12)
        ea.setAlignment(Qt.AlignCenter)

        self.btn_error_retry = QPushButton("🔄 Повторить скан")
        self.btn_error_retry.setFocusPolicy(Qt.NoFocus)
        self.btn_error_retry.setMinimumHeight(38)
        self.btn_error_retry.setStyleSheet(
            "QPushButton { background:#1e293b; color:#f8fafc; font-size:14px; font-weight:700; "
            "padding:6px 18px; border:1px solid #475569; border-radius:8px; }"
            "QPushButton:hover { background:#334155; }"
        )
        self.btn_error_retry.clicked.connect(self._retry_last_scan)
        ea.addWidget(self.btn_error_retry)

        self.btn_error_skip = QPushButton("✖ Сбросить ошибку (Esc)")
        self.btn_error_skip.setFocusPolicy(Qt.NoFocus)
        self.btn_error_skip.setMinimumHeight(38)
        self.btn_error_skip.setStyleSheet(
            "QPushButton { background:#7f1d1d; color:#fef2f2; font-size:14px; font-weight:700; "
            "padding:6px 18px; border:1px solid #ef4444; border-radius:8px; }"
            "QPushButton:hover { background:#991b1b; }"
        )
        self.btn_error_skip.clicked.connect(self.reset)
        ea.addWidget(self.btn_error_skip)

        self.error_actions.hide()
        v.addWidget(self.error_actions)

        v.addStretch(1)
        outer.addLayout(v, stretch=1)
        return self.result_card

    def _show_result(
        self,
        state: str,
        foreign: Optional[Dict[str, Any]] = None,
        message: str = "",
        sub: str = ""
    ):
        """
        Отображает состояние на главном табло в режиме «Светофор 2 метра»:
        - 'idle'  : спокойный тёмно-синий фон, ожидание сканирования
        - 'ok'    : ярко-зелёное табло, превью DataMatrix, крупный GTIN и Серия (SN)
        - 'error' : контрастное красное табло тревоги, крупная причина ошибки и безопасное сокращение длинного ШК
        """
        self.last_result_state = state
        self._refresh_header_route()

        if state == "idle":
            self.result_card.setStyleSheet(
                "QFrame#resultCard {"
                "  background:#141c25; border:2px dashed #2d3b4e; border-radius:14px;"
                "}"
            )
            self.dm_preview_box.hide()
            self.result_icon.setText("⏳")
            self.result_title.setText("ГОТОВО К СКАНИРОВАНИЮ")
            self.result_title.setStyleSheet(
                "color:#94a3b8; font-size:30px; font-weight:800; letter-spacing:1.5px; background:transparent; border:none;"
            )
            self.result_subtitle.setText(
                "Наведите сканер на штрихкод товара — этикетка Честного Знака распечатается автоматически"
            )
            self.result_subtitle.setStyleSheet(
                "color:#64748b; font-size:16px; font-weight:600; background:transparent; border:none;"
            )
            self.result_code_badge.hide()
            self.error_actions.hide()
            self.status_dot.setStyleSheet("color:#64748b; font-size:30px;")

        elif state == "ok":
            self.result_card.setStyleSheet(
                "QFrame#resultCard {"
                "  background:#092e1d; border:3px solid #22c55e; border-radius:14px;"
                "}"
            )
            info = _format_cz_breakdown(self.last_cz_code, foreign)
            bound_printer = resolve_bound_printer_for_scan() or "принтер поста"

            # Отрисовка превью DataMatrix
            if self.last_cz_code:
                try:
                    pix = render_datamatrix(self.last_cz_code, size=108)
                    self.dm_image_label.setPixmap(pix)
                    self.dm_preview_box.show()
                except Exception:
                    self.dm_preview_box.hide()
            else:
                self.dm_preview_box.hide()

            self.result_icon.setText("✅")
            self.result_title.setText("НАПЕЧАТАНО — НАКЛЕЙТЕ ЧЗ")
            self.result_title.setStyleSheet(
                "color:#4ade80; font-size:36px; font-weight:900; letter-spacing:2px; background:transparent; border:none;"
            )

            self.result_subtitle.setText(
                f"{info['name']}   ·   Принтер: {bound_printer}"
            )
            self.result_subtitle.setStyleSheet(
                "color:#dcfce7; font-size:19px; font-weight:700; background:transparent; border:none;"
            )

            badge_parts = [f"GTIN ЧЗ: {info['gtin']}", f"СЕРИЯ (SN): {info['sn']}"]
            if info["shk"] != "—" and info["shk"] != info["gtin"]:
                badge_parts.insert(0, f"ШК: {info['shk']}")
            if info["cz_short"] != "—":
                badge_parts.append(f"КОД: {info['cz_short']}")

            self.result_code_badge.setText("   |   ".join(badge_parts))
            self.result_code_badge.setToolTip(self.last_cz_code or "")
            self.result_code_badge.setStyleSheet(
                "background:rgba(5, 46, 22, 0.85); color:#f0fdf4; "
                "font-family:Consolas,monospace; font-size:17px; font-weight:700; "
                "padding:10px 16px; border:1px solid #22c55e; border-radius:8px;"
            )
            self.result_code_badge.show()

            self.error_actions.hide()
            self.status_dot.setStyleSheet("color:#22c55e; font-size:32px;")
            _play_sound("ok")

        elif state == "error":
            self.result_card.setStyleSheet(
                "QFrame#resultCard {"
                "  background:#3b0d11; border:3px solid #ef4444; border-radius:14px;"
                "}"
            )
            self.dm_preview_box.hide()
            self.result_icon.setText("⛔")

            # Формируем короткий и крупный заголовок для чтения с 2 метров,
            # а длинные детали и сам штрихкод уводим в подзаголовок и моноширинную плашку
            raw_msg = (message or "ОШИБКА СКАНИРОВАНИЯ").strip()
            raw_sub = (sub or "").strip()

            low_msg = (raw_msg + " " + raw_sub).lower()
            if "неизвестный шк" in low_msg or "не связан" in low_msg or "не удалось связать" in low_msg:
                headline = "СТОП! ШК НЕ СВЯЗАН С ЧЕСТНЫМ ЗНАКОМ"
            elif "нет свободных кодов" in low_msg or "нет кодов" in low_msg or "закончились" in low_msg:
                headline = "НЕТ СВОБОДНЫХ КОДОВ ЧЗ В ПУЛЕ"
            elif "принтер" in low_msg or "печат" in low_msg:
                headline = "ОШИБКА ПЕЧАТИ НА ПРИНТЕРЕ"
            elif len(raw_msg) > 42:
                headline = "ОШИБКА ПРОВЕРКИ ШТРИХКОДА"
            else:
                headline = raw_msg.upper()

            self.result_title.setText(headline)
            self.result_title.setStyleSheet(
                "color:#fecaca; font-size:33px; font-weight:900; letter-spacing:1.5px; background:transparent; border:none;"
            )

            # Извлекаем чистый отсканированный код без технических префиксов __UNKNOWN_SHK__:
            detail_text = raw_sub if raw_sub and raw_sub != raw_msg else raw_msg
            detail_text = _clean_display_code(detail_text)
            if len(detail_text) > 110:
                detail_text = detail_text[:107] + "…"

            self.result_subtitle.setText(
                detail_text or "Отсканированный штрихкод отсутствует в таблице привязок и пуле кодов ЧЗ"
            )
            self.result_subtitle.setStyleSheet(
                "color:#fee2e2; font-size:18px; font-weight:700; background:transparent; border:none;"
            )

            scanned_raw = ""
            if foreign and isinstance(foreign, dict):
                scanned_raw = foreign.get("shk") or foreign.get("raw") or foreign.get("gtin") or ""
            cleaned_code = _elide_middle(scanned_raw, 54)
            if cleaned_code and cleaned_code != "—":
                self.result_code_badge.setText(f"ОТСКАНИРОВАН ШК: {cleaned_code}   ·   ПЕЧАТЬ ЗАБЛОКИРОВАНА")
                self.result_code_badge.setToolTip(_clean_display_code(scanned_raw))
                self.result_code_badge.setStyleSheet(
                    "background:rgba(69, 10, 10, 0.9); color:#fef2f2; "
                    "font-family:Consolas,monospace; font-size:17px; font-weight:700; "
                    "padding:10px 16px; border:1px solid #f87171; border-radius:8px;"
                )
                self.result_code_badge.show()
            else:
                self.result_code_badge.hide()

            self.error_actions.show()
            self.status_dot.setStyleSheet("color:#ef4444; font-size:32px;")
            _play_sound("error")

    # =========================================================
    # 4. КРУПНЫЕ СЧЁТЧИКИ СМЕНЫ (Видимость с 2 метров)
    # =========================================================
    def _build_counter(self) -> QFrame:
        box = QFrame()
        box.setObjectName("counter")
        box.setStyleSheet("QFrame#counter { background:transparent; border:none; }")
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        self.counter_labels = {}
        cards_spec = [
            ("scans",  "СКАНИРОВАНИЙ", "#38bdf8", "#0f2236", "#1e3a5f"),
            ("prints", "НАПЕЧАТАНО ЧЗ", "#4ade80", "#09261a", "#14532d"),
            ("errors", "ОШИБОК / БРАК", "#f87171", "#2c1014", "#7f1d1d"),
            ("limit",  "ЛИМИТ СМЕНЫ",   "#fbbf24", "#291d0a", "#78350f"),
        ]

        for key, label_text, num_color, bg_color, border_color in cards_spec:
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background:{bg_color}; border:1px solid {border_color}; border-radius:10px; }}"
            )
            ch = QHBoxLayout(card)
            ch.setContentsMargins(16, 10, 16, 10)
            ch.setSpacing(10)

            k = QLabel(label_text)
            k.setStyleSheet("color:#cbd5e1; font-size:12px; font-weight:800; letter-spacing:1px; border:none;")

            v = QLabel("0")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            v.setStyleSheet(
                f"color:{num_color}; font-family:Consolas,monospace; "
                f"font-size:26px; font-weight:900; border:none;"
            )
            self.counter_labels[key] = v

            ch.addWidget(k)
            ch.addStretch(1)
            ch.addWidget(v)
            h.addWidget(card, stretch=1)

        return box

    def _refresh_counter(self):
        c = self.store.counter
        self.counter_labels["scans"].setText(str(c.get("scans", 0)))
        self.counter_labels["prints"].setText(str(c.get("prints", 0)))
        self.counter_labels["errors"].setText(str(c.get("errors", 0)))
        self.counter_labels["limit"].setText(str(c.get("limit", 0)))

    # =========================================================
    # 5. MINI-JOURNAL (С защитой от растягивания длинными GTIN/SN)
    # =========================================================
    def _build_mini_journal(self) -> QFrame:
        box = QFrame()
        box.setObjectName("card")
        box.setStyleSheet(
            "QFrame#card { background:#151d26; border:1px solid #263240; border-radius:10px; }"
        )
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 12, 18, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        ttl = QLabel("ПОСЛЕДНИЕ ОПЕРАЦИИ НА ПОСТУ")
        ttl.setStyleSheet(
            "color:#94a3b8; font-size:12px; font-weight:700; letter-spacing:1.2px;"
        )
        head.addWidget(ttl)
        head.addStretch(1)

        btn_open = QPushButton("📜  Полный журнал   F9")
        btn_open.setFocusPolicy(Qt.NoFocus)
        btn_open.setFixedHeight(32)
        btn_open.setMinimumWidth(200)
        btn_open.setStyleSheet("font-size:13px; font-weight:600; padding: 4px 14px;")
        btn_open.clicked.connect(self._open_full_journal)
        head.addWidget(btn_open)

        v.addLayout(head)

        self.journal_table = QTableWidget(0, 4)
        self.journal_table.setHorizontalHeaderLabels(
            ["Время", "Статус операции", "GTIN / ШК", "Серия (SN)"]
        )
        hh = self.journal_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        self.journal_table.setColumnWidth(0, 100)
        self.journal_table.setColumnWidth(2, 220)
        self.journal_table.setColumnWidth(3, 190)
        self.journal_table.verticalHeader().setVisible(False)
        self.journal_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.journal_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.journal_table.setShowGrid(False)
        self.journal_table.setFocusPolicy(Qt.NoFocus)
        self.journal_table.setTextElideMode(Qt.ElideMiddle)
        self.journal_table.setWordWrap(False)
        self.journal_table.setStyleSheet("""
            QTableWidget {
                background: transparent;
                border: none;
                font-size: 14px;
            }
            QTableWidget::item {
                padding: 8px 6px;
                border-bottom: 1px solid #263240;
            }
        """)
        v.addWidget(self.journal_table, stretch=1)

        return box

    def _refresh_mini_journal(self):
        try:
            rows = self.store.get_recent_audit(limit=6)
        except Exception:
            rows = []

        t = self.journal_table
        t.setRowCount(0)

        for r in rows:
            i = t.rowCount()
            t.insertRow(i)
            t.setRowHeight(i, 36)

            time_item = QTableWidgetItem(r.get("time", ""))
            f = time_item.font()
            f.setFamily("Consolas")
            f.setPixelSize(14)
            time_item.setFont(f)
            time_item.setForeground(QBrush(QColor("#94a3b8")))
            t.setItem(i, 0, time_item)

            action = r.get("action", "")
            res = str(r.get("result", "") or "-")
            clean_res = _elide_middle(res, 56)
            text = f"{action}" + (f" — {clean_res}" if clean_res and clean_res != "-" else "")

            res_low = res.lower()
            if "успех" in res_low:
                color = "#4ade80"
            elif any(k in res_low for k in ("ошибка", "не найден", "нет кодов", "неизвестный")):
                color = "#f87171"
            else:
                color = "#fbbf24"

            item_result = QTableWidgetItem(text)
            item_result.setToolTip(res)
            f_res = item_result.font()
            f_res.setPixelSize(14)
            f_res.setBold(True)
            item_result.setFont(f_res)
            item_result.setForeground(QBrush(QColor(color)))
            t.setItem(i, 1, item_result)

            raw_gtin = _clean_display_code(r.get("gtin") or "-")
            gtin_item = QTableWidgetItem(_elide_middle(raw_gtin, 22))
            gtin_item.setToolTip(raw_gtin)
            f2 = gtin_item.font()
            f2.setFamily("Consolas")
            f2.setPixelSize(14)
            gtin_item.setFont(f2)
            t.setItem(i, 2, gtin_item)

            raw_sn = _clean_display_code(r.get("sn") or "-")
            sn_item = QTableWidgetItem(_elide_middle(raw_sn, 20))
            sn_item.setToolTip(raw_sn)
            f3 = sn_item.font()
            f3.setFamily("Consolas")
            f3.setPixelSize(14)
            sn_item.setFont(f3)
            t.setItem(i, 3, sn_item)

    # =========================================================
    # 6. ПАНЕЛЬ ДЕЙСТВИЙ (С защитой фокуса Qt.NoFocus)
    # =========================================================
    def _build_actions(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        self.btn_print = QPushButton("🖨  Повтор печати   F2")
        self.btn_print.setProperty("primary", True)
        self.btn_print.setMinimumHeight(44)
        self.btn_print.setFocusPolicy(Qt.NoFocus)
        self.btn_print.clicked.connect(self.do_print)

        self.btn_manual = QPushButton("✏️  Ручной ввод ШК   F5")
        self.btn_manual.setMinimumHeight(44)
        self.btn_manual.setFocusPolicy(Qt.NoFocus)
        self.btn_manual.clicked.connect(self._open_manual)

        self.btn_journal = QPushButton("📜  Полный журнал   F9")
        self.btn_journal.setMinimumHeight(44)
        self.btn_journal.setFocusPolicy(Qt.NoFocus)
        self.btn_journal.clicked.connect(self._open_full_journal)

        self.btn_cancel = QPushButton("✖  Сбросить табло   Esc")
        self.btn_cancel.setMinimumHeight(44)
        self.btn_cancel.setFocusPolicy(Qt.NoFocus)
        self.btn_cancel.clicked.connect(self.reset)

        for b in (self.btn_print, self.btn_manual, self.btn_journal, self.btn_cancel):
            h.addWidget(b, stretch=1)

        self.btn_print.setEnabled(False)
        return wrap

    # =========================================================
    # 7. ОБРАБОТКА СКАНИРОВАНИЯ И ПЕЧАТИ
    # =========================================================
    def _retry_last_scan(self):
        if self.last_foreign and isinstance(self.last_foreign, dict):
            prev_raw = self.last_foreign.get("raw") or self.last_foreign.get("shk") or ""
            if prev_raw and not self.scan_input.text().strip():
                self.scan_input.setText(_clean_display_code(prev_raw))
        self.do_scan(mode="auto")

    def do_scan(self, mode: str = "auto"):
        raw = self.scan_input.text().strip()
        if not raw:
            self._restore_scan_focus()
            return

        foreign = parse_foreign_barcode(raw)
        if isinstance(foreign, dict):
            foreign.setdefault("raw", raw)
            foreign.setdefault("shk", raw)
        else:
            foreign = {"raw": raw, "shk": raw, "gtin": raw, "serial": "—"}

        self.last_foreign = foreign
        clean_gtin_log = _clean_display_code(foreign.get("gtin") or raw)
        clean_sn_log = _clean_display_code(foreign.get("serial") or "—")

        self.store.increment_counter("scans")

        if mode == "err":
            self.store.increment_counter("errors")
            self.store.log(
                "Скан",
                point=self.store.current_point,
                operator=self.store.current_operator,
                gtin=clean_gtin_log,
                sn=clean_sn_log,
                result="не найден"
            )
            self._show_result(
                "error",
                foreign=foreign,
                message="СТОП! ШК НЕ СВЯЗАН С ЧЕСТНЫМ ЗНАКОМ",
                sub=f"ШК {raw} не найден в базе привязок и пуле кодов ЧЗ"
            )
            self._refresh_counter()
            self._refresh_mini_journal()
            self.btn_print.setEnabled(False)
            self.scan_input.clear()
            self._restore_scan_focus()
            return

        # Основной путь — сверка отсканированного ШК со всеми кодами ЧЗ через Store.process_scan
        result = self.store.process_scan(
            foreign_code=raw,
            point=self.store.current_point,
            operator=self.store.current_operator,
        )

        cz = result.get("cz") if isinstance(result, dict) else None
        if isinstance(result, dict) and (result.get("status") == "ok" or cz is not None) and cz:
            self.last_cz_code = cz.get("code")
            foreign["gtin"] = cz.get("gtin") or foreign.get("gtin") or clean_gtin_log
            foreign["serial"] = cz.get("sn") or foreign.get("serial") or clean_sn_log

            self.store.log(
                "Скан",
                point=self.store.current_point,
                operator=self.store.current_operator,
                gtin=_clean_display_code(foreign["gtin"]),
                sn=_clean_display_code(foreign["serial"]),
                result="успех"
            )

            self._auto_print(self.last_cz_code, foreign)
            self._refresh_counter()
            self._refresh_mini_journal()
            self.btn_print.setEnabled(True)
        else:
            self.store.increment_counter("errors")
            err_msg = (
                (result.get("message") or result.get("info"))
                if isinstance(result, dict) else None
            ) or f"Неизвестный ШК ({_elide_middle(raw, 32)}): не связан с Честным Знаком"

            self.store.log(
                "Скан",
                point=self.store.current_point,
                operator=self.store.current_operator,
                gtin=_elide_middle(clean_gtin_log, 24),
                sn="—",
                result="не найден"
            )
            self._show_result(
                "error",
                foreign=foreign,
                message="СТОП! ШК НЕ СВЯЗАН С ЧЕСТНЫМ ЗНАКОМ",
                sub=err_msg
            )
            self._refresh_counter()
            self._refresh_mini_journal()
            self.btn_print.setEnabled(False)

        self.scan_input.clear()
        self._restore_scan_focus()

    def _auto_print(self, code: str, foreign: Optional[Dict[str, Any]] = None):
        from printer import print_code, load_config
        cfg = load_config()

        point_cfg = dict(cfg or {})
        try:
            from printer import get_printer_for_point
            point_cfg = dict(get_printer_for_point(cfg, self.store.current_point) or cfg)
        except Exception:
            pass

        bound_printer = resolve_bound_printer_for_scan()
        if bound_printer:
            point_cfg["printer_name"] = bound_printer
            point_cfg["name"] = bound_printer

        if not point_cfg.get("printer_name") and not point_cfg.get("printer_ip"):
            self._show_result(
                "error",
                foreign=foreign,
                message="ПРИНТЕР НЕ НАСТРОЕН",
                sub="Привяжите принтер к сканеру в разделе Админ -> Устройства"
            )
            return

        point_cfg["_verified_cz_code"] = True
        result = print_code(code, point_cfg)

        if isinstance(result, dict) and result.get("ok"):
            self.store.mark_printed(code, point=self.store.current_point)
            self.store.increment_counter("prints")
            self.store.log(
                "Автопечать",
                point=self.store.current_point,
                operator=self.store.current_operator,
                gtin=_clean_display_code((foreign or {}).get("gtin")),
                sn=_clean_display_code((foreign or {}).get("serial")),
                result="успех"
            )
            self._show_result("ok", foreign=foreign)
        else:
            self.store.increment_counter("errors")
            err_info = result.get("info", "Ошибка принтера") if isinstance(result, dict) else str(result)
            self.store.log(
                "Автопечать",
                point=self.store.current_point,
                operator=self.store.current_operator,
                gtin=_clean_display_code((foreign or {}).get("gtin")),
                sn=_clean_display_code((foreign or {}).get("serial")),
                result="ошибка"
            )
            self._show_result(
                "error",
                foreign=foreign,
                message="ОШИБКА ПЕЧАТИ НА ПРИНТЕРЕ",
                sub=err_info
            )

        self._refresh_counter()

    def do_print(self):
        """Повтор печати последнего кода ЧЗ (F2)."""
        if not self.last_cz_code:
            self._restore_scan_focus()
            return

        from printer import print_code, load_config
        cfg = load_config()

        try:
            from printer import get_printer_for_point
            point_cfg = dict(get_printer_for_point(cfg, self.store.current_point) or cfg)
        except Exception:
            point_cfg = dict(cfg or {})

        bound_printer = resolve_bound_printer_for_scan()
        if bound_printer:
            point_cfg["printer_name"] = bound_printer
            point_cfg["name"] = bound_printer

        point_cfg["_verified_cz_code"] = True
        result = print_code(self.last_cz_code, point_cfg)

        if isinstance(result, dict) and result.get("ok"):
            self.store.mark_printed(self.last_cz_code, point=self.store.current_point)
            self.store.increment_counter("prints")
            self.store.log(
                "Повтор печати",
                point=self.store.current_point,
                operator=self.store.current_operator,
                gtin=_clean_display_code((self.last_foreign or {}).get("gtin")),
                sn=_clean_display_code((self.last_foreign or {}).get("serial")),
                result="успех"
            )
            self._show_result("ok", foreign=self.last_foreign)
        else:
            self.store.increment_counter("errors")
            err_info = result.get("info", "Ошибка принтера") if isinstance(result, dict) else str(result)
            self._show_result(
                "error",
                foreign=self.last_foreign,
                message="ОШИБКА ПЕЧАТИ НА ПРИНТЕРЕ",
                sub=err_info
            )

        self._refresh_counter()
        self._refresh_mini_journal()
        self._restore_scan_focus()

    def _open_manual(self):
        """Диалог ручного ввода штрихкода (F5), если этикетка на коробке повреждена."""
        code, ok = QInputDialog.getText(
            self,
            "Ручной ввод штрихкода (F5)",
            "Введите или вставьте штрихкод товара (EAN-13 / GTIN-14 / GS1):"
        )
        if ok and code and code.strip():
            self.scan_input.setText(code.strip())
            self.do_scan(mode="auto")
        else:
            self._restore_scan_focus()

    def _open_full_journal(self):
        """Переключить на вкладку Админ -> Журнал (F9)."""
        try:
            win = self.window()
            if hasattr(win, "tabs"):
                win.tabs.setCurrentIndex(1)
                if hasattr(win, "admin"):
                    win.admin.switch("journal")
        except Exception as e:
            log.error("Не удалось открыть журнал: %s", e)

    def reset(self):
        self.last_cz_code = None
        self.last_foreign = None
        self.scan_input.clear()
        self.btn_print.setEnabled(False)
        self._show_result("idle")
        self._restore_scan_focus()