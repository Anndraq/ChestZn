r"""
Интерактивный предпросмотр этикетки (label_preview.py).
Полностью синхронизирован с AdminWindow и базой загруженных кодов (pool_codes + gtin_map):
- Прилипание к сетке (Snap-to-Grid) с шагом 1 мм / 2 мм / 5 мм при Drag & Drop и ресайзе.
- Настоящая динамическая сетка: линии отрисовываются ровно с выбранным шагом (1 мм, 2 мм, 5 мм),
  а при выборе «Откл» сетка полностью отключается (исправлено, чтобы вид сетки реально менялся).
- Зажатая клавиша Alt временно отключает прилипание к сетке для свободной пиксельной подгонки.
- Миллиметровая линейка сверху и слева с рисками (1 мм, 5 мм) и числовыми отметками каждые 10 мм.
- Пунктирная рамка зоны безопасности (1.5 мм от физического края термоэтикетки).
- Полноценное изменение размеров (растягивание/сжатие) синей рамки выделения (как в Paint и Photoshop):
  8 маркеров изменения размера (углы и стороны) для текстовых блоков и DataMatrix.
- Настраиваемое межстрочное расстояние (Line Spacing) для многострочных текстовых полей (1.0 - 2.5).
- Автоматический перенос строк (Word Wrap) строго внутри заданной рамки и границ этикетки.
"""
import re
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from PySide6.QtWidgets import QWidget, QInputDialog, QMenu
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QFontMetrics, QPixmap
from PySide6.QtCore import Qt, QRect, QPoint


def _is_light_theme() -> bool:
    try:
        from styles import is_light_theme
        return is_light_theme()
    except Exception:
        return False


try:
    from barcode import render_datamatrix
except ImportError:
    from ChestZn.barcode import render_datamatrix

DEFAULT_REALISTIC_CZ_CODE = (
    "010460700123456721A1B2C3D4E5F6G\x1d91EE06\x1d92dGVzdGNyeXB0b3RhaWx2ZXJ5bG9uZ3N0cmluZ2hlcmU="
)

# Расширенные образцы данных для стандартных полей шаблонов
SAMPLE_FIELD_VALUES = {
    "gtin": "04607001234567",
    "shk": "4607001234567",
    "serial": "SN00012345",
    "sn": "SN00012345",
    "name": "Кроссовки мужские демисезонные кожаные р.42 черные",
    "name_ru": "Кроссовки мужские демисезонные кожаные р.42 черные",
    "model": "Арт. CR-9021-BLK",
    "lot": "Партия 01-2026",
    "batch": "Партия 01-2026",
    "exp": "12.2029",
    "exp_date": "12.2029",
    "date": "01.11.2026",
    "country": "Россия",
    "importer": "ООО «ЧестЗн Снабжение»",
    "manufacturer": "АО «ЧестЗн Продакшн»",
    "composition": "Верх: 100% натуральная кожа; Подкладка: текстиль; Подошва: ТЭП",
    "material": "Натуральная кожа",
    "size_eu": "42",
    "qty": "1 шт.",
}

SAMPLE_PRESETS = [
    {
        "title": "Стандартное наименование (среднее)",
        "name": "Кроссовки мужские демисезонные кожаные р.42 черные",
    },
    {
        "title": "Короткое наименование",
        "name": "Чай 'Майский' черный байховый 100г",
    },
    {
        "title": "Очень длинное наименование (проверка переноса и переполнения)",
        "name": "Куртка мужская зимняя утепленная ветрозащитная с отстегивающимся капюшоном мембрана 5000/5000 синяя р.50-52 рост 182",
    },
]


class LabelPreviewWidget(QWidget):
    """
    Виджет визуального предпросмотра и конструктора этикетки:
      - Прилипание к сетке (Snap-to-Grid) с шагом 1 мм / 2 мм / 5 мм.
      - Полноценная динамическая сетка (линии рисуются с выбранным шагом).
      - Линейка в миллиметрах и пунктирные границы зоны безопасности (1.5 мм).
      - 8 маркеров изменения размера (углы и стороны) синей рамки, как в Paint/Photoshop.
      - Настройка межстрочного интервала (line_spacing) для текстовых блоков.
      - Автоматический перенос строк (Word Wrap) строго внутри заданной пользователем рамки.
      - Подстановка настоящего DataMatrix из базы по выбранному ШК.
      - Перетаскивание элементов мышью (Drag & Drop) и изменение размера DataMatrix.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_admin = parent

        self._tpl: Optional[Dict[str, Any]] = None
        self._demo_code: str = ""
        self._current_shk: str = ""
        self._current_gtin: str = ""
        self._current_sn: str = ""
        self._cached_base_code: str = ""
        self._cached_base_pixmap: Optional[QPixmap] = None

        self._field_rects: List[Tuple[QRect, str, int, str, str]] = []
        self._dm_screen_rects: List[QRect] = []

        self._selected: Optional[Tuple[str, int]] = None
        self._dragging: Optional[Tuple[str, int, str]] = None
        self._drag_offset: Optional[QPoint] = None

        # Ресайз через маркеры: (tab_key, row_idx, kind, handle_id)
        # handle_id: 'tl', 'tc', 'tr', 'ml', 'mr', 'bl', 'bc', 'br'
        self._resizing: Optional[Tuple[str, int, str, str]] = None
        self._resize_start_pos: Optional[QPoint] = None
        self._resize_start_dots_rect: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)
        self._live_resize_rect: Optional[Tuple[str, int, int, int, int, int]] = None  # (tab, row, x, y, w, h)

        self._live_drag_pos: Optional[Tuple[str, int, int, int]] = None

        # --- Настройки сетки, линеек и зоны безопасности ---
        self.snap_to_grid: bool = True
        self.grid_step_mm: float = 1.0       # Шаг сетки: 1.0 мм по умолчанию
        self.show_grid: bool = True          # Отображать сетку линий/точек
        self.show_safety_margin: bool = True # Пунктир зоны безопасности 1.5 мм
        self.safety_margin_mm: float = 1.5   # 1.5 мм от физического края
        self.show_ruler: bool = True         # Миллиметровые линейки сверху и слева
        self.current_sample_idx: int = 0     # Текущий пресет тестового наименования

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 320)

    # ------------------------------------------------------------
    # Публичные методы управления шрифтом, стилем и сеткой
    # ------------------------------------------------------------

    def _ensure_selected_field(self, prefer_text: bool = True) -> Optional[Tuple[str, int, Dict[str, Any]]]:
        """Возвращает (tab_key, row_idx, field_dict) выбранного поля или автоматически выбирает первое подходящее."""
        tab_key, _, _, _, fields = self._get_active_tab_context()
        if not fields:
            return None

        if self._selected and self._selected[0] == tab_key:
            r_idx = self._selected[1]
            if 0 <= r_idx < len(fields):
                f = fields[r_idx]
                if not prefer_text or f.get("name") != "dataMatrix":
                    return tab_key, r_idx, f

        if prefer_text:
            for idx, f in enumerate(fields):
                if f.get("name") != "dataMatrix":
                    self._selected = (tab_key, idx)
                    self._notify_selection_changed()
                    return tab_key, idx, f

        self._selected = (tab_key, 0)
        self._notify_selection_changed()
        return tab_key, 0, fields[0]

    def _notify_selection_changed(self):
        """Синхронизирует панель инструментов AdminWindow с текущим выбранным элементом на холсте."""
        if not self.parent_admin:
            return
        tab_key, _, _, _, fields = self._get_active_tab_context()
        if self._selected and self._selected[0] == tab_key and 0 <= self._selected[1] < len(fields):
            row_idx = self._selected[1]
            f = fields[row_idx]
            try:
                widgets = self.parent_admin._tpl_tab_widgets.get(tab_key)
                if widgets and "fields" in widgets:
                    tbl = widgets["fields"]
                    tbl.blockSignals(True)
                    tbl.selectRow(row_idx)
                    tbl.blockSignals(False)
            except Exception:
                pass
            if hasattr(self.parent_admin, "_tpl_sync_selected_field_ui"):
                try:
                    self.parent_admin._tpl_sync_selected_field_ui(f)
                except Exception:
                    pass
            elif f.get("name") != "dataMatrix" and hasattr(self.parent_admin, "_tpl_update_font_badge"):
                try:
                    self.parent_admin._tpl_update_font_badge(int(f.get("font_size") or 10))
                except Exception:
                    pass
        else:
            if hasattr(self.parent_admin, "_tpl_sync_selected_field_ui"):
                try:
                    self.parent_admin._tpl_sync_selected_field_ui(None)
                except Exception:
                    pass

    def change_selected_font_size(self, delta: int) -> bool:
        """
        Увеличивает (delta > 0) или уменьшает (delta < 0) размер шрифта
        выделенного текстового поля (от 6 до 36 pt), либо размер DataMatrix (в мм).
        """
        tab_key, _, _, _, fields = self._get_active_tab_context()
        if not fields:
            return False

        if self._selected and self._selected[0] == tab_key and 0 <= self._selected[1] < len(fields):
            row_idx = self._selected[1]
            f = fields[row_idx]
            if f.get("name") == "dataMatrix":
                cur_mm = float(f.get("size") or 19.0)
                new_mm = max(8.0, min(80.0, cur_mm + delta))
                if abs(new_mm - cur_mm) < 0.01:
                    return False
                if self.parent_admin and hasattr(self.parent_admin, "_update_field_size"):
                    try:
                        self.parent_admin._update_field_size(tab_key, row_idx, new_mm)
                    except Exception:
                        pass
                f["size"] = new_mm
                self._notify_selection_changed()
                self.update()
                return True

        sel_info = self._ensure_selected_field(prefer_text=True)
        if not sel_info:
            return False

        tab_key, row_idx, f = sel_info
        if f.get("name") == "dataMatrix":
            return False

        cur_font_size = int(f.get("font_size") or 10)
        if self.parent_admin and hasattr(self.parent_admin, "_tpl_tab_widgets"):
            w = self.parent_admin._tpl_tab_widgets.get(tab_key)
            if w and "fields" in w:
                tbl = w["fields"]
                if 0 <= row_idx < tbl.rowCount():
                    spin = tbl.cellWidget(row_idx, 3)
                    if spin and hasattr(spin, "value") and spin.value() > 0:
                        cur_font_size = int(spin.value())

        new_font_size = max(6, min(36, cur_font_size + delta))
        if new_font_size == cur_font_size:
            return False

        if self.parent_admin and hasattr(self.parent_admin, "_update_field_font"):
            try:
                self.parent_admin._update_field_font(tab_key, row_idx, new_font_size)
            except Exception as e:
                print("update_field_font error:", e)

        f["font_size"] = new_font_size
        self._notify_selection_changed()
        self.update()
        return True

    def set_selected_font_size(self, font_size: int) -> bool:
        """Устанавливает конкретный размер шрифта в pt для выделенного текстового поля."""
        sel_info = self._ensure_selected_field(prefer_text=True)
        if not sel_info:
            return False
        _, _, f = sel_info
        if f.get("name") == "dataMatrix":
            return False
        cur_font_size = max(6, int(f.get("font_size") or 10))
        return self.change_selected_font_size(font_size - cur_font_size)

    def change_selected_line_spacing(self, delta: float) -> bool:
        """
        Изменяет межстрочный интервал (line_spacing) выбранного текстового поля
        (шаг обычно ±0.1, диапазон от 0.8 до 2.5).
        """
        sel_info = self._ensure_selected_field(prefer_text=True)
        if not sel_info:
            return False
        tab_key, row_idx, f = sel_info
        if f.get("name") == "dataMatrix":
            return False
        cur_ls = float(f.get("line_spacing") or 1.15)
        new_ls = round(max(0.8, min(2.5, cur_ls + delta)), 2)
        if abs(new_ls - cur_ls) < 0.01:
            return False
        f["line_spacing"] = new_ls
        if self.parent_admin and hasattr(self.parent_admin, "_update_field_style"):
            try:
                self.parent_admin._update_field_style(tab_key, row_idx, line_spacing=new_ls)
            except Exception:
                pass
        self._notify_selection_changed()
        self.update()
        return True

    def toggle_selected_bold(self) -> bool:
        """Переключает жирный шрифт (Bold) для выбранного текстового поля."""
        sel_info = self._ensure_selected_field(prefer_text=True)
        if not sel_info:
            return False
        tab_key, row_idx, f = sel_info
        if f.get("name") == "dataMatrix":
            return False
        new_bold = not bool(f.get("bold", False))
        f["bold"] = new_bold
        if self.parent_admin and hasattr(self.parent_admin, "_update_field_style"):
            try:
                self.parent_admin._update_field_style(tab_key, row_idx, bold=new_bold)
            except Exception:
                pass
        self._notify_selection_changed()
        self.update()
        return True

    def toggle_selected_italic(self) -> bool:
        """Переключает курсив (Italic) для выбранного текстового поля."""
        sel_info = self._ensure_selected_field(prefer_text=True)
        if not sel_info:
            return False
        tab_key, row_idx, f = sel_info
        if f.get("name") == "dataMatrix":
            return False
        new_italic = not bool(f.get("italic", False))
        f["italic"] = new_italic
        if self.parent_admin and hasattr(self.parent_admin, "_update_field_style"):
            try:
                self.parent_admin._update_field_style(tab_key, row_idx, italic=new_italic)
            except Exception:
                pass
        self._notify_selection_changed()
        self.update()
        return True

    def edit_selected_field_label(self) -> bool:
        """Открывает диалог редактирования подписи выбранного текстового поля."""
        sel_info = self._ensure_selected_field(prefer_text=True)
        if not sel_info:
            return False
        tab_key, row_idx, f = sel_info
        if f.get("name") == "dataMatrix":
            return False
        cur_label = str(f.get("label") or "")
        fname = str(f.get("name") or "Поле")
        new_label, ok = QInputDialog.getText(
            self,
            f"Подпись поля «{fname}»",
            "Введите подпись поля (оставьте пустым для вывода без префикса):",
            text=cur_label,
        )
        if not ok:
            return False
        if self.parent_admin and hasattr(self.parent_admin, "_update_field_label"):
            self.parent_admin._update_field_label(tab_key, row_idx, new_label.strip())
        self.update()
        return True

    def delete_selected_field(self) -> bool:
        """Удаляет выбранное текстовое поле с этикетки (защищая обязательный DataMatrix)."""
        if not self._selected:
            return False
        tab_key, row_idx = self._selected
        _, _, _, _, fields = self._get_active_tab_context()
        if not (0 <= row_idx < len(fields)):
            return False
        f = fields[row_idx]
        if f.get("name") == "dataMatrix" and tab_key in ("combined", "cz"):
            if self.parent_admin and hasattr(self.parent_admin, "toast"):
                self.parent_admin.toast.show_message("Поле DataMatrix обязательно для этого режима", "warn")
            return False
        if self.parent_admin and hasattr(self.parent_admin, "_tpl_remove_field"):
            self.parent_admin._tpl_remove_field(tab_key, row_idx)
            self._selected = None
            self._notify_selection_changed()
            self.update()
            return True
        return False

    def set_grid_step(self, step_mm: float):
        """Устанавливает шаг сетки в мм (0.0 — отключить сетку). Мгновенно перерисовывает холст с новым шагом."""
        self.grid_step_mm = float(step_mm)
        self.snap_to_grid = (self.grid_step_mm > 0.0)
        self.show_grid = (self.grid_step_mm > 0.0)
        self.update()

    def toggle_safety_margin(self):
        """Включает/выключает отображение зоны безопасности."""
        self.show_safety_margin = not self.show_safety_margin
        self.update()

    def toggle_ruler(self):
        """Включает/выключает миллиметровую линейку."""
        self.show_ruler = not self.show_ruler
        self.update()

    def toggle_grid(self):
        """Включает/выключает визуальную сетку."""
        self.show_grid = not self.show_grid
        if not self.show_grid:
            self.grid_step_mm = 0.0
            self.snap_to_grid = False
        else:
            if self.grid_step_mm <= 0:
                self.grid_step_mm = 1.0
            self.snap_to_grid = True
        self.update()

    def set_sample_preset(self, idx: int):
        """Переключает пресет тестового товара для проверки переноса текста."""
        if 0 <= idx < len(SAMPLE_PRESETS):
            self.current_sample_idx = idx
            SAMPLE_FIELD_VALUES["name"] = SAMPLE_PRESETS[idx]["name"]
            SAMPLE_FIELD_VALUES["name_ru"] = SAMPLE_PRESETS[idx]["name"]
            self.update()

    def set_template(self, tpl: Optional[dict], demo_code: str = ""):
        self._tpl = tpl
        if demo_code and demo_code != self._demo_code:
            self._apply_dm_code(demo_code)
        self.update()

    def set_demo_code(self, demo_code: str, shk: str = ""):
        if demo_code:
            self._apply_dm_code(demo_code, shk=shk)
            self.update()

    def _apply_dm_code(self, dm_code: str, shk: str = "", gtin: str = "", sn: str = ""):
        """Устанавливает активный код ДМ и разбирает из него GTIN и серийный номер (SN) для полей шаблона."""
        clean_dm = (dm_code or "").strip()
        if clean_dm != self._demo_code:
            self._demo_code = clean_dm
            self._cached_base_pixmap = None

        m = re.match(r"^(?:01|\(01\))(\d{14})(?:21|\(21\))([^\x1d\(\r\n]{1,24})", clean_dm.lstrip("\x1d"))
        if m:
            self._current_gtin = gtin or m.group(1)
            self._current_sn = sn or m.group(2).strip()
        else:
            self._current_gtin = gtin or self._current_gtin
            self._current_sn = sn or self._current_sn

        if shk:
            self._current_shk = shk
        elif self._current_gtin:
            self._current_shk = (
                self._current_gtin[1:]
                if len(self._current_gtin) == 14 and self._current_gtin.startswith("0")
                else self._current_gtin
            )

    def set_shk_for_preview(self, shk_or_gtin: str) -> Dict[str, Any]:
        """
        По переданному ШК товара (или GTIN) находит в базе данных соответствующий ему
        настоящий DataMatrix (ДМ) и обновляет предпросмотр шаблона.
        """
        raw_q = (shk_or_gtin or "").strip()
        try:
            import db
            with db.get_conn() as conn:
                if raw_q:
                    candidates = [raw_q]
                    if raw_q.isdigit() and len(raw_q) == 13:
                        candidates.append("0" + raw_q)
                    elif raw_q.isdigit() and len(raw_q) == 14 and raw_q.startswith("0"):
                        candidates.append(raw_q[1:])

                    ph = ",".join(["?"] * len(candidates))
                    for r_m in conn.execute(
                        f"SELECT cz_gtin FROM gtin_map WHERE foreign_gtin IN ({ph})",
                        tuple(candidates)
                    ).fetchall():
                        cz_g = str(r_m[0] or "").strip()
                        if cz_g and cz_g not in candidates:
                            candidates.append(cz_g)

                    ph2 = ",".join(["?"] * len(candidates))
                    row = conn.execute(
                        f"SELECT code, gtin, sn FROM pool_codes WHERE gtin IN ({ph2}) ORDER BY used ASC, id ASC LIMIT 1",
                        tuple(candidates)
                    ).fetchone()
                    if row and row[0]:
                        self._apply_dm_code(str(row[0]), shk=raw_q, gtin=str(row[1] or ""), sn=str(row[2] or ""))
                        self.update()
                        return {
                            "ok": True,
                            "code": self._demo_code,
                            "gtin": self._current_gtin,
                            "sn": self._current_sn,
                            "shk": raw_q,
                        }

                row_any = conn.execute(
                    "SELECT code, gtin, sn FROM pool_codes ORDER BY used ASC, id ASC LIMIT 1"
                ).fetchone()
                if row_any and row_any[0]:
                    self._apply_dm_code(
                        str(row_any[0]),
                        gtin=str(row_any[1] or ""),
                        sn=str(row_any[2] or "")
                    )
                    self.update()
                    return {
                        "ok": True,
                        "code": self._demo_code,
                        "gtin": self._current_gtin,
                        "sn": self._current_sn,
                        "shk": self._current_shk,
                    }
        except Exception:
            pass

        return {
            "ok": False,
            "code": self._demo_code or DEFAULT_REALISTIC_CZ_CODE,
            "gtin": self._current_gtin or SAMPLE_FIELD_VALUES["gtin"],
            "sn": self._current_sn or SAMPLE_FIELD_VALUES["sn"],
            "shk": raw_q,
        }

    def _resolve_active_code(self) -> str:
        """Возвращает реальный код ДМ, подтянутый к выбранному ШК из пула (или дефолтный код)."""
        if self._demo_code:
            return self._demo_code

        res = self.set_shk_for_preview("")
        if res.get("ok") and self._demo_code:
            return self._demo_code

        return DEFAULT_REALISTIC_CZ_CODE

    def _resolve_sample_dm(self) -> Tuple[str, str, str, str]:
        """Возвращает кортеж (dm_code, sn_val, gtin_val, matched_shk) для статусной строки AdminWindow."""
        code = self._resolve_active_code()
        gtin_val = self._current_gtin or SAMPLE_FIELD_VALUES["gtin"]
        sn_val = self._current_sn or SAMPLE_FIELD_VALUES["sn"]
        shk_val = self._current_shk or gtin_val
        return code, sn_val, gtin_val, shk_val

    def _get_fast_dm_pixmap(self, target_px: int) -> QPixmap:
        """Мгновенно возвращает QPixmap настоящего GS1 DataMatrix ECC200."""
        code = self._resolve_active_code()
        if self._cached_base_pixmap is None or self._cached_base_code != code:
            self._cached_base_code = code
            self._cached_base_pixmap = render_datamatrix(code, size=360)

        if self._cached_base_pixmap is None or self._cached_base_pixmap.isNull():
            return QPixmap()

        sz = max(16, int(target_px))
        return self._cached_base_pixmap.scaled(sz, sz, Qt.KeepAspectRatio, Qt.FastTransformation)

    # ------------------------------------------------------------
    # Получение параметров активной вкладки шаблона из AdminWindow
    # ------------------------------------------------------------

    def _get_active_tab_context(self) -> Tuple[str, float, float, int, List[Dict[str, Any]]]:
        if self.parent_admin is not None and hasattr(self.parent_admin, "tpl_tabs") and hasattr(self.parent_admin, "_tpl_tab_widgets"):
            try:
                tab_idx = self.parent_admin.tpl_tabs.currentIndex()
                tab_keys = ["combined", "cz", "label"]
                tab_key = tab_keys[tab_idx] if 0 <= tab_idx < len(tab_keys) else "combined"
                widgets = self.parent_admin._tpl_tab_widgets.get(tab_key)
                if widgets:
                    w_mm = float(widgets["w"].value())
                    h_mm = float(widgets["h"].value())
                    dpi = int(widgets["dpi"].currentText() or 203)
                    fields = self.parent_admin._collect_fields_from_table(tab_key)
                    return tab_key, w_mm, h_mm, dpi, fields
            except Exception:
                pass

        if self._tpl:
            w_mm = float(self._tpl.get("width_mm") or self._tpl.get("w") or 58.0)
            h_mm = float(self._tpl.get("height_mm") or self._tpl.get("h") or 40.0)
            dpi = int(self._tpl.get("dpi") or 203)
            raw_fields = self._tpl.get("fields", [])
            if isinstance(raw_fields, dict):
                flat = []
                for d_it in raw_fields.get("dm", []):
                    flat.append({
                        "name": "dataMatrix",
                        "label": "DataMatrix",
                        "x": d_it.get("x", 16),
                        "y": d_it.get("y", 16),
                        "size": round(float(d_it.get("size_dots", 120)) / dpi * 25.4, 1),
                        "font_size": 0,
                    })
                for t_it in raw_fields.get("fields", []) + raw_fields.get("static_texts", []):
                    flat.append({
                        "name": t_it.get("name") or t_it.get("key") or "text",
                        "label": t_it.get("label") or t_it.get("text") or "Текст",
                        "x": t_it.get("x", 16),
                        "y": t_it.get("y", 16),
                        "width": t_it.get("width"),
                        "height": t_it.get("height"),
                        "font_size": t_it.get("font_size", 10),
                        "line_spacing": float(t_it.get("line_spacing") or 1.15),
                        "bold": bool(t_it.get("bold", False)),
                        "italic": bool(t_it.get("italic", False)),
                    })
                return "combined", w_mm, h_mm, dpi, flat
            elif isinstance(raw_fields, list):
                return "combined", w_mm, h_mm, dpi, raw_fields

        return "", 58.0, 40.0, 203, []

    def _label_size_dots(self) -> Tuple[int, int, int]:
        _, w_mm, h_mm, dpi, _ = self._get_active_tab_context()
        w_dots = max(40, int(round(w_mm / 25.4 * dpi)))
        h_dots = max(40, int(round(h_mm / 25.4 * dpi)))
        return w_dots, h_dots, dpi

    def _get_safe_padding_dots(self, dpi: int) -> int:
        """Возвращает отступ от края этикетки в точках принтера (учитывая зону безопасности)."""
        margin_mm = self.safety_margin_mm if (self.show_safety_margin and self.safety_margin_mm > 0) else 0.8
        return max(4, int(round((margin_mm / 25.4) * dpi)))

    def _get_scale_and_offset(self) -> Tuple[float, int, int, int, int]:
        w_dots, h_dots, _ = self._label_size_dots()
        if w_dots <= 0 or h_dots <= 0:
            return 1.0, 0, 0, w_dots, h_dots

        ruler_space = 22 if self.show_ruler else 0
        margin = 18
        avail_w = max(10, self.width() - 2 * margin - ruler_space)
        avail_h = max(10, self.height() - 2 * margin - ruler_space)

        scale = min(avail_w / w_dots, avail_h / h_dots)
        disp_w = int(w_dots * scale)
        disp_h = int(h_dots * scale)

        offset_x = (self.width() + ruler_space - disp_w) // 2
        offset_y = (self.height() + ruler_space - disp_h) // 2
        return scale, offset_x, offset_y, w_dots, h_dots

    def _dots_to_screen(self, x_dots: float, y_dots: float) -> QPoint:
        scale, off_x, off_y, _, _ = self._get_scale_and_offset()
        return QPoint(int(round(off_x + x_dots * scale)), int(round(off_y + y_dots * scale)))

    def _screen_to_dots(self, sx: float, sy: float) -> Tuple[int, int]:
        scale, off_x, off_y, _, _ = self._get_scale_and_offset()
        if scale <= 0:
            return int(sx), int(sy)
        return int(round((sx - off_x) / scale)), int(round((sy - off_y) / scale))

    def _px_to_dots(self, px_val: float) -> int:
        scale, _, _, _, _ = self._get_scale_and_offset()
        if scale <= 0:
            return int(px_val)
        return int(round(px_val / scale))

    def _snap_dots(self, val_dots: float, dpi: int) -> int:
        """Привязывает координаты в точках принтера к выбранному шагу сетки (в мм)."""
        if not self.snap_to_grid or self.grid_step_mm <= 0:
            return int(round(val_dots))
        step_dots = max(1.0, (self.grid_step_mm / 25.4) * dpi)
        return int(round(round(val_dots / step_dots) * step_dots))

    # ------------------------------------------------------------
    # Отрисовка холста этикетки, сетки, линеек и элементов
    # ------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        is_lt = _is_light_theme()
        painter.fillRect(self.rect(), QColor("#f1f5f9") if is_lt else QColor("#141b23"))

        tab_key, w_mm, h_mm, dpi, fields = self._get_active_tab_context()
        if not tab_key and not fields:
            painter.setPen(QPen(QColor("#8b97a6")))
            painter.drawText(self.rect(), Qt.AlignCenter, "Выберите шаблон этикетки")
            return

        scale, off_x, off_y, w_dots, h_dots = self._get_scale_and_offset()
        disp_w = int(w_dots * scale)
        disp_h = int(h_dots * scale)

        # Холст этикетки (белый)
        label_rect = QRect(off_x, off_y, disp_w, disp_h)
        painter.fillRect(label_rect, Qt.white)

        dots_per_mm = dpi / 25.4

        # 1. Динамическая визуальная сетка: строго по выбранному шагу self.grid_step_mm
        if self.show_grid and self.grid_step_mm > 0:
            step_px = self.grid_step_mm * dots_per_mm * scale
            if step_px >= 3.0:
                # В зависимости от шага (1 мм, 2 мм, 5 мм) отрисовываем регулярные вертикальные и горизонтальные линии
                grid_color = QColor("#dbeafe") if is_lt else QColor("#1e293b")
                accent_color = QColor("#93c5fd") if is_lt else QColor("#334155")

                cur_mm = self.grid_step_mm
                while cur_mm < w_mm:
                    gx = off_x + int(round(cur_mm * dots_per_mm * scale))
                    if gx < off_x + disp_w:
                        is_major = (abs(round(cur_mm) - cur_mm) < 0.05) and (int(round(cur_mm)) % 5 == 0)
                        painter.setPen(QPen(accent_color if is_major else grid_color, 1, Qt.SolidLine if is_major else Qt.DotLine))
                        painter.drawLine(gx, off_y, gx, off_y + disp_h)
                    cur_mm += self.grid_step_mm

                cur_mm_y = self.grid_step_mm
                while cur_mm_y < h_mm:
                    gy = off_y + int(round(cur_mm_y * dots_per_mm * scale))
                    if gy < off_y + disp_h:
                        is_major_y = (abs(round(cur_mm_y) - cur_mm_y) < 0.05) and (int(round(cur_mm_y)) % 5 == 0)
                        painter.setPen(QPen(accent_color if is_major_y else grid_color, 1, Qt.SolidLine if is_major_y else Qt.DotLine))
                        painter.drawLine(off_x, gy, off_x + disp_w, gy)
                    cur_mm_y += self.grid_step_mm

        # 2. Пунктирная рамка зоны безопасности (1.5 мм от края)
        sm_px = int(round(self.safety_margin_mm * dots_per_mm * scale)) if (self.show_safety_margin and self.safety_margin_mm > 0) else 3
        if self.show_safety_margin and self.safety_margin_mm > 0:
            if disp_w > 2 * sm_px and disp_h > 2 * sm_px:
                safe_rect = QRect(off_x + sm_px, off_y + sm_px, disp_w - 2 * sm_px, disp_h - 2 * sm_px)
                painter.setPen(QPen(QColor("#f59e0b"), 1, Qt.DashLine))
                painter.drawRect(safe_rect)

        # 3. Внешняя рамка этикетки
        painter.setPen(QPen(QColor("#0f172a"), 2))
        painter.drawRect(label_rect)

        # 4. Линейка сверху и слева
        if self.show_ruler:
            self._draw_rulers(painter, off_x, off_y, disp_w, disp_h, w_mm, h_mm, dpi, scale)

        # 5. Отрисовка элементов
        self._field_rects.clear()
        self._dm_screen_rects.clear()

        painter.save()
        painter.setClipRect(label_rect.adjusted(1, 1, -1, -1))

        for row_idx, f in enumerate(fields):
            if f.get("name") == "dataMatrix":
                self._draw_single_dm(painter, tab_key, row_idx, f, scale, off_x, off_y, disp_w, disp_h, dpi, sm_px)

        for row_idx, f in enumerate(fields):
            if f.get("name") != "dataMatrix":
                self._draw_single_text(painter, tab_key, row_idx, f, scale, off_x, off_y, disp_w, disp_h, dpi, sm_px)

        painter.restore()

        # 6. Информационный HUD-бейдж в реальном времени при Drag & Drop и ресайзе рамки
        if self._dragging or self._resizing:
            self._draw_hud_badge(painter, off_x, off_y, disp_w, disp_h, dpi)

    def _draw_rulers(
        self,
        painter: QPainter,
        off_x: int,
        off_y: int,
        disp_w: int,
        disp_h: int,
        w_mm: float,
        h_mm: float,
        dpi: int,
        scale: float
    ):
        """Отрисовывает миллиметровые шкалы сверху и слева от холста этикетки."""
        dots_per_mm = dpi / 25.4
        step_mm_px = dots_per_mm * scale

        font = QFont("Consolas", 7)
        painter.setFont(font)

        is_lt = _is_light_theme()
        r_bg = QColor("#e2e8f0") if is_lt else QColor("#1e293b")
        r_border = QColor("#cbd5e1") if is_lt else QColor("#475569")
        r_tick_main = QColor("#475569") if is_lt else QColor("#94a3b8")
        r_tick_sub = QColor("#94a3b8") if is_lt else QColor("#64748b")
        r_text = QColor("#0f172a") if is_lt else QColor("#cbd5e1")

        # Верхняя линейка
        ruler_top_y = off_y - 18
        painter.fillRect(QRect(off_x, ruler_top_y, disp_w, 16), r_bg)
        painter.setPen(QPen(r_border, 1))
        painter.drawRect(QRect(off_x, ruler_top_y, disp_w, 16))

        cur_mm = 0.0
        while cur_mm <= w_mm + 0.1:
            x_pos = int(round(off_x + cur_mm * step_mm_px))
            if x_pos > off_x + disp_w:
                break
            is_10mm = (int(round(cur_mm)) % 10 == 0)
            is_5mm = (int(round(cur_mm)) % 5 == 0)

            tick_len = 12 if is_10mm else (7 if is_5mm else 4)
            painter.setPen(QPen(r_tick_main if is_10mm else r_tick_sub, 1))
            painter.drawLine(x_pos, off_y - 2, x_pos, off_y - 2 - tick_len)

            if is_10mm and cur_mm > 0 and (x_pos + 12) < (off_x + disp_w):
                painter.setPen(QPen(r_text))
                painter.drawText(x_pos + 2, off_y - 6, f"{int(round(cur_mm))}")
            cur_mm += 1.0

        # Левая линейка
        ruler_left_x = off_x - 18
        painter.fillRect(QRect(ruler_left_x, off_y, 16, disp_h), r_bg)
        painter.setPen(QPen(r_border, 1))
        painter.drawRect(QRect(ruler_left_x, off_y, 16, disp_h))

        cur_mm = 0.0
        while cur_mm <= h_mm + 0.1:
            y_pos = int(round(off_y + cur_mm * step_mm_px))
            if y_pos > off_y + disp_h:
                break
            is_10mm = (int(round(cur_mm)) % 10 == 0)
            is_5mm = (int(round(cur_mm)) % 5 == 0)

            tick_len = 12 if is_10mm else (7 if is_5mm else 4)
            painter.setPen(QPen(r_tick_main if is_10mm else r_tick_sub, 1))
            painter.drawLine(off_x - 2, y_pos, off_x - 2 - tick_len, y_pos)

            if is_10mm and cur_mm > 0 and (y_pos + 8) < (off_y + disp_h):
                painter.setPen(QPen(r_text))
                painter.drawText(ruler_left_x + 2, y_pos - 2, f"{int(round(cur_mm))}")
            cur_mm += 1.0

    def _draw_hud_badge(self, painter: QPainter, off_x: int, off_y: int, disp_w: int, disp_h: int, dpi: int):
        """Отрисовывает информационный бейдж с точными координатами в мм и точках."""
        info_lines = []
        is_warning = False

        if self._live_drag_pos:
            _tab, _row, x_dots, y_dots = self._live_drag_pos
            x_mm = x_dots / dpi * 25.4
            y_mm = y_dots / dpi * 25.4
            info_lines.append(f"X: {x_mm:.1f} мм ({x_dots} dot)  Y: {y_mm:.1f} мм ({y_dots} dot)")

        elif self._live_resize_rect is not None:
            _tab, _row, x_d, y_d, w_d, h_d = self._live_resize_rect
            w_mm = w_d / dpi * 25.4
            h_mm = h_d / dpi * 25.4
            info_lines.append(f"Рамка: {w_mm:.1f}×{h_mm:.1f} мм ({w_d}×{h_d} dot)")

        if self.snap_to_grid and self.grid_step_mm > 0:
            info_lines.append(f"Сетка: {self.grid_step_mm:.1f} мм [Alt — свободно]")
        else:
            info_lines.append("Сетка: ВЫКЛ (свободное перемещение)")

        text = "  |  ".join(info_lines)
        font = QFont("Consolas", 8, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        bw = fm.horizontalAdvance(text) + 16
        bh = 22

        badge_x = max(10, off_x + (disp_w - bw) // 2)
        badge_y = off_y + disp_h + 8
        if badge_y + bh > self.height():
            badge_y = off_y + 8

        b_rect = QRect(badge_x, badge_y, bw, bh)
        bg_col = QColor("#7c2d12") if is_warning else QColor("#0f172a")
        border_col = QColor("#f97316") if is_warning else QColor("#2563eb")

        painter.fillRect(b_rect, bg_col)
        painter.setPen(QPen(border_col, 1))
        painter.drawRect(b_rect)

        painter.setPen(QPen(QColor("#fef08a") if is_warning else Qt.white))
        painter.drawText(b_rect, Qt.AlignCenter, text)

    def _add_resize_handles(self, rect: QRect, tab_key: str, row_idx: int, field_name: str, painter: QPainter):
        """Отрисовывает 8 маркеров изменения размера (углы и стороны) как в графических редакторах."""
        h_sz = 8
        half = h_sz // 2

        handles = {
            "tl": QRect(rect.left() - half, rect.top() - half, h_sz, h_sz),
            "tc": QRect(rect.center().x() - half, rect.top() - half, h_sz, h_sz),
            "tr": QRect(rect.right() - half, rect.top() - half, h_sz, h_sz),
            "ml": QRect(rect.left() - half, rect.center().y() - half, h_sz, h_sz),
            "mr": QRect(rect.right() - half, rect.center().y() - half, h_sz, h_sz),
            "bl": QRect(rect.left() - half, rect.bottom() - half, h_sz, h_sz),
            "bc": QRect(rect.center().x() - half, rect.bottom() - half, h_sz, h_sz),
            "br": QRect(rect.right() - half, rect.bottom() - half, h_sz, h_sz),
        }

        painter.setPen(QPen(QColor("#1d4ed8"), 1))
        for h_id, h_rect in handles.items():
            painter.fillRect(h_rect, Qt.white)
            painter.drawRect(h_rect)
            self._field_rects.append((h_rect, tab_key, row_idx, field_name, f"handle:{h_id}"))

    def _draw_single_dm(
        self,
        painter: QPainter,
        tab_key: str,
        row_idx: int,
        f: Dict[str, Any],
        scale: float,
        off_x: int,
        off_y: int,
        disp_w: int,
        disp_h: int,
        dpi: int,
        sm_px: int,
    ):
        x_dots = int(f.get("x", 16))
        y_dots = int(f.get("y", 16))
        size_mm = float(f.get("size") or 19.0)
        size_dots = max(20, int(round(size_mm / 25.4 * dpi)))

        if self._live_drag_pos and self._live_drag_pos[0] == tab_key and self._live_drag_pos[1] == row_idx:
            x_dots, y_dots = self._live_drag_pos[2], self._live_drag_pos[3]

        if self._live_resize_rect and self._live_resize_rect[0] == tab_key and self._live_resize_rect[1] == row_idx:
            x_dots, y_dots = self._live_resize_rect[2], self._live_resize_rect[3]
            size_dots = max(self._live_resize_rect[4], self._live_resize_rect[5])
            size_mm = round(size_dots / dpi * 25.4, 1)

        left_px = off_x + sm_px
        right_px = off_x + disp_w - sm_px
        top_px = off_y + sm_px
        bottom_px = off_y + disp_h - sm_px

        max_dm_px = max(18, min(right_px - left_px, bottom_px - top_px))
        ssz = max(18, min(max_dm_px, int(round(size_dots * scale))))

        sx = int(round(off_x + x_dots * scale))
        sy = int(round(off_y + y_dots * scale))
        sx = max(left_px, min(sx, max(left_px, right_px - ssz)))
        sy = max(top_px, min(sy, max(top_px, bottom_px - ssz)))

        rect = QRect(sx, sy, ssz, ssz)
        self._field_rects.append((rect, tab_key, row_idx, "dataMatrix", "dm"))
        self._dm_screen_rects.append(rect)

        pix = self._get_fast_dm_pixmap(ssz)
        if not pix.isNull():
            painter.drawPixmap(rect, pix)
        else:
            painter.setPen(QPen(QColor("#dc2626"), 1, Qt.DashLine))
            painter.drawRect(rect)
            painter.drawText(rect, Qt.AlignCenter, "DM")

        is_sel = (self._selected == (tab_key, row_idx))
        if is_sel:
            painter.fillRect(rect, QColor(37, 99, 235, 24))
            painter.setPen(QPen(QColor("#2563eb"), 2, Qt.SolidLine))
            painter.drawRect(rect)

            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            painter.setPen(QPen(QColor("#1d4ed8")))
            painter.drawText(
                rect.left(),
                max(off_y + 11, rect.top() - 4),
                f"ДМ {int(round(size_mm))}×{int(round(size_mm))} мм (X:{x_dots} Y:{y_dots})"
            )
            self._add_resize_handles(rect, tab_key, row_idx, "dataMatrix", painter)

    def _draw_single_text(
        self,
        painter: QPainter,
        tab_key: str,
        row_idx: int,
        f: Dict[str, Any],
        scale: float,
        off_x: int,
        off_y: int,
        disp_w: int,
        disp_h: int,
        dpi: int,
        sm_px: int,
    ):
        x_dots = int(f.get("x", 16))
        y_dots = int(f.get("y", 16))

        # Читаем заданные пользователем размеры рамки (если они были настроены растягиванием)
        saved_w_dots = int(f["width"]) if ("width" in f and f["width"] is not None) else None
        saved_h_dots = int(f["height"]) if ("height" in f and f["height"] is not None) else None

        if self._live_drag_pos and self._live_drag_pos[0] == tab_key and self._live_drag_pos[1] == row_idx:
            x_dots, y_dots = self._live_drag_pos[2], self._live_drag_pos[3]

        if self._live_resize_rect and self._live_resize_rect[0] == tab_key and self._live_resize_rect[1] == row_idx:
            x_dots, y_dots = self._live_resize_rect[2], self._live_resize_rect[3]
            saved_w_dots = self._live_resize_rect[4]
            saved_h_dots = self._live_resize_rect[5]

        left_px = off_x + sm_px
        right_px = off_x + disp_w - sm_px
        top_px = off_y + sm_px
        bottom_px = off_y + disp_h - sm_px

        sx = int(round(off_x + x_dots * scale))
        sy = int(round(off_y + y_dots * scale))

        font_size_pt = max(6, int(f.get("font_size") or 10))
        target_font_px = max(7, int(round(font_size_pt * dpi / 72.0 * scale)))
        is_bold = bool(f.get("bold", False))
        is_italic = bool(f.get("italic", False))
        line_spacing = float(f.get("line_spacing") or 1.15)

        fname = str(f.get("name") or "").strip()
        fname_l = fname.lower()
        flabel = str(f.get("label") or fname or "Поле").strip()

        if fname_l == "gtin" and self._current_gtin:
            sample_val = self._current_gtin
        elif fname_l in ("sn", "serial") and self._current_sn:
            sample_val = self._current_sn
        elif fname_l == "shk" and self._current_shk:
            sample_val = self._current_shk
        else:
            sample_val = SAMPLE_FIELD_VALUES.get(fname_l, "")

        if flabel and sample_val and fname_l not in ("name", "name_ru"):
            text_str = f"{flabel}: {sample_val}"
        elif sample_val:
            text_str = sample_val
        else:
            text_str = flabel

        font = QFont("Arial")
        font.setBold(is_bold)
        font.setItalic(is_italic)
        font.setPixelSize(target_font_px)
        fm = QFontMetrics(font)

        # Вычисляем ширину и высоту текстовой рамки:
        # Если пользователь растянул рамку — используем её точный размер; иначе рассчитываем естественный размер по тексту
        if saved_w_dots is not None and saved_w_dots > 20:
            box_w = max(24, int(round(saved_w_dots * scale)))
        else:
            avail_natural = max(32, right_px - sx)
            words = text_str.split()
            max_word_w = max((fm.horizontalAdvance(w) for w in words), default=0) + 8
            single_line_w = fm.horizontalAdvance(text_str) + 8
            box_w = min(avail_natural, max(max_word_w, single_line_w))

        # Удерживаем рамку внутри физических границ этикетки
        box_w = min(box_w, right_px - left_px)
        sx = max(left_px, min(sx, right_px - box_w))

        # Расчёт строк с учётом Word Wrap и заданного межстрочного расстояния (line_spacing)
        line_h = int(round(fm.height() * line_spacing))
        ascent = fm.ascent()

        # Разбиваем текст на строки по ширине рамки
        wrapped_lines: List[str] = []
        cur_line = ""
        for word in text_str.split():
            test_line = f"{cur_line} {word}".strip()
            if fm.horizontalAdvance(test_line) <= (box_w - 6) or not cur_line:
                cur_line = test_line
            else:
                wrapped_lines.append(cur_line)
                cur_line = word
        if cur_line:
            wrapped_lines.append(cur_line)

        natural_h = max(fm.height() + 4, len(wrapped_lines) * line_h + 4)
        if saved_h_dots is not None and saved_h_dots > 14:
            box_h = max(18, int(round(saved_h_dots * scale)))
        else:
            box_h = natural_h

        box_h = min(box_h, bottom_px - top_px)
        sy = max(top_px, min(sy, bottom_px - box_h))

        rect = QRect(sx, sy, box_w, box_h)
        self._field_rects.append((rect, tab_key, row_idx, flabel or fname, "text"))

        # 1. Рисуем текст с учётом межстрочного интервала строго внутри рамки
        painter.save()
        painter.setClipRect(rect, Qt.IntersectClip)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#0f172a")))

        cur_y = sy + ascent + 2
        for line in wrapped_lines:
            if cur_y + (fm.descent()) > (sy + box_h + 2):
                break
            painter.drawText(sx + 3, cur_y, line)
            cur_y += line_h
        painter.restore()

        # 2. Рисуем синюю рамку выделения и 8 маркеров изменения размера (как в Paint / Photoshop)
        is_sel = (self._selected == (tab_key, row_idx))
        if is_sel:
            painter.fillRect(rect, QColor(37, 99, 235, 26))
            painter.setPen(QPen(QColor("#2563eb"), 1.5, Qt.DashLine))
            painter.drawRect(rect)

            style_tag = ""
            if is_bold:
                style_tag += " B"
            if is_italic:
                style_tag += " I"
            if abs(line_spacing - 1.15) > 0.05:
                style_tag += f" ↕{line_spacing:.1f}"

            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            painter.setPen(QPen(QColor("#1d4ed8")))
            painter.drawText(
                rect.left(),
                max(off_y + 11, rect.top() - 4),
                f"{flabel} [{font_size_pt} pt{style_tag}]"
            )

            # Маркеры изменения размера (углы и стороны)
            self._add_resize_handles(rect, tab_key, row_idx, flabel or fname, painter)

    # ------------------------------------------------------------
    # Обработка событий мыши и клавиатуры
    # ------------------------------------------------------------

    def _get_handle_cursor(self, handle_id: str) -> Qt.CursorShape:
        mapping = {
            "tl": Qt.SizeFDiagCursor,
            "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor,
            "bl": Qt.SizeBDiagCursor,
            "tc": Qt.SizeVerCursor,
            "bc": Qt.SizeVerCursor,
            "ml": Qt.SizeHorCursor,
            "mr": Qt.SizeHorCursor,
        }
        return mapping.get(handle_id, Qt.ArrowCursor)

    def wheelEvent(self, event):
        """Изменение размера шрифта выбранного текстового поля (или размера ДМ) колёсиком мыши."""
        pos = event.position().toPoint()
        for rect, tab_key, row_idx, name, kind in reversed(self._field_rects):
            if kind in ("text", "dm") and rect.contains(pos):
                self._selected = (tab_key, row_idx)
                self._notify_selection_changed()
                break

        if self._selected:
            delta_y = event.angleDelta().y()
            if delta_y != 0:
                step = 1 if delta_y > 0 else -1
                if event.modifiers() & Qt.ShiftModifier:
                    step *= 2
                if self.change_selected_font_size(step):
                    event.accept()
                    return

        super().wheelEvent(event)

    def keyPressEvent(self, event):
        """Горячие клавиши (+ / - / B / I / Delete) при выделенном поле."""
        if self._selected:
            key = event.key()
            mods = event.modifiers()
            if key in (Qt.Key_Plus, Qt.Key_Equal) or (mods & Qt.ControlModifier and key == Qt.Key_Plus):
                if self.change_selected_font_size(+1):
                    event.accept()
                    return
            elif key in (Qt.Key_Minus, Qt.Key_Underscore) or (mods & Qt.ControlModifier and key == Qt.Key_Minus):
                if self.change_selected_font_size(-1):
                    event.accept()
                    return
            elif key == Qt.Key_B and (mods & Qt.ControlModifier):
                if self.toggle_selected_bold():
                    event.accept()
                    return
            elif key == Qt.Key_I and (mods & Qt.ControlModifier):
                if self.toggle_selected_italic():
                    event.accept()
                    return
            elif key == Qt.Key_Delete:
                if self.delete_selected_field():
                    event.accept()
                    return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        pos = event.position().toPoint()

        # 1. Проверяем клик по одному из 8 маркеров изменения размера (растягивание рамки)
        for rect, tab_key, row_idx, name, kind in reversed(self._field_rects):
            if kind.startswith("handle:"):
                handle_id = kind.split(":", 1)[1]
                if rect.contains(pos):
                    _, _, _, dpi, fields = self._get_active_tab_context()
                    f = fields[row_idx] if 0 <= row_idx < len(fields) else {}

                    # Определяем текущие координаты и размеры в точках принтера
                    cur_x = int(f.get("x", 16))
                    cur_y = int(f.get("y", 16))
                    if f.get("name") == "dataMatrix":
                        sz_mm = float(f.get("size") or 19.0)
                        cur_w = max(20, int(round(sz_mm / 25.4 * dpi)))
                        cur_h = cur_w
                    else:
                        cur_w = int(f["width"]) if ("width" in f and f["width"] is not None) else self._px_to_dots(rect.width())
                        cur_h = int(f["height"]) if ("height" in f and f["height"] is not None) else self._px_to_dots(rect.height())

                    self._resizing = (tab_key, row_idx, f.get("name", "text"), handle_id)
                    self._resize_start_pos = pos
                    self._resize_start_dots_rect = (cur_x, cur_y, cur_w, cur_h)
                    self._live_resize_rect = (tab_key, row_idx, cur_x, cur_y, cur_w, cur_h)
                    self._selected = (tab_key, row_idx)
                    self._notify_selection_changed()
                    self.setCursor(self._get_handle_cursor(handle_id))
                    self.update()
                    return

        # 2. Проверяем клик по самому элементу для перетаскивания (Drag & Drop)
        for rect, tab_key, row_idx, name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                self._dragging = (tab_key, row_idx, name)
                self._drag_offset = pos - rect.topLeft()
                self._selected = (tab_key, row_idx)
                self._live_drag_pos = None
                self._notify_selection_changed()
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                return

        self._selected = None
        self._notify_selection_changed()
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        alt_pressed = bool(event.modifiers() & Qt.AltModifier)

        # Растягивание / сжатие рамки через маркеры
        if self._resizing and self._resize_start_pos and self._resize_start_dots_rect:
            tab_key, row_idx, f_name, handle_id = self._resizing
            _, _, _, dpi, _ = self._get_active_tab_context()
            w_dots, h_dots, _ = self._label_size_dots()
            pad_dots = self._get_safe_padding_dots(dpi)

            orig_x, orig_y, orig_w, orig_h = self._resize_start_dots_rect
            dx_px = pos.x() - self._resize_start_pos.x()
            dy_px = pos.y() - self._resize_start_pos.y()
            dx_dots = self._px_to_dots(dx_px)
            dy_dots = self._px_to_dots(dy_px)

            new_x, new_y, new_w, new_h = orig_x, orig_y, orig_w, orig_h

            if "r" in handle_id:
                new_w = max(24, orig_w + dx_dots)
            if "l" in handle_id:
                clamped_dx = min(orig_w - 24, dx_dots)
                new_x = orig_x + clamped_dx
                new_w = orig_w - clamped_dx

            if "b" in handle_id:
                new_h = max(16, orig_h + dy_dots)
            if "t" in handle_id:
                clamped_dy = min(orig_h - 16, dy_dots)
                new_y = orig_y + clamped_dy
                new_h = orig_h - clamped_dy

            if f_name == "dataMatrix":
                # Для DataMatrix сохраняем квадратную пропорцию
                sq_sz = max(20, max(new_w, new_h))
                new_w = sq_sz
                new_h = sq_sz

            if self.snap_to_grid and not alt_pressed:
                new_x = self._snap_dots(new_x, dpi)
                new_y = self._snap_dots(new_y, dpi)
                new_w = self._snap_dots(new_w, dpi)
                new_h = self._snap_dots(new_h, dpi)

            new_x = max(pad_dots, min(w_dots - pad_dots - 20, new_x))
            new_y = max(pad_dots, min(h_dots - pad_dots - 14, new_y))
            new_w = max(20, min(w_dots - pad_dots - new_x, new_w))
            new_h = max(14, min(h_dots - pad_dots - new_y, new_h))

            self._live_resize_rect = (tab_key, row_idx, new_x, new_y, new_w, new_h)
            self.setCursor(self._get_handle_cursor(handle_id))
            self.update()
            return

        # Перетаскивание рамки мышью
        if self._dragging:
            new_tl = pos - self._drag_offset
            x_dots, y_dots = self._screen_to_dots(new_tl.x(), new_tl.y())
            tab_key, row_idx, _name = self._dragging

            _, _, _, dpi, _ = self._get_active_tab_context()
            if self.snap_to_grid and not alt_pressed:
                x_dots = self._snap_dots(x_dots, dpi)
                y_dots = self._snap_dots(y_dots, dpi)

            w_dots, h_dots, _ = self._label_size_dots()
            pad_dots = self._get_safe_padding_dots(dpi)

            x_dots = max(pad_dots, min(w_dots - pad_dots - 24, x_dots))
            y_dots = max(pad_dots, min(h_dots - pad_dots - 16, y_dots))

            self._live_drag_pos = (tab_key, row_idx, x_dots, y_dots)
            self.update()
            return

        # Наведение курсора на маркеры изменения размера
        for rect, _tab_key, _row_idx, _name, kind in reversed(self._field_rects):
            if kind.startswith("handle:"):
                handle_id = kind.split(":", 1)[1]
                if rect.contains(pos):
                    self.setCursor(self._get_handle_cursor(handle_id))
                    return

        # Наведение на элементы
        for rect, _tab_key, _row_idx, _name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                self.setCursor(Qt.OpenHandCursor)
                return

        self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            tab_key, row_idx, f_name, _hid = self._resizing
            if self._live_resize_rect is not None:
                _, _, x_d, y_d, w_d, h_d = self._live_resize_rect
                _, _, _, dpi, _ = self._get_active_tab_context()
                try:
                    if f_name == "dataMatrix":
                        sz_mm = round(max(w_d, h_d) / dpi * 25.4, 0)
                        self.parent_admin._update_field_position(tab_key, row_idx, x_d, y_d)
                        self.parent_admin._update_field_size(tab_key, row_idx, max(8.0, sz_mm))
                    else:
                        self.parent_admin._update_field_position(tab_key, row_idx, x_d, y_d)
                        if hasattr(self.parent_admin, "_update_field_dimensions"):
                            self.parent_admin._update_field_dimensions(tab_key, row_idx, w_d, h_d)
                except Exception as e:
                    print("save resize error:", e)

            self._resizing = None
            self._resize_start_pos = None
            self._resize_start_dots_rect = None
            self._live_resize_rect = None
            self._notify_selection_changed()

        if self._dragging:
            if self._live_drag_pos is not None:
                tab_key, row_idx, x, y = self._live_drag_pos
                try:
                    self.parent_admin._update_field_position(tab_key, row_idx, x, y)
                except Exception as e:
                    print("save pos error:", e)
            self._live_drag_pos = None
            self._dragging = None
            self._notify_selection_changed()

        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mouseDoubleClickEvent(self, event):
        pos = event.position().toPoint()
        _, _, _, dpi, fields = self._get_active_tab_context()
        w_dots, h_dots, _ = self._label_size_dots()
        pad_dots = self._get_safe_padding_dots(dpi)

        for rect, tab_key, row_idx, name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                if row_idx >= len(fields):
                    return
                f = fields[row_idx]
                if kind == "dm":
                    cur_sz = int(round(float(f.get("size") or 19)))
                    default = f"{f.get('x', pad_dots)} {f.get('y', pad_dots)} {cur_sz}"
                    text, ok = QInputDialog.getText(
                        self,
                        "Параметры DataMatrix",
                        "Введите: X  Y  Размер_мм (например: 12 12 19):",
                        text=default
                    )
                    if not ok or not text.strip():
                        return
                    try:
                        parts = text.strip().split()
                        x = max(pad_dots, min(w_dots - 40, int(parts[0])))
                        y = max(pad_dots, min(h_dots - 40, int(parts[1]))) if len(parts) > 1 else pad_dots
                        self.parent_admin._update_field_position(tab_key, row_idx, x, y)
                        if len(parts) > 2:
                            sz_mm = max(8.0, min(80.0, float(parts[2])))
                            self.parent_admin._update_field_size(tab_key, row_idx, sz_mm)
                        self._notify_selection_changed()
                        self.update()
                    except Exception:
                        pass
                    return
                else:
                    cur_f = int(f.get("font_size") or 10)
                    default = f"{f.get('x', pad_dots)} {f.get('y', pad_dots)} {cur_f}"
                    text, ok = QInputDialog.getText(
                        self,
                        f"Параметры поля «{name}»",
                        "Введите: X  Y  Размер_шрифта_pt (например: 160 12 10):",
                        text=default
                    )
                    if not ok or not text.strip():
                        return
                    try:
                        parts = text.strip().split()
                        x = max(pad_dots, min(w_dots - 40, int(parts[0])))
                        y = max(pad_dots, min(h_dots - 20, int(parts[1]))) if len(parts) > 1 else pad_dots
                        self.parent_admin._update_field_position(tab_key, row_idx, x, y)
                        if len(parts) > 2:
                            f_sz = max(6, min(36, int(parts[2])))
                            self.parent_admin._update_field_font(tab_key, row_idx, f_sz)
                            f["font_size"] = f_sz
                        self._notify_selection_changed()
                        self.update()
                    except Exception:
                        pass
                    return

    def contextMenuEvent(self, event):
        """Контекстное меню по правому клику для настройки шрифта, стиля, полей, сетки и линеек."""
        menu = QMenu(self)

        if self._selected:
            _tk, _r = self._selected
            _, _, _, _, _f_all = self._get_active_tab_context()
            if 0 <= _r < len(_f_all) and _f_all[_r].get("name") != "dataMatrix":
                _f_data = _f_all[_r]
                cur_pt = int(_f_data.get("font_size") or 10)
                cur_ls = float(_f_data.get("line_spacing") or 1.15)
                f_name = str(_f_data.get("label") or _f_data.get("name") or "Поле")

                m_font = menu.addMenu(f"🔤 Поле «{f_name}» ({cur_pt} pt)")
                act_inc = m_font.addAction("➕ Увеличить шрифт (+1 pt) [Колёсико вверх / +]")
                act_inc.triggered.connect(lambda: self.change_selected_font_size(+1))

                act_dec = m_font.addAction("➖ Уменьшить шрифт (-1 pt) [Колёсико вниз / -]")
                act_dec.triggered.connect(lambda: self.change_selected_font_size(-1))

                m_font.addSeparator()
                act_ls_inc = m_font.addAction(f"↕ Увеличить межстрочный интервал (+0.1) [текущий: {cur_ls:.2f}]")
                act_ls_inc.triggered.connect(lambda: self.change_selected_line_spacing(+0.1))

                act_ls_dec = m_font.addAction(f"↕ Уменьшить межстрочный интервал (-0.1)")
                act_ls_dec.triggered.connect(lambda: self.change_selected_line_spacing(-0.1))

                m_font.addSeparator()
                act_bold = m_font.addAction("🅱 Жирный шрифт (Bold)")
                act_bold.setCheckable(True)
                act_bold.setChecked(bool(_f_data.get("bold", False)))
                act_bold.triggered.connect(self.toggle_selected_bold)

                act_italic = m_font.addAction("𝑰 Курсив (Italic)")
                act_italic.setCheckable(True)
                act_italic.setChecked(bool(_f_data.get("italic", False)))
                act_italic.triggered.connect(self.toggle_selected_italic)

                act_lbl = m_font.addAction("✏ Изменить подпись поля...")
                act_lbl.triggered.connect(self.edit_selected_field_label)

                m_font.addSeparator()
                act_del = m_font.addAction("🗑 Удалить это поле с этикетки")
                act_del.triggered.connect(self.delete_selected_field)

                menu.addSeparator()

        if self.parent_admin and hasattr(self.parent_admin, "_tpl_add_field_active_tab"):
            act_add_f = menu.addAction("➕ Добавить поле на этикетку...")
            act_add_f.triggered.connect(self.parent_admin._tpl_add_field_active_tab)
            menu.addSeparator()

        is_lt = _is_light_theme()
        m_bg = "#ffffff" if is_lt else "#1e293b"
        m_fg = "#0f172a" if is_lt else "#f1f5f9"
        m_bd = "#cbd5e1" if is_lt else "#334155"
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {m_bg};
                color: {m_fg};
                border: 1px solid {m_bd};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 24px 6px 12px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: #2563eb;
                color: #ffffff;
            }}
        """)

        # 1. Шаг сетки (Snap-to-Grid) с реальной перерисовкой
        m_grid = menu.addMenu("📐 Шаг сетки (Snap-to-Grid)")
        act_1mm = m_grid.addAction("1 мм (по умолчанию)")
        act_1mm.setCheckable(True)
        act_1mm.setChecked(self.snap_to_grid and abs(self.grid_step_mm - 1.0) < 0.1)
        act_1mm.triggered.connect(lambda: self.set_grid_step(1.0))

        act_2mm = m_grid.addAction("2 мм")
        act_2mm.setCheckable(True)
        act_2mm.setChecked(self.snap_to_grid and abs(self.grid_step_mm - 2.0) < 0.1)
        act_2mm.triggered.connect(lambda: self.set_grid_step(2.0))

        act_5mm = m_grid.addAction("5 мм")
        act_5mm.setCheckable(True)
        act_5mm.setChecked(self.snap_to_grid and abs(self.grid_step_mm - 5.0) < 0.1)
        act_5mm.triggered.connect(lambda: self.set_grid_step(5.0))

        act_off = m_grid.addAction("Отключить сетку и прилипание (Alt)")
        act_off.setCheckable(True)
        act_off.setChecked(not self.snap_to_grid or self.grid_step_mm <= 0)
        act_off.triggered.connect(lambda: self.set_grid_step(0.0))

        # 2. Переключатели отображения
        act_toggle_grid = menu.addAction("Отображать сетку")
        act_toggle_grid.setCheckable(True)
        act_toggle_grid.setChecked(self.show_grid)
        act_toggle_grid.triggered.connect(self.toggle_grid)

        act_sm = menu.addAction("Зона безопасности (1.5 мм от края)")
        act_sm.setCheckable(True)
        act_sm.setChecked(self.show_safety_margin)
        act_sm.triggered.connect(self.toggle_safety_margin)

        act_ruler = menu.addAction("Миллиметровая линейка")
        act_ruler.setCheckable(True)
        act_ruler.setChecked(self.show_ruler)
        act_ruler.triggered.connect(self.toggle_ruler)

        menu.addSeparator()

        # 3. Тестовый образец наименования
        m_samples = menu.addMenu("📦 Тестовый образец товара")
        for i, preset in enumerate(SAMPLE_PRESETS):
            act_p = m_samples.addAction(preset["title"])
            act_p.setCheckable(True)
            act_p.setChecked(self.current_sample_idx == i)
            act_p.triggered.connect(lambda _=False, idx=i: self.set_sample_preset(idx))

        menu.exec(event.globalPos())