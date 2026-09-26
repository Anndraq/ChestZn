"""Админ-панель: боковое меню + QStackedWidget с разделами."""
import re
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QGridLayout, QSizePolicy, QAbstractItemView,
    QLineEdit, QComboBox, QMessageBox, QCheckBox, QSpinBox,
    QDoubleSpinBox, QInputDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from store import Store
from barcode import render_datamatrix
from toast import Toast
from change_password_window import ChangePasswordWindow
from security_question_window import SecurityQuestionWindow
from printer import (list_printers, detect_language, load_config,
                     save_config, print_code, default_printer,
                     get_printer_for_point, set_printer_for_point)
from logger import log_file_path, log_dir_path, get_logger
log = get_logger("admin")
from backup import (create_backup, list_backups, restore_backup,
                    BACKUP_DIR, MAX_BACKUPS)
from export import export_pool, export_queue
from importer import (import_pool_codes, import_gtin_map,
                      download_pool_template, download_gtin_map_template)


def table(headers: list) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    return t


def cell(text, color: Optional[str] = None, mono: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text) if text is not None else "—")
    if color:
        it.setForeground(QBrush(QColor(color)))
    if mono:
        f = it.font(); f.setFamily("Consolas"); it.setFont(f)
    return it


class AdminWindow(QWidget):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.toast = Toast(self)

        from PySide6.QtCore import QTimer
        self._tpl_preview_timer = QTimer(self)
        self._tpl_preview_timer.setSingleShot(True)
        self._tpl_preview_timer.setInterval(300)
        self._tpl_preview_timer.timeout.connect(self._tpl_refresh_preview_now)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ============ SIDEBAR ============
        sidebar = QFrame(); sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(10, 14, 10, 14)
        sb.setSpacing(2)

        def section(text):
            lbl = QLabel(text)
            lbl.setObjectName("navSection")
            sb.addWidget(lbl)

        def nav(text, page_id):
            btn = QPushButton(text)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda: self.switch(page_id))
            btn.setProperty("page_id", page_id)
            sb.addWidget(btn)
            return btn

        section("ОБЗОР")
        self.nav_dashboard = nav("📊  Дашборд", "dashboard")
        self.nav_queue     = nav("🔄  Очередь", "queue")

        section("НАСТРОЙКА")
        self.nav_points       = nav("🏢  Точки", "points")
        self.nav_users        = nav("👥  Пользователи", "users")
        self.nav_templates    = nav("🏷️  Шаблоны", "templates")
        self.nav_pool         = nav("🎫  Пул кодов", "pool")
        self.nav_imports      = nav("📥  Импорты", "imports")
        self.nav_scanned      = nav("🔗  Привязки ШК→ЧЗ", "scanned")
        self.nav_printer      = nav("🖨  Принтеры", "printer")

        section("КОНТРОЛЬ")
        self.nav_journal       = nav("📜  Журнал", "journal")
        self.nav_discrepancies = nav("⚠  Расхождения", "discrepancies")
        self.nav_security      = nav("🔒  Безопасность", "security")
        self.nav_logs          = nav("📄  Логи", "logs")
        self.nav_backup        = nav("💾  Бэкапы", "backup")

        sb.addStretch(1)

        # ============ STACK ============
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_dashboard())      # 0
        self.stack.addWidget(self._page_queue())          # 1
        self.stack.addWidget(self._page_points())         # 2
        self.stack.addWidget(self._page_users())          # 3
        self.stack.addWidget(self._page_templates())      # 4
        self.stack.addWidget(self._page_pool())           # 5
        self.stack.addWidget(self._page_imports())        # 6
        self.stack.addWidget(self._page_scanned())        # 7
        self.stack.addWidget(self._page_printer())        # 8
        self.stack.addWidget(self._page_journal())        # 9
        self.stack.addWidget(self._page_logs())           # 10
        self.stack.addWidget(self._page_backup())         # 11
        self.stack.addWidget(self._page_security())       # 12
        self.stack.addWidget(self._page_discrepancies())  # 13

        root.addWidget(sidebar)
        root.addWidget(self.stack, stretch=1)

        self._journal_page = 0
        self._journal_total = 0

        self.switch("dashboard")

    # ------------------------------------------------------------------
    def switch(self, page_id: str):
        pages = {
            "dashboard": 0, "queue": 1, "points": 2, "users": 3,
            "templates": 4, "pool": 5,
            "imports": 6, "scanned": 7, "printer": 8,
            "journal": 9, "logs": 10, "backup": 11, "security": 12,
            "discrepancies": 13,
        }
        idx = pages.get(page_id, 0)
        self.stack.setCurrentIndex(idx)
        for btn in [self.nav_dashboard, self.nav_queue, self.nav_points,
                    self.nav_users, self.nav_templates,
                    self.nav_pool, self.nav_imports, self.nav_scanned,
                    self.nav_printer, self.nav_journal, self.nav_logs,
                    self.nav_backup, self.nav_security, self.nav_discrepancies]:
            btn.setChecked(btn.property("page_id") == page_id)
        self.refresh(page_id)

    def refresh(self, page_id: str):
        if page_id == "dashboard":      self._refresh_dashboard()
        if page_id == "queue":          self._refresh_queue()
        if page_id == "points":         self._refresh_points()
        if page_id == "users":          self._refresh_users()
        if page_id == "templates":      self._refresh_templates()
        if page_id == "pool":           self._refresh_pool()
        if page_id == "imports":        self._refresh_imports()
        if page_id == "scanned":        self._refresh_scanned()
        if page_id == "printer":        self._refresh_printer()
        if page_id == "journal":        self._refresh_journal()
        if page_id == "logs":           self._refresh_logs()
        if page_id == "security":       self._refresh_security()
        if page_id == "backup":         self._refresh_backup()
        if page_id == "discrepancies":  self._refresh_discrepancies()

    # ==================================================================
    # DASHBOARD
    # ==================================================================
    def _page_dashboard(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        title = QLabel("Дашборд"); title.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(title)
        sub = QLabel("Сводка за смену"); sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        v.addWidget(sub)

        kpi_row = QHBoxLayout(); kpi_row.setSpacing(12)
        self.kpi_labels = {}
        for key, label, color in [
            ("scans",  "Сканов",     "accent"),
            ("prints", "Печатей",    "ok"),
            ("queue",  "В очереди",  "warn"),
            ("errors", "Ошибок",     "err"),
        ]:
            kpi_row.addWidget(self._make_kpi(key, label, color))
        v.addLayout(kpi_row)
                
        return w

    def _make_kpi(self, key: str, label: str, color: str) -> QFrame:
        box = QFrame(); box.setObjectName("kpi")
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 14, 16, 14)
        l = QLabel(label); l.setProperty("class", "kpiLabel")
        l.setStyleSheet("color:#8b97a6; font-size:11px; letter-spacing:.5px;")
        val = QLabel("0")
        val.setProperty("class", "kpiValue")
        colors = {"accent": "#3b82f6", "ok": "#22c55e", "warn": "#f59e0b", "err": "#ef4444"}
        val.setStyleSheet(
            f"color:{colors[color]}; font-size:24px; "
            f"font-weight:600; font-family:Consolas,monospace;"
        )
        v.addWidget(l); v.addWidget(val)
        self.kpi_labels[key] = val
        return box

    def _refresh_dashboard(self):
        c = self.store.counter
        self.kpi_labels["scans"].setText(str(c["scans"]))
        self.kpi_labels["prints"].setText(str(c["prints"]))
        self.kpi_labels["errors"].setText(str(c["errors"]))
        self.kpi_labels["queue"].setText(str(len(self.store.queue)))

    # ==================================================================
    # QUEUE
    # ==================================================================
    def _page_queue(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        h = QHBoxLayout()
        tt = QLabel("Очередь синхронизации")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        h.addWidget(tt)
        h.addStretch(1)

        btn_export = QPushButton("⬇ Экспорт CSV")
        btn_export.clicked.connect(self._export_queue)
        h.addWidget(btn_export)

        btn = QPushButton("🔄 Повторить все")
        btn.setProperty("primary", True)
        btn.clicked.connect(self._retry_all)
        h.addWidget(btn)

        v.addLayout(h)

        self.queue_table = table(["#", "GTIN", "SN", "Точка", "Оператор", "Время"])
        v.addWidget(self.queue_table, stretch=1)
        return w

    def _refresh_queue(self):
        t = self.queue_table
        t.setRowCount(0)
        for i, q in enumerate(self.store.queue):
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(str(i+1), mono=True))
            t.setItem(r, 1, cell(q["gtin"], mono=True))
            t.setItem(r, 2, cell(q["sn"], mono=True))
            t.setItem(r, 3, cell(q["point"]))
            t.setItem(r, 4, cell(q["operator"]))
            t.setItem(r, 5, cell(q["time"], mono=True))

    def _retry_all(self):
        n = len(self.store.queue)
        self.store.clear_queue()
        self.toast.show_message(f"Обработано: {n}", "ok")
        self._refresh_queue()
        self._refresh_dashboard()

    def _export_queue(self):
        result = export_queue(self)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    # ==================================================================
    # POINTS
    # ==================================================================
    def _page_points(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        tt = QLabel("Точки"); tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)
        self.points_table = table(
            ["ID", "Название", "Адрес", "Статус", "IP", "Оператор", "Принтер"]
        )
        v.addWidget(self.points_table, stretch=1)
        return w

    def _refresh_points(self):
        t = self.points_table
        t.setRowCount(0)
        for p in self.store.points:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(p["id"], mono=True))
            t.setItem(r, 1, cell(p["name"]))
            t.setItem(r, 2, cell(p["address"], "#8b97a6"))
            t.setItem(r, 3, cell("онлайн" if p["online"] else "офлайн",
                                 "#22c55e" if p["online"] else "#f59e0b"))
            t.setItem(r, 4, cell(p["ip"], mono=True))
            t.setItem(r, 5, cell(p["operator"]))
            t.setItem(r, 6, cell(p["printer"]))

    # ==================================================================
    # USERS
    # ==================================================================
    def _page_users(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        tt = QLabel("Пользователи"); tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)
        self.users_table = table(
            ["ФИО", "Логин", "Роль", "Точка", "Последний вход", "Статус"]
        )
        v.addWidget(self.users_table, stretch=1)
        return w

    def _refresh_users(self):
        t = self.users_table
        t.setRowCount(0)
        role_color = {"Администратор": "#ef4444", "Старший смены": "#f59e0b",
                      "Оператор": "#93c5fd"}
        for u in self.store.users:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(u["fio"]))
            t.setItem(r, 1, cell(u["login"], mono=True))
            t.setItem(r, 2, cell(u["role"], role_color.get(u["role"], "#e6edf3")))
            t.setItem(r, 3, cell(u["point"]))
            t.setItem(r, 4, cell(u["last_login"] or "—", mono=True))
            t.setItem(r, 5, cell("активен" if u["active"] else "отключён",
                                 "#22c55e" if u["active"] else "#ef4444"))

    # ==================================================================
    # TEMPLATES
    # ==================================================================
    def _page_templates(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Шаблоны этикеток")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_new = QPushButton("➕ Новый шаблон")
        btn_new.clicked.connect(self._tpl_create)
        head.addWidget(btn_new)

        btn_del = QPushButton("🗑 Удалить")
        btn_del.clicked.connect(self._tpl_delete)
        head.addWidget(btn_del)

        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(12)

        self.tpl_list = QTableWidget(0, 2)
        self.tpl_list.setHorizontalHeaderLabels(["Шаблон", "По умолч."])
        hh_l = self.tpl_list.horizontalHeader()
        hh_l.setSectionResizeMode(0, QHeaderView.Stretch)
        hh_l.setSectionResizeMode(1, QHeaderView.Fixed)
        self.tpl_list.setColumnWidth(1, 80)
        self.tpl_list.verticalHeader().setVisible(False)
        self.tpl_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tpl_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tpl_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tpl_list.setShowGrid(False)
        self.tpl_list.setFixedWidth(240)
        self.tpl_list.itemSelectionChanged.connect(self._on_template_select)
        row.addWidget(self.tpl_list)

        right = QVBoxLayout()
        right.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("Название:"))
        self.tpl_name_edit = QLineEdit()
        self.tpl_name_edit.textChanged.connect(self._tpl_refresh_preview)
        name_row.addWidget(self.tpl_name_edit, stretch=1)
        right.addLayout(name_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)

        size_row.addWidget(QLabel("Ширина:"))
        self.tpl_w = QDoubleSpinBox()
        self.tpl_w.setRange(20, 300)
        self.tpl_w.setDecimals(0)
        self.tpl_w.setSuffix(" мм")
        self.tpl_w.setValue(58)
        self.tpl_w.setFixedWidth(110)
        self.tpl_w.valueChanged.connect(self._tpl_refresh_preview)
        size_row.addWidget(self.tpl_w)

        size_row.addWidget(QLabel("Высота:"))
        self.tpl_h = QDoubleSpinBox()
        self.tpl_h.setRange(15, 300)
        self.tpl_h.setDecimals(0)
        self.tpl_h.setSuffix(" мм")
        self.tpl_h.setValue(40)
        self.tpl_h.setFixedWidth(110)
        self.tpl_h.valueChanged.connect(self._tpl_refresh_preview)
        size_row.addWidget(self.tpl_h)

        size_row.addSpacing(16)
        size_row.addWidget(QLabel("DPI:"))
        self.tpl_dpi = QComboBox()
        self.tpl_dpi.addItems(["203", "300", "600"])
        self.tpl_dpi.setFixedWidth(80)
        size_row.addWidget(self.tpl_dpi)

        size_row.addStretch(1)
        right.addLayout(size_row)

        fields_head = QHBoxLayout()
        fields_head.setSpacing(6)

        lbl_fields = QLabel("ПОЛЯ ЭТИКЕТКИ")
        lbl_fields.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;"
        )
        fields_head.addWidget(lbl_fields)
        fields_head.addStretch(1)

        btn_add_field = QPushButton("+")
        btn_add_field.setFixedSize(40, 30)
        btn_add_field.setToolTip("Добавить поле")
        btn_add_field.setStyleSheet("font-size:20px; font-weight:700; padding:0;")
        btn_add_field.clicked.connect(self._tpl_add_field)
        fields_head.addWidget(btn_add_field)

        right.addLayout(fields_head)

        self.tpl_fields = QTableWidget(0, 5)
        self.tpl_fields.setHorizontalHeaderLabels(
            ["Поле", "Подпись на этикетке", "Обяз.", "Шрифт", ""]
        )
        hh = self.tpl_fields.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        self.tpl_fields.setColumnWidth(0, 130)
        self.tpl_fields.setColumnWidth(2, 50)
        self.tpl_fields.setColumnWidth(3, 85)
        self.tpl_fields.setColumnWidth(4, 42)
        self.tpl_fields.verticalHeader().setVisible(True)
        self.tpl_fields.verticalHeader().setFixedWidth(30)
        self.tpl_fields.verticalHeader().setDefaultSectionSize(34)
        self.tpl_fields.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tpl_fields.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tpl_fields.setShowGrid(False)
        self.tpl_fields.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tpl_fields.itemChanged.connect(lambda _: self._tpl_refresh_preview())
        right.addWidget(self.tpl_fields, stretch=1)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)

        btn_save = QPushButton("💾 Сохранить шаблон")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._tpl_save)
        save_row.addWidget(btn_save)

        btn_make_default = QPushButton("⭐ По умолчанию")
        btn_make_default.clicked.connect(self._tpl_make_default)
        save_row.addWidget(btn_make_default)

        save_row.addStretch(1)
        right.addLayout(save_row)

        row.addLayout(right, stretch=1)

        row.addWidget(self._build_template_preview())

        v.addLayout(row, stretch=1)

        return w

    def _refresh_templates(self):
        self.tpl_list.blockSignals(True)
        try:
            self.tpl_list.setRowCount(0)
            tpls = self.store.list_templates()
            for tpl in tpls:
                r = self.tpl_list.rowCount()
                self.tpl_list.insertRow(r)
                self.tpl_list.setItem(r, 0, cell(tpl["name"]))
                self.tpl_list.setItem(r, 1, cell(
                    "⭐" if tpl["is_default"] else "",
                    "#f59e0b"
                ))
        finally:
            self.tpl_list.blockSignals(False)

        if self.tpl_list.rowCount():
            self.tpl_list.selectRow(0)

        self._refresh_bindings()

    def _on_template_select(self):
        rows = self.tpl_list.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        tpls = self.store.list_templates()
        if idx >= len(tpls):
            return

        self._current_tpl_code = tpls[idx]["code"]
        tpl = tpls[idx]

        self.tpl_name_edit.blockSignals(True)
        self.tpl_w.blockSignals(True)
        self.tpl_h.blockSignals(True)
        self.tpl_fields.blockSignals(True)

        try:
            self.tpl_name_edit.setText(tpl["name"])
            self.tpl_w.setValue(float(tpl["width_mm"]))
            self.tpl_h.setValue(float(tpl["height_mm"]))
            self.tpl_dpi.setCurrentText(str(tpl["dpi"]))

            t = self.tpl_fields
            t.setRowCount(0)

            for f in tpl["fields"]:
                r = t.rowCount()
                t.insertRow(r)

                item_name = QTableWidgetItem(f.get("name", ""))
                item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, 0, item_name)

                t.setItem(r, 1, QTableWidgetItem(f.get("label", "")))

                chk = QCheckBox()
                chk.setChecked(bool(f.get("required")))
                wrap_chk = QWidget()
                wl = QHBoxLayout(wrap_chk)
                wl.setContentsMargins(0, 0, 0, 0)
                wl.addWidget(chk)
                wl.setAlignment(Qt.AlignCenter)
                t.setCellWidget(r, 2, wrap_chk)

                spin = QSpinBox()
                spin.setRange(6, 40)
                spin.setValue(int(f.get("font_size", 12)))
                spin.setAlignment(Qt.AlignCenter)
                t.setCellWidget(r, 3, spin)

                btn_rm = QPushButton("×")
                btn_rm.setFixedSize(32, 26)
                btn_rm.setStyleSheet(
                    "color:#ef4444; border:none; font-weight:700; "
                    "font-size:18px; padding:0; background:transparent;"
                )
                btn_rm.setCursor(Qt.PointingHandCursor)
                btn_rm.clicked.connect(lambda _, rr=r: self._tpl_remove_field(rr))

                wrap_rm = QWidget()
                wl2 = QHBoxLayout(wrap_rm)
                wl2.setContentsMargins(0, 0, 0, 0)
                wl2.addWidget(btn_rm)
                wl2.setAlignment(Qt.AlignCenter)
                t.setCellWidget(r, 4, wrap_rm)

            t.resizeRowsToContents()
        finally:
            self.tpl_name_edit.blockSignals(False)
            self.tpl_w.blockSignals(False)
            self.tpl_h.blockSignals(False)
            self.tpl_fields.blockSignals(False)

        self._tpl_refresh_preview()

    def _current_template(self):
        rows = self.tpl_list.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        tpls = self.store.list_templates()
        if idx >= len(tpls):
            return None
        return tpls[idx]

    def _tpl_add_field(self):
        name, ok = QInputDialog.getText(
            self, "Новое поле",
            "Системное имя (например, country, manufacturer, batch):"
        )
        if not ok or not name.strip():
            return
        name = name.strip().lower().replace(" ", "_")

        t = self.tpl_fields
        r = t.rowCount()
        t.insertRow(r)

        item_name = QTableWidgetItem(name)
        item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
        t.setItem(r, 0, item_name)
        t.setItem(r, 1, QTableWidgetItem(name))

        chk = QCheckBox()
        wrap_chk = QWidget()
        wl = QHBoxLayout(wrap_chk)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(chk)
        wl.setAlignment(Qt.AlignCenter)
        t.setCellWidget(r, 2, wrap_chk)

        spin = QSpinBox()
        spin.setRange(6, 40)
        spin.setValue(12)
        spin.setAlignment(Qt.AlignCenter)
        t.setCellWidget(r, 3, spin)

        btn_rm = QPushButton("✕")
        btn_rm.setStyleSheet("color:#ef4444; border:none; font-weight:600;")
        btn_rm.setCursor(Qt.PointingHandCursor)
        btn_rm.clicked.connect(lambda _, rr=r: self._tpl_remove_field(rr))

        wrap_rm = QWidget()
        wl2 = QHBoxLayout(wrap_rm)
        wl2.setContentsMargins(0, 0, 0, 0)
        wl2.addWidget(btn_rm)
        wl2.setAlignment(Qt.AlignCenter)
        t.setCellWidget(r, 4, wrap_rm)

        t.resizeRowsToContents()
        self._tpl_refresh_preview()

    def _tpl_remove_field(self, row_idx: int):
        t = self.tpl_fields
        if 0 <= row_idx < t.rowCount():
            t.removeRow(row_idx)
            self._relink_field_buttons()
            self._tpl_refresh_preview()

    def _relink_field_buttons(self):
        t = self.tpl_fields
        for r in range(t.rowCount()):
            btn = t.cellWidget(r, 4)
            if btn:
                try:
                    btn.clicked.disconnect()
                except Exception:
                    pass
                btn.clicked.connect(lambda _, rr=r: self._tpl_remove_field(rr))

    def _collect_fields_from_table(self) -> list:
        t = self.tpl_fields
        fields = []
        for r in range(t.rowCount()):
            name = t.item(r, 0).text() if t.item(r, 0) else ""
            label = t.item(r, 1).text() if t.item(r, 1) else ""
            wrap = t.cellWidget(r, 2)
            chk = wrap.findChild(QCheckBox) if wrap else None
            required = chk.isChecked() if chk else False
            spin = t.cellWidget(r, 3)
            font_size = spin.value() if spin else 12
            if not name:
                continue
            fields.append({
                "name": name, "label": label,
                "required": required, "font_size": font_size,
            })
        return fields

    def _tpl_save(self):
        tpl = self._current_template()
        if not tpl:
            self.toast.show_message("Не выбран шаблон", "warn")
            return

        name = self.tpl_name_edit.text().strip()
        if not name:
            self.toast.show_message("Введите название шаблона", "warn")
            return

        fields = self._collect_fields_from_table()
        if not fields:
            self.toast.show_message("Добавьте хотя бы одно поле", "warn")
            return

        result = self.store.update_template(
            code=tpl["code"],
            name=name,
            fields=fields,
            width_mm=self.tpl_w.value(),
            height_mm=self.tpl_h.value(),
            dpi=int(self.tpl_dpi.currentText()),
        )
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
            self._refresh_templates()
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _tpl_make_default(self):
        tpl = self._current_template()
        if not tpl:
            return
        result = self.store.update_template(code=tpl["code"], is_default=True)
        if result["ok"]:
            self.toast.show_message(f"«{tpl['name']}» — по умолчанию", "ok")
            self._refresh_templates()
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _tpl_create(self):
        code, ok = QInputDialog.getText(
            self, "Новый шаблон",
            "Код (латиница, без пробелов, уникальный):"
        )
        if not ok or not code.strip():
            return
        code = code.strip().lower().replace(" ", "_")

        name, ok = QInputDialog.getText(self, "Новый шаблон", "Название:")
        if not ok or not name.strip():
            return
        name = name.strip()

        default_fields = [
            {"name": "dataMatrix", "label": "", "required": True, "font_size": 0},
            {"name": "name_ru", "label": "Наименование", "required": True, "font_size": 13},
            {"name": "importer", "label": "Импортёр", "required": True, "font_size": 10},
        ]

        result = self.store.create_template(
            code=code, name=name, fields=default_fields
        )
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
            self._refresh_templates()
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _tpl_delete(self):
        tpl = self._current_template()
        if not tpl:
            self.toast.show_message("Не выбран шаблон", "warn")
            return

        reply = QMessageBox.warning(
            self, "Удалить шаблон?",
            f"Удалить «{tpl['name']}»?\n\nЭто действие нельзя отменить.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        result = self.store.delete_template(tpl["code"])
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
            self._refresh_templates()
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _refresh_bindings(self):
        try:
            self.bind_table.setRowCount(0)
        except AttributeError:
            return

    def _build_template_preview(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(320)

        v = QVBoxLayout(card)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        title = QLabel("ПРЕДПРОСМОТР ЭТИКЕТКИ")
        title.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;"
        )
        v.addWidget(title)

        self.tpl_preview_area = QFrame()
        self.tpl_preview_area.setStyleSheet(
            "background:#ffffff; border-radius:6px;"
        )
        self.tpl_preview_area.setMinimumHeight(180)

        pv = QHBoxLayout(self.tpl_preview_area)
        pv.setContentsMargins(12, 12, 12, 12)
        pv.setSpacing(10)
        pv.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.tpl_preview_dm = QLabel()
        self.tpl_preview_dm.setFixedSize(100, 100)
        self.tpl_preview_dm.setStyleSheet("background:transparent;")
        self.tpl_preview_dm.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        pv.addWidget(self.tpl_preview_dm, alignment=Qt.AlignTop)

        self.tpl_preview_text = QVBoxLayout()
        self.tpl_preview_text.setSpacing(3)
        self.tpl_preview_text.setAlignment(Qt.AlignTop)
        pv.addLayout(self.tpl_preview_text, stretch=1)

        v.addWidget(self.tpl_preview_area)

        self.tpl_preview_size = QLabel("")
        self.tpl_preview_size.setStyleSheet("color:#8b97a6; font-size:11px;")
        self.tpl_preview_size.setAlignment(Qt.AlignCenter)
        v.addWidget(self.tpl_preview_size)

        v.addStretch(1)
        return card

    def _tpl_refresh_preview(self):
        if hasattr(self, "_tpl_preview_timer"):
            self._tpl_preview_timer.start()

    def _tpl_refresh_preview_now(self):
        if not hasattr(self, "tpl_preview_text"):
            return

        while self.tpl_preview_text.count():
            item = self.tpl_preview_text.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            fields = self._collect_fields_from_table()
        except Exception:
            fields = []

        samples = {
            "name_ru":     "Наушники беспроводные BT-500",
            "model":       "BT-500",
            "importer":    "ООО «Ромашка», г. Москва",
            "exp_date":    "12.2026",
            "qty":         "1 шт.",
            "material":    "Кожа натуральная",
            "size_eu":     "42",
            "composition": "Хлопок 100%",
            "country":     "Китай (CN)",
            "lot":         "LOT-2025-04",
            "serial":      "SN0004821",
            "gtin":        "04600123456782",
            "manufacturer": "Shenzhen Tech Co.",
            "batch":       "B-2025-04",
        }

        has_dm = False

        for f in fields:
            name = f.get("name", "")
            label = f.get("label", "") or ""
            font_size = int(f.get("font_size", 12)) or 11

            if name == "dataMatrix":
                has_dm = True
                continue

            value = samples.get(name, f"[{name}]")
            text = f"{label}: {value}" if label else value

            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:#000000; background:transparent; "
                f"font-family:'Consolas', monospace; "
                f"font-size:{max(9, min(font_size, 20))}px;"
            )
            lbl.setWordWrap(True)
            self.tpl_preview_text.addWidget(lbl)

        if has_dm:
            sample_code = "01" + "04600123456782" + "21" + "SN0004821" + "\x1d91ABCD\x1d92XYZ12345"
            pix = render_datamatrix(sample_code, 100)
            self.tpl_preview_dm.setPixmap(pix)
            self.tpl_preview_dm.show()
        else:
            self.tpl_preview_dm.hide()

        try:
            w_mm = self.tpl_w.value()
            h_mm = self.tpl_h.value()
            dpi = self.tpl_dpi.currentText()
            self.tpl_preview_size.setText(
                f"{int(w_mm)} × {int(h_mm)} мм · {dpi} DPI"
            )
        except Exception:
            pass

    # ==================================================================
    # POOL (с фильтром по точке + импорт xlsx)
    # ==================================================================
    def _page_pool(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Пул кодов ЧЗ")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_export = QPushButton("⬇ Экспорт CSV")
        btn_export.clicked.connect(self._export_pool)
        head.addWidget(btn_export)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_pool)
        head.addWidget(btn_refresh)

        v.addLayout(head)

        # Фильтр по точке
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        filter_row.addWidget(QLabel("Точка:"))
        self.pool_point_filter = QComboBox()
        self.pool_point_filter.setFixedWidth(220)
        self.pool_point_filter.addItem("Все точки", None)
        for p in self.store.points:
            self.pool_point_filter.addItem(
                f"{p['id']} — {p['name']}", p["id"]
            )
        idx = self.pool_point_filter.findData(self.store.current_point)
        if idx >= 0:
            self.pool_point_filter.setCurrentIndex(idx)
        self.pool_point_filter.currentIndexChanged.connect(self._refresh_pool)
        filter_row.addWidget(self.pool_point_filter)

        filter_row.addStretch(1)

        self.pool_count_label = QLabel("")
        self.pool_count_label.setStyleSheet("color:#8b97a6; font-size:11px;")
        filter_row.addWidget(self.pool_count_label)

        v.addLayout(filter_row)

        # Кнопки импорта
        toolbar2 = QHBoxLayout()
        toolbar2.setSpacing(8)

        btn_import_xlsx = QPushButton("📥 Импорт xlsx (ЦРПТ)")
        btn_import_xlsx.setProperty("primary", True)
        btn_import_xlsx.clicked.connect(self._import_codes_xlsx)
        toolbar2.addWidget(btn_import_xlsx)

        btn_import = QPushButton("⬆ Импорт CSV")
        btn_import.clicked.connect(self._import_pool)
        toolbar2.addWidget(btn_import)

        btn_template = QPushButton("📄 Скачать шаблон CSV")
        btn_template.clicked.connect(self._template_pool)
        toolbar2.addWidget(btn_template)

        toolbar2.addStretch(1)
        v.addLayout(toolbar2)

        self.pool_table = table(
            ["GTIN", "Точка", "Всего", "Свободно", "Использовано", "Напечатано"]
        )
        v.addWidget(self.pool_table, stretch=1)
        return w

    def _refresh_pool(self):
        point = self.pool_point_filter.currentData() if hasattr(self, "pool_point_filter") else None

        t = self.pool_table
        t.setRowCount(0)

        if point:
            rows = self._pool_stats_by_point(point)
            for r_data in rows:
                r = t.rowCount(); t.insertRow(r)
                t.setItem(r, 0, cell(r_data["gtin"], mono=True))
                t.setItem(r, 1, cell(r_data["point"], mono=True))
                t.setItem(r, 2, cell(r_data["total"], mono=True))
                t.setItem(r, 3, cell(r_data["free"], "#22c55e", mono=True))
                t.setItem(r, 4, cell(r_data["used"], "#f59e0b", mono=True))
                t.setItem(r, 5, cell(r_data["printed"], mono=True))
        else:
            for p_data in self.store.pool_stats():
                r = t.rowCount(); t.insertRow(r)
                t.setItem(r, 0, cell(p_data["gtin"], mono=True))
                t.setItem(r, 1, cell("—"))
                t.setItem(r, 2, cell(p_data["total"], mono=True))
                t.setItem(r, 3, cell(p_data["free"], "#22c55e", mono=True))
                t.setItem(r, 4, cell(p_data["used"], "#f59e0b", mono=True))
                t.setItem(r, 5, cell(p_data["printed"], mono=True))

        try:
            total = self.store.count_pool(point)
            suffix = f" (точка {point})" if point else " (все точки)"
            self.pool_count_label.setText(f"Всего кодов: {total}{suffix}")
        except Exception:
            pass

    def _pool_stats_by_point(self, point: str) -> list:
        """Статистика по GTIN с фильтром по точке."""
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT gtin, assigned_point AS point, "
                "       COUNT(*) AS total, "
                "       SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free, "
                "       SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used, "
                "       SUM(CASE WHEN printed=1 THEN 1 ELSE 0 END) AS printed "
                "FROM pool_codes WHERE assigned_point = ? "
                "GROUP BY gtin ORDER BY gtin",
                (point,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _export_pool(self):
        result = export_pool(self)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _import_pool(self):
        result = import_pool_codes(self.store, self)
        if result["ok"]:
            QMessageBox.information(self, "Импорт выполнен", result["info"])
            self._refresh_pool()
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _template_pool(self):
        result = download_pool_template(self)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _import_codes_xlsx(self):
        """Открыть окно импорта xlsx (ЦРПТ)."""
        from import_codes_window import ImportCodesWindow

        win = ImportCodesWindow(self.store, self)
        result = win.exec()
        if result == ImportCodesWindow.Accepted:
            self._refresh_pool()
            self._refresh_imports()

    # ==================================================================
    # IMPORTS (история импортов)
    # ==================================================================
    def _page_imports(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("История импортов кодов ЧЗ")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_imports)
        head.addWidget(btn_refresh)

        v.addLayout(head)

        self.imports_table = table(
            ["Заказ", "Файл", "Точка", "Кодов", "Кто", "Когда"]
        )
        v.addWidget(self.imports_table, stretch=1)
        return w

    def _refresh_imports(self):
        try:
            rows = self.store.list_import_batches(limit=200)
        except Exception:
            rows = []

        t = self.imports_table
        t.setRowCount(0)
        for r_data in rows:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(r_data["name"]))
            t.setItem(r, 1, cell(r_data["file_name"], mono=True))
            t.setItem(r, 2, cell(r_data["point"], mono=True))
            t.setItem(r, 3, cell(r_data["total_codes"], mono=True))
            t.setItem(r, 4, cell(r_data["imported_by"] or "—"))
            t.setItem(r, 5, cell(r_data["imported_at"] or "—", mono=True))

    # ==================================================================
    # SCANNED (история привязок ШК → ЧЗ)
    # ==================================================================
    def _page_scanned(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Привязки: иностранный ШК → код ЧЗ")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_scanned)
        head.addWidget(btn_refresh)

        v.addLayout(head)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Точка:"))
        self.scanned_point_filter = QComboBox()
        self.scanned_point_filter.setFixedWidth(220)
        self.scanned_point_filter.addItem("Все точки", None)
        for p in self.store.points:
            self.scanned_point_filter.addItem(
                f"{p['id']} — {p['name']}", p["id"]
            )
        idx = self.scanned_point_filter.findData(self.store.current_point)
        if idx >= 0:
            self.scanned_point_filter.setCurrentIndex(idx)
        self.scanned_point_filter.currentIndexChanged.connect(self._refresh_scanned)
        filter_row.addWidget(self.scanned_point_filter)

        filter_row.addStretch(1)
        v.addLayout(filter_row)

        self.scanned_table = table(
            ["Время", "Точка", "Оператор",
             "Иностранный ШК", "GTIN (ЧЗ)", "Код ЧЗ"]
        )
        v.addWidget(self.scanned_table, stretch=1)
        return w

    def _refresh_scanned(self):
        point = (self.scanned_point_filter.currentData()
                 if hasattr(self, "scanned_point_filter") else None)

        try:
            rows = self.store.list_scanned_shk(point=point, limit=500)
        except Exception:
            rows = []

        t = self.scanned_table
        t.setRowCount(0)
        for r_data in rows:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(r_data["scanned_at"], mono=True))
            t.setItem(r, 1, cell(r_data["point"], mono=True))
            t.setItem(r, 2, cell(r_data["operator"] or "—"))
            fc = r_data["foreign_code"]
            t.setItem(r, 3, cell(
                fc[:40] + "…" if len(fc) > 40 else fc, mono=True
            ))
            t.setItem(r, 4, cell(r_data["cz_gtin"], mono=True))
            cc = r_data["cz_code"]
            t.setItem(r, 5, cell(
                cc[:60] + "…" if len(cc) > 60 else cc, mono=True
            ))

    # ==================================================================
    # DISCREPANCIES (расхождения: сканы vs коды)
    # ==================================================================
    def _page_discrepancies(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Расхождения: сканы vs коды")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_export = QPushButton("⬇ Экспорт CSV")
        btn_export.clicked.connect(self._export_discrepancies)
        head.addWidget(btn_export)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_discrepancies)
        head.addWidget(btn_refresh)

        v.addLayout(head)

        sub = QLabel(
            "Сравнение пула кодов и счётчика сканов по GTIN. "
            "«Перебор» = оператор пикнул больше раз, чем было кодов."
        )
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        sub.setWordWrap(True)
        v.addWidget(sub)

        # --- Фильтр по точке ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Точка:"))

        self.disc_point_filter = QComboBox()
        self.disc_point_filter.setFixedWidth(220)
        self.disc_point_filter.addItem("Все точки", None)
        for p in self.store.points:
            self.disc_point_filter.addItem(
                f"{p['id']} — {p['name']}", p["id"]
            )   
        idx = self.disc_point_filter.findData(self.store.current_point)
        if idx >= 0:
            self.disc_point_filter.setCurrentIndex(idx)
        self.disc_point_filter.currentIndexChanged.connect(
            self._refresh_discrepancies
        )
        filter_row.addWidget(self.disc_point_filter)

        self.disc_only_overflow = QCheckBox("Только перебор")
        self.disc_only_overflow.stateChanged.connect(self._refresh_discrepancies)
        filter_row.addWidget(self.disc_only_overflow)
    
        self.disc_only_unprinted = QCheckBox("Только с ненапечатанными")
        self.disc_only_unprinted.stateChanged.connect(self._refresh_discrepancies)
        filter_row.addWidget(self.disc_only_unprinted)

        filter_row.addStretch(1)

        self.disc_summary = QLabel("")
        self.disc_summary.setStyleSheet("color:#8b97a6; font-size:11px;")
        filter_row.addWidget(self.disc_summary)

        v.addLayout(filter_row)

        # --- Таблица ---
        self.disc_table = table([
            "GTIN", "Точка", "Кодов", "Сканов",
            "Использовано", "Свободно", "Статус"
        ])
        v.addWidget(self.disc_table, stretch=1)

        # --- Вторая таблица: ненапечатанные коды ---
        unprinted_head = QHBoxLayout()
        unprinted_head.setSpacing(8)
    
        lbl_unp = QLabel("НЕНАПЕЧАТАННЫЕ КОДЫ")
        lbl_unp.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;"
        )
        unprinted_head.addWidget(lbl_unp)

        unprinted_head.addStretch(1)

        self.disc_unprinted_count = QLabel("")
        self.disc_unprinted_count.setStyleSheet("color:#8b97a6; font-size:11px;")
        unprinted_head.addWidget(self.disc_unprinted_count)

        btn_reprint = QPushButton("🖨 Перепечатать")
        btn_reprint.setProperty("primary", True)
        btn_reprint.clicked.connect(self._disc_reprint_selected)
        unprinted_head.addWidget(btn_reprint)

        btn_mark = QPushButton("✅ Пометить напечатанным")
        btn_mark.clicked.connect(self._disc_mark_selected_printed)
        unprinted_head.addWidget(btn_mark)

        btn_export_unp = QPushButton("⬇ Экспорт CSV")
        btn_export_unp.clicked.connect(self._export_unprinted)
        unprinted_head.addWidget(btn_export_unp)

        v.addLayout(unprinted_head)

        self.disc_unprinted_table = table([
            "GTIN", "Код ЧЗ", "SN", "Точка", "Оператор", "Взят"
        ])
        self.disc_unprinted_table.setMinimumHeight(150)
        v.addWidget(self.disc_unprinted_table, stretch=1)

        return w


    def _refresh_discrepancies(self):
        point = (self.disc_point_filter.currentData()
                 if hasattr(self, "disc_point_filter") else None)
        only_overflow = (self.disc_only_overflow.isChecked()
                         if hasattr(self, "disc_only_overflow") else False)
        only_unprinted = (self.disc_only_unprinted.isChecked()
                          if hasattr(self, "disc_only_unprinted") else False)

        try:
            rows = self.store.get_discrepancies(point=point)
        except Exception as e:
            log.error("Ошибка get_discrepancies: %s", e)
            rows = []

        # Если «только с ненапечатанными» — оставляем только GTIN,
        # у которых есть ненапечатанные коды
        if only_unprinted:
            try:
                unprinted_by_gtin = self._count_unprinted_by_gtin(point)
            except Exception:
                unprinted_by_gtin = {}
            rows = [r for r in rows
                    if unprinted_by_gtin.get((r["gtin"], r["point"]), 0) > 0]

        if only_overflow:
            rows = [r for r in rows if r["overflow"]]

        t = self.disc_table
        t.setRowCount(0)

        total_overflow = 0

        for r in rows:
            i = t.rowCount(); t.insertRow(i)
            t.setItem(i, 0, cell(r["gtin"], mono=True))
            t.setItem(i, 1, cell(r["point"] or "—"))

            # Кодов
            t.setItem(i, 2, cell(r["total"], mono=True))

            # Сканов — подсветка, если больше
            scans_color = "#ef4444" if r["overflow"] else "#e6edf3"
            t.setItem(i, 3, cell(r["scans"], scans_color, mono=True))

            t.setItem(i, 4, cell(r["used"], mono=True))
            t.setItem(i, 5, cell(r["free"], "#22c55e", mono=True))

            if r["overflow"]:
                status = f"⚠ Перебор +{r['scans'] - r['total']}"
                status_color = "#ef4444"
                total_overflow += 1
            else:
                status = "✅"
                status_color = "#22c55e"

            t.setItem(i, 6, cell(status, status_color))

        self.disc_summary.setText(
            f"Всего GTIN: {len(rows)}   ·   "
            f"с перебором: {total_overflow}"
        )
        if total_overflow > 0:
            self.disc_summary.setStyleSheet(
                "color:#ef4444; font-size:11px; font-weight:600;"
            )
        else:
            self.disc_summary.setStyleSheet("color:#8b97a6; font-size:11px;")

        # Обновляем вторую таблицу — ненапечатанные коды
        self._refresh_unprinted_codes(point)


    def _count_unprinted_by_gtin(self, point: str = None) -> dict:
        """Сколько ненапечатанных кодов на каждый (gtin, point)."""
        import db
        with db.get_conn() as conn:
            if point:
                rows = conn.execute(
                    "SELECT gtin, used_point AS point, COUNT(*) AS cnt "
                    "FROM pool_codes "
                    "WHERE used = 1 AND printed = 0 AND used_point = ? "
                    "GROUP BY gtin, used_point",
                    (point,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT gtin, used_point AS point, COUNT(*) AS cnt "
                    "FROM pool_codes "
                    "WHERE used = 1 AND printed = 0 "
                    "GROUP BY gtin, used_point"
                ).fetchall()
        return {(r["gtin"], r["point"] or "—"): r["cnt"] for r in rows}
        

    def _refresh_unprinted_codes(self, point: str = None):
        """Обновить вторую таблицу — ненапечатанные коды."""
        try:
            rows = self.store.list_unprinted_for_point(
                point or self.store.current_point, limit=500
            )   
        except Exception as e:
            log.error("Ошибка list_unprinted_for_point: %s", e)
            rows = []

        t = self.disc_unprinted_table
        t.setRowCount(0)

        for r in rows:
            i = t.rowCount(); t.insertRow(i)
            t.setItem(i, 0, cell(r.get("gtin", ""), mono=True))

            code = r.get("code") or ""
            t.setItem(i, 1, cell(
                code[:40] + "…" if len(code) > 40 else code, mono=True
            ))
            t.setItem(i, 2, cell(r.get("sn", "—"), mono=True))
            t.setItem(i, 3, cell(r.get("used_point", "—"), mono=True))
            t.setItem(i, 4, cell("—", "#8b97a6"))
            t.setItem(i, 5, cell(r.get("created_at", "—"), mono=True))

        n = len(rows)
        self.disc_unprinted_count.setText(f"Всего: {n}")
        if n > 0:
            self.disc_unprinted_count.setStyleSheet(
                "color:#f59e0b; font-size:11px; font-weight:600;"
            )
        else:
            self.disc_unprinted_count.setStyleSheet(
                "color:#8b97a6; font-size:11px;"
            )

        self._unprinted_rows = rows


    def _disc_reprint_selected(self):
        """Перепечатать выбранный ненапечатанный код."""
        rows = self.disc_unprinted_table.selectionModel().selectedRows()
        if not rows:
            self.toast.show_message("Выберите код из списка", "warn")
            return

        idx = rows[0].row()
        if idx >= len(getattr(self, "_unprinted_rows", [])):
            return

        r = self._unprinted_rows[idx]
        code = r.get("code")
        point = r.get("used_point") or self.store.current_point

        from printer import print_code, load_config, get_printer_for_point
        cfg = load_config()
        cfg_for_point = get_printer_for_point(cfg, point)

        try:
            result = print_code(code, cfg_for_point)
        except Exception as e:
            result = {"ok": False, "info": f"{type(e).__name__}: {e}"}

        if result["ok"]:
            self.store.mark_printed(code, point=point)
            self.store.increment_counter("prints")
            self.store.log("Перепечать (админ)", point=point,
                           operator="админ", result="успех")
            self.toast.show_message(f"Напечатано: {result['info']}", "ok")
            self._refresh_discrepancies()
        else:
            self.store.increment_counter("errors")
            self.toast.show_message(f"Ошибка: {result['info']}", "err")
                

    def _disc_mark_selected_printed(self):
        """Пометить выбранный код как напечатанный (вручную)."""
        rows = self.disc_unprinted_table.selectionModel().selectedRows()
        if not rows:
            self.toast.show_message("Выберите код из списка", "warn")
            return

        idx = rows[0].row()
        if idx >= len(getattr(self, "_unprinted_rows", [])):
            return

        r = self._unprinted_rows[idx]
        code = r.get("code")
        point = r.get("used_point") or self.store.current_point

        reply = QMessageBox.question(
            self, "Пометить как напечатанный?",
            f"Пометить код как напечатанный?\n\n"
            f"GTIN: {r.get('gtin')}\n"
            f"SN: {r.get('sn')}\n\n"
            f"Используйте, если этикетка напечатана вручную\n"
            f"или принтер напечатал, но система не подтвердила.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.store.mark_printed(code, point=point)
        self.store.log("Помечено напечатанным (админ)", point=point,
                       operator="админ", result="успех")
        self.toast.show_message("Помечено", "ok")
        self._refresh_discrepancies()


    def _export_unprinted(self):
        """Экспорт ненапечатанных в CSV."""
        from PySide6.QtWidgets import QFileDialog
        import csv
        from datetime import datetime

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт ненапечатанных",
            f"chestzn_unprinted_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv",
            "CSV (*.csv);;Все файлы (*)"
        )
        if not path:
            return
            
        rows = getattr(self, "_unprinted_rows", [])
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["GTIN", "Код ЧЗ", "SN", "Точка", "Взят"])
                for r in rows:
                    writer.writerow([
                        r.get("gtin", ""),
                        r.get("code", ""),
                        r.get("sn", ""),
                        r.get("used_point", ""),
                        r.get("created_at", ""),
                    ])
            self.toast.show_message(f"Экспортировано: {len(rows)} строк", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка: {e}", "err")


    def _export_discrepancies(self):
        from PySide6.QtWidgets import QFileDialog
        import csv
        from datetime import datetime
            
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт расхождений",
            f"chestzn_discrepancies_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv",
            "CSV (*.csv);;Все файлы (*)"
        )
        if not path:
            return

        try:
            rows = self.store.get_discrepancies(
                point=(self.disc_point_filter.currentData()
                       if hasattr(self, "disc_point_filter") else None)
            )
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow([
                    "GTIN", "Точка", "Кодов", "Сканов",
                    "Использовано", "Свободно", "Перебор"
                ])
                for r in rows:
                    writer.writerow([
                        r["gtin"], r["point"] or "", r["total"], r["scans"],
                        r["used"], r["free"],
                        ("да" if r["overflow"] else "нет"),
                    ])
            self.toast.show_message(f"Экспортировано: {len(rows)} строк", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка: {e}", "err")

    # ==================================================================
    # PRINTER (принтеры по точкам)
    # ==================================================================
    def _page_printer(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        tt = QLabel("Принтеры по точкам")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        sub = QLabel(
            "Каждая точка использует свой принтер. "
            "Если для точки не задан — используется общий (fallback)."
        )
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        sub.setWordWrap(True)
        v.addWidget(sub)

        point_card = QFrame(); point_card.setObjectName("card")
        pc = QHBoxLayout(point_card)
        pc.setContentsMargins(18, 12, 18, 12)
        pc.setSpacing(10)

        pc.addWidget(QLabel("Настроить принтер для точки:"))
        self.printer_point_combo = QComboBox()
        self.printer_point_combo.setFixedWidth(240)
        for p in self.store.points:
            self.printer_point_combo.addItem(
                f"{p['id']} — {p['name']}", p["id"]
            )
        idx = self.printer_point_combo.findData(self.store.current_point)
        if idx >= 0:
            self.printer_point_combo.setCurrentIndex(idx)
        self.printer_point_combo.currentIndexChanged.connect(self._refresh_printer_form)
        pc.addWidget(self.printer_point_combo)

        pc.addStretch(1)
        v.addWidget(point_card)

        card = QFrame(); card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 14, 18, 14)
        cv.setSpacing(10)

        h = QHBoxLayout()
        h.addWidget(QLabel("Найденные принтеры Windows:"))
        btn_scan = QPushButton("🔄 Обновить список")
        btn_scan.clicked.connect(self._refresh_printer_table)
        h.addStretch(1)
        h.addWidget(btn_scan)
        cv.addLayout(h)

        self.printer_table = table(["Принтер", "Порт", "Язык", "По умолчанию"])
        self.printer_table.setMinimumHeight(150)
        self.printer_table.itemSelectionChanged.connect(self._on_printer_select)
        cv.addWidget(self.printer_table)

        v.addWidget(card)

        card2 = QFrame(); card2.setObjectName("card")
        g = QGridLayout(card2)
        g.setContentsMargins(18, 14, 18, 14)
        g.setSpacing(10)

        self.cfg_printer_name = QLineEdit()
        self.cfg_printer_ip   = QLineEdit()
        self.cfg_printer_port = QLineEdit()
        self.cfg_printer_lang = QComboBox()
        self.cfg_printer_lang.addItems(["auto", "zpl", "tspl", "ezpl", "windows"])
        self.cfg_label_w = QLineEdit()
        self.cfg_label_h = QLineEdit()
        self.cfg_dpi     = QComboBox()
        self.cfg_dpi.addItems(["203", "300", "600"])
        self.cfg_copies  = QLineEdit()

        rows = [
            ("Принтер (имя Windows):", self.cfg_printer_name),
            ("IP-адрес (если сеть):",  self.cfg_printer_ip),
            ("Порт:",                  self.cfg_printer_port),
            ("Язык команд:",           self.cfg_printer_lang),
            ("Ширина этикетки, мм:",   self.cfg_label_w),
            ("Высота этикетки, мм:",   self.cfg_label_h),
            ("DPI:",                   self.cfg_dpi),
            ("Копий:",                 self.cfg_copies),
        ]
        for i, (lbl, widget) in enumerate(rows):
            l = QLabel(lbl); l.setStyleSheet("color:#8b97a6;")
            g.addWidget(l, i, 0)
            g.addWidget(widget, i, 1)

        v.addWidget(card2)

        fallback_card = QFrame(); fallback_card.setObjectName("card")
        fb = QVBoxLayout(fallback_card)
        fb.setContentsMargins(18, 12, 18, 12)
        fb.setSpacing(6)

        fb_title = QLabel("ОБЩИЙ ПРИНТЕР (FALLBACK)")
        fb_title.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;"
        )
        fb.addWidget(fb_title)

        self.fallback_label = QLabel("")
        self.fallback_label.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-family:Consolas,monospace;"
        )
        self.fallback_label.setWordWrap(True)
        fb.addWidget(self.fallback_label)

        v.addWidget(fallback_card)

        h2 = QHBoxLayout()
        btn_save = QPushButton("💾 Сохранить для точки")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._save_printer_cfg)
        h2.addWidget(btn_save)

        btn_clear = QPushButton("🗑 Очистить принтер точки")
        btn_clear.clicked.connect(self._clear_printer_cfg)
        h2.addWidget(btn_clear)

        btn_test = QPushButton("🧪 Тест печати")
        btn_test.clicked.connect(self._test_print)
        h2.addWidget(btn_test)

        h2.addStretch(1)
        v.addLayout(h2)

        v.addStretch(1)
        return w

    def _refresh_printer(self):
        self._refresh_printer_table()
        self._refresh_printer_form()

    def _refresh_printer_table(self):
        t = self.printer_table
        t.setRowCount(0)
        try:
            default = default_printer()
        except Exception:
            default = ""
        for p in list_printers():
            r = t.rowCount(); t.insertRow(r)
            lang = detect_language(p["name"])
            t.setItem(r, 0, cell(p["name"]))
            t.setItem(r, 1, cell(p["port"], mono=True))
            t.setItem(r, 2, cell(lang))
            t.setItem(r, 3, cell("да" if p["name"] == default else "", "#22c55e"))

    def _refresh_printer_form(self):
        cfg = load_config()
        point = self.printer_point_combo.currentData() or self.store.current_point
        point_cfg = get_printer_for_point(cfg, point)

        self.cfg_printer_name.setText(point_cfg.get("printer_name", ""))
        self.cfg_printer_ip.setText(point_cfg.get("printer_ip", ""))
        self.cfg_printer_port.setText(str(point_cfg.get("printer_port", 9100)))
        self.cfg_printer_lang.setCurrentText(point_cfg.get("printer_lang", "auto"))
        self.cfg_label_w.setText(str(point_cfg.get("label_width_mm", 58)))
        self.cfg_label_h.setText(str(point_cfg.get("label_height_mm", 40)))
        self.cfg_dpi.setCurrentText(str(point_cfg.get("dpi", 203)))
        self.cfg_copies.setText(str(point_cfg.get("copies", 1)))

        general_name = cfg.get("printer_name", "") or "—"
        general_ip = cfg.get("printer_ip", "") or "—"
        printers = cfg.get("printers") or {}

        if point in printers:
            self.fallback_label.setText(
                f"Для точки {point} задан принтер:\n"
                f"  {printers[point].get('printer_name', '—')} "
                f"({printers[point].get('printer_lang', 'auto')})\n"
                f"Общий (fallback): {general_name} / {general_ip}"
            )
        else:
            self.fallback_label.setText(
                f"Для точки {point} свой принтер НЕ задан.\n"
                f"Будет использоваться общий (fallback): "
                f"{general_name} / {general_ip}"
            )

    def _on_printer_select(self):
        rows = self.printer_table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        name = self.printer_table.item(idx, 0).text()
        lang = self.printer_table.item(idx, 2).text()
        self.cfg_printer_name.setText(name)
        self.cfg_printer_lang.setCurrentText(lang)

    def _save_printer_cfg(self):
        cfg = load_config()
        point = self.printer_point_combo.currentData() or self.store.current_point

        printer_cfg = {
            "printer_name": self.cfg_printer_name.text().strip(),
            "printer_ip": self.cfg_printer_ip.text().strip(),
            "printer_lang": self.cfg_printer_lang.currentText(),
        }

        try:
            printer_cfg["printer_port"] = int(
                self.cfg_printer_port.text().strip() or 9100
            )
        except ValueError:
            printer_cfg["printer_port"] = 9100

        try:
            printer_cfg["label_width_mm"] = float(
                self.cfg_label_w.text().strip() or 58
            )
            printer_cfg["label_height_mm"] = float(
                self.cfg_label_h.text().strip() or 40
            )
        except ValueError:
            pass

        try:
            printer_cfg["dpi"] = int(self.cfg_dpi.currentText())
        except ValueError:
            printer_cfg["dpi"] = 203

        try:
            printer_cfg["copies"] = int(self.cfg_copies.text().strip() or 1)
        except ValueError:
            printer_cfg["copies"] = 1

        cfg = set_printer_for_point(cfg, point, printer_cfg)
        save_config(cfg)

        self.toast.show_message(f"Принтер для {point} сохранён", "ok")
        self._refresh_printer_form()

    def _clear_printer_cfg(self):
        cfg = load_config()
        point = self.printer_point_combo.currentData() or self.store.current_point

        printers = dict(cfg.get("printers") or {})
        if point in printers:
            del printers[point]
            cfg["printers"] = printers
            save_config(cfg)
            self.toast.show_message(
                f"Принтер для {point} удалён (будет использоваться общий)", "ok"
            )
        else:
            self.toast.show_message(
                f"Для точки {point} свой принтер не задан", "warn"
            )

        self._refresh_printer_form()

    def _test_print(self):
        cfg = load_config()
        point = self.printer_point_combo.currentData() or self.store.current_point
        point_cfg = get_printer_for_point(cfg, point)

        sample = "01" + "04600123456782" + "21" + "SN00000001" + "\x1d91TEST\x1d92SAMPLE01"
        try:
            result = print_code(sample, point_cfg)
        except Exception as e:
            result = {"ok": False, "info": f"{type(e).__name__}: {e}"}

        if result["ok"]:
            self.toast.show_message(f"Тест ({point}): {result['info']}", "ok")
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    # ==================================================================
    # BACKUP
    # ==================================================================
    def _page_backup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        tt = QLabel("Резервное копирование")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        sub = QLabel(f"Папка: {BACKUP_DIR}")
        sub.setStyleSheet("color:#8b97a6; font-size:11px;")
        sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(sub)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        btn_create = QPushButton("💾 Создать бэкап")
        btn_create.setProperty("primary", True)
        btn_create.clicked.connect(self._backup_create)
        toolbar.addWidget(btn_create)

        btn_restore = QPushButton("♻ Восстановить выбранный")
        btn_restore.clicked.connect(self._backup_restore)
        toolbar.addWidget(btn_restore)

        btn_folder = QPushButton("📁 Открыть папку")
        btn_folder.clicked.connect(self._backup_open_folder)
        toolbar.addWidget(btn_folder)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_backup)
        toolbar.addWidget(btn_refresh)

        toolbar.addStretch(1)
        v.addLayout(toolbar)

        self.backup_table = QTableWidget(0, 4)
        self.backup_table.setHorizontalHeaderLabels(
            ["Имя файла", "Размер", "Дата", "Путь"]
        )
        header = self.backup_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.backup_table.verticalHeader().setVisible(False)
        self.backup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.backup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.backup_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.backup_table.setShowGrid(False)
        v.addWidget(self.backup_table, stretch=1)

        self.backup_status = QLabel("")
        self.backup_status.setStyleSheet("color:#8b97a6; font-size:11px;")
        v.addWidget(self.backup_status)

        card_cleanup = QFrame(); card_cleanup.setObjectName("card")
        cvc = QVBoxLayout(card_cleanup)
        cvc.setContentsMargins(18, 14, 18, 14)
        cvc.setSpacing(10)

        ttl = QLabel("ОЧИСТКА СТАРЫХ ЗАПИСЕЙ ЖУРНАЛА")
        ttl.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;"
        )
        cvc.addWidget(ttl)

        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("Хранить журнал:"))
        self.cleanup_days = QLineEdit()
        self.cleanup_days.setFixedWidth(80)
        row.addWidget(self.cleanup_days)
        row.addWidget(QLabel("дней"))

        btn_save_ret = QPushButton("💾 Сохранить")
        btn_save_ret.clicked.connect(self._cleanup_save_retention)
        row.addWidget(btn_save_ret)

        row.addSpacing(20)

        btn_clean = QPushButton("🧹 Очистить сейчас")
        btn_clean.setProperty("primary", True)
        btn_clean.clicked.connect(self._cleanup_run)
        row.addWidget(btn_clean)

        row.addStretch(1)
        cvc.addLayout(row)

        self.cleanup_status = QLabel("")
        self.cleanup_status.setStyleSheet("color:#8b97a6; font-size:11px;")
        cvc.addWidget(self.cleanup_status)

        v.addWidget(card_cleanup)
        return w

    def _refresh_backup(self):
        files = list_backups()
        t = self.backup_table
        t.setRowCount(0)
        for f in files:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(f["name"], mono=True))
            t.setItem(r, 1, cell(f"{f['size_kb']} КБ", mono=True))
            t.setItem(r, 2, cell(f["mtime"].strftime("%Y-%m-%d %H:%M:%S"), mono=True))
            t.setItem(r, 3, cell(f["path"], mono=True))

        self.backup_status.setText(
            f"Всего бэкапов: {len(files)} (максимум {MAX_BACKUPS})"
        )

        try:
            days = self.store.get_audit_retention_days()
            self.cleanup_days.setText(str(days))
        except Exception:
            self.cleanup_days.setText("365")

        last_cleanup = self.store.get_setting("last_cleanup")
        if last_cleanup:
            self.cleanup_status.setText(
                f"Последняя очистка: {last_cleanup}. "
                f"Записи старше {self.cleanup_days.text()} дней удаляются автоматически."
            )
        else:
            self.cleanup_status.setText(
                "Очистка ещё не выполнялась. "
                "Записи старше указанного срока удаляются автоматически раз в сутки."
            )

    def _backup_create(self):
        result = create_backup()
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")
        self._refresh_backup()

    def _backup_restore(self):
        rows = self.backup_table.selectionModel().selectedRows()
        if not rows:
            self.toast.show_message("Выберите бэкап из списка", "warn")
            return

        idx = rows[0].row()
        name = self.backup_table.item(idx, 0).text()
        path = self.backup_table.item(idx, 3).text()

        reply = QMessageBox.warning(
            self, "Подтвердите восстановление",
            f"Восстановить БД из:\n{name}\n\n"
            "Текущая база будет сохранена как резервная копия.\n"
            "После восстановления потребуется ПЕРЕЗАПУСК приложения.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        result = restore_backup(path)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
            QMessageBox.information(
                self, "Готово",
                result["info"] + "\n\nЗакройте и запустите приложение заново.",
            )
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _backup_open_folder(self):
        import os
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(BACKUP_DIR))
        except Exception as e:
            self.toast.show_message(f"Не удалось открыть: {e}", "err")

    def _cleanup_save_retention(self):
        try:
            days = int(self.cleanup_days.text().strip() or "365")
        except ValueError:
            self.toast.show_message("Введите число дней", "err")
            return
        if days < 7:
            self.toast.show_message("Минимум 7 дней", "warn")
            return
        self.store.set_audit_retention_days(days)
        self.toast.show_message(f"Хранить журнал: {days} дней", "ok")
        self._refresh_backup()

    def _cleanup_run(self):
        result = self.store.cleanup_old_audit()
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")
        self._refresh_backup()

    # ==================================================================
    # LOGS
    # ==================================================================
    def _page_logs(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        tt = QLabel("Логи приложения")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        self.log_path_label = QLabel(f"Файл: {log_file_path()}")
        self.log_path_label.setStyleSheet("color:#8b97a6; font-size:11px;")
        self.log_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.log_path_label)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        lbl_level = QLabel("Уровень:")
        lbl_level.setStyleSheet("color:#8b97a6;")
        toolbar.addWidget(lbl_level)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["ВСЕ", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.log_level_combo.setFixedWidth(120)
        self.log_level_combo.currentTextChanged.connect(self._refresh_logs)
        toolbar.addWidget(self.log_level_combo)

        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Поиск по тексту...")
        self.log_search.textChanged.connect(self._refresh_logs)
        toolbar.addWidget(self.log_search, stretch=1)

        self.log_limit_combo = QComboBox()
        self.log_limit_combo.addItems(["100", "500", "1000", "5000"])
        self.log_limit_combo.setCurrentText("500")
        self.log_limit_combo.setFixedWidth(100)
        self.log_limit_combo.currentTextChanged.connect(self._refresh_logs)
        toolbar.addWidget(self.log_limit_combo)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_logs)
        toolbar.addWidget(btn_refresh)

        btn_folder = QPushButton("📁 Папка")
        btn_folder.clicked.connect(self._open_logs_folder)
        toolbar.addWidget(btn_folder)

        btn_export = QPushButton("💾 Экспорт")
        btn_export.clicked.connect(self._export_logs)
        toolbar.addWidget(btn_export)

        v.addLayout(toolbar)

        self.logs_table = QTableWidget(0, 4)
        self.logs_table.setHorizontalHeaderLabels(["Время", "Уровень", "Модуль", "Сообщение"])
        header = self.logs_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.logs_table.verticalHeader().setVisible(False)
        self.logs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.logs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.logs_table.setShowGrid(False)
        self.logs_table.setWordWrap(False)
        v.addWidget(self.logs_table, stretch=1)

        self.log_status = QLabel("")
        self.log_status.setStyleSheet("color:#8b97a6; font-size:11px;")
        v.addWidget(self.log_status)
        return w

    def _refresh_logs(self):
        import re
        from pathlib import Path

        path = Path(log_file_path())
        if not path.exists():
            self.logs_table.setRowCount(0)
            self.log_status.setText("Файл логов ещё не создан")
            return

        level_filter = self.log_level_combo.currentText()
        search_text = self.log_search.text().strip().lower()
        limit = int(self.log_limit_combo.currentText())

        pattern = re.compile(
            r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
            r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
            r"\[([^\]]+)\]\s+"
            r"(.*)$"
        )

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except Exception as e:
            log.error("Не удалось прочитать лог: %s", e)
            self.log_status.setText(f"Ошибка чтения: {e}")
            return

        parsed = []
        for line in reversed(all_lines):
            m = pattern.match(line.rstrip("\n"))
            if not m:
                continue
            time_s, level, module, message = m.groups()
            parsed.append({
                "time": time_s, "level": level,
                "module": module, "message": message,
            })
            if len(parsed) >= limit * 3:
                break

        if level_filter != "ВСЕ":
            parsed = [r for r in parsed if r["level"] == level_filter]

        if search_text:
            parsed = [r for r in parsed
                      if search_text in r["message"].lower()
                      or search_text in r["module"].lower()]

        parsed = parsed[:limit]

        t = self.logs_table
        t.setRowCount(0)
        t.setSortingEnabled(False)

        level_colors = {
            "DEBUG": "#64748b", "INFO": "#e6edf3",
            "WARNING": "#f59e0b", "ERROR": "#ef4444",
            "CRITICAL": "#dc2626",
        }

        for entry in parsed:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(entry["time"], mono=True))
            t.setItem(r, 1, cell(entry["level"],
                                 level_colors.get(entry["level"], "#e6edf3")))
            t.setItem(r, 2, cell(entry["module"], mono=True))
            t.setItem(r, 3, cell(entry["message"]))

        t.setSortingEnabled(True)

        shown = len(parsed)
        total = len(all_lines)
        self.log_status.setText(
            f"Показано: {shown} из {total} строк "
            f"(файл: {path.stat().st_size // 1024} КБ)"
        )

    def _open_logs_folder(self):
        import os
        folder = log_dir_path()
        try:
            os.startfile(folder)
        except Exception as e:
            self.toast.show_message(f"Не удалось открыть: {e}", "err")

    def _export_logs(self):
        from PySide6.QtWidgets import QFileDialog
        import csv
        from datetime import datetime

        t = self.logs_table
        if t.rowCount() == 0:
            self.toast.show_message("Нечего экспортировать", "warn")
            return

        default_name = f"chestzn_logs_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить логи", default_name, "CSV (*.csv);;Все файлы (*)"
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["Время", "Уровень", "Модуль", "Сообщение"])
                for r in range(t.rowCount()):
                    row = [
                        t.item(r, 0).text() if t.item(r, 0) else "",
                        t.item(r, 1).text() if t.item(r, 1) else "",
                        t.item(r, 2).text() if t.item(r, 2) else "",
                        t.item(r, 3).text() if t.item(r, 3) else "",
                    ]
                    writer.writerow(row)
            log.info("Экспорт логов: %s (%s строк)", path, t.rowCount())
            self.toast.show_message(f"Экспортировано: {t.rowCount()} строк", "ok")
        except Exception as e:
            log.error("Ошибка экспорта логов: %s", e)
            self.toast.show_message(f"Ошибка: {e}", "err")

    # ==================================================================
    # JOURNAL
    # ==================================================================
    def _page_journal(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Журнал операций")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_export = QPushButton("⬇ Экспорт CSV")
        btn_export.clicked.connect(self._export_journal)
        head.addWidget(btn_export)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_journal)
        head.addWidget(btn_refresh)

        btn_verify = QPushButton("🔐 Проверить цепочку")
        btn_verify.clicked.connect(self._verify_audit_chain)
        head.addWidget(btn_verify)

        v.addLayout(head)

        row1 = QHBoxLayout()
        row1.setSpacing(8)

        row1.addWidget(QLabel("С:"))
        self.j_date_from = QLineEdit()
        self.j_date_from.setPlaceholderText("ГГГГ-ММ-ДД")
        self.j_date_from.setFixedWidth(110)
        row1.addWidget(self.j_date_from)

        row1.addWidget(QLabel("По:"))
        self.j_date_to = QLineEdit()
        self.j_date_to.setPlaceholderText("ГГГГ-ММ-ДД")
        self.j_date_to.setFixedWidth(110)
        row1.addWidget(self.j_date_to)

        row1.addWidget(QLabel("Точка:"))
        self.j_point = QComboBox()
        self.j_point.setFixedWidth(140)
        row1.addWidget(self.j_point)

        row1.addWidget(QLabel("Оператор:"))
        self.j_operator = QComboBox()
        self.j_operator.setFixedWidth(160)
        row1.addWidget(self.j_operator)

        row1.addWidget(QLabel("Результат:"))
        self.j_result = QComboBox()
        self.j_result.setFixedWidth(140)
        row1.addWidget(self.j_result)

        row1.addStretch(1)

        row1.addWidget(QLabel("Строк:"))
        self.j_page_size = QComboBox()
        self.j_page_size.addItems(["50", "100", "200", "500"])
        self.j_page_size.setCurrentText("100")
        self.j_page_size.setFixedWidth(80)
        self.j_page_size.currentTextChanged.connect(self._journal_reset_page)
        row1.addWidget(self.j_page_size)

        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)

        row2.addWidget(QLabel("Поиск (GTIN/SN):"))
        self.j_search = QLineEdit()
        self.j_search.setPlaceholderText("например, 04600123456782 или SN…")
        self.j_search.returnPressed.connect(self._journal_reset_page)
        row2.addWidget(self.j_search, stretch=1)

        btn_apply = QPushButton("🔍 Применить")
        btn_apply.setProperty("primary", True)
        btn_apply.clicked.connect(self._journal_reset_page)
        row2.addWidget(btn_apply)

        btn_clear = QPushButton("✖ Сбросить фильтры")
        btn_clear.clicked.connect(self._journal_clear_filters)
        row2.addWidget(btn_clear)

        v.addLayout(row2)

        self.journal_table = table(["Время", "Действие", "Точка", "Оператор",
                                    "GTIN", "Серия", "Результат", "Создано"])
        v.addWidget(self.journal_table, stretch=1)

        pag = QHBoxLayout()
        pag.setSpacing(8)

        self.j_btn_prev = QPushButton("◀ Назад")
        self.j_btn_prev.clicked.connect(self._journal_prev_page)
        pag.addWidget(self.j_btn_prev)

        self.j_btn_next = QPushButton("Вперёд ▶")
        self.j_btn_next.clicked.connect(self._journal_next_page)
        pag.addWidget(self.j_btn_next)

        pag.addSpacing(20)
        self.j_page_label = QLabel("Страница 1 из 1")
        self.j_page_label.setStyleSheet("color:#8b97a6;")
        pag.addWidget(self.j_page_label)

        pag.addStretch(1)
        self.j_total_label = QLabel("Всего: 0")
        self.j_total_label.setStyleSheet("color:#8b97a6;")
        pag.addWidget(self.j_total_label)

        v.addLayout(pag)
        return w

    def _journal_page_size(self) -> int:
        return int(self.j_page_size.currentText())

    def _journal_total_pages(self) -> int:
        size = self._journal_page_size()
        if self._journal_total == 0:
            return 1
        return (self._journal_total + size - 1) // size

    def _journal_reset_page(self):
        self._journal_page = 0
        self._refresh_journal()

    def _journal_prev_page(self):
        if self._journal_page > 0:
            self._journal_page -= 1
            self._refresh_journal()

    def _journal_next_page(self):
        if self._journal_page < self._journal_total_pages() - 1:
            self._journal_page += 1
            self._refresh_journal()

    def _journal_clear_filters(self):
        self.j_date_from.clear()
        self.j_date_to.clear()
        self.j_search.clear()
        self.j_point.setCurrentIndex(0)
        self.j_operator.setCurrentIndex(0)
        self.j_result.setCurrentIndex(0)
        self._journal_reset_page()

    def _journal_get_filters(self) -> dict:
        return {
            "date_from": self.j_date_from.text().strip() or None,
            "date_to":   self.j_date_to.text().strip() or None,
            "point":     self.j_point.currentData(),
            "operator":  self.j_operator.currentData(),
            "result":    self.j_result.currentData(),
            "search":    self.j_search.text().strip() or None,
        }

    def _journal_fill_filter_values(self):
        try:
            values = self.store.get_audit_filter_values()
        except Exception as e:
            log.error("Ошибка при получении значений фильтров: %s", e)
            return

        def _fill(combo: QComboBox, items: list, all_label: str):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label, None)
            for item in items:
                combo.addItem(item, item)
            if current:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        _fill(self.j_point,    values["points"],    "Все")
        _fill(self.j_operator, values["operators"], "Все")
        _fill(self.j_result,   values["results"],   "Все")

    def _refresh_journal(self):
        self._journal_fill_filter_values()

        filters = self._journal_get_filters()
        size = self._journal_page_size()
        offset = self._journal_page * size

        rows, total = self.store.query_audit(filters, limit=size, offset=offset)
        self._journal_total = total

        if not rows and self._journal_page > 0:
            self._journal_page = max(0, self._journal_total_pages() - 1)
            offset = self._journal_page * size
            rows, total = self.store.query_audit(filters, limit=size, offset=offset)

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
            i = t.rowCount(); t.insertRow(i)
            t.setItem(i, 0, cell(r.get("time", ""), mono=True))
            t.setItem(i, 1, cell(r.get("action", "")))
            t.setItem(i, 2, cell(r.get("point") or "—"))
            t.setItem(i, 3, cell(r.get("operator") or "—"))
            t.setItem(i, 4, cell(r.get("gtin") or "—", mono=True))
            t.setItem(i, 5, cell(r.get("sn") or "—", mono=True))
            res = r.get("result") or "—"
            t.setItem(i, 6, cell(res, result_colors.get(res, "#f59e0b")))
            t.setItem(i, 7, cell(r.get("created_at", ""), mono=True, color="#8b97a6"))

        pages = self._journal_total_pages()
        self.j_page_label.setText(
            f"Страница {self._journal_page + 1} из {pages}"
        )
        self.j_total_label.setText(f"Всего: {total}")
        self.j_btn_prev.setEnabled(self._journal_page > 0)
        self.j_btn_next.setEnabled(self._journal_page < pages - 1)

    def _export_journal(self):
        from PySide6.QtWidgets import QFileDialog
        import csv
        from datetime import datetime

        filters = self._journal_get_filters()

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        default_name = f"chestzn_journal_{stamp}.csv"

        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт журнала", default_name,
            "CSV (*.csv);;Все файлы (*)"
        )
        if not path:
            return

        try:
            rows, total = self.store.query_audit(filters, limit=1_000_000, offset=0)

            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["Время", "Действие", "Точка", "Оператор",
                                 "GTIN", "Серия", "Результат", "Создано"])
                for r in rows:
                    writer.writerow([
                        r.get("time", ""),
                        r.get("action", ""),
                        r.get("point") or "",
                        r.get("operator") or "",
                        r.get("gtin") or "",
                        r.get("sn") or "",
                        r.get("result") or "",
                        r.get("created_at", ""),
                    ])

            log.info("Экспорт журнала (с фильтрами): %s строк → %s", total, path)
            self.toast.show_message(f"Экспортировано {total} строк", "ok")

        except Exception as e:
            log.error("Ошибка экспорта журнала: %s", e)
            self.toast.show_message(f"Ошибка: {e}", "err")

    def _verify_audit_chain(self):
        result = self.store.verify_audit_chain()

        if result["ok"]:
            QMessageBox.information(self, "Проверка журнала", result["info"])
        else:
            lines = [f"Всего записей: {result['total']}",
                     f"Повреждений: {len(result['broken'])}", ""]
            for b in result["broken"][:20]:
                lines.append(f"#{b['id']}  {b['time']}  {b['action']}")
                lines.append(f"    {b['reason']}")
            if len(result["broken"]) > 20:
                lines.append(f"...и ещё {len(result['broken']) - 20}")

            QMessageBox.warning(self, "⚠ Журнал повреждён", "\n".join(lines))
            log.warning("Журнал повреждён: %s повреждений", len(result["broken"]))

    # ==================================================================
    # SECURITY
    # ==================================================================
    def _page_security(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        tt = QLabel("Безопасность")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        sub = QLabel("Управление паролем и восстановлением доступа")
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        v.addWidget(sub)

        card1 = QFrame(); card1.setObjectName("card")
        cv1 = QVBoxLayout(card1)
        cv1.setContentsMargins(18, 14, 18, 14)
        cv1.setSpacing(10)

        title1 = QLabel("Пароль администратора")
        title1.setStyleSheet("color:#e6edf3; font-size:14px; font-weight:600;")
        cv1.addWidget(title1)

        desc1 = QLabel("Смените текущий пароль на новый. "
                       "Понадобится ввод текущего пароля.")
        desc1.setStyleSheet("color:#8b97a6; font-size:12px;")
        desc1.setWordWrap(True)
        cv1.addWidget(desc1)

        btn_change_pwd = QPushButton("🔑 Сменить пароль администратора")
        btn_change_pwd.setProperty("primary", True)
        btn_change_pwd.setMinimumHeight(38)
        btn_change_pwd.clicked.connect(self._on_change_password)
        cv1.addWidget(btn_change_pwd)

        v.addWidget(card1)

        card2 = QFrame(); card2.setObjectName("card")
        cv2 = QVBoxLayout(card2)
        cv2.setContentsMargins(18, 14, 18, 14)
        cv2.setSpacing(10)

        title2 = QLabel("Секретный вопрос (восстановление доступа)")
        title2.setStyleSheet("color:#e6edf3; font-size:14px; font-weight:600;")
        cv2.addWidget(title2)

        self.security_status = QLabel("")
        self.security_status.setWordWrap(True)
        self.security_status.setStyleSheet("font-size:12px;")
        cv2.addWidget(self.security_status)

        self.btn_security = QPushButton("Изменить секретный вопрос")
        self.btn_security.setMinimumHeight(38)
        self.btn_security.clicked.connect(self._on_change_security)
        cv2.addWidget(self.btn_security)

        v.addWidget(card2)
        v.addStretch(1)
        return w

    def _refresh_security(self):
        login = "admin"
        has_q = self.store.has_security_question(login)

        if has_q:
            q = self.store.get_security_question(login)
            self.security_status.setText(f"✅ Вопрос настроен: <b>{q}</b>")
            self.security_status.setStyleSheet("color:#22c55e; font-size:12px;")
            self.btn_security.setText("🔒 Изменить секретный вопрос")
        else:
            self.security_status.setText(
                "⚠ Вопрос <b>не настроен</b>. "
                "Восстановление пароля будет недоступно."
            )
            self.security_status.setStyleSheet("color:#f59e0b; font-size:12px;")
            self.btn_security.setText("🔒 Настроить секретный вопрос")

    def _on_change_password(self):
        win = ChangePasswordWindow(self.store, "admin", self)
        result = win.exec()
        if result == ChangePasswordWindow.Accepted and win.changed:
            self.toast.show_message("Пароль изменён", "ok")

    def _on_change_security(self):
        win = SecurityQuestionWindow(self.store, "admin", self)
        result = win.exec()
        if result == SecurityQuestionWindow.Accepted:
            self.toast.show_message("Секретный вопрос обновлён", "ok")
            self._refresh_security()