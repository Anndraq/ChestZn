"""
Интерактивный предпросмотр этикетки.
Оптимизировано: кэш DataMatrix + запись в БД только на mouseRelease.
"""
from PySide6.QtWidgets import (
    QWidget, QSizePolicy, QInputDialog, QMenu
)
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QAction


RESIZE_HANDLE_SIZE = 12
DM_CACHE_SIZE = 400          # рендерим один раз в этом размере
KEY_STEP_DOTS = 1
KEY_STEP_DOTS_SHIFT = 10


class LabelPreviewWidget(QWidget):
    def __init__(self, parent_admin, parent=None):
        super().__init__(parent)
        self.parent_admin = parent_admin
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor)

        self._dragging = None          # (tab_key, idx, name)
        self._drag_offset = QPoint(0, 0)
        self._live_drag_pos = None     # (tab_key, idx, x_dots, y_dots)

        self._resizing = None          # (tab_key, idx, name)
        self._live_resize_size = None  # размер в точках

        self._selected = None          # (tab_key, idx)
        self._dm_pixmap = None         # QPixmap 400×400
        self._field_rects = []

    # ==========================================================
    # Параметры этикетки
    # ==========================================================
    def _current_tab_key(self):
        try:
            idx = self.parent_admin.tpl_tabs.currentIndex()
            return ["combined", "cz", "label"][idx]
        except Exception:
            return "combined"

    def _tab_widgets(self, tab_key=None):
        if tab_key is None:
            tab_key = self._current_tab_key()
        try:
            return tab_key, self.parent_admin._tpl_tab_widgets.get(tab_key)
        except Exception:
            return tab_key, None

    def _label_size_dots(self):
        tab_key, widgets = self._tab_widgets()
        if not widgets:
            return 240, 240, 203
        w_mm = widgets["w"].value()
        h_mm = widgets["h"].value()
        dpi = int(widgets["dpi"].currentText())
        w_dots = int(round(w_mm / 25.4 * dpi))
        h_dots = int(round(h_mm / 25.4 * dpi))
        return max(50, w_dots), max(50, h_dots), dpi

    def _preview_scale(self):
        w_dots, h_dots, _ = self._label_size_dots()
        margin = 24
        avail_w = max(60, self.width() - margin * 2)
        avail_h = max(60, self.height() - margin * 2)
        return min(avail_w / w_dots, avail_h / h_dots)

    def _label_rect(self):
        w_dots, h_dots, _ = self._label_size_dots()
        scale = self._preview_scale()
        w_px = int(w_dots * scale)
        h_px = int(h_dots * scale)
        x = (self.width() - w_px) // 2
        y = (self.height() - h_px) // 2
        return QRect(x, y, w_px, h_px)

    def _dots_to_screen(self, x_dots, y_dots):
        rect = self._label_rect()
        scale = self._preview_scale()
        return QPoint(int(rect.x() + x_dots * scale),
                      int(rect.y() + y_dots * scale))

    def _screen_to_dots(self, x_px, y_px):
        rect = self._label_rect()
        scale = self._preview_scale()
        if scale <= 0:
            return 0, 0
        x_dots = int(round((x_px - rect.x()) / scale))
        y_dots = int(round((y_px - rect.y()) / scale))
        w_dots, h_dots, _ = self._label_size_dots()
        x_dots = max(0, min(w_dots, x_dots))
        y_dots = max(0, min(h_dots, y_dots))
        return x_dots, y_dots

    def _px_to_dots(self, px):
        scale = self._preview_scale()
        if scale <= 0:
            return 0
        return int(round(px / scale))

    # ==========================================================
    # Кэш DataMatrix — один раз 400×400, потом масштабируем
    # ==========================================================
    def _get_dm_pixmap(self):
        if self._dm_pixmap is not None:
            return self._dm_pixmap
        try:
            from barcode import render_datamatrix
            sample = ("01" + "04600123456782" + "21" + "SN0004821" +
                      "\x1d91ABCD\x1d92XYZ12345")
            self._dm_pixmap = render_datamatrix(sample, DM_CACHE_SIZE)
            return self._dm_pixmap
        except Exception as e:
            print("dm render:", e)
            return None

    # ==========================================================
    # Поля / значения
    # ==========================================================
    def _collect_fields(self, tab_key):
        try:
            return self.parent_admin._collect_fields_from_table(tab_key)
        except Exception:
            return []

    def _get_sample_value(self, name):
        samples = {
            "name_ru": "Наушники BT-500",
            "model": "BT-500",
            "importer": "ООО «Ромашка»",
            "exp_date": "12.2026",
            "qty": "1 шт.",
            "material": "Кожа нат.",
            "size_eu": "42",
            "composition": "Хлопок 100%",
            "country": "Китай",
            "lot": "LOT-2025-04",
            "serial": "SN0004821",
            "gtin": "04600123456782",
            "manufacturer": "Shenzhen",
            "batch": "B-2025-04",
        }
        return samples.get(name, f"[{name}]")

    def _dm_size_dots(self, field, widgets):
        """Размер DataMatrix в точках — учитывает live resize."""
        if self._live_resize_size is not None:
            return self._live_resize_size

        size_mm = field.get("size")
        dpi = int(widgets["dpi"].currentText())
        if size_mm:
            return int(round(float(size_mm) / 25.4 * dpi))
        w_mm = widgets["w"].value()
        h_mm = widgets["h"].value()
        return int(round(min(w_mm, h_mm) * 0.5 / 25.4 * dpi))

    # ==========================================================
    # Отрисовка
    # ==========================================================
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        painter.fillRect(self.rect(), QColor("#1c2530"))

        rect = self._label_rect()
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#3a4a5c"), 1))
        painter.drawRect(rect)

        self._draw_grid(painter, rect)

        tab_key, widgets = self._tab_widgets()
        if not widgets:
            self._draw_hint(painter)
            return

        fields = self._collect_fields(tab_key)
        scale = self._preview_scale()

        self._field_rects = []

        for idx, f in enumerate(fields):
            name = f.get("name", "")
            label = f.get("label", "") or ""
            font_size = f.get("font_size", 12) or 11
            x_dots = f.get("x", 0)
            y_dots = f.get("y", 0)

            # Применяем live-состояние
            if (self._live_drag_pos and
                    self._live_drag_pos[0] == tab_key and
                    self._live_drag_pos[1] == idx):
                x_dots = self._live_drag_pos[2]
                y_dots = self._live_drag_pos[3]

            top_left = self._dots_to_screen(x_dots, y_dots)

            if name == "dataMatrix":
                dm_size_dots = self._dm_size_dots(f, widgets)
                dm_size_px = max(30, int(dm_size_dots * scale))

                pix = self._get_dm_pixmap()
                dm_rect = QRect(top_left.x(), top_left.y(),
                                dm_size_px, dm_size_px)

                if pix and not pix.isNull():
                    painter.drawPixmap(dm_rect, pix)
                else:
                    painter.fillRect(dm_rect, QColor("#000000"))

                self._field_rects.append((dm_rect, tab_key, idx, name, "dm"))

                handle = QRect(dm_rect.right() - RESIZE_HANDLE_SIZE + 1,
                               dm_rect.bottom() - RESIZE_HANDLE_SIZE + 1,
                               RESIZE_HANDLE_SIZE, RESIZE_HANDLE_SIZE)
                self._field_rects.append(
                    (handle, tab_key, idx, name, "handle")
                )

                if self._selected == (tab_key, idx):
                    painter.setPen(QPen(QColor("#3b82f6"), 2, Qt.DashLine))
                    painter.drawRect(dm_rect)
                    painter.fillRect(handle, QColor("#3b82f6"))
                    painter.setPen(QPen(QColor("#ffffff"), 1))
                    painter.drawRect(handle)
                else:
                    painter.setPen(QPen(QColor("#9ca3af"), 1, Qt.DotLine))
                    painter.drawRect(dm_rect)
            else:
                sample = self._get_sample_value(name)
                text = f"{label}: {sample}" if label else sample

                font = QFont("Consolas")
                font.setPixelSize(max(8, int(font_size * scale * 3)))
                painter.setFont(font)

                fm = painter.fontMetrics()
                text_w = fm.horizontalAdvance(text)
                text_h = fm.height()
                text_rect = QRect(top_left.x(), top_left.y(),
                                  text_w, text_h)

                painter.setPen(QColor("#000000"))
                painter.drawText(text_rect,
                                 Qt.AlignLeft | Qt.AlignTop, text)

                self._field_rects.append(
                    (text_rect, tab_key, idx, name, "text")
                )

                if self._selected == (tab_key, idx):
                    painter.setPen(QPen(QColor("#3b82f6"), 2, Qt.DashLine))
                    painter.drawRect(text_rect)

        self._draw_hint(painter)

    def _draw_grid(self, painter, rect):
        try:
            _, _, dpi = self._label_size_dots()
        except Exception:
            return
        scale = self._preview_scale()
        step_dots = 5 / 25.4 * dpi
        step_px = step_dots * scale
        if step_px < 8:
            return

        painter.setPen(QPen(QColor("#e5e7eb"), 1, Qt.DotLine))
        x = rect.x() + step_px
        while x < rect.right():
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())
            x += step_px
        y = rect.y() + step_px
        while y < rect.bottom():
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
            y += step_px

    def _draw_hint(self, painter):
        painter.setPen(QColor("#8b97a6"))
        f = QFont()
        f.setPixelSize(10)
        painter.setFont(f)
        hint = ("Перетаскивание: ЛКМ · Размер: уголок · Меню: ПКМ · "
                "Стрелки: сдвиг")
        painter.drawText(0, self.height() - 16, self.width(), 16,
                         Qt.AlignCenter, hint)

    # ==========================================================
    # Мышь
    # ==========================================================
    def mousePressEvent(self, event):
        self.setFocus()

        if event.button() == Qt.RightButton:
            self._show_context_menu(event.position().toPoint())
            return

        if event.button() != Qt.LeftButton:
            return

        pos = event.position().toPoint()

        # Уголок ресайза — приоритет
        for rect, tab_key, idx, name, kind in reversed(self._field_rects):
            if kind == "handle" and rect.contains(pos):
                self._selected = (tab_key, idx)
                self._resizing = (tab_key, idx, name)
                self._live_resize_size = None

                # Инициализируем текущим размером
                fields = self._collect_fields(tab_key)
                _, widgets = self._tab_widgets(tab_key)
                if idx < len(fields) and widgets:
                    self._live_resize_size = self._dm_size_dots(
                        fields[idx], widgets
                    )

                self.setCursor(Qt.SizeFDiagCursor)
                self.update()
                return

        # Клик по элементу
        for rect, tab_key, idx, name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                self._dragging = (tab_key, idx, name)
                self._drag_offset = pos - rect.topLeft()
                self._selected = (tab_key, idx)
                self._live_drag_pos = None
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                return

        self._selected = None
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()

        # === Ресайз ===
        if self._resizing:
            tab_key, idx, name = self._resizing
            fields = self._collect_fields(tab_key)
            if idx >= len(fields):
                return
            f = fields[idx]

            start_tl = self._dots_to_screen(f.get("x", 0), f.get("y", 0))

            new_w_px = pos.x() - start_tl.x()
            new_h_px = pos.y() - start_tl.y()

            new_size_px = max(new_w_px, new_h_px, 20)
            new_size_dots = self._px_to_dots(new_size_px)

            w_dots, h_dots, _ = self._label_size_dots()

            # ЗАЩИТА: размер не должен выходить за правый/нижний край этикетки
            x_dots = f.get("x", 0)
            y_dots = f.get("y", 0)
            max_dots_x = w_dots - x_dots
            max_dots_y = h_dots - y_dots
            max_allowed = min(max_dots_x, max_dots_y)

            new_size_dots = max(15, min(max_allowed, new_size_dots))

            self._live_resize_size = new_size_dots
            self.update()
            return

        # === Перетаскивание ===
        if self._dragging:
            new_tl = pos - self._drag_offset
            x_dots, y_dots = self._screen_to_dots(
                new_tl.x(), new_tl.y()
            )
            tab_key, idx, name = self._dragging

            # ЗАЩИТА: не даём вылезти за правый/нижний край
            w_dots, h_dots, _ = self._label_size_dots()
            el_w, el_h = self._get_element_size_dots(tab_key, idx, name)

            max_x = max(0, w_dots - el_w)
            max_y = max(0, h_dots - el_h)

            x_dots = max(0, min(max_x, x_dots))
            y_dots = max(0, min(max_y, y_dots))

            self._live_drag_pos = (tab_key, idx, x_dots, y_dots)
            self.update()
            return

        # === Hover ===
        for rect, tab_key, idx, name, kind in reversed(self._field_rects):
            if kind == "handle" and rect.contains(pos):
                self.setCursor(Qt.SizeFDiagCursor)
                return
        for rect, tab_key, idx, name, kind in self._field_rects:
            if kind in ("dm", "text") and rect.contains(pos):
                self.setCursor(Qt.OpenHandCursor)
                return
        self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        # === Завершение ресайза ===
        if self._resizing:
            tab_key, idx, name = self._resizing
            if self._live_resize_size is not None:
                # Конвертируем точки в мм
                _, _, dpi = self._label_size_dots()
                size_mm = self._live_resize_size / dpi * 25.4
                try:
                    self.parent_admin._update_field_size(
                        tab_key, idx, round(size_mm, 1)
                    )
                except Exception as e:
                    print("save size:", e)
            self._live_resize_size = None
            self._resizing = None

        # === Завершение перетаскивания ===
        if self._dragging:
            if self._live_drag_pos is not None:
                tab_key, idx, x, y = self._live_drag_pos
                try:
                    self.parent_admin._update_field_position(
                        tab_key, idx, x, y
                    )
                except Exception as e:
                    print("save pos:", e)
            self._live_drag_pos = None
            self._dragging = None

        self.setCursor(Qt.ArrowCursor)
        try:
            self.parent_admin._tpl_refresh_preview()
        except Exception:
            pass
        self.update()

    def mouseDoubleClickEvent(self, event):
        pos = event.position().toPoint()
        for rect, tab_key, idx, name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                fields = self._collect_fields(tab_key)
                if idx >= len(fields):
                    return
                f = fields[idx]
                default = f"{f.get('x', 0)} {f.get('y', 0)}"
                text, ok = QInputDialog.getText(
                    self,
                    f"Координаты поля «{name}»",
                    "Введите X и Y (в точках принтера):",
                    text=default
                )
                if not ok or not text.strip():
                    return
                try:
                    parts = text.strip().split()
                    x = int(parts[0])
                    y = int(parts[1]) if len(parts) > 1 else 0
                    self.parent_admin._update_field_position(
                        tab_key, idx, x, y
                    )
                    self.update()
                except Exception:
                    pass
                return

    # ==========================================================
    # Клавиатура
    # ==========================================================
    def keyPressEvent(self, event):
        if not self._selected:
            super().keyPressEvent(event)
            return

        tab_key, idx = self._selected
        fields = self._collect_fields(tab_key)
        if idx >= len(fields):
            super().keyPressEvent(event)
            return

        f = fields[idx]
        x = f.get("x", 0)
        y = f.get("y", 0)

        step = KEY_STEP_DOTS
        if event.modifiers() & Qt.ShiftModifier:
            step = KEY_STEP_DOTS_SHIFT

        key = event.key()
        if key == Qt.Key_Left:
            x -= step
        elif key == Qt.Key_Right:
            x += step
        elif key == Qt.Key_Up:
            y -= step
        elif key == Qt.Key_Down:
            y += step
        else:
            super().keyPressEvent(event)
            return

        x = max(0, x)
        y = max(0, y)

        try:
            self.parent_admin._update_field_position(tab_key, idx, x, y)
        except Exception:
            pass
        self.update()

    # ==========================================================
    # Контекстное меню
    # ==========================================================
    def _show_context_menu(self, pos):
        target = None
        for rect, tab_key, idx, name, kind in reversed(self._field_rects):
            if kind in ("dm", "text") and rect.contains(pos):
                target = (tab_key, idx, name, rect, kind)
                break

        menu = QMenu(self)

        if target:
            tab_key, idx, name, rect, kind = target
            self._selected = (tab_key, idx)
            self.update()

            act_center_h = QAction("↔ Центр по горизонтали", self)
            act_center_h.triggered.connect(
                lambda: self._align_center_h(tab_key, idx, name)
            )
            menu.addAction(act_center_h)

            act_center_v = QAction("↕ Центр по вертикали", self)
            act_center_v.triggered.connect(
                lambda: self._align_center_v(tab_key, idx, name)
            )
            menu.addAction(act_center_v)

            act_center = QAction("⊙ По центру (оба)", self)
            act_center.triggered.connect(
                lambda: self._align_center(tab_key, idx, name)
            )
            menu.addAction(act_center)

            menu.addSeparator()

            for label, fx in [
                ("◤ Левый верхний", self._align_top_left),
                ("◥ Правый верхний", self._align_top_right),
                ("◣ Левый нижний", self._align_bottom_left),
                ("◢ Правый нижний", self._align_bottom_right),
            ]:
                act = QAction(label, self)
                act.triggered.connect(
                    lambda _, f=fx, k=tab_key, i=idx, n=name: f(k, i, n)
                )
                menu.addAction(act)

            menu.addSeparator()

            if name == "dataMatrix":
                act_size = QAction("📐 Изменить размер...", self)
                act_size.triggered.connect(
                    lambda: self._prompt_size(tab_key, idx, name)
                )
                menu.addAction(act_size)

            if kind == "text":
                act_font = QAction("🔤 Изменить шрифт...", self)
                act_font.triggered.connect(
                    lambda: self._prompt_font(tab_key, idx, name)
                )
                menu.addAction(act_font)

            menu.addSeparator()

            act_del = QAction("🗑 Удалить поле", self)
            act_del.triggered.connect(
                lambda: self._delete_field(tab_key, idx)
            )
            menu.addAction(act_del)
        else:
            act_clear = QAction("Снять выделение", self)
            act_clear.triggered.connect(self._clear_selection)
            menu.addAction(act_clear)

        menu.exec(self.mapToGlobal(pos))

    def _clear_selection(self):
        self._selected = None
        self.update()

    # ==========================================================
    # Выравнивание
    # ==========================================================
    def _get_element_size_dots(self, tab_key, idx, name):
        _, widgets = self._tab_widgets(tab_key)
        if not widgets:
            return 0, 0

        fields = self._collect_fields(tab_key)
        if idx >= len(fields):
            return 0, 0

        f = fields[idx]
        if name == "dataMatrix":
            size_dots = self._dm_size_dots(f, widgets)
            return size_dots, size_dots
        else:
            sample = self._get_sample_value(name)
            label = f.get("label", "") or ""
            text = f"{label}: {sample}" if label else sample
            font_size = f.get("font_size", 12) or 11
            dpi = int(widgets["dpi"].currentText())

            # Приблизительная ширина/высота в точках принтера
            # 1 pt ≈ 1.33 px, а dpi/72 — это масштаб
            font_dots = int(font_size * dpi / 72.0)
            width_dots = int(len(text) * font_dots * 0.62)
            height_dots = int(font_dots * 1.4)
            return width_dots, height_dots

    def _apply_position(self, tab_key, idx, x, y):
        try:
            self.parent_admin._update_field_position(tab_key, idx, x, y)
        except Exception:
            pass
        self.update()

    def _align_center_h(self, tab_key, idx, name):
        w_dots, _, _ = self._label_size_dots()
        el_w, _ = self._get_element_size_dots(tab_key, idx, name)
        x = max(0, (w_dots - el_w) // 2)
        fields = self._collect_fields(tab_key)
        y = fields[idx].get("y", 0) if idx < len(fields) else 0
        self._apply_position(tab_key, idx, x, y)

    def _align_center_v(self, tab_key, idx, name):
        _, h_dots, _ = self._label_size_dots()
        _, el_h = self._get_element_size_dots(tab_key, idx, name)
        y = max(0, (h_dots - el_h) // 2)
        fields = self._collect_fields(tab_key)
        x = fields[idx].get("x", 0) if idx < len(fields) else 0
        self._apply_position(tab_key, idx, x, y)

    def _align_center(self, tab_key, idx, name):
        w_dots, h_dots, _ = self._label_size_dots()
        el_w, el_h = self._get_element_size_dots(tab_key, idx, name)
        x = max(0, (w_dots - el_w) // 2)
        y = max(0, (h_dots - el_h) // 2)
        self._apply_position(tab_key, idx, x, y)

    def _align_top_left(self, tab_key, idx, name):
        self._apply_position(tab_key, idx, 0, 0)

    def _align_top_right(self, tab_key, idx, name):
        w_dots, _, _ = self._label_size_dots()
        el_w, _ = self._get_element_size_dots(tab_key, idx, name)
        x = max(0, w_dots - el_w)
        fields = self._collect_fields(tab_key)
        y = fields[idx].get("y", 0) if idx < len(fields) else 0
        self._apply_position(tab_key, idx, x, y)

    def _align_bottom_left(self, tab_key, idx, name):
        _, h_dots, _ = self._label_size_dots()
        _, el_h = self._get_element_size_dots(tab_key, idx, name)
        y = max(0, h_dots - el_h)
        fields = self._collect_fields(tab_key)
        x = fields[idx].get("x", 0) if idx < len(fields) else 0
        self._apply_position(tab_key, idx, x, y)

    def _align_bottom_right(self, tab_key, idx, name):
        w_dots, h_dots, _ = self._label_size_dots()
        el_w, el_h = self._get_element_size_dots(tab_key, idx, name)
        x = max(0, w_dots - el_w)
        y = max(0, h_dots - el_h)
        self._apply_position(tab_key, idx, x, y)

    # ==========================================================
    # Диалоги
    # ==========================================================
    def _prompt_size(self, tab_key, idx, name):
        _, widgets = self._tab_widgets(tab_key)
        if not widgets:
            return
        fields = self._collect_fields(tab_key)
        if idx >= len(fields):
            return
        f = fields[idx]
        default_mm = f.get("size")
        if not default_mm:
            w_mm = widgets["w"].value()
            h_mm = widgets["h"].value()
            default_mm = min(w_mm, h_mm) * 0.5

        val, ok = QInputDialog.getDouble(
            self, "Размер DataMatrix", "Размер (мм):",
            value=float(default_mm), minValue=5.0, maxValue=200.0,
            decimals=1
        )
        if not ok:
            return
        try:
            self.parent_admin._update_field_size(tab_key, idx, val)
        except Exception:
            pass
        self.update()

    def _prompt_font(self, tab_key, idx, name):
        fields = self._collect_fields(tab_key)
        if idx >= len(fields):
            return
        f = fields[idx]
        default = int(f.get("font_size", 12) or 12)
        val, ok = QInputDialog.getInt(
            self, "Шрифт", "Размер шрифта (pt):",
            value=default, minValue=6, maxValue=40
        )
        if not ok:
            return
        try:
            self.parent_admin._update_field_font(tab_key, idx, val)
        except Exception:
            pass
        self.update()

    def _delete_field(self, tab_key, idx):
        try:
            self.parent_admin._tpl_remove_field(tab_key, idx)
        except Exception:
            pass
        self._selected = None
        self.update()