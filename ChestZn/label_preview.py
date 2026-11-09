"""
Интерактивный предпросмотр этикетки (label_preview.py).
- Адаптивный фон холста (светлый/тёмный).
- Строгий клиппинг по холсту (ни один пиксель не выходит за белое поле).
- Корректная пропорция шрифта и координат относительно DPI принтера.
- Поддержка перетаскивания (Drag & Drop) и изменения размера DataMatrix.
"""
import re
from typing import Optional, Dict, Any, List, Tuple
from PySide6.QtWidgets import QWidget, QInputDialog, QApplication
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QFontMetrics, QPixmap
from PySide6.QtCore import Qt, QRect, QPoint

try:
    from barcode import render_datamatrix
except ImportError:
    from ChestZn.barcode import render_datamatrix

DEFAULT_REALISTIC_CZ_CODE = (
    "010460700123456721A1B2C3D4E5F6G\x1d91EE06\x1d92dGVzdGNyeXB0b3RhaWx2ZXJ5bG9uZ3N0cmluZ2hlcmU="
)

SAMPLE_FIELD_VALUES = {
    "gtin": "04607001234567",
    "shk": "4607001234567",
    "serial": "SN00012345",
    "sn": "SN00012345",
    "name": "Наименование товара",
    "name_ru": "Наименование товара",
    "model": "Модель / Артикул",
    "lot": "Партия 01",
    "exp": "12.2027",
    "date": "01.11.2026",
    "country": "RU",
    "importer": "ООО «Импортёр-Сервис»",
}


class LabelPreviewWidget(QWidget):
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
        self._selected: Optional[Tuple[str, int]] = None
        self._dragging: Optional[Tuple[str, int, str]] = None
        self._drag_offset: Optional[QPoint] = None
        self._resizing: Optional[Tuple[str, int, str]] = None

        self._live_drag_pos: Optional[Tuple[str, int, int, int]] = None
        self._live_resize_size_dots: Optional[int] = None

        self.setMouseTracking(True)
        self.setMinimumSize(260, 260)

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
                    self._apply_dm_code(str(row_any[0]), gtin=str(row_any[1] or ""), sn=str(row_any[2] or ""))
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
        if self._demo_code:
            return self._demo_code
        res = self.set_shk_for_preview("")
        if res.get("ok") and self._demo_code:
            return self._demo_code
        return DEFAULT_REALISTIC_CZ_CODE

    def _get_fast_dm_pixmap(self, target_px: int) -> QPixmap:
        code = self._resolve_active_code()
        if self._cached_base_pixmap is None or self._cached_base_code != code:
            self._cached_base_code = code
            self._cached_base_pixmap = render_datamatrix(code, size=360)

        if self._cached_base_pixmap is None or self._cached_base_pixmap.isNull():
            return QPixmap()

        sz = max(16, int(target_px))
        return self._cached_base_pixmap.scaled(sz, sz, Qt.KeepAspectRatio, Qt.FastTransformation)

    def _get_active_tab_context(self) -> Tuple[str, float, float, int, List[Dict[str, Any]]]:
        if (
            self.parent_admin is not None
            and hasattr(self.parent_admin, "tpl_tabs")
            and hasattr(self.parent_admin, "_tpl_tab_widgets")
        ):
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
            fields = self._tpl.get("fields", [])
            return "combined", w_mm, h_mm, dpi, fields

        return "", 58.0, 40.0, 203, []

    def _label_size_dots(self) -> Tuple[int, int, int]:
        _, w_mm, h_mm, dpi, _ = self._get_active_tab_context()
        w_dots = max(40, int(round(w_mm / 25.4 * dpi)))
        h_dots = max(40, int(round(h_mm / 25.4 * dpi)))
        return w_dots, h_dots, dpi

    def _get_scale_and_offset(self) -> Tuple[float, int, int, int, int]:
        w_dots, h_dots, _ = self._label_size_dots()
        margin = 16
        avail_w = max(10, self.width() - 2 * margin)
        avail_h = max(10, self.height() - 2 * margin)

        scale = min(avail_w / w_dots, avail_h / h_dots)
        disp_w = int(w_dots * scale)
        disp_h = int(h_dots * scale)

        offset_x = (self.width() - disp_w) // 2
        offset_y = (self.height() - disp_h) // 2
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        app = QApplication.instance()
        is_light = "CLEAN SLATE" in (app.styleSheet() if app else "")
        bg_color = QColor("#f1f5f9") if is_light else QColor("#10151c")
        painter.fillRect(self.rect(), bg_color)

        tab_key, w_mm, h_mm, dpi, fields = self._get_active_tab_context()
        if not tab_key and not fields:
            painter.setPen(QPen(QColor("#64748b" if is_light else "#8b97a6")))
            painter.drawText(self.rect(), Qt.AlignCenter, "Выберите шаблон")
            return

        scale, off_x, off_y, w_dots, h_dots = self._get_scale_and_offset()
        disp_w = int(w_dots * scale)
        disp_h = int(h_dots * scale)

        label_rect = QRect(off_x, off_y, disp_w, disp_h)
        painter.fillRect(label_rect, Qt.white)

        step_5mm_px = (5.0 / 25.4) * dpi * scale
        if step_5mm_px >= 10:
            grid_pen = QColor("#e2e8f0" if is_light else "#2a3542")
            painter.setPen(QPen(grid_pen, 1, Qt.SolidLine))
            gx = off_x + step_5mm_px
            while gx < off_x + disp_w:
                painter.drawLine(int(gx), off_y, int(gx), off_y + disp_h)
                gx += step_5mm_px
            gy = off_y + step_5mm_px
            while gy < off_y + disp_h:
                painter.drawLine(off_x, int(gy), off_x + disp_w, int(gy))
                gy += step_5mm_px

        self._field_rects.clear()

        # Строгий клиппинг по белому холсту
        painter.setClipRect(label_rect)

        for row_idx, f in enumerate(fields):
            if f.get("name") == "dataMatrix":
                self._draw_single_dm(painter, tab_key, row_idx, f, scale, off_x, off_y, dpi)

        for row_idx, f in enumerate(fields):
            if f.get("name") != "dataMatrix":
                self._draw_single_text(painter, tab_key, row_idx, f, scale, off_x, off_y, dpi)

        painter.setClipping(False)

        painter.setPen(QPen(QColor("#cbd5e1" if is_light else "#334155"), 2))
        painter.drawRect(label_rect)

    def _draw_single_dm(self, painter, tab_key, row_idx, f, scale, off_x, off_y, dpi):
        x_dots = int(f.get("x", 16))
        y_dots = int(f.get("y", 16))
        size_mm = float(f.get("size") or 20.0)
        size_dots = max(20, int(round(size_mm / 25.4 * dpi)))

        if self._live_drag_pos and self._live_drag_pos[0] == tab_key and self._live_drag_pos[1] == row_idx:
            x_dots, y_dots = self._live_drag_pos[2], self._live_drag_pos[3]

        if (
            self._live_resize_size_dots is not None
            and self._resizing
            and self._resizing[0] == tab_key
            and self._resizing[1] == row_idx
        ):
            size_dots = self._live_resize_size_dots

        sx = int(round(off_x + x_dots * scale))
        sy = int(round(off_y + y_dots * scale))
        ssz = max(18, int(round(size_dots * scale)))

        rect = QRect(sx, sy, ssz, ssz)
        self._field_rects.append((rect, tab_key, row_idx, "dataMatrix", "dm"))

        pix = self._get_fast_dm_pixmap(ssz)
        if not pix.isNull():
            painter.drawPixmap(rect, pix)
        else:
            painter.setPen(QPen(QColor("#dc2626"), 1, Qt.DashLine))
            painter.drawRect(rect)
            painter.drawText(rect, Qt.AlignCenter, "DM")

        is_sel = (self._selected == (tab_key, row_idx))
        if is_sel:
            painter.setPen(QPen(QColor("#2563eb"), 2, Qt.SolidLine))
            painter.drawRect(rect)

            handle_size = 10
            hr = QRect(rect.right() - handle_size + 1, rect.bottom() - handle_size + 1, handle_size, handle_size)
            painter.fillRect(hr, QColor("#2563eb"))
            painter.setPen(QPen(Qt.white, 1))
            painter.drawRect(hr)
            self._field_rects.append((hr, tab_key, row_idx, "dataMatrix", "handle"))

    def _draw_single_text(self, painter, tab_key, row_idx, f, scale, off_x, off_y, dpi):
        x_dots = int(f.get("x", 16))
        y_dots = int(f.get("y", 16))

        if self._live_drag_pos and self._live_drag_pos[0] == tab_key and self._live_drag_pos[1] == row_idx:
            x_dots, y_dots = self._live_drag_pos[2], self._live_drag_pos[3]

        sx = int(round(off_x + x_dots * scale))
        sy = int(round(off_y + y_dots * scale))

        font_size_pt = max(6, int(f.get("font_size") or 10))
        font_px = max(9, int(round(font_size_pt * 1.33 * scale)))
        font = QFont("Arial", font_px)
        font.setBold(bool(f.get("bold", False)))
        painter.setFont(font)

        fname = str(f.get("name") or "").strip()
        flabel = str(f.get("label") or fname or "Поле").strip()

        sample_val = SAMPLE_FIELD_VALUES.get(fname.lower(), flabel)
        if flabel and sample_val and flabel != sample_val and fname.lower() not in ("name", "name_ru"):
            text_str = f"{flabel}: {sample_val}"
        else:
            text_str = sample_val or flabel

        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text_str)
        th = fm.height()

        rect = QRect(sx, sy, tw + 8, th + 4)
        self._field_rects.append((rect, tab_key, row_idx, flabel or fname, "text"))

        is_sel = (self._selected == (tab_key, row_idx))
        if is_sel:
            painter.fillRect(rect, QColor(37, 99, 235, 40))
            painter.setPen(QPen(QColor("#2563eb"), 1, Qt.DashLine))
            painter.drawRect(rect)

        painter.setPen(QPen(QColor("#0f172a")))
        painter.drawText(QRect(sx + 2, sy, tw + 6, th + 2), Qt.AlignLeft | Qt.AlignVCenter, text_str)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()

        for rect, tab_key, row_idx, name, kind in reversed(self._field_rects):
            if kind == "handle" and rect.contains(pos):
                self._resizing = (tab_key, row_idx, name)
                self._selected = (tab_key, row_idx)
                self._live_resize_size_dots = None
                self.setCursor(Qt.SizeFDiagCursor)
                self.update()
                return

        for rect, tab_key, row_idx, name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                self._dragging = (tab_key, row_idx, name)
                self._drag_offset = pos - rect.topLeft()
                self._selected = (tab_key, row_idx)
                self._live_drag_pos = None
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                return

        self._selected = None
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        w_dots, h_dots, _ = self._label_size_dots()

        if self._resizing:
            tab_key, row_idx, _ = self._resizing
            _, _, _, dpi, fields = self._get_active_tab_context()
            if row_idx < len(fields):
                f = fields[row_idx]
                start_tl = self._dots_to_screen(f.get("x", 0), f.get("y", 0))
                new_size_px = max(20, max(pos.x() - start_tl.x(), pos.y() - start_tl.y()))
                new_size_dots = self._px_to_dots(new_size_px)
                max_allowed = min(w_dots - int(f.get("x", 0)), h_dots - int(f.get("y", 0)))
                self._live_resize_size_dots = max(24, min(max_allowed, new_size_dots))
                self.update()
            return

        if self._dragging:
            new_tl = pos - self._drag_offset
            x_dots, y_dots = self._screen_to_dots(new_tl.x(), new_tl.y())
            tab_key, row_idx, _ = self._dragging

            x_dots = max(0, min(max(0, w_dots - 10), x_dots))
            y_dots = max(0, min(max(0, h_dots - 10), y_dots))

            self._live_drag_pos = (tab_key, row_idx, x_dots, y_dots)
            self.update()
            return

        for rect, _, _, _, kind in reversed(self._field_rects):
            if kind == "handle" and rect.contains(pos):
                self.setCursor(Qt.SizeFDiagCursor)
                return
            if kind in ("dm", "text") and rect.contains(pos):
                self.setCursor(Qt.OpenHandCursor)
                return
        self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            tab_key, row_idx, _ = self._resizing
            if self._live_resize_size_dots is not None:
                _, _, dpi = self._label_size_dots()
                size_mm = round(self._live_resize_size_dots / dpi * 25.4, 0)
                try:
                    self.parent_admin._update_field_size(tab_key, row_idx, max(10.0, size_mm))
                except Exception:
                    pass
            self._live_resize_size_dots = None
            self._resizing = None

        if self._dragging:
            if self._live_drag_pos is not None:
                tab_key, row_idx, x, y = self._live_drag_pos
                try:
                    self.parent_admin._update_field_position(tab_key, row_idx, x, y)
                except Exception:
                    pass
            self._live_drag_pos = None
            self._dragging = None

        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mouseDoubleClickEvent(self, event):
        pos = event.position().toPoint()
        _, _, _, _, fields = self._get_active_tab_context()
        for rect, tab_key, row_idx, name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                if row_idx < len(fields):
                    f = fields[row_idx]
                    text, ok = QInputDialog.getText(
                        self,
                        f"Координаты поля «{name}»",
                        "Введите X и Y через пробел (в точках):",
                        text=f"{f.get('x', 0)} {f.get('y', 0)}",
                    )
                    if ok and text.strip():
                        try:
                            parts = text.strip().split()
                            x = max(0, int(parts[0]))
                            y = max(0, int(parts[1])) if len(parts) > 1 else 0
                            self.parent_admin._update_field_position(tab_key, row_idx, x, y)
                            self.update()
                        except Exception:
                            pass
                return