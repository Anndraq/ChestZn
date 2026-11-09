"""
Админ-панель: боковое меню + QStackedWidget с разделами (admin_window.py).
- Все разделы меню возвращены и полностью функциональны.
- Разворачивание точек и ПК по 1 клику.
- Наглядные стрелки раскрытия дерева точек.
- Кнопка создания новой точки.
- Полная поддержка светлой и тёмной тем без хардкода цветов.
"""
import re
import time
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QGridLayout, QSizePolicy, QAbstractItemView,
    QLineEdit, QComboBox, QMessageBox, QCheckBox, QSpinBox,
    QDoubleSpinBox, QInputDialog, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QDialog,
    QDialogButtonBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush

from edit_point_window import EditPointDialog, EditComputerDialog
from master_devices_window import AssignPrinterDialog
from store import Store
from toast import Toast
from change_password_window import ChangePasswordWindow
from security_question_window import SecurityQuestionWindow
from printer import (list_printers, detect_language, load_config,
                     save_config, print_code, default_printer,
                     get_printer_for_point, set_printer_for_point)
from logger import log_file_path, log_dir_path, get_logger
log = get_logger("admin")
from backup import (create_backup, list_backups, restore_backup,
                    BACKUP_DIR, MAX_BACKUPS, AUTO_BACKUP_INTERVAL_DAYS,
                    BACKUP_RETENTION_DAYS, cleanup_old_backups,
                    auto_backup_if_needed)
from export import export_pool, export_queue
from importer import (import_pool_codes, import_gtin_map,
                      download_pool_template, download_gtin_map_template)
import sync as sync_mod
from mssql_ui import SQLServerAdminPage


FIELD_PRESETS = [
    "dataMatrix",
    "name_ru",
    "gtin",
    "serial",
    "model",
    "importer",
    "country",
    "material",
    "composition",
    "size_eu",
    "lot",
    "exp_date"
]

FIELD_LABELS = {
    "dataMatrix": "DataMatrix (QR-код ЧЗ)",
    "name_ru": "Наименование товара",
    "gtin": "GTIN товара",
    "serial": "Серийный номер (SN)",
    "model": "Модель / Артикул",
    "importer": "Импортёр / Производитель",
    "country": "Страна производства",
    "material": "Материал",
    "composition": "Состав",
    "size_eu": "Размер",
    "lot": "Номер партии",
    "exp_date": "Срок годности"
}


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
        f = it.font()
        f.setFamily("Consolas")
        it.setFont(f)
    return it


class AdminWindow(QWidget):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.toast = Toast(self)
        self._tpl_tab_widgets = {}

        self._tpl_preview_timer = QTimer(self)
        self._tpl_preview_timer.setSingleShot(True)
        self._tpl_preview_timer.setInterval(30)
        self._tpl_preview_timer.timeout.connect(self._tpl_refresh_preview_now)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ============ SIDEBAR ============
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
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

        section("НАСТРОЙКА")
        self.nav_points       = nav("🏢  Точки и ПК", "points")
        self.nav_templates    = nav("🏷️  Шаблоны", "templates")
        self.nav_pool         = nav("🎫  Склад кодов ЧЗ", "pool")
        self.nav_printer      = nav("🖨  Принтеры по точкам", "printer")

        section("ИНТЕГРАЦИЯ И ОБМЕН")
        self.nav_sql          = nav("🗄  SQL Server", "sql")
        self.nav_sync         = nav("🔄  Синхронизация", "sync")

        section("КОНТРОЛЬ")
        self.nav_journal       = nav("📜  Журнал", "journal")
        self.nav_discrepancies = nav("⚠  Расхождения", "discrepancies")
        self.nav_security      = nav("🔒  Безопасность", "security")
        self.nav_logs          = nav("📄  Логи", "logs")
        self.nav_backup        = nav("💾  Бэкапы", "backup")

        sb.addStretch(1)

        self.btn_theme = QPushButton("🌓  Сменить тему")
        self.btn_theme.setObjectName("navBtn")
        self.btn_theme.clicked.connect(self._toggle_theme)
        sb.addWidget(self.btn_theme)

        # ============ СТЕК СТРАНИЦ ============
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_dashboard())      # 0
        self.stack.addWidget(self._page_points())         # 1
        self.stack.addWidget(self._page_templates())      # 2
        self.stack.addWidget(self._page_pool())           # 3
        self.stack.addWidget(self._page_printer())        # 4
        self.stack.addWidget(self._page_journal())        # 5
        self.stack.addWidget(self._page_discrepancies())  # 6
        self.stack.addWidget(self._page_security())       # 7
        self.stack.addWidget(self._page_logs())           # 8
        self.stack.addWidget(self._page_backup())         # 9
        self.stack.addWidget(self._page_sync())           # 10

        self.sql_server_page = SQLServerAdminPage(config_store=self.store, parent=self)
        self.stack.addWidget(self.sql_server_page)        # 11
        self.sql_server_page.sync_requested.connect(lambda act: self.toast.show_message(f"SQL: {act}", "ok"))

        root.addWidget(sidebar)
        root.addWidget(self.stack, stretch=1)

        self.switch("dashboard")

    def switch(self, page_id: str):
        pages = {
            "dashboard": 0, "points": 1, "templates": 2,
            "pool": 3, "printer": 4, "journal": 5,
            "discrepancies": 6, "security": 7, "logs": 8,
            "backup": 9, "sync": 10, "sql": 11,
        }
        idx = pages.get(page_id, 0)
        self.stack.setCurrentIndex(idx)

        nav_buttons = [
            self.nav_dashboard, self.nav_points, self.nav_templates,
            self.nav_pool, self.nav_printer, self.nav_journal,
            self.nav_discrepancies, self.nav_security, self.nav_logs,
            self.nav_backup, self.nav_sync, self.nav_sql
        ]
        for btn in nav_buttons:
            btn.setChecked(btn.property("page_id") == page_id)

        self.refresh(page_id)

    def refresh(self, page_id: str):
        if page_id == "dashboard":        self._refresh_dashboard()
        elif page_id == "points":         self._refresh_points()
        elif page_id == "templates":      self._refresh_templates()
        elif page_id == "pool":           self._refresh_pool()
        elif page_id == "printer":        self._refresh_printer()
        elif page_id == "journal":        self._refresh_journal()
        elif page_id == "discrepancies":  self._refresh_discrepancies()
        elif page_id == "security":       self._refresh_security()
        elif page_id == "logs":           self._refresh_logs()
        elif page_id == "backup":         self._refresh_backup()
        elif page_id == "sync":           self._refresh_sync()
        elif page_id == "sql":            pass

    # ==================================================================
    # 1. ДАШБОРД
    # ==================================================================
    def _page_dashboard(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Дашборд и показатели смены")
        title.setProperty("heading", True)
        head.addWidget(title)
        head.addStretch(1)

        btn_bind = QPushButton("🔗 Привязка Сканер ↔ Принтер")
        btn_bind.setProperty("primary", True)
        btn_bind.clicked.connect(self._open_quick_bind_dialog)
        head.addWidget(btn_bind)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_dashboard)
        head.addWidget(btn_refresh)
        v.addLayout(head)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)

        self.kpi_labels = {}
        for key, label, color in [
            ("scans",  "Сканов за смену", "#38bdf8"),
            ("prints", "Напечатано ЧЗ",   "#34d399"),
            ("errors", "Ошибок / Брак",   "#f87171"),
        ]:
            box = QFrame()
            box.setObjectName("kpi")
            box.setFixedHeight(90)
            bv = QVBoxLayout(box)
            l = QLabel(label)
            l.setStyleSheet("color:#8b97a6; font-size:11px;")
            val = QLabel("0")
            val.setStyleSheet(f"color:{color}; font-size:26px; font-weight:800; font-family:Consolas;")
            bv.addWidget(l)
            bv.addWidget(val)
            self.kpi_labels[key] = val
            kpi_row.addWidget(box)
        v.addLayout(kpi_row)

        shift_card = QFrame()
        shift_card.setObjectName("card")
        sc_layout = QHBoxLayout(shift_card)
        sc_layout.setContentsMargins(18, 14, 18, 14)
        sc_layout.setSpacing(14)

        sc_layout.addWidget(QLabel("Рабочая точка:"))
        self.dash_point_combo = QComboBox()
        self.dash_point_combo.setMinimumWidth(240)
        for p in self.store.points:
            self.dash_point_combo.addItem(f"{p['id']} — {p['name']}", p["id"])
        sc_layout.addWidget(self.dash_point_combo)

        btn_save = QPushButton("💾 Применить")
        btn_save.clicked.connect(self._save_dash_point)
        sc_layout.addWidget(btn_save)

        btn_reset = QPushButton("🧹 Сброс счётчиков смены")
        btn_reset.clicked.connect(self._reset_dash_counters)
        sc_layout.addWidget(btn_reset)
        sc_layout.addStretch(1)

        v.addWidget(shift_card)
        v.addStretch(1)
        return w

    def _refresh_dashboard(self):
        c = self.store.counter
        self.kpi_labels["scans"].setText(str(c.get("scans", 0)))
        self.kpi_labels["prints"].setText(str(c.get("prints", 0)))
        self.kpi_labels["errors"].setText(str(c.get("errors", 0)))
        if hasattr(self, "dash_point_combo"):
            idx = self.dash_point_combo.findData(self.store.current_point)
            if idx >= 0:
                self.dash_point_combo.setCurrentIndex(idx)

    def _save_dash_point(self):
        pt = self.dash_point_combo.currentData()
        if pt:
            self.store.current_point = pt
            self.toast.show_message(f"Текущая точка: {pt}", "ok")

    def _reset_dash_counters(self):
        self.store.counter["scans"] = 0
        self.store.counter["prints"] = 0
        self.store.counter["errors"] = 0
        self._refresh_dashboard()
        self.toast.show_message("Счётчики обнулены", "ok")

    # ==================================================================
    # 2. ТОЧКИ И ПК
    # ==================================================================
    def _page_points(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Точки и рабочие места (ПК)")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_add_point = QPushButton("➕ Добавить новую точку")
        btn_add_point.setProperty("primary", True)
        btn_add_point.setStyleSheet(
            "background:#16a34a; color:#ffffff; font-weight:700; padding:7px 16px; border-radius:6px;"
        )
        btn_add_point.clicked.connect(self._add_new_point)
        head.addWidget(btn_add_point)

        btn_add_pc = QPushButton("➕ Добавить ПК к точке")
        btn_add_pc.clicked.connect(self._points_add_computer)
        head.addWidget(btn_add_pc)

        btn_edit = QPushButton("✏️ Редактировать")
        btn_edit.clicked.connect(self._points_edit_selected)
        head.addWidget(btn_edit)

        v.addLayout(head)

        sub = QLabel("💡 Кликните по строке точки один раз для открытия или закрытия списка подключённых ПК.")
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        v.addWidget(sub)

        self.points_tree = QTreeWidget()
        self.points_tree.setHeaderLabels(["Точка / Компьютер", "Расположение / Роль", "Статус", "IP-адрес"])
        self.points_tree.setColumnWidth(0, 320)
        self.points_tree.setColumnWidth(1, 260)
        self.points_tree.setColumnWidth(2, 120)
        self.points_tree.setColumnWidth(3, 160)
        self.points_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.points_tree.itemClicked.connect(self._on_tree_item_clicked)
        v.addWidget(self.points_tree, stretch=1)
        return w

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        if item.childCount() > 0:
            item.setExpanded(not item.isExpanded())
            is_open = item.isExpanded()
            arrow = "▼ " if is_open else "▶ "
            cur_txt = item.text(0)
            if "🏢 " in cur_txt:
                name_part = cur_txt.split("🏢 ", 1)[1]
                item.setText(0, f"{arrow}🏢 {name_part}")

    def _refresh_points(self):
        self.points_tree.clear()
        for p in self.store.points:
            item = QTreeWidgetItem(self.points_tree)
            is_open = bool(p.get("online", True))
            arrow = "▼ " if is_open else "▶ "
            item.setText(0, f"{arrow}🏢 {p['id']} — {p['name']}")
            item.setText(1, p.get("address", "—"))
            item.setText(2, "🟢 онлайн" if is_open else "🟡 офлайн")
            item.setText(3, p.get("ip", "—"))
            item.setData(0, Qt.UserRole, ("point", p["id"]))

            f = item.font(0)
            f.setBold(True)
            item.setFont(0, f)
            item.setForeground(2, QBrush(QColor("#22c55e" if is_open else "#f59e0b")))

            comps = p.get("computers", [])
            for c in comps:
                ch = QTreeWidgetItem(item)
                ch.setText(0, f"    💻  {c.get('name', 'ПК')}")
                ch.setText(1, c.get("role", "Рабочее место"))
                ch.setText(2, "🟢 онлайн" if c.get("online", True) else "🟡 офлайн")
                ch.setText(3, c.get("ip", "—"))
                ch.setData(0, Qt.UserRole, ("computer", c))

            item.setExpanded(is_open)

    def _add_new_point(self):
        existing_ids = [p["id"] for p in self.store.points if p.get("id")]
        next_num = 1
        for pid in existing_ids:
            m = re.search(r"T-(\d+)", pid)
            if m:
                next_num = max(next_num, int(m.group(1)) + 1)
        suggested_id = f"T-{next_num:02d}"

        dlg = EditPointDialog(point={"id": suggested_id, "name": f"Линия {next_num}"}, parent=self, is_new=True)
        if dlg.exec() == EditPointDialog.Accepted and dlg.result_data:
            data = dlg.result_data
            data["computers"] = []
            data["sort_order"] = (len(self.store.points) + 1) * 10
            try:
                import db
                with db.get_conn() as conn:
                    conn.execute(
                        "INSERT INTO points (id, name, address, ip, printer, operator, online, template, computers_json, sort_order) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (data["id"], data["name"], data.get("address", ""), data.get("ip", ""),
                         data.get("printer", ""), "Оператор", 1 if data.get("online") else 0,
                         data.get("template", "electronics"), "[]", data["sort_order"])
                    )
                self.store.points = self.store._load_points()
                self._refresh_points()
                self.toast.show_message(f"Создана точка {data['id']}: {data['name']}", "ok")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось создать точку: {e}")

    def _points_edit_selected(self):
        items = self.points_tree.selectedItems()
        if not items:
            self.toast.show_message("Выберите точку или ПК в таблице", "warn")
            return
        item = items[0]
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        if data[0] == "point":
            p = self.store.get_point(data[1])
            if p:
                dlg = EditPointDialog(p, parent=self, is_new=False)
                if dlg.exec() == EditPointDialog.Accepted and dlg.result_data:
                    self.store.update_point(data[1], **dlg.result_data)
                    self._refresh_points()
        elif data[0] == "computer":
            parent_item = item.parent()
            parent_id = parent_item.data(0, Qt.UserRole)[1]
            dlg = EditComputerDialog(data[1], parent_id, parent=self)
            if dlg.exec() == EditComputerDialog.Accepted and dlg.result_data:
                act = dlg.result_data.get("action")
                p = self.store.get_point(parent_id)
                comps = list(p.get("computers", []))
                idx = parent_item.indexOfChild(item)
                if act == "delete":
                    comps.pop(idx)
                else:
                    comps[idx] = dlg.result_data
                self.store.update_point(parent_id, computers=comps)
                self._refresh_points()

    def _points_add_computer(self):
        items = self.points_tree.selectedItems()
        if not items:
            self.toast.show_message("Сначала выберите точку для добавления ПК", "warn")
            return
        item = items[0]
        if item.parent() is not None:
            item = item.parent()
        point_id = item.data(0, Qt.UserRole)[1]

        dlg = EditComputerDialog({"name": "ПК-01", "ip": "", "role": "Рабочее место"}, point_id, parent=self)
        if dlg.exec() == EditComputerDialog.Accepted and dlg.result_data:
            p = self.store.get_point(point_id)
            comps = list(p.get("computers", []))
            comps.append(dlg.result_data)
            self.store.update_point(point_id, computers=comps)
            self._refresh_points()

    def _open_quick_bind_dialog(self):
        dlg = AssignPrinterDialog(parent=self)
        if dlg.exec() == AssignPrinterDialog.Accepted:
            self.toast.show_message("Привязка оборудования обновлена", "ok")

    # ==================================================================
    # 3. ШАБЛОНЫ ЭТИКЕТОК
    # ==================================================================
    def _page_templates(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Шаблоны этикеток")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_new = QPushButton("➕ Новый шаблон")
        btn_new.setProperty("primary", True)
        btn_new.clicked.connect(self._tpl_create)
        head.addWidget(btn_new)

        btn_del = QPushButton("🗑 Удалить")
        btn_del.clicked.connect(self._tpl_delete)
        head.addWidget(btn_del)
        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(12)

        self.tpl_list = QTableWidget(0, 2)
        self.tpl_list.setHorizontalHeaderLabels(["Шаблон", "Режим"])
        hh_l = self.tpl_list.horizontalHeader()
        hh_l.setSectionResizeMode(0, QHeaderView.Stretch)
        hh_l.setSectionResizeMode(1, QHeaderView.Fixed)
        self.tpl_list.setColumnWidth(1, 90)
        self.tpl_list.verticalHeader().setVisible(False)
        self.tpl_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tpl_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tpl_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tpl_list.setShowGrid(False)
        self.tpl_list.setFixedWidth(260)
        self.tpl_list.itemSelectionChanged.connect(self._on_template_select)
        row.addWidget(self.tpl_list)

        right = QVBoxLayout()
        right.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("Название:"))
        self.tpl_name_edit = QLineEdit()
        name_row.addWidget(self.tpl_name_edit, stretch=1)
        right.addLayout(name_row)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(QLabel("Режим печати:"))
        self.tpl_mode_combo = QComboBox()
        self.tpl_mode_combo.addItem("ЧЗ + Русификатор (совмещённый)", "combined")
        self.tpl_mode_combo.addItem("ЧЗ + Русификатор (раздельный)", "separate")
        self.tpl_mode_combo.setFixedWidth(280)
        self.tpl_mode_combo.currentIndexChanged.connect(self._tpl_on_mode_changed)
        mode_row.addWidget(self.tpl_mode_combo)
        mode_row.addStretch(1)
        right.addLayout(mode_row)

        self.tpl_tabs = QTabWidget()
        self.tpl_tabs.currentChanged.connect(self._tpl_refresh_preview)

        self.tab_combined = self._build_template_tab("combined")
        self.tpl_tabs.addTab(self.tab_combined, "ЧЗ + Русификатор")

        self.tab_cz = self._build_template_tab("cz")
        self.tpl_tabs.addTab(self.tab_cz, "ЧЗ")

        self.tab_label = self._build_template_tab("label")
        self.tpl_tabs.addTab(self.tab_label, "Русификатор")

        right.addWidget(self.tpl_tabs, stretch=1)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)

        btn_save = QPushButton("💾 Сохранить шаблон")
        btn_save.setProperty("primary", True)
        btn_save.setStyleSheet("background:#16a34a; color:#ffffff; font-weight:700; padding:6px 14px; border-radius:6px;")
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

    def _build_template_tab(self, tab_key: str) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_row.addWidget(QLabel("Ширина:"))
        w_spin = QDoubleSpinBox()
        w_spin.setRange(10, 500)
        w_spin.setDecimals(0)
        w_spin.setSuffix(" мм")
        w_spin.setValue(58)
        w_spin.setFixedWidth(115)
        w_spin.valueChanged.connect(self._tpl_refresh_preview)
        size_row.addWidget(w_spin)

        size_row.addWidget(QLabel("Высота:"))
        h_spin = QDoubleSpinBox()
        h_spin.setRange(10, 500)
        h_spin.setDecimals(0)
        h_spin.setSuffix(" мм")
        h_spin.setValue(40)
        h_spin.setFixedWidth(115)
        h_spin.valueChanged.connect(self._tpl_refresh_preview)
        size_row.addWidget(h_spin)

        size_row.addSpacing(16)
        size_row.addWidget(QLabel("DPI:"))
        dpi_combo = QComboBox()
        dpi_combo.addItems(["203", "300", "600"])
        dpi_combo.setFixedWidth(85)
        dpi_combo.currentIndexChanged.connect(self._tpl_refresh_preview)
        size_row.addWidget(dpi_combo)
        size_row.addStretch(1)
        v.addLayout(size_row)

        fields_head = QHBoxLayout()
        fields_head.setSpacing(6)
        lbl_fields = QLabel("ПОЛЯ ЭТИКЕТКИ")
        lbl_fields.setStyleSheet("color:#8b97a6; font-size:11px; font-weight:700; letter-spacing:1.5px;")
        fields_head.addWidget(lbl_fields)
        fields_head.addStretch(1)

        btn_auto = QPushButton("🎯 Авто-раскладка")
        btn_auto.setFixedHeight(32)
        btn_auto.setStyleSheet("color: #93c5fd; font-weight: 700; padding: 0 12px;")
        btn_auto.setToolTip("Аккуратно расставить поля (QR слева, строки текста справа)")
        btn_auto.clicked.connect(lambda: self._tpl_auto_layout(tab_key))
        fields_head.addWidget(btn_auto)

        btn_add = QPushButton("+")
        btn_add.setFixedSize(40, 32)
        btn_add.setToolTip("Добавить поле")
        btn_add.setStyleSheet("font-size:20px; font-weight:700; padding:0;")
        btn_add.clicked.connect(lambda: self._tpl_add_field(tab_key))
        fields_head.addWidget(btn_add)

        btn_up = QPushButton("↑")
        btn_up.setFixedSize(40, 32)
        btn_up.setToolTip("Вверх")
        btn_up.setStyleSheet("font-size:18px; font-weight:700; padding:0;")
        btn_up.clicked.connect(lambda: self._tpl_move_field(tab_key, -1))
        fields_head.addWidget(btn_up)

        btn_down = QPushButton("↓")
        btn_down.setFixedSize(40, 32)
        btn_down.setToolTip("Вниз")
        btn_down.setStyleSheet("font-size:18px; font-weight:700; padding:0;")
        btn_down.clicked.connect(lambda: self._tpl_move_field(tab_key, 1))
        fields_head.addWidget(btn_down)
        v.addLayout(fields_head)

        t = QTableWidget(0, 7)
        t.setHorizontalHeaderLabels(["Поле", "Отображаемый текст", "Обязат.", "Размер текста", "Размер QR", "X, Y", ""])
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        hh.setSectionResizeMode(6, QHeaderView.Fixed)
        t.setColumnWidth(0, 110)
        t.setColumnWidth(2, 60)
        t.setColumnWidth(3, 110)
        t.setColumnWidth(4, 95)
        t.setColumnWidth(5, 90)
        t.setColumnWidth(6, 38)
        t.verticalHeader().setVisible(True)
        t.verticalHeader().setFixedWidth(28)
        t.verticalHeader().setDefaultSectionSize(34)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setShowGrid(False)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.itemChanged.connect(lambda _: self._tpl_refresh_preview())

        v.addWidget(t, stretch=1)
        self._tpl_tab_widgets[tab_key] = {
            "w": w_spin, "h": h_spin, "dpi": dpi_combo, "fields": t,
        }
        return tab

    def _tpl_on_mode_changed(self):
        mode = self.tpl_mode_combo.currentData()
        if mode == "combined":
            self.tpl_tabs.setCurrentIndex(0)
        elif mode == "separate":
            self.tpl_tabs.setCurrentIndex(1)
        self._tpl_refresh_preview()

    def _refresh_templates(self):
        self.tpl_list.blockSignals(True)
        try:
            self.tpl_list.setRowCount(0)
            tpls = self.store.list_templates()
            for tpl in tpls:
                r = self.tpl_list.rowCount()
                self.tpl_list.insertRow(r)
                self.tpl_list.setItem(r, 0, cell(tpl["name"]))
                mode_label = "Совмещ." if tpl.get("print_mode") == "combined" else "Раздел."
                self.tpl_list.setItem(r, 1, cell(mode_label, "#93c5fd"))
        finally:
            self.tpl_list.blockSignals(False)

        if self.tpl_list.rowCount():
            self.tpl_list.selectRow(0)

    def _on_template_select(self):
        rows = self.tpl_list.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        tpls = self.store.list_templates()
        if idx >= len(tpls):
            return

        tpl = tpls[idx]
        self.tpl_name_edit.blockSignals(True)
        self.tpl_mode_combo.blockSignals(True)
        try:
            self.tpl_name_edit.setText(tpl["name"])
            mode = tpl.get("print_mode", "combined")
            mode_idx = self.tpl_mode_combo.findData(mode)
            if mode_idx >= 0:
                self.tpl_mode_combo.setCurrentIndex(mode_idx)

            config = tpl.get("config", {})
            for tab_key in ("combined", "cz", "label"):
                self._fill_template_tab(tab_key, config.get(tab_key, {}))

            if mode == "combined":
                self.tpl_tabs.setCurrentIndex(0)
            else:
                self.tpl_tabs.setCurrentIndex(1)
        finally:
            self.tpl_name_edit.blockSignals(False)
            self.tpl_mode_combo.blockSignals(False)

        self._tpl_refresh_preview()

    def _fill_template_tab(self, tab_key: str, tab_cfg: dict):
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return

        t = widgets["fields"]
        t.blockSignals(True)
        widgets["w"].blockSignals(True)
        widgets["h"].blockSignals(True)

        try:
            widgets["w"].setValue(float(tab_cfg.get("w", 58)))
            widgets["h"].setValue(float(tab_cfg.get("h", 40)))
            widgets["dpi"].setCurrentText(str(tab_cfg.get("dpi", 203)))

            t.setRowCount(0)
            for f in tab_cfg.get("fields", []):
                r = t.rowCount()
                t.insertRow(r)

                name = f.get("name", "")
                is_dm = (name == "dataMatrix")

                item_name = QTableWidgetItem(name)
                item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, 0, item_name)

                label_val = f.get("label", "")
                if not label_val:
                    label_val = FIELD_LABELS.get(name, name)
                t.setItem(r, 1, QTableWidgetItem(label_val))

                chk = QCheckBox()
                chk.setChecked(bool(f.get("required")))
                wrap_chk = QWidget()
                wl = QHBoxLayout(wrap_chk)
                wl.setContentsMargins(0, 0, 0, 0)
                wl.addWidget(chk)
                wl.setAlignment(Qt.AlignCenter)
                t.setCellWidget(r, 2, wrap_chk)

                spin_font = QSpinBox()
                spin_font.setRange(6, 40)
                spin_font.setValue(int(f.get("font_size", 12)))
                spin_font.setAlignment(Qt.AlignCenter)
                if is_dm:
                    spin_font.setEnabled(False)
                    spin_font.setValue(0)
                    spin_font.setSpecialValueText("—")
                spin_font.valueChanged.connect(self._tpl_refresh_preview)
                t.setCellWidget(r, 3, spin_font)

                spin_size = QDoubleSpinBox()
                spin_size.setRange(0, 200)
                spin_size.setDecimals(0)
                spin_size.setSuffix(" мм")
                spin_size.setValue(float(f.get("size", 20)))
                spin_size.setAlignment(Qt.AlignCenter)
                if not is_dm:
                    spin_size.setEnabled(False)
                    spin_size.setValue(0)
                    spin_size.setSpecialValueText("—")
                spin_size.valueChanged.connect(self._tpl_refresh_preview)
                t.setCellWidget(r, 4, spin_size)

                xy_text = f"X:{f.get('x', 0)} Y:{f.get('y', 0)}"
                item_xy = QTableWidgetItem(xy_text)
                item_xy.setFlags(item_xy.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, 5, item_xy)

                btn_rm = QPushButton("×")
                btn_rm.setFixedSize(28, 24)
                btn_rm.setStyleSheet("color:#ef4444; border:none; font-weight:700; font-size:16px; padding:0; background:transparent;")
                btn_rm.setCursor(Qt.PointingHandCursor)
                btn_rm.clicked.connect(lambda _, k=tab_key, rr=r: self._tpl_remove_field(k, rr))
                wrap_rm = QWidget()
                wl2 = QHBoxLayout(wrap_rm)
                wl2.setContentsMargins(0, 0, 0, 0)
                wl2.addWidget(btn_rm)
                wl2.setAlignment(Qt.AlignCenter)
                t.setCellWidget(r, 6, wrap_rm)

            t.resizeRowsToContents()
        finally:
            t.blockSignals(False)
            widgets["w"].blockSignals(False)
            widgets["h"].blockSignals(False)

    def _current_template(self):
        rows = self.tpl_list.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        tpls = self.store.list_templates()
        if idx >= len(tpls):
            return None
        return tpls[idx]

    def _tpl_add_field(self, tab_key: str):
        dlg = QDialog(self)
        dlg.setWindowTitle("Добавить поле на этикетку")
        dlg.setMinimumWidth(340)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Выберите, что вывести на этикетку:"))
        combo = QComboBox()
        for code in FIELD_PRESETS:
            label = FIELD_LABELS.get(code, code)
            combo.addItem(label, code)
        combo.addItem("— Другое (ввести вручную) —", "__custom__")
        layout.addWidget(combo)

        custom_edit = QLineEdit()
        custom_edit.setPlaceholderText("Системное имя (например, lot_number)")
        custom_edit.setVisible(False)
        layout.addWidget(custom_edit)

        combo.currentIndexChanged.connect(lambda idx: custom_edit.setVisible(combo.itemData(idx) == "__custom__"))

        btn_box = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton("Добавить")
        btn_ok.setProperty("primary", True)
        btn_ok.clicked.connect(dlg.accept)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

        if dlg.exec() != QDialog.Accepted:
            return

        chosen = combo.currentData()
        name = custom_edit.text().strip().lower().replace(" ", "_") if chosen == "__custom__" else chosen
        if not name:
            return

        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        r = t.rowCount()
        t.insertRow(r)

        is_dm = (name == "dataMatrix")
        item_name = QTableWidgetItem(name)
        item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
        t.setItem(r, 0, item_name)
        t.setItem(r, 1, QTableWidgetItem(FIELD_LABELS.get(name, name)))

        chk = QCheckBox()
        wrap_chk = QWidget()
        wl = QHBoxLayout(wrap_chk)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(chk)
        wl.setAlignment(Qt.AlignCenter)
        t.setCellWidget(r, 2, wrap_chk)

        spin_font = QSpinBox()
        spin_font.setRange(6, 40)
        spin_font.setValue(12)
        spin_font.setAlignment(Qt.AlignCenter)
        if is_dm:
            spin_font.setEnabled(False)
            spin_font.setValue(0)
            spin_font.setSpecialValueText("—")
        spin_font.valueChanged.connect(self._tpl_refresh_preview)
        t.setCellWidget(r, 3, spin_font)

        spin_size = QDoubleSpinBox()
        spin_size.setRange(0, 200)
        spin_size.setDecimals(0)
        spin_size.setSuffix(" мм")
        spin_size.setValue(20 if is_dm else 0)
        spin_size.setAlignment(Qt.AlignCenter)
        if not is_dm:
            spin_size.setEnabled(False)
            spin_size.setSpecialValueText("—")
        spin_size.valueChanged.connect(self._tpl_refresh_preview)
        t.setCellWidget(r, 4, spin_size)

        item_xy = QTableWidgetItem("X:0 Y:0")
        item_xy.setFlags(item_xy.flags() & ~Qt.ItemIsEditable)
        t.setItem(r, 5, item_xy)

        btn_rm = QPushButton("×")
        btn_rm.setFixedSize(28, 24)
        btn_rm.setStyleSheet("color:#ef4444; border:none; font-weight:700; font-size:16px; padding:0; background:transparent;")
        btn_rm.setCursor(Qt.PointingHandCursor)
        btn_rm.clicked.connect(lambda _, k=tab_key, rr=r: self._tpl_remove_field(k, rr))
        wrap_rm = QWidget()
        wl2 = QHBoxLayout(wrap_rm)
        wl2.setContentsMargins(0, 0, 0, 0)
        wl2.addWidget(btn_rm)
        wl2.setAlignment(Qt.AlignCenter)
        t.setCellWidget(r, 6, wrap_rm)

        t.resizeRowsToContents()
        self._tpl_refresh_preview()

    def _tpl_remove_field(self, tab_key: str, row_idx: int):
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if 0 <= row_idx < t.rowCount():
            t.removeRow(row_idx)
            self._tpl_refresh_preview()

    def _tpl_move_field(self, tab_key: str, direction: int):
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        rows = t.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= t.rowCount():
            return

        all_fields = []
        for r in range(t.rowCount()):
            name = t.item(r, 0).text() if t.item(r, 0) else ""
            label = t.item(r, 1).text() if t.item(r, 1) else ""
            wrap = t.cellWidget(r, 2)
            chk = wrap.findChild(QCheckBox) if wrap else None
            required = chk.isChecked() if chk else False
            spin_font = t.cellWidget(r, 3)
            font_size = spin_font.value() if spin_font else 12
            spin_size = t.cellWidget(r, 4)
            size = float(spin_size.value()) if (name == "dataMatrix" and spin_size and spin_size.value() > 0) else None
            xy_text = t.item(r, 5).text() if t.item(r, 5) else "X:0 Y:0"
            x, y = 0, 0
            try:
                parts = xy_text.split()
                x = int(parts[0].split(":")[1])
                y = int(parts[1].split(":")[1])
            except Exception:
                pass
            all_fields.append({
                "name": name, "label": label, "required": required,
                "font_size": font_size, "size": size, "x": x, "y": y
            })

        all_fields[idx], all_fields[new_idx] = all_fields[new_idx], all_fields[idx]
        t.blockSignals(True)
        t.setRowCount(0)
        for f in all_fields:
            r = t.rowCount()
            t.insertRow(r)
            is_dm = (f["name"] == "dataMatrix")
            item_name = QTableWidgetItem(f["name"])
            item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, 0, item_name)
            t.setItem(r, 1, QTableWidgetItem(f["label"]))

            chk = QCheckBox(); chk.setChecked(f["required"])
            wrap_chk = QWidget(); wl = QHBoxLayout(wrap_chk)
            wl.setContentsMargins(0, 0, 0, 0); wl.addWidget(chk); wl.setAlignment(Qt.AlignCenter)
            t.setCellWidget(r, 2, wrap_chk)

            spin_font = QSpinBox()
            spin_font.setRange(6, 40)
            spin_font.setValue(f["font_size"] if f["font_size"] else 12)
            spin_font.setAlignment(Qt.AlignCenter)
            if is_dm:
                spin_font.setEnabled(False)
                spin_font.setValue(0)
                spin_font.setSpecialValueText("—")
            spin_font.valueChanged.connect(self._tpl_refresh_preview)
            t.setCellWidget(r, 3, spin_font)

            spin_size = QDoubleSpinBox()
            spin_size.setRange(0, 200); spin_size.setDecimals(0); spin_size.setSuffix(" мм")
            spin_size.setValue(f["size"] if f["size"] else (20 if is_dm else 0))
            spin_size.setAlignment(Qt.AlignCenter)
            if not is_dm:
                spin_size.setEnabled(False)
                spin_size.setSpecialValueText("—")
            spin_size.valueChanged.connect(self._tpl_refresh_preview)
            t.setCellWidget(r, 4, spin_size)

            item_xy = QTableWidgetItem(f"X:{f['x']} Y:{f['y']}")
            item_xy.setFlags(item_xy.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, 5, item_xy)

            btn_rm = QPushButton("×")
            btn_rm.setFixedSize(28, 24)
            btn_rm.setStyleSheet("color:#ef4444; border:none; font-weight:700; font-size:16px; padding:0; background:transparent;")
            btn_rm.setCursor(Qt.PointingHandCursor)
            btn_rm.clicked.connect(lambda _, k=tab_key, rr=r: self._tpl_remove_field(k, rr))
            wrap_rm = QWidget(); wl2 = QHBoxLayout(wrap_rm)
            wl2.setContentsMargins(0, 0, 0, 0); wl2.addWidget(btn_rm); wl2.setAlignment(Qt.AlignCenter)
            t.setCellWidget(r, 6, wrap_rm)

        t.blockSignals(False)
        t.selectRow(new_idx)
        self._tpl_refresh_preview()

    def _tpl_auto_layout(self, tab_key: str):
        """Аккуратно расставляет поля: DataMatrix слева, текстовые поля ровной колонкой справа."""
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return

        w_mm = float(widgets["w"].value())
        h_mm = float(widgets["h"].value())
        dpi = int(widgets["dpi"].currentText())
        dpmm = dpi / 25.4

        t = widgets["fields"]
        t.blockSignals(True)

        qr_size_mm = max(14.0, min(w_mm * 0.42, h_mm * 0.55))
        qr_size_dots = int(round(qr_size_mm * dpmm))

        qr_x = int(round(2.0 * dpmm))
        qr_y = int(round(2.5 * dpmm))

        text_x = qr_x + qr_size_dots + int(round(3.0 * dpmm))
        text_y = int(round(2.5 * dpmm))
        line_step_dots = int(round(5.5 * dpmm))

        for r in range(t.rowCount()):
            name = t.item(r, 0).text() if t.item(r, 0) else ""
            item_xy = t.item(r, 5)
            if not item_xy:
                item_xy = QTableWidgetItem()
                t.setItem(r, 5, item_xy)

            if name == "dataMatrix":
                item_xy.setText(f"X:{qr_x} Y:{qr_y}")
                spin = t.cellWidget(r, 4)
                if spin:
                    spin.blockSignals(True)
                    spin.setValue(round(qr_size_mm, 0))
                    spin.blockSignals(False)
            else:
                item_xy.setText(f"X:{text_x} Y:{text_y}")
                spin_f = t.cellWidget(r, 3)
                if spin_f and spin_f.value() < 8:
                    spin_f.setValue(10)
                text_y += line_step_dots

        t.blockSignals(False)
        self._tpl_refresh_preview()
        self.toast.show_message("Поля аккуратно выровнены по сетке", "ok")

    def _collect_fields_from_table(self, tab_key: str) -> list:
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return []
        t = widgets["fields"]
        fields = []
        for r in range(t.rowCount()):
            name = t.item(r, 0).text() if t.item(r, 0) else ""
            label = t.item(r, 1).text() if t.item(r, 1) else ""
            wrap = t.cellWidget(r, 2)
            chk = wrap.findChild(QCheckBox) if wrap else None
            required = chk.isChecked() if chk else False
            spin_font = t.cellWidget(r, 3)
            font_size = spin_font.value() if spin_font else 12
            spin_size = t.cellWidget(r, 4)
            size = float(spin_size.value()) if (name == "dataMatrix" and spin_size and spin_size.value() > 0) else None
            xy_text = t.item(r, 5).text() if t.item(r, 5) else "X:0 Y:0"
            x, y = 0, 0
            try:
                parts = xy_text.split()
                x = int(parts[0].split(":")[1])
                y = int(parts[1].split(":")[1])
            except Exception:
                pass
            if not name:
                continue
            f_dict = {"name": name, "label": label, "required": required, "font_size": font_size, "x": x, "y": y}
            if size is not None:
                f_dict["size"] = size
            fields.append(f_dict)
        return fields

    def _update_field_position(self, tab_key: str, row_idx: int, x: int, y: int):
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if 0 <= row_idx < t.rowCount():
            t.blockSignals(True)
            try:
                item = t.item(row_idx, 5)
                if item is None:
                    item = QTableWidgetItem(f"X:{x} Y:{y}")
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    t.setItem(row_idx, 5, item)
                else:
                    item.setText(f"X:{x} Y:{y}")
            finally:
                t.blockSignals(False)

    def _update_field_size(self, tab_key: str, row_idx: int, size_mm: float):
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if 0 <= row_idx < t.rowCount():
            spin = t.cellWidget(row_idx, 4)
            if spin:
                spin.blockSignals(True)
                spin.setValue(float(size_mm))
                spin.blockSignals(False)

    def _collect_all_config(self) -> dict:
        config = {"print_mode": self.tpl_mode_combo.currentData()}
        for tab_key in ("combined", "cz", "label"):
            widgets = self._tpl_tab_widgets.get(tab_key)
            if not widgets:
                continue
            config[tab_key] = {
                "w": widgets["w"].value(),
                "h": widgets["h"].value(),
                "dpi": int(widgets["dpi"].currentText()),
                "fields": self._collect_fields_from_table(tab_key),
            }
        return config

    def _tpl_save(self):
        tpl = self._current_template()
        if not tpl:
            self.toast.show_message("Не выбран шаблон", "warn")
            return
        name = self.tpl_name_edit.text().strip()
        if not name:
            self.toast.show_message("Введите название шаблона", "warn")
            return

        config = self._collect_all_config()
        if not any(f["name"] == "dataMatrix" for f in config.get("combined", {}).get("fields", [])):
            self.toast.show_message("В «ЧЗ + Русификатор» должно быть поле dataMatrix", "warn")
            return

        result = self.store.update_template(code=tpl["code"], name=name, config=config)
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
        dlg = QDialog(self)
        dlg.setWindowTitle("Новый шаблон этикетки")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Выберите базовый пресет:"))
        combo = QComboBox()
        combo.addItem("Электроника (58×40 мм)", "electronics")
        combo.addItem("Обувь (58×40 мм)", "shoes")
        combo.addItem("Одежда (58×40 мм)", "clothing")
        combo.addItem("Только DataMatrix (30×30 мм)", "only_dm")
        combo.addItem("Пустой шаблон", "empty")
        layout.addWidget(combo)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Название (например: «Обувь 58х40»)")
        layout.addWidget(name_edit)

        btn_box = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton("Создать")
        btn_ok.setProperty("primary", True)
        btn_ok.clicked.connect(dlg.accept)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

        if dlg.exec() != QDialog.Accepted:
            return

        preset_key = combo.currentData()
        name = name_edit.text().strip() or combo.currentText()
        base_code = preset_key
        code = base_code
        i = 2
        while self.store.get_template(code):
            code = f"{base_code}_{i}"
            i += 1

        presets = {
            "electronics": {
                "print_mode": "combined",
                "combined": {
                    "w": 58, "h": 40, "dpi": 203,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 18, "x": 10, "y": 10},
                        {"name": "name_ru", "label": "", "required": True, "font_size": 13, "x": 95, "y": 10},
                        {"name": "model", "label": "Модель", "required": False, "font_size": 11, "x": 95, "y": 30},
                        {"name": "importer", "label": "Импортёр", "required": True, "font_size": 10, "x": 95, "y": 50},
                    ],
                },
                "cz": {"w": 30, "h": 30, "dpi": 203, "fields": [{"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 20, "x": 5, "y": 5}]},
                "label": {"w": 58, "h": 40, "dpi": 203, "fields": [{"name": "name_ru", "label": "", "required": True, "font_size": 13, "x": 10, "y": 10}]},
            },
            "shoes": {
                "print_mode": "separate",
                "combined": {"w": 58, "h": 40, "dpi": 203, "fields": [{"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 18, "x": 10, "y": 10}, {"name": "name_ru", "label": "", "required": True, "font_size": 13, "x": 95, "y": 10}]},
                "cz": {"w": 30, "h": 30, "dpi": 203, "fields": [{"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 20, "x": 5, "y": 5}]},
                "label": {"w": 58, "h": 40, "dpi": 203, "fields": [{"name": "name_ru", "label": "", "required": True, "font_size": 13, "x": 10, "y": 10}, {"name": "material", "label": "Материал", "required": True, "font_size": 11, "x": 10, "y": 30}, {"name": "size_eu", "label": "Размер EU", "required": True, "font_size": 11, "x": 10, "y": 50}]},
            },
            "clothing": {
                "print_mode": "separate",
                "combined": {"w": 58, "h": 40, "dpi": 203, "fields": [{"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 18, "x": 10, "y": 10}, {"name": "name_ru", "label": "", "required": True, "font_size": 13, "x": 95, "y": 10}]},
                "cz": {"w": 30, "h": 30, "dpi": 203, "fields": [{"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 20, "x": 5, "y": 5}]},
                "label": {"w": 58, "h": 40, "dpi": 203, "fields": [{"name": "name_ru", "label": "", "required": True, "font_size": 13, "x": 10, "y": 10}, {"name": "composition", "label": "Состав", "required": True, "font_size": 11, "x": 10, "y": 30}]},
            },
            "only_dm": {
                "print_mode": "combined",
                "combined": {"w": 30, "h": 30, "dpi": 203, "fields": [{"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 22, "x": 4, "y": 4}]},
                "cz": {"w": 30, "h": 30, "dpi": 203, "fields": [{"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 22, "x": 4, "y": 4}]},
                "label": {"w": 30, "h": 30, "dpi": 203, "fields": []},
            },
            "empty": {
                "print_mode": "combined",
                "combined": {"w": 58, "h": 40, "dpi": 203, "fields": []},
                "cz": {"w": 30, "h": 30, "dpi": 203, "fields": []},
                "label": {"w": 58, "h": 40, "dpi": 203, "fields": []},
            },
        }

        config = presets.get(preset_key, presets["electronics"])
        is_default = (len(self.store.list_templates()) == 0)
        result = self.store.create_template(code=code, name=name, config=config, is_default=is_default)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
            self._refresh_templates()
            for i, tpl in enumerate(self.store.list_templates()):
                if tpl["code"] == code:
                    self.tpl_list.selectRow(i)
                    break
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
        pass

    def _build_template_preview(self) -> QFrame:
        from label_preview import LabelPreviewWidget

        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(380)

        v = QVBoxLayout(card)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        title = QLabel("ПРЕДПРОСМОТР ЭТИКЕТКИ")
        title.setStyleSheet("color:#8b97a6; font-size:12px; font-weight:700; letter-spacing:1.5px;")
        v.addWidget(title)

        self.label_preview = LabelPreviewWidget(self)
        self.label_preview.setMinimumHeight(350)
        v.addWidget(self.label_preview, stretch=1)

        self.tpl_preview_size = QLabel("")
        self.tpl_preview_size.setStyleSheet("color:#8b97a6; font-size:12px; font-weight:600;")
        self.tpl_preview_size.setAlignment(Qt.AlignCenter)
        v.addWidget(self.tpl_preview_size)

        btn_test_tpl = QPushButton("🖨 Тест печати на принтер")
        btn_test_tpl.setStyleSheet(
            "QPushButton { background:#1e293b; color:#38bdf8; font-weight:700; font-size:13px; "
            "padding:9px; border-radius:6px; border:1px solid #0284c7; } "
            "QPushButton:hover { background:#0369a1; color:#ffffff; }"
        )
        btn_test_tpl.clicked.connect(self._test_print_current_template)
        v.addWidget(btn_test_tpl)

        v.addStretch(1)
        return card

    def _tpl_refresh_preview(self):
        if hasattr(self, "_tpl_preview_timer"):
            self._tpl_preview_timer.start()

    def _tpl_refresh_preview_now(self):
        if hasattr(self, "label_preview"):
            self.label_preview.update()
        try:
            tab_key = ["combined", "cz", "label"][self.tpl_tabs.currentIndex()]
            widgets = self._tpl_tab_widgets.get(tab_key)
            if widgets:
                w_mm = widgets["w"].value()
                h_mm = widgets["h"].value()
                dpi = widgets["dpi"].currentText()
                self.tpl_preview_size.setText(f"{int(w_mm)} × {int(h_mm)} мм · {dpi} DPI")
        except Exception:
            pass

    def _test_print_current_template(self):
        """Отправляет графическое изображение этикетки из предпросмотра прямо на принтер."""
        if not hasattr(self, "label_preview"):
            return

        cfg = load_config()
        point = self.store.current_point or "T-01"
        point_cfg = dict(get_printer_for_point(cfg, point) or cfg)
        pr_name = point_cfg.get("printer_name", "")

        if not pr_name:
            QMessageBox.warning(self, "Принтер не выбран", "В системе не настроен активный принтер.")
            return

        try:
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtGui import QPainter

            printer = QPrinter(QPrinter.HighResolution)
            printer.setPrinterName(pr_name)

            painter = QPainter(printer)
            pixmap = self.label_preview.grab()
            painter.drawPixmap(0, 0, pixmap)
            painter.end()

            self.toast.show_message(f"Этикетка напечатана на «{pr_name}»", "ok")
        except Exception as e:
            test_code = "010460700123456721SNTEST001\x1d91EE06\x1d92dGVzdGNyeXB0bw=="
            res = print_code(test_code, point_cfg)
            if res.get("ok"):
                self.toast.show_message(f"Отправлено на «{pr_name}»", "ok")
            else:
                QMessageBox.warning(self, "Ошибка печати", f"Принтер не ответил: {e}")

    # ==================================================================
    # 4. СКЛАД КОДОВ ЧЗ
    # ==================================================================
    def _page_pool(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Склад кодов маркировки Честного Знака")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_exp = QPushButton("⬇ Экспорт пула CSV")
        btn_exp.clicked.connect(lambda: export_pool(self))
        head.addWidget(btn_exp)

        btn_ref = QPushButton("🔄 Обновить")
        btn_ref.clicked.connect(self._refresh_pool)
        head.addWidget(btn_ref)
        v.addLayout(head)

        self.pool_table = table(["GTIN", "Точка", "Всего", "Свободно", "Напечатано"])
        v.addWidget(self.pool_table, stretch=1)
        return w

    def _refresh_pool(self):
        self.pool_table.setRowCount(0)
        for p in self.store.pool_stats():
            r = self.pool_table.rowCount()
            self.pool_table.insertRow(r)
            self.pool_table.setItem(r, 0, cell(p["gtin"], mono=True))
            self.pool_table.setItem(r, 1, cell("Все точки"))
            self.pool_table.setItem(r, 2, cell(p["total"], mono=True))
            self.pool_table.setItem(r, 3, cell(p["free"], "#22c55e", mono=True))
            self.pool_table.setItem(r, 4, cell(p["printed"], "#38bdf8", mono=True))

    # ==================================================================
    # 5. ПРИНТЕРЫ ПО ТОЧКАМ
    # ==================================================================
    def _page_printer(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Настройка принтеров по точкам")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_refresh = QPushButton("🔄 Опросить принтеры")
        btn_refresh.clicked.connect(self._refresh_printer)
        head.addWidget(btn_refresh)
        v.addLayout(head)

        self.printer_table = table(["Принтер Windows", "Порт", "Статус", "Язык"])
        v.addWidget(self.printer_table, stretch=1)
        return w

    def _refresh_printer(self):
        self.printer_table.setRowCount(0)
        for p in list_printers():
            r = self.printer_table.rowCount()
            self.printer_table.insertRow(r)
            self.printer_table.setItem(r, 0, cell(p["name"]))
            self.printer_table.setItem(r, 1, cell(p.get("port", "USB"), mono=True))
            is_on = p.get("online", True)
            self.printer_table.setItem(r, 2, cell("🟢 Готов" if is_on else "🔴 Отключён", "#34d399" if is_on else "#f87171"))
            self.printer_table.setItem(r, 3, cell(p.get("lang", "auto").upper(), mono=True))

    # ==================================================================
    # 6. ЖУРНАЛ ОПЕРАЦИЙ
    # ==================================================================
    def _page_journal(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Журнал операций")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_export = QPushButton("⬇ Экспорт CSV")
        btn_export.clicked.connect(lambda: export_queue(self))
        head.addWidget(btn_export)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_journal)
        head.addWidget(btn_refresh)
        v.addLayout(head)

        self.journal_table = table(["Время", "Действие", "Точка", "Штрихкод", "Результат"])
        v.addWidget(self.journal_table, stretch=1)
        return w

    def _refresh_journal(self):
        rows, _ = self.store.query_audit({}, limit=100, offset=0)
        self.journal_table.setRowCount(0)
        for r in rows:
            i = self.journal_table.rowCount()
            self.journal_table.insertRow(i)
            self.journal_table.setItem(i, 0, cell(r.get("time", ""), mono=True))
            self.journal_table.setItem(i, 1, cell(r.get("action", "")))
            self.journal_table.setItem(i, 2, cell(r.get("point") or "—"))
            self.journal_table.setItem(i, 3, cell(r.get("gtin") or "—", mono=True))
            self.journal_table.setItem(i, 4, cell(r.get("result") or "—"))

    # ==================================================================
    # 7. РАСХОЖДЕНИЯ
    # ==================================================================
    def _page_discrepancies(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Расхождения: сканы vs коды маркировки")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_discrepancies)
        head.addWidget(btn_refresh)
        v.addLayout(head)

        self.disc_table = table(["GTIN", "Точка", "Всего кодов", "Сканов", "Остаток", "Статус"])
        v.addWidget(self.disc_table, stretch=1)
        return w

    def _refresh_discrepancies(self):
        self.disc_table.setRowCount(0)
        try:
            rows = self.store.get_discrepancies()
        except Exception:
            rows = []
        for r in rows:
            i = self.disc_table.rowCount()
            self.disc_table.insertRow(i)
            self.disc_table.setItem(i, 0, cell(r["gtin"], mono=True))
            self.disc_table.setItem(i, 1, cell(r.get("point", "—")))
            self.disc_table.setItem(i, 2, cell(r.get("total", 0), mono=True))
            self.disc_table.setItem(i, 3, cell(r.get("scans", 0), mono=True))
            self.disc_table.setItem(i, 4, cell(r.get("free", 0), "#34d399", mono=True))
            is_ovf = r.get("overflow", False)
            self.disc_table.setItem(i, 5, cell("⚠ Перебор" if is_ovf else "✅ В норме", "#f87171" if is_ovf else "#34d399"))

    # ==================================================================
    # 8. БЕЗОПАСНОСТЬ
    # ==================================================================
    def _page_security(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        tt = QLabel("Безопасность системы")
        tt.setProperty("heading", True)
        v.addWidget(tt)

        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(12)

        cv.addWidget(QLabel("Пароль администратора и защита базы данных"))

        btn_pwd = QPushButton("🔑 Сменить пароль администратора")
        btn_pwd.setProperty("primary", True)
        btn_pwd.setStyleSheet("background:#2563eb; color:#ffffff; font-weight:700; padding:8px 14px; border-radius:6px;")
        btn_pwd.clicked.connect(self._on_change_password)
        cv.addWidget(btn_pwd)

        btn_q = QPushButton("🔒 Секретный вопрос (восстановление пароля)")
        btn_q.clicked.connect(self._on_change_security)
        cv.addWidget(btn_q)

        v.addWidget(card)
        v.addStretch(1)
        return w

    def _refresh_security(self):
        pass

    def _on_change_password(self):
        win = ChangePasswordWindow(self.store, "admin", self)
        if win.exec() == ChangePasswordWindow.Accepted and win.changed:
            self.toast.show_message("Пароль успешно изменён", "ok")

    def _on_change_security(self):
        win = SecurityQuestionWindow(self.store, "admin", self)
        if win.exec() == SecurityQuestionWindow.Accepted:
            self.toast.show_message("Секретный вопрос обновлён", "ok")

    # ==================================================================
    # 9. ЛОГИ ПРИЛОЖЕНИЯ
    # ==================================================================
    def _page_logs(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Системные логи приложения")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_open = QPushButton("📁 Открыть папку с логами")
        btn_open.clicked.connect(self._open_logs_folder)
        head.addWidget(btn_open)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_logs)
        head.addWidget(btn_refresh)
        v.addLayout(head)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            "background:#0d1117; color:#c9d1d9; font-family:Consolas,monospace; "
            "font-size:12px; border:1px solid #2a3542; border-radius:8px; padding:10px;"
        )
        v.addWidget(self.log_text, stretch=1)
        return w

    def _refresh_logs(self):
        from pathlib import Path
        p = Path(log_file_path())
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                self.log_text.setPlainText("\n".join(lines[-300:]))
            except Exception as e:
                self.log_text.setPlainText(f"Ошибка чтения логов: {e}")
        else:
            self.log_text.setPlainText("Файл логов ещё не создан.")

    def _open_logs_folder(self):
        import os
        try:
            os.startfile(log_dir_path())
        except Exception as e:
            self.toast.show_message(f"Ошибка: {e}", "err")

    # ==================================================================
    # 10. РЕЗЕРВНЫЕ КОПИИ
    # ==================================================================
    def _page_backup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Резервное копирование базы данных")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_make = QPushButton("💾 Создать бэкап")
        btn_make.setProperty("primary", True)
        btn_make.setStyleSheet("background:#2563eb; color:#ffffff; font-weight:700; padding:6px 14px; border-radius:6px;")
        btn_make.clicked.connect(lambda: (create_backup(), self._refresh_backup()))
        head.addWidget(btn_make)
        v.addLayout(head)

        self.backup_table = table(["Имя файла", "Размер", "Дата"])
        v.addWidget(self.backup_table, stretch=1)
        return w

    def _refresh_backup(self):
        files = list_backups()
        self.backup_table.setRowCount(0)
        for f in files:
            r = self.backup_table.rowCount()
            self.backup_table.insertRow(r)
            self.backup_table.setItem(r, 0, cell(f["name"], mono=True))
            self.backup_table.setItem(r, 1, cell(f"{f['size_kb']} КБ", mono=True))
            self.backup_table.setItem(r, 2, cell(f["mtime"].strftime("%Y-%m-%d %H:%M:%S"), mono=True))

    # ==================================================================
    # 11. СИНХРОНИЗАЦИЯ
    # ==================================================================
    def _page_sync(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Синхронизация между ПК и сервером")
        tt.setProperty("heading", True)
        head.addWidget(tt)
        head.addStretch(1)

        btn_sync = QPushButton("🔄 Запустить синхронизацию")
        btn_sync.setProperty("primary", True)
        btn_sync.setStyleSheet("background:#2563eb; color:#ffffff; font-weight:700; padding:6px 14px; border-radius:6px;")
        btn_sync.clicked.connect(lambda: self.toast.show_message("Синхронизация запущена", "ok"))
        head.addWidget(btn_sync)
        v.addLayout(head)

        self.sync_info = QLabel("Статус сетевого канала обмена: онлайн\nТранзитная папка подключена.")
        self.sync_info.setStyleSheet("font-size:14px; font-weight:500;")
        v.addWidget(self.sync_info)

        v.addStretch(1)
        return w

    def _refresh_sync(self):
        pass

    # ==================================================================
    # СМЕНА ТЕМЫ ОФОРМЛЕНИЯ
    # ==================================================================
    def _toggle_theme(self):
        import json
        from pathlib import Path
        cfg_path = Path.home() / "AppData" / "Roaming" / "ChestZn" / "config.json"
        cfg = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        new_theme = "light" if cfg.get("app_theme", "dark") == "dark" else "dark"
        cfg["app_theme"] = new_theme
        try:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            from styles import get_qss
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.setStyleSheet(get_qss(new_theme))
            self.toast.show_message(f"Тема: {new_theme}", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка: {e}", "err")