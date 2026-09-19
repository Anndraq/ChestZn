"""Админ-панель: боковое меню + QStackedWidget с разделами."""
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QGridLayout, QSizePolicy, QAbstractItemView,
    QLineEdit, QComboBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from store import Store
from barcode import render_datamatrix
from toast import Toast
from printer import (list_printers, detect_language, load_config,
                     save_config, print_code, default_printer)


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
        self.nav_journal = nav("📜  Журнал", "journal")

        sb.addStretch(1)

        # ============ STACK ============
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_dashboard())
        self.stack.addWidget(self._page_queue())
        self.stack.addWidget(self._page_points())
        self.stack.addWidget(self._page_users())
        self.stack.addWidget(self._page_integrations())
        self.stack.addWidget(self._page_templates())
        self.stack.addWidget(self._page_pool())
        self.stack.addWidget(self._page_printer())
        self.stack.addWidget(self._page_journal())

        root.addWidget(sidebar)
        root.addWidget(self.stack, stretch=1)

        self.switch("dashboard")

    # ------------------------------------------------------------------
    def switch(self, page_id: str):
        pages = {
            "dashboard": 0, "queue": 1, "points": 2, "users": 3,
            "integrations": 4, "templates": 5, "pool": 6,
            "printer": 7, "journal": 8,
        }
        idx = pages.get(page_id, 0)
        self.stack.setCurrentIndex(idx)
        for btn in [self.nav_dashboard, self.nav_queue, self.nav_points,
                    self.nav_users, self.nav_integrations, self.nav_templates,
                    self.nav_pool, self.nav_printer, self.nav_journal]:
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

        # KPI
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

        # Интеграции
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

    # ==================================================================
    # QUEUE
    # ==================================================================
    def _page_queue(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        h = QHBoxLayout()
        tt = QLabel("Очередь синхронизации"); tt.setStyleSheet("font-size:18px; font-weight:600;")
        h.addWidget(tt); h.addStretch(1)
        btn = QPushButton("🔄 Повторить все"); btn.setProperty("primary", True)
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
            t.setItem(r, 4, cell(u["last_login"], mono=True))
            t.setItem(r, 5, cell("активен", "#22c55e"))

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
        v.addStretch(1)
        return w

    def _refresh_integrations(self):
        # очищаем
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

            for k, v in [
                ("Endpoint", integ["endpoint"]),
                ("Контур", integ["contour"]),
                ("Последний ответ", integ["last"]),
                ("За сутки", f"{integ['calls_today']} вызовов"),
            ]:
                line = QLabel(f"{k}: {v}")
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

    def _check_integration(self, key: str):
        integ = self.store.integrations[key]
        if key == "onec":
            self.toast.show_message(f"{integ['name']}: timeout", "err")
        else:
            self.toast.show_message(f"{integ['name']}: связь в норме · 0.6 с", "ok")

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

        # Список шаблонов
        self.tpl_list = QTableWidget(0, 2)
        self.tpl_list.setHorizontalHeaderLabels(["Шаблон", "Версия"])
        self.tpl_list.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tpl_list.verticalHeader().setVisible(False)
        self.tpl_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tpl_list.setMaximumWidth(320)
        self.tpl_list.itemSelectionChanged.connect(self._on_template_select)
        row.addWidget(self.tpl_list)

        # Поля шаблона
        self.tpl_fields = QTableWidget(0, 2)
        self.tpl_fields.setHorizontalHeaderLabels(["Поле", "Обязательность"])
        self.tpl_fields.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tpl_fields.verticalHeader().setVisible(False)
        self.tpl_fields.setEditTriggers(QAbstractItemView.NoEditTriggers)
        row.addWidget(self.tpl_fields, stretch=1)

        # Предпросмотр
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

        # DataMatrix-пример
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
        tt = QLabel("Пул кодов ЧЗ"); tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

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

        # ---------- Список принтеров ----------
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

        # ---------- Параметры ----------
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

        # ---------- Кнопки ----------
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
    # JOURNAL
    # ==================================================================
    def _page_journal(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        tt = QLabel("Журнал операций"); tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        self.journal_table = table(["Время", "Действие", "Точка", "Оператор",
                                    "GTIN", "Серия", "Результат"])
        v.addWidget(self.journal_table, stretch=1)
        return w

    def _refresh_journal(self):
        t = self.journal_table
        t.setRowCount(0)
        for r in self.store.audit[:200]:
            i = t.rowCount(); t.insertRow(i)
            t.setItem(i, 0, cell(r["time"], mono=True))
            t.setItem(i, 1, cell(r["action"]))
            t.setItem(i, 2, cell(r.get("point", "—")))
            t.setItem(i, 3, cell(r.get("operator", "—")))
            t.setItem(i, 4, cell(r.get("gtin", "—"), mono=True))
            t.setItem(i, 5, cell(r.get("sn", "—"), mono=True))
            result = r.get("result", "—")
            color = {"успех": "#22c55e", "не найден": "#ef4444"}.get(result, "#f59e0b")
            t.setItem(i, 6, cell(result, color))