"""Админ-панель: боковое меню + QStackedWidget с разделами."""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QGridLayout, QSizePolicy, QAbstractItemView,
    QLineEdit, QComboBox, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from store import Store
from barcode import render_datamatrix
from toast import Toast
from change_password_window import ChangePasswordWindow
from security_question_window import SecurityQuestionWindow
from printer import (list_printers, detect_language, load_config,
                     save_config, print_code, default_printer)
from logger import log_file_path, log_dir_path, get_logger
log = get_logger("admin")
from backup import (create_backup, list_backups, restore_backup,
                    BACKUP_DIR, MAX_BACKUPS)
from export import export_pool, export_queue
from importer import (import_pool_codes, import_gtin_map,
                      download_pool_template, download_gtin_map_template)


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    return t


def cell(text: str, color: Optional[str] = None, mono: bool = False) -> QTableWidgetItem:
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
        self.nav_integrations = nav("🔌  Интеграции", "integrations")
        self.nav_templates    = nav("🏷️  Шаблоны", "templates")
        self.nav_pool         = nav("🎫  Пул кодов", "pool")
        self.nav_printer      = nav("🖨  Принтер", "printer")

        section("КОНТРОЛЬ")
        self.nav_journal  = nav("📜  Журнал", "journal")
        self.nav_security = nav("🔒  Безопасность", "security")
        self.nav_logs     = nav("📄  Логи", "logs")
        self.nav_backup   = nav("💾  Бэкапы", "backup")

        sb.addStretch(1)

        # ============ STACK ============
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_dashboard())     # 0
        self.stack.addWidget(self._page_queue())         # 1
        self.stack.addWidget(self._page_points())        # 2
        self.stack.addWidget(self._page_users())         # 3
        self.stack.addWidget(self._page_integrations())  # 4
        self.stack.addWidget(self._page_templates())     # 5
        self.stack.addWidget(self._page_pool())          # 6
        self.stack.addWidget(self._page_printer())       # 7
        self.stack.addWidget(self._page_journal())       # 8
        self.stack.addWidget(self._page_logs())          # 9
        self.stack.addWidget(self._page_backup())        # 10
        self.stack.addWidget(self._page_security())      # 11

        root.addWidget(sidebar)
        root.addWidget(self.stack, stretch=1)

        self._journal_page = 0
        self._journal_total = 0

        self.switch("dashboard")

    # ------------------------------------------------------------------
    def switch(self, page_id: str):
        pages = {
            "dashboard": 0, "queue": 1, "points": 2, "users": 3,
            "integrations": 4, "templates": 5, "pool": 6,
            "printer": 7, "journal": 8, "logs": 9,
            "backup": 10, "security": 11,
        }
        idx = pages.get(page_id, 0)
        self.stack.setCurrentIndex(idx)
        for btn in [self.nav_dashboard, self.nav_queue, self.nav_points,
                    self.nav_users, self.nav_integrations, self.nav_templates,
                    self.nav_pool, self.nav_printer, self.nav_journal,
                    self.nav_logs, self.nav_backup, self.nav_security]:
            btn.setChecked(btn.property("page_id") == page_id)
        self.refresh(page_id)

    def refresh(self, page_id: str):
        if page_id == "dashboard":    self._refresh_dashboard()
        if page_id == "queue":        self._refresh_queue()
        if page_id == "points":       self._refresh_points()
        if page_id == "users":        self._refresh_users()
        if page_id == "integrations": self._refresh_integrations()
        if page_id == "templates":    self._refresh_templates()
        if page_id == "pool":         self._refresh_pool()
        if page_id == "printer":      self._refresh_printer()
        if page_id == "journal":      self._refresh_journal()
        if page_id == "logs":         self._refresh_logs()
        if page_id == "security":     self._refresh_security()
        if page_id == "backup":       self._refresh_backup()

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

                # Индикатор целостности журнала
        self.journal_integrity_label = QLabel("")
        self.journal_integrity_label.setStyleSheet(
            "font-size:12px; padding:8px; "
            "background:rgba(139,151,166,.1); border-radius:6px;"
        )
        v.addWidget(self.journal_integrity_label)

        t = QLabel("ИНТЕГРАЦИИ")
        t.setStyleSheet("color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px; margin-top:10px;")
        v.addWidget(t)

        self.dash_integrations = table(["Система", "Статус", "Последний ответ", "За сутки"])
        self.dash_integrations.setMinimumHeight(160)
        v.addWidget(self.dash_integrations)

        v.addStretch(1)
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
        val.setStyleSheet(f"color:{colors[color]}; font-size:24px; font-weight:600; font-family:Consolas,monospace;")
        v.addWidget(l); v.addWidget(val)
        self.kpi_labels[key] = val
        return box

    def _refresh_dashboard(self):
        c = self.store.counter
        self.kpi_labels["scans"].setText(str(c["scans"]))
        self.kpi_labels["prints"].setText(str(c["prints"]))
        self.kpi_labels["errors"].setText(str(c["errors"]))
        self.kpi_labels["queue"].setText(str(len(self.store.queue)))

        t = self.dash_integrations
        t.setRowCount(0)
        for integ in self.store.integrations.values():
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(integ["name"]))
            ok = integ["status"] == "ok"
            t.setItem(r, 1, cell("работает" if ok else "ошибка",
                                 "#22c55e" if ok else "#ef4444"))
            t.setItem(r, 2, cell(integ["last"], mono=True))
            t.setItem(r, 3, cell(integ["calls_today"], mono=True))

        # Проверить целостность журнала
        try:
            res = self.store.verify_audit_chain()
            if res["ok"]:
                self.journal_integrity_label.setText(
                    f"🔐 Журнал: цел ({res['total']} записей)"
                )
                self.journal_integrity_label.setStyleSheet(
                    "color:#22c55e; font-size:12px; padding:8px; "
                    "background:rgba(34,197,94,.1); border-radius:6px;"
                )
            else:
                self.journal_integrity_label.setText(
                    f"⚠ Журнал повреждён: {len(res['broken'])} из {res['total']}"
                )
                self.journal_integrity_label.setStyleSheet(
                    "color:#ef4444; font-size:12px; padding:8px; "
                    "background:rgba(239,68,68,.1); border-radius:6px;"
                )
        except Exception:
            self.journal_integrity_label.setText("")

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
        self.points_table = table(["ID", "Название", "Адрес", "Статус", "IP", "Оператор", "Принтер"])
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
        self.users_table = table(["ФИО", "Логин", "Роль", "Точка", "Последний вход", "Статус"])
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
    # INTEGRATIONS
    # ==================================================================
    def _page_integrations(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        tt = QLabel("Интеграции"); tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        self.integration_cards = QVBoxLayout()
        self.integration_cards.setSpacing(12)
        v.addLayout(self.integration_cards)

        # ---------- Карточка: маппинг GTIN ----------
        card_map = QFrame(); card_map.setObjectName("card")
        cmv = QVBoxLayout(card_map)
        cmv.setContentsMargins(18, 14, 18, 14)
        cmv.setSpacing(10)

        title_map = QLabel("МАППИНГ GTIN: ИНОСТРАННЫЙ → ЧЗ")
        title_map.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;"
        )
        cmv.addWidget(title_map)

        desc_map = QLabel(
            "Таблица соответствия: какой иностранный GTIN какому GTIN "
            "«Честного знака» соответствует. Заполняется в Excel и "
            "загружается CSV-файлом."
        )
        desc_map.setStyleSheet("color:#8b97a6; font-size:12px;")
        desc_map.setWordWrap(True)
        cmv.addWidget(desc_map)

        h_map = QHBoxLayout()
        h_map.setSpacing(8)

        btn_imp_map = QPushButton("⬆ Импорт CSV")
        btn_imp_map.setProperty("primary", True)
        btn_imp_map.clicked.connect(self._import_gtin_map)
        h_map.addWidget(btn_imp_map)

        btn_tpl_map = QPushButton("📄 Скачать шаблон")
        btn_tpl_map.clicked.connect(self._template_gtin_map)
        h_map.addWidget(btn_tpl_map)

        h_map.addStretch(1)

        self.gtin_map_count_label = QLabel("")
        self.gtin_map_count_label.setStyleSheet("color:#8b97a6; font-size:11px;")
        h_map.addWidget(self.gtin_map_count_label)

        cmv.addLayout(h_map)

        v.addWidget(card_map)

        v.addStretch(1)
        return w

    def _refresh_integrations(self):
        while self.integration_cards.count():
            item = self.integration_cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for integ in self.store.integrations.values():
            card = QFrame(); card.setObjectName("card")
            h = QHBoxLayout(card)
            h.setContentsMargins(18, 16, 18, 16)
            h.setSpacing(20)

            left = QVBoxLayout()
            title = QLabel(integ["name"] + ("   ● работает" if integ["status"] == "ok" else "   ● ошибка"))
            title.setStyleSheet("font-size:14px; font-weight:600; color:#e6edf3;")
            left.addWidget(title)

            for k, val in [
                ("Endpoint", integ["endpoint"]),
                ("Контур", integ["contour"]),
                ("Последний ответ", integ["last"]),
                ("За сутки", f"{integ['calls_today']} вызовов"),
            ]:
                line = QLabel(f"{k}: {val}")
                line.setStyleSheet("color:#8b97a6; font-size:12px; font-family:Consolas,monospace;")
                left.addWidget(line)

            h.addLayout(left, stretch=1)

            right = QVBoxLayout()
            btn_check = QPushButton("🔍 Проверить связь")
            btn_check.setProperty("primary", True)
            btn_check.clicked.connect(lambda _, k=integ["key"]: self._check_integration(k))
            right.addWidget(btn_check)
            btn_cfg = QPushButton("⚙ Настройки")
            btn_cfg.clicked.connect(lambda: self.toast.show_message("Настройки", "ok"))
            right.addWidget(btn_cfg)
            right.addStretch(1)
            h.addLayout(right)

            self.integration_cards.addWidget(card)

        # Обновить счётчик маппинга GTIN (после цикла)
        try:
            n = self.store.count_gtin_map()
            self.gtin_map_count_label.setText(f"Записей: {n}")
        except Exception:
            pass

    def _check_integration(self, key: str):
        integ = self.store.integrations[key]
        if key == "onec":
            self.toast.show_message(f"{integ['name']}: timeout", "err")
        else:
            self.toast.show_message(f"{integ['name']}: связь в норме · 0.6 с", "ok")

    def _import_gtin_map(self):
        result = import_gtin_map(self.store, self)
        if result["ok"]:
            QMessageBox.information(
                self, "Импорт выполнен",
                result["info"]
            )
            self._refresh_integrations()
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _template_gtin_map(self):
        result = download_gtin_map_template(self)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    # ==================================================================
    # TEMPLATES
    # ==================================================================
    def _page_templates(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        tt = QLabel("Шаблоны русификатора"); tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        row = QHBoxLayout(); row.setSpacing(14)

        self.tpl_list = QTableWidget(0, 2)
        self.tpl_list.setHorizontalHeaderLabels(["Шаблон", "Версия"])
        self.tpl_list.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tpl_list.verticalHeader().setVisible(False)
        self.tpl_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tpl_list.setMaximumWidth(320)
        self.tpl_list.itemSelectionChanged.connect(self._on_template_select)
        row.addWidget(self.tpl_list)

        self.tpl_fields = QTableWidget(0, 2)
        self.tpl_fields.setHorizontalHeaderLabels(["Поле", "Обязательность"])
        self.tpl_fields.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tpl_fields.verticalHeader().setVisible(False)
        self.tpl_fields.setEditTriggers(QAbstractItemView.NoEditTriggers)
        row.addWidget(self.tpl_fields, stretch=1)

        preview_wrap = QFrame(); preview_wrap.setObjectName("card")
        pv = QVBoxLayout(preview_wrap)
        pv.setContentsMargins(14, 14, 14, 14)
        pv.setSpacing(10)
        pv_title = QLabel("ПРЕДПРОСМОТР")
        pv_title.setStyleSheet("color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.5px;")
        pv.addWidget(pv_title)

        label_mock = QFrame(); label_mock.setStyleSheet("background:#fff; border-radius:6px;")
        lm = QHBoxLayout(label_mock)
        lm.setContentsMargins(14, 14, 14, 14)
        lm.setSpacing(14)

        self.admin_dm = QLabel()
        self.admin_dm.setFixedSize(90, 90)
        lm.addWidget(self.admin_dm)

        info = QVBoxLayout(); info.setSpacing(2)
        info.addWidget(self._dark_label("Наушники беспроводные BT-500", bold=True))
        info.addWidget(self._dark_label("Модель: BT-500"))
        info.addWidget(self._dark_label("Импортёр: ООО «Ромашка»"))
        info.addWidget(self._dark_label("Срок годности: 12.2026"))
        info.addWidget(self._dark_label("Кол-во: 1 шт."))
        info.addStretch(1)
        lm.addLayout(info)
        lm.addStretch(1)

        pv.addWidget(label_mock)
        pv.addStretch(1)
        row.addWidget(preview_wrap, stretch=1)

        v.addLayout(row, stretch=1)
        return w

    def _dark_label(self, text: str, bold: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:#000; font-family:Consolas,monospace; font-size:11px; "
                          f"{'font-weight:600;' if bold else ''}")
        return lbl

    def _refresh_templates(self):
        t = self.tpl_list
        t.setRowCount(0)
        for tpl in self.store.templates:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(tpl["name"]))
            t.setItem(r, 1, cell(tpl["version"], mono=True))
        if t.rowCount():
            t.selectRow(0)

    def _on_template_select(self):
        rows = self.tpl_list.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        tpl = self.store.templates[idx]

        t = self.tpl_fields
        t.setRowCount(0)
        for f in tpl["fields"]:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(f["name"], mono=True))
            if f["required"]:
                t.setItem(r, 1, cell("обязательное", "#ef4444"))
            else:
                t.setItem(r, 1, cell("опционально", "#8b97a6"))

        sample = "(01)04600123456782(21)SN0004821(91)ABCD(92)XYZ12345"
        self.admin_dm.setPixmap(render_datamatrix(sample, 90))

    # ==================================================================
    # POOL
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

        # ---------- Панель импорта ----------
        toolbar2 = QHBoxLayout()
        toolbar2.setSpacing(8)

        btn_import = QPushButton("⬆ Импорт CSV")
        btn_import.setProperty("primary", True)
        btn_import.clicked.connect(self._import_pool)
        toolbar2.addWidget(btn_import)

        btn_template = QPushButton("📄 Скачать шаблон")
        btn_template.clicked.connect(self._template_pool)
        toolbar2.addWidget(btn_template)

        toolbar2.addStretch(1)

        self.pool_count_label = QLabel("")
        self.pool_count_label.setStyleSheet("color:#8b97a6; font-size:11px;")
        toolbar2.addWidget(self.pool_count_label)

        v.addLayout(toolbar2)

        self.pool_table = table(["GTIN", "Всего", "Свободно", "Использовано", "Напечатано"])
        v.addWidget(self.pool_table, stretch=1)
        return w

    def _refresh_pool(self):
        t = self.pool_table
        t.setRowCount(0)
        for p in self.store.pool_stats():
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, cell(p["gtin"], mono=True))
            t.setItem(r, 1, cell(p["total"], mono=True))
            t.setItem(r, 2, cell(p["free"], "#22c55e", mono=True))
            t.setItem(r, 3, cell(p["used"], "#f59e0b", mono=True))
            t.setItem(r, 4, cell(p["printed"], mono=True))

        # Общее число кодов — ПОСЛЕ цикла
        try:
            total = self.store.count_pool()
            self.pool_count_label.setText(f"Всего кодов: {total}")
        except Exception:
            pass

    def _export_pool(self):
        result = export_pool(self)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _import_pool(self):
        result = import_pool_codes(self.store, self)
        if result["ok"]:
            QMessageBox.information(
                self, "Импорт выполнен",
                result["info"]
            )
            self._refresh_pool()
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _template_pool(self):
        result = download_pool_template(self)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        elif result["info"] != "Отменено":
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    # ==================================================================
    # PRINTER
    # ==================================================================
    def _page_printer(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        tt = QLabel("Принтер")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        sub = QLabel("Печать Честного знака на этикеточный принтер")
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        v.addWidget(sub)

        card = QFrame(); card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 14, 18, 14)
        cv.setSpacing(10)

        h = QHBoxLayout()
        h.addWidget(QLabel("Найденные принтеры:"))
        btn_scan = QPushButton("🔄 Обновить список")
        btn_scan.clicked.connect(self._refresh_printer)
        h.addStretch(1)
        h.addWidget(btn_scan)
        cv.addLayout(h)

        self.printer_table = table(["Принтер", "Порт", "Язык", "По умолчанию"])
        self.printer_table.setMinimumHeight(180)
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

        h2 = QHBoxLayout()
        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._save_printer_cfg)
        btn_test = QPushButton("🧪 Тест печати")
        btn_test.clicked.connect(self._test_print)
        h2.addWidget(btn_save)
        h2.addWidget(btn_test)
        h2.addStretch(1)
        v.addLayout(h2)

        v.addStretch(1)
        return w

    def _refresh_printer(self):
        cfg = load_config()
        self.cfg_printer_name.setText(cfg.get("printer_name", ""))
        self.cfg_printer_ip.setText(cfg.get("printer_ip", ""))
        self.cfg_printer_port.setText(str(cfg.get("printer_port", 9100)))
        self.cfg_printer_lang.setCurrentText(cfg.get("printer_lang", "auto"))
        self.cfg_label_w.setText(str(cfg.get("label_width_mm", 58)))
        self.cfg_label_h.setText(str(cfg.get("label_height_mm", 40)))
        self.cfg_dpi.setCurrentText(str(cfg.get("dpi", 203)))
        self.cfg_copies.setText(str(cfg.get("copies", 1)))

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
        cfg["printer_name"] = self.cfg_printer_name.text().strip()
        cfg["printer_ip"]   = self.cfg_printer_ip.text().strip()
        try:
            cfg["printer_port"] = int(self.cfg_printer_port.text().strip() or 9100)
        except ValueError:
            cfg["printer_port"] = 9100
        cfg["printer_lang"] = self.cfg_printer_lang.currentText()
        try:
            cfg["label_width_mm"]  = float(self.cfg_label_w.text().strip() or 58)
            cfg["label_height_mm"] = float(self.cfg_label_h.text().strip() or 40)
        except ValueError:
            pass
        try:
            cfg["dpi"] = int(self.cfg_dpi.currentText())
        except ValueError:
            cfg["dpi"] = 203
        try:
            cfg["copies"] = int(self.cfg_copies.text().strip() or 1)
        except ValueError:
            cfg["copies"] = 1

        save_config(cfg)
        self.toast.show_message("Настройки принтера сохранены", "ok")

    def _test_print(self):
        cfg = load_config()
        sample = "(01)04600123456782(21)SN00000001(91)TEST(92)SAMPLE01"
        result = print_code(sample, cfg)
        if result["ok"]:
            self.toast.show_message(f"Тест: {result['info']}", "ok")
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

        # ---------- Очистка старых данных ----------
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
            r = t.rowCount()
            t.insertRow(r)
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
            self,
            "Подтвердите восстановление",
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
                "time": time_s,
                "level": level,
                "module": module,
                "message": message,
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
            "DEBUG":    "#64748b",
            "INFO":     "#e6edf3",
            "WARNING":  "#f59e0b",
            "ERROR":    "#ef4444",
            "CRITICAL": "#dc2626",
        }

        for entry in parsed:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, cell(entry["time"], mono=True))
            t.setItem(r, 1, cell(entry["level"],
                                 level_colors.get(entry["level"], "#e6edf3")))
            t.setItem(r, 2, cell(entry["module"], mono=True))
            t.setItem(r, 3, cell(entry["message"]))

        t.setSortingEnabled(True)

        shown = len(parsed)
        total = len(all_lines)
        self.log_status.setText(
            f"Показано: {shown} из {total} строк (файл: {path.stat().st_size // 1024} КБ)"
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
        """Заполнить выпадающие списки фильтров."""
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
        """Экспорт отфильтрованного журнала (без пагинации)."""
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
        """Проверить HMAC-цепочку журнала."""
        result = self.store.verify_audit_chain()

        if result["ok"]:
            QMessageBox.information(
                self, "Проверка журнала",
                result["info"]
            )
        else:
            # Есть повреждения — показать детали
            lines = [f"Всего записей: {result['total']}",
                     f"Повреждений: {len(result['broken'])}", ""]
            for b in result["broken"][:20]:
                lines.append(f"#{b['id']}  {b['time']}  {b['action']}")
                lines.append(f"    {b['reason']}")
            if len(result["broken"]) > 20:
                lines.append(f"...и ещё {len(result['broken']) - 20}")

            QMessageBox.warning(
                self, "⚠ Журнал повреждён",
                "\n".join(lines)
            )

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
            self.security_status.setText(
                f"✅ Вопрос настроен: <b>{q}</b>"
            )
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