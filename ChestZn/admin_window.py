"""Админ-панель: боковое меню + QStackedWidget с разделами."""
import re
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QGridLayout, QSizePolicy, QAbstractItemView,
    QLineEdit, QComboBox, QMessageBox, QCheckBox, QSpinBox,
    QDoubleSpinBox, QInputDialog, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QDialog
)

from edit_point_window import EditPointDialog
from device_dialogs import (
    EditComputerDialog, EditPrinterDialog, EditScannerDialog
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
                    BACKUP_DIR, MAX_BACKUPS, AUTO_BACKUP_INTERVAL_DAYS,
                    BACKUP_RETENTION_DAYS, cleanup_old_backups,
                    auto_backup_if_needed)
from export import export_pool, export_queue
from importer import (import_pool_codes, import_gtin_map,
                      download_pool_template, download_gtin_map_template)
import sync as sync_mod
from mssql_ui import SQLServerAdminPage


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


def _is_light_theme() -> bool:
    """Быстрая проверка светлой темы из кэша styles.py (0 мс, без обращений к диску в cell())."""
    try:
        from styles import is_light_theme
        return is_light_theme()
    except Exception:
        return False


def cell(text, color: Optional[str] = None, mono: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text) if text is not None else "—")
    if color:
        c_low = str(color).lower()
        if _is_light_theme():
            light_map = {
                "#e6edf3": "#0f172a",
                "#f8fafc": "#0f172a",
                "#c9d1d9": "#1e293b",
                "#f1f5f9": "#0f172a",
                "#38bdf8": "#0284c7",
                "#93c5fd": "#2e5988",
                "#4ade80": "#246e53",
                "#34d399": "#059669",
                "#22c55e": "#2e8b69",
                "#f87171": "#dc2626",
                "#f59e0b": "#d97706",
            }
            color = light_map.get(c_low, color)
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
        self._tpl_preview_timer.setInterval(30)
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
        self.nav_queue.hide()

        section("НАСТРОЙКА")
        self.nav_points       = nav("🏢  Точки и ПК", "devices")
        self.nav_devices      = nav("🖧  Устройства", "devices")
        self.nav_devices.hide()
        self.nav_templates    = nav("🏷️  Шаблоны", "templates")
        self.nav_pool         = nav("🎫  Склад кодов ЧЗ", "pool")
        self.nav_imports      = nav("📥  Импорты", "imports")
        self.nav_imports.hide()
        self.nav_printer      = nav("🖨  Принтеры по точкам", "printer")
        self.nav_printer.hide()

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

        self.lbl_sidebar_role = QLabel("👑 Режим: Мастер-ПК")
        self.lbl_sidebar_role.setStyleSheet(
            "background:#1e293b; color:#38bdf8; font-size:11px; font-weight:600; "
            "padding:6px 10px; border-radius:6px; border:1px solid #334155;"
        )
        self.lbl_sidebar_role.setAlignment(Qt.AlignCenter)
        sb.addWidget(self.lbl_sidebar_role)

        self.btn_theme = QPushButton("🌓  Сменить тему")
        self.btn_theme.setObjectName("navBtn")
        self.btn_theme.clicked.connect(self._toggle_theme)
        sb.addWidget(self.btn_theme)

        # ============ STACK ============
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_dashboard())      # 0
        self.stack.addWidget(self._page_queue())          # 1
        self.stack.addWidget(self._page_points())         # 2
        self.stack.addWidget(self._page_devices())        # 3
        self.stack.addWidget(self._page_templates())      # 4
        self.stack.addWidget(self._page_pool())           # 5
        self.stack.addWidget(QWidget())                   # 6 (перенесено во вкладку Склада кодов ЧЗ)
        self.stack.addWidget(self._page_printer())        # 7
        self.stack.addWidget(self._page_journal())        # 8
        self.stack.addWidget(self._page_logs())           # 9
        self.stack.addWidget(self._page_backup())         # 10
        self.stack.addWidget(self._page_security())       # 11
        self.stack.addWidget(self._page_discrepancies())  # 12
        self.stack.addWidget(self._page_sync())           # 13
        # Создание страницы SQL Server и добавление в стек страниц
        self.sql_server_page = SQLServerAdminPage(config_store=self.store, parent=self)
        self.stack.addWidget(self.sql_server_page)  # 14
        self.sql_server_page.sync_requested.connect(self._handle_sql_sync)

        root.addWidget(sidebar)
        root.addWidget(self.stack, stretch=1)

        self._journal_page = 0
        self._journal_total = 0

        self._apply_pc_role_ui()
        self.apply_theme(refresh_data=False)
        QTimer.singleShot(2000, self._run_auto_backup_on_startup)
        self.switch("dashboard")

    # ------------------------------------------------------------------
    def switch(self, page_id: str):
        pages = {
            "dashboard": 0, "queue": 1, "points": 2,
            "devices": 3, "templates": 4, "pool": 5,
            "imports": 6, "printer": 7,
            "journal": 8, "logs": 9, "backup": 10, "security": 11,
            "discrepancies": 12, "sync": 13, "sql": 14,
        }
        idx = pages.get(page_id, 0)
        self.stack.setCurrentIndex(idx)
        for btn in [self.nav_dashboard, self.nav_queue, self.nav_points,
                    self.nav_devices, self.nav_templates, self.nav_pool,
                    self.nav_imports, self.nav_printer, self.nav_journal,
                    self.nav_logs, self.nav_backup, self.nav_security,
                    self.nav_discrepancies, self.nav_sync, self.nav_sql]:
            btn.setChecked(btn.property("page_id") == page_id)
        self.refresh(page_id)

    def refresh(self, page_id: str):
        if page_id == "dashboard":      self._refresh_dashboard()
        if page_id == "queue":          self._refresh_queue()
        if page_id == "points":         self._refresh_points()
        if page_id == "devices":        self._refresh_devices()
        if page_id == "templates":      self._refresh_templates()
        if page_id == "pool":           self._refresh_pool()
        if page_id == "imports":        self._refresh_imports()
        if page_id == "printer":        self._refresh_printer()
        if page_id == "journal":        self._refresh_journal()
        if page_id == "logs":           self._refresh_logs()
        if page_id == "security":       self._refresh_security()
        if page_id == "backup":         self._refresh_backup()
        if page_id == "discrepancies":  self._refresh_discrepancies()
        if page_id == "sync":           self._refresh_sync()
        if page_id == "sql":            self._refresh_sql()

    # ==================================================================
    # DASHBOARD (KPI + Управление сменой + Сводка по точкам и сканерам)
    # ==================================================================
    def _apply_pc_role_ui(self):
        """Применяет режим работы: Мастер-ПК (сервер смены + MS SQL) или Обычный ПК (рабочий пост упаковки)."""
        try:
            role = sync_mod.get_pc_role(self.store)
            pc_name = sync_mod.get_pc_node_name(self.store)
        except Exception:
            role = "master"
            pc_name = "PC-MAIN"

        is_master = (role == "master")
        if hasattr(self, "nav_sql"):
            self.nav_sql.setVisible(is_master)
        if hasattr(self, "lbl_sidebar_role"):
            is_lt = _is_light_theme()
            if is_master:
                self.lbl_sidebar_role.setText(f"👑 Мастер-ПК · {pc_name}")
                if is_lt:
                    self.lbl_sidebar_role.setStyleSheet(
                        "background:#dcfce7; color:#246e53; font-size:11px; font-weight:700; "
                        "padding:6px 10px; border-radius:6px; border:1px solid #2e8b69;"
                    )
                else:
                    self.lbl_sidebar_role.setStyleSheet(
                        "background:#0f291e; color:#34d399; font-size:11px; font-weight:600; "
                        "padding:6px 10px; border-radius:6px; border:1px solid #059669;"
                    )
            else:
                self.lbl_sidebar_role.setText(f"💻 Обычный ПК · {pc_name}")
                if is_lt:
                    self.lbl_sidebar_role.setStyleSheet(
                        "background:#dbeafe; color:#2e5988; font-size:11px; font-weight:700; "
                        "padding:6px 10px; border-radius:6px; border:1px solid #3b82f6;"
                    )
                else:
                    self.lbl_sidebar_role.setStyleSheet(
                        "background:#172554; color:#60a5fa; font-size:11px; font-weight:600; "
                        "padding:6px 10px; border-radius:6px; border:1px solid #3b6ea5;"
                    )

    def _run_auto_backup_on_startup(self):
        """Фоновая проверка автобэкапа в отдельном потоке-демоне без блокировки UI."""
        import threading
        def _bg():
            try:
                res = auto_backup_if_needed()
                if res and res.get("ok"):
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(0, lambda: self.toast.show_message(res.get("info", "Создан плановый автобэкап БД"), "ok"))
            except Exception as e:
                log.error("auto_backup_if_needed: %s", e)
        threading.Thread(target=_bg, daemon=True).start()

    def _clean_legacy_demo_points_in_db(self):
        """Очищает старые демо-принтеры (TSC TE200, Godex G500, Zebra ZD421) и демо-операторов из БД (1 раз за сессию)."""
        if getattr(self, "_legacy_cleaned", False):
            return
        self._legacy_cleaned = True
        try:
            import db
            with db.get_conn() as conn:
                try:
                    conn.execute(
                        "UPDATE points SET printer = '' "
                        "WHERE printer IN ('Zebra ZD421', 'TSC TE200', 'Godex G500')"
                    )
                    conn.commit()
                except Exception:
                    pass
                try:
                    conn.execute(
                        "UPDATE points SET operator = '' "
                        "WHERE operator IN ('Иванов И.И.', 'Петрова А.С.', 'Сидоров В.П.', 'Оператор')"
                    )
                    conn.commit()
                except Exception:
                    pass
            if hasattr(self.store, "_load_points"):
                self.store.points = self.store._load_points()
            else:
                for pt in getattr(self.store, "points", []):
                    if pt.get("printer") in ("Zebra ZD421", "TSC TE200", "Godex G500"):
                        pt["printer"] = ""
                    if pt.get("operator") in ("Иванов И.И.", "Петрова А.С.", "Сидоров В.П.", "Оператор"):
                        pt["operator"] = ""
        except Exception:
            pass

    def _page_dashboard(self) -> QWidget:
        self._clean_legacy_demo_points_in_db()

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Дашборд и управление сменой")
        title.setStyleSheet("font-size:18px; font-weight:600;")
        title_col.addWidget(title)

        sub = QLabel("Сводка выработки за смену, привязка «1 Сканер = 1 Принтер» и остатки кодов ЧЗ")
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        title_col.addWidget(sub)
        head.addLayout(title_col)
        head.addStretch(1)

        btn_bind_hw = QPushButton("🔗 Привязка Сканер ↔ Принтер")
        btn_bind_hw.setProperty("primary", True)
        btn_bind_hw.clicked.connect(self._open_master_devices_window)
        head.addWidget(btn_bind_hw)

        btn_refresh_dash = QPushButton("🔄 Обновить")
        btn_refresh_dash.clicked.connect(self._refresh_dashboard)
        head.addWidget(btn_refresh_dash)
        v.addLayout(head)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)

        self.kpi_labels = {}
        for key, label, color in [
            ("scans",  "Сканов за смену",   "accent"),
            ("prints", "Напечатано ЧЗ",     "ok"),
            ("errors", "Ошибок / Брак",     "err"),
            ("queue",  "В очереди обмена",  "warn"),
        ]:
            kpi_row.addWidget(self._make_kpi(key, label, color))

        v.addLayout(kpi_row)

        # Панель управления параметрами текущей смены (без разделения по операторам — только точка и лимит)
        shift_card = QFrame()
        shift_card.setObjectName("card")
        sc_layout = QVBoxLayout(shift_card)
        sc_layout.setContentsMargins(18, 14, 18, 14)
        sc_layout.setSpacing(10)

        sc_title = QLabel("ПАРАМЕТРЫ ТЕКУЩЕЙ СМЕНЫ НА ЭТОМ ПК")
        sc_title.setStyleSheet("color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1px;")
        sc_layout.addWidget(sc_title)

        sc_row = QHBoxLayout()
        sc_row.setSpacing(14)

        sc_row.addWidget(QLabel("Активная точка:"))
        self.dash_point_combo = QComboBox()
        self.dash_point_combo.setMinimumWidth(220)
        for p in self.store.points:
            self.dash_point_combo.addItem(f"{p['id']} — {p['name']}", p["id"])
        sc_row.addWidget(self.dash_point_combo)

        # Лимит смены убран: размер партии задаётся заказом с сервера

        btn_save_shift = QPushButton("💾 Применить")
        btn_save_shift.setProperty("primary", True)
        btn_save_shift.clicked.connect(self._dash_save_shift_settings)
        sc_row.addWidget(btn_save_shift)

        btn_reset_shift = QPushButton("🧹 Новая смена (Сбросить счётчики)")
        btn_reset_shift.clicked.connect(self._dash_reset_shift_counters)
        sc_row.addWidget(btn_reset_shift)

        sc_row.addStretch(1)
        sc_layout.addLayout(sc_row)
        v.addWidget(shift_card)

        # Таблица сводки по точкам, сканерам и остаткам кодов ЧЗ
        pts_card = QFrame()
        pts_card.setObjectName("card")
        pv = QVBoxLayout(pts_card)
        pv.setContentsMargins(18, 14, 18, 14)
        pv.setSpacing(8)

        pts_head = QHBoxLayout()
        pts_lbl = QLabel("СВОДКА ПО ТОЧКАМ, СКАНЕРАМ И ОСТАТКАМ КОДОВ ЧЗ")
        pts_lbl.setStyleSheet("color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1px;")
        pts_head.addWidget(pts_lbl)
        pts_head.addStretch(1)

        self.dash_route_lbl = QLabel("")
        self.dash_route_lbl.setStyleSheet("color:#4ade80; font-family:Consolas,monospace; font-size:12px; font-weight:600;")
        pts_head.addWidget(self.dash_route_lbl)
        pv.addLayout(pts_head)

        self.dash_points_table = table([
            "Точка", "Наименование", "Сканер (Пост)", "Привязанный принтер",
            "Всего кодов ЧЗ", "Свободно ЧЗ", "Напечатано ЧЗ"
        ])
        pv.addWidget(self.dash_points_table, stretch=1)
        v.addWidget(pts_card, stretch=1)

        return w

    def _make_kpi(self, key: str, label: str, color: str) -> QFrame:
        box = QFrame()
        box.setObjectName("kpi")
        box.setFixedHeight(90)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        v = QVBoxLayout(box)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)

        l = QLabel(label)
        l.setStyleSheet(
            "color:#8b97a6; font-size:11px; letter-spacing:.5px;"
        )

        val = QLabel("0")
        colors = {
            "accent": "#3b82f6",
            "ok":     "#22c55e",
            "warn":   "#f59e0b",
            "err":    "#ef4444",
        }
        val.setStyleSheet(
            f"color:{colors[color]}; font-size:26px; "
            f"font-weight:600; font-family:Consolas,monospace;"
        )

        v.addWidget(l)
        v.addWidget(val)
        v.addStretch(1)

        self.kpi_labels[key] = val
        return box

    def _dash_save_shift_settings(self):
        """Сохраняет активную точку смены."""
        pt_id = self.dash_point_combo.currentData()
        if pt_id:
            self.store.current_point = pt_id

        try:
            import db
            with db.get_conn() as conn:
                conn.execute("UPDATE counters SET [limit] = ? WHERE id = 1", (new_limit,))
                conn.commit()
        except Exception:
            pass

        # Синхронизируем шапку и счётчики с открытым OperatorWindow
        try:
            win = self.window()
            if hasattr(win, "operator"):
                op_w = win.operator
                if hasattr(op_w, "lbl_point") and pt_id:
                    pt_obj = self.store.get_point(pt_id) or {}
                    op_w.lbl_point.setText(f"📍 Точка: {pt_id} · {pt_obj.get('name', '')}")
                if hasattr(op_w, "_refresh_counter"):
                    op_w._refresh_counter()
        except Exception:
            pass

        self._refresh_dashboard()
        self.toast.show_message(f"Настройки смены сохранены (точка {pt_id})", "ok")

    def _dash_reset_shift_counters(self):
        """Обнуляет счётчики сканов, печатей и ошибок при начале новой смены."""
        ans = QMessageBox.question(
            self,
            "Новая смена",
            "Сбросить счётчики сканирований, печатей и ошибок за текущую смену в 0?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ans != QMessageBox.Yes:
            return

        self.store.counter["scans"] = 0
        self.store.counter["prints"] = 0
        self.store.counter["errors"] = 0
        try:
            import db
            with db.get_conn() as conn:
                conn.execute("UPDATE counters SET scans = 0, prints = 0, errors = 0 WHERE id = 1")
                conn.commit()
        except Exception:
            pass

        try:
            win = self.window()
            if hasattr(win, "operator") and hasattr(win.operator, "_refresh_counter"):
                win.operator._refresh_counter()
        except Exception:
            pass

        self.store.log("Сброс счётчиков смены", point=self.store.current_point,
                       operator="Система", result="успех")
        self._refresh_dashboard()
        self.toast.show_message("Счётчики смены сброшены в 0", "ok")

    def _open_master_devices_window(self):
        """Открывает окно быстрой привязки «1 Сканер = 1 Принтер» (MasterDevicesWidget из master_devices_window.py)."""
        try:
            import master_devices_window as mdw
            if hasattr(mdw, "MasterDevicesWidget"):
                dlg = QDialog(self)
                dlg.setWindowTitle("Привязка оборудования (1 Сканер = 1 Принтер)")
                dlg.resize(1080, 660)
                lay = QVBoxLayout(dlg)
                lay.setContentsMargins(0, 0, 0, 0)
                widget = mdw.MasterDevicesWidget(dlg)
                widget.store = self.store
                lay.addWidget(widget)
                dlg.exec()
            elif hasattr(mdw, "MasterDevicesWindow"):
                dlg = mdw.MasterDevicesWindow(store=self.store, parent=self)
                dlg.exec()
            else:
                raise RuntimeError("В master_devices_window не найден класс MasterDevicesWidget")

            self._refresh_devices()
            self._refresh_dashboard()
            try:
                win = self.window()
                if hasattr(win, "operator") and hasattr(win.operator, "engine"):
                    win.operator.engine.start_all()
                if hasattr(win, "operator") and hasattr(win.operator, "_refresh_header_route"):
                    win.operator._refresh_header_route()
            except Exception:
                pass
        except Exception as e:
            self.toast.show_message(f"Ошибка открытия окна привязки: {e}", "err")

    def _get_live_connected_devices(self):
        """
        Опрашивает физически подключенные в данный момент сканеры и принтеры
        через device_discovery (detect_local_scanners / detect_local_printers).
        Если ничего не подключено в данный момент — возвращает пустые списки.
        """
        import json
        from pathlib import Path

        try:
            from device_discovery import detect_local_scanners, detect_local_printers
            live_scanners = detect_local_scanners(connected_only=True) or []
            live_printers = detect_local_printers(connected_only=True) or []
        except Exception as e:
            log.debug("Ошибка опроса подключенных устройств: %s", e)
            live_scanners = []
            live_printers = []

        # Загружаем привязки из devices_config.json, но оставляем только те,
        # где устройство реально физически подключено прямо сейчас!
        bindings = {}
        try:
            from workstation_engine import _resolve_config_path
            cfg_p = _resolve_config_path("devices_config.json")
        except Exception:
            cfg_p = Path(__file__).resolve().parent / "devices_config.json"

        if cfg_p.exists():
            try:
                data = json.loads(cfg_p.read_text(encoding="utf-8"))
                bindings = data.get("bindings") or {}
            except Exception:
                bindings = {}

        live_pr_names = {pr["name"]: pr for pr in live_printers if pr.get("name")}
        rows_out = []
        used_printers = set()

        for sc in live_scanners:
            sc_dev = sc.get("device") or sc.get("name") or ""
            sc_title = sc.get("name") or sc_dev
            bound_pr = bindings.get(sc_dev) or bindings.get(sc_title) or ""
            if bound_pr and bound_pr in live_pr_names:
                used_printers.add(bound_pr)
                rows_out.append((sc_title, bound_pr, True))
            elif bound_pr:
                rows_out.append((sc_title, f"{bound_pr} (отключён)", False))
            else:
                rows_out.append((sc_title, "⚠️ Принтер не привязан", False))

        # Если есть физически подключенные сейчас принтеры, которые ещё не привязаны к подключенному сканеру
        for pr_name in live_pr_names:
            if pr_name not in used_printers:
                rows_out.append(("", pr_name, True))

        return rows_out

    def _refresh_dashboard(self):
        self._clean_legacy_demo_points_in_db()

        c = self.store.counter
        self.kpi_labels["scans"].setText(str(c.get("scans", 0)))
        self.kpi_labels["prints"].setText(str(c.get("prints", 0)))
        self.kpi_labels["errors"].setText(str(c.get("errors", 0)))
        if "limit" in self.kpi_labels:
            self.kpi_labels["limit"].setText(str(c.get("limit", 0)))
        self.kpi_labels["queue"].setText(str(len(self.store.queue)))

        if hasattr(self, "dash_point_combo"):
            idx = self.dash_point_combo.findData(self.store.current_point)
            if idx >= 0:
                self.dash_point_combo.setCurrentIndex(idx)
        if hasattr(self, "dash_limit_spin"):
            self.dash_limit_spin.setValue(int(c.get("limit", 0)))

        # Опрашиваем АКТУАЛЬНЫЕ данные по подключенным сканерам и принтерам
        live_rows = self._get_live_connected_devices()
        active_full_pairs = [
            (item[0], item[1], item[2] if len(item) > 2 else True)
            for item in live_rows if item[0] and item[1]
        ]

        try:
            win = self.window()
            if hasattr(win, "operator") and hasattr(win.operator, "_refresh_header_route"):
                win.operator._refresh_header_route()
        except Exception:
            pass

        if hasattr(self, "dash_route_lbl"):
            if active_full_pairs:
                parts = []
                for sc, pr, is_on in active_full_pairs:
                    status_icon = "🟢" if is_on else "🔴"
                    parts.append(f"📡 {sc} → 🖨 {pr} {status_icon}")
                self.dash_route_lbl.setText("  |  ".join(parts))
            else:
                self.dash_route_lbl.setText("🔴 Нет активных подключенных устройств")

        if hasattr(self, "dash_points_table"):
            t = self.dash_points_table
            t.setRowCount(0)

            if not live_rows:
                return

            import db
            stats_by_pt = {}
            try:
                with db.get_conn() as conn:
                    rows = conn.execute(
                        "SELECT assigned_point, COUNT(*) AS total, "
                        "SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free, "
                        "SUM(CASE WHEN printed=1 THEN 1 ELSE 0 END) AS printed "
                        "FROM pool_codes GROUP BY assigned_point"
                    ).fetchall()
                    for r in rows:
                        stats_by_pt[r["assigned_point"]] = dict(r)
            except Exception:
                pass

            cur_pt_id = self.store.current_point or "Т-01"
            cur_pt_obj = self.store.get_point(cur_pt_id) or {}
            cur_pt_name = cur_pt_obj.get("name", "—")
            st = stats_by_pt.get(cur_pt_id, {"total": 0, "free": 0, "printed": 0})

            for idx_row, row_data in enumerate(live_rows):
                sc_lbl = row_data[0]
                pr_lbl = row_data[1]
                is_online = row_data[2] if len(row_data) > 2 else True

                r = t.rowCount()
                t.insertRow(r)
                t.setItem(r, 0, cell(cur_pt_id, mono=True))
                t.setItem(r, 1, cell(cur_pt_name))
                t.setItem(
                    r, 2,
                    cell(f"📡 {sc_lbl}", "#38bdf8", mono=True) if sc_lbl else cell("", mono=True)
                )
                pr_color = "#4ade80" if is_online else "#f87171"
                t.setItem(
                    r, 3,
                    cell(f"🖨 {pr_lbl}", pr_color, mono=True) if pr_lbl else cell("", mono=True)
                )
                t.setItem(r, 4, cell(st.get("total", 0) if idx_row == 0 else "", mono=True))
                t.setItem(r, 5, cell(st.get("free", 0) if idx_row == 0 else "", "#22c55e", mono=True))
                t.setItem(r, 6, cell(st.get("printed", 0) if idx_row == 0 else "", "#38bdf8", mono=True))

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

        head = QHBoxLayout()
        tt = QLabel("Точки и подключённые компьютеры")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_edit = QPushButton("✏️ Редактировать")
        btn_edit.setProperty("primary", True)
        btn_edit.clicked.connect(self._points_edit_selected)
        head.addWidget(btn_edit)

        btn_add_pc = QPushButton("➕ Добавить ПК")
        btn_add_pc.clicked.connect(self._points_add_computer)
        head.addWidget(btn_add_pc)

        btn_expand = QPushButton("▾ Развернуть все")
        btn_expand.clicked.connect(lambda: self.points_tree.expandAll())
        head.addWidget(btn_expand)

        btn_collapse = QPushButton("▸ Свернуть все")
        btn_collapse.clicked.connect(lambda: self.points_tree.collapseAll())
        head.addWidget(btn_collapse)

        v.addLayout(head)

        self.points_tree = QTreeWidget()
        self.points_tree.setHeaderLabels([
            "Точка / Компьютер", "Расположение / Роль",
            "Статус", "IP-адрес"
        ])
        self.points_tree.setColumnWidth(0, 320)
        self.points_tree.setColumnWidth(1, 260)
        self.points_tree.setColumnWidth(2, 120)
        self.points_tree.setColumnWidth(3, 160)
        self.points_tree.setAlternatingRowColors(False)
        self.points_tree.setRootIsDecorated(True)
        # Стили дерева управляются централизованно через styles.py и apply_theme()

        self.points_tree.itemDoubleClicked.connect(
            lambda item, col: self._points_edit_item(item)
        )
        self.points_tree.itemClicked.connect(
            lambda item, col: item.setExpanded(not item.isExpanded())
        )

        v.addWidget(self.points_tree, stretch=1)
        return w

    def _refresh_points(self):
        import socket
        self.points_tree.clear()

        if not hasattr(self, "_remote_pc_cache"):
            self._remote_pc_cache = {}

        try:
            my_role = sync_mod.get_pc_role(self.store)
            my_node = sync_mod.get_pc_node_name(self.store).strip()
        except Exception:
            my_role = "master"
            my_node = socket.gethostname() or "PC-MAIN"

        is_master = (my_role == "master")
        real_hostname = socket.gethostname().strip()
        real_ip = getattr(self, "_cached_real_ip", None)
        if not real_ip:
            real_ip = "127.0.0.1"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.05)
                s.connect(("8.8.8.8", 80))
                real_ip = s.getsockname()[0]
                s.close()
            except Exception:
                try:
                    real_ip = socket.gethostbyname(real_hostname)
                except Exception:
                    real_ip = "127.0.0.1"
            self._cached_real_ip = real_ip

        local_ips = {"127.0.0.1", "localhost", real_ip.lower()}
        cur_pt_id = getattr(self.store, "current_point", "T-01") or "T-01"

        for p in self.store.points:
            item = QTreeWidgetItem(self.points_tree)

            item.setText(0, f"{p['id']} — {p['name']}")
            item.setText(1, p.get("address", "—"))
            item.setText(3, real_ip if p["id"] == cur_pt_id else p.get("ip", "—"))

            f = item.font(0)
            f.setBold(True)
            f.setPointSize(f.pointSize() + 1)
            item.setFont(0, f)

            computers = p.get("computers", [])
            point_has_online_comp = False

            explicit_match_found = any(
                ((c.get("ip") or "").strip().lower() in local_ips)
                or ((c.get("name") or "").strip().lower() in (my_node.lower(), real_hostname.lower()))
                for c in computers
            )

            if not computers:
                child = QTreeWidgetItem(item)
                child.setText(0, "    Нет подключённых компьютеров")
                child.setForeground(0, QBrush(QColor("#64748b")))
            else:
                for idx_c, c in enumerate(computers):
                    ch = QTreeWidgetItem(item)
                    c_name = c.get("name", "ПК")
                    c_ip = (c.get("ip") or "").strip()
                    c_ip_l = c_ip.lower()
                    c_name_l = c_name.lower()

                    is_local = (
                        (c_ip_l in local_ips and c_ip_l != "")
                        or (c_name_l in (my_node.lower(), real_hostname.lower()))
                        or (
                            not explicit_match_found
                            and p["id"] == cur_pt_id
                            and (c_name_l in ("пк-01", "pc-01", "master") or idx_c == 0)
                        )
                    )

                    if is_local:
                        point_has_online_comp = True
                        display_name = f"{c_name} ({real_hostname})" if real_hostname and real_hostname.lower() != c_name_l else c_name
                        role_tag = "👑 Мастер-ПК" if is_master else "💻 Рабочий ПК"
                        ch.setText(0, f"    💻  {display_name}")
                        ch.setText(1, f"{role_tag} · {c.get('role', 'Основной оператор')}")
                        ch.setText(2, "🟢  онлайн")
                        ch.setForeground(2, QBrush(QColor("#22c55e")))
                        ch.setText(3, real_ip)
                    else:
                        cached = self._remote_pc_cache.get(c_ip)
                        c_online = bool(cached[0]) if cached else False
                        lat = cached[1] if cached else None
                        if c_online:
                            point_has_online_comp = True
                            latency_str = f" ({int(lat) if lat else '<1'} мс)"
                            ch.setText(0, f"    💻  {c_name}")
                            ch.setText(1, f"{c.get('role', 'Рабочее место')} · подключён")
                            ch.setText(2, f"🟢  онлайн{latency_str}")
                            ch.setForeground(2, QBrush(QColor("#22c55e")))
                        else:
                            ch.setText(0, f"    💻  {c_name}")
                            ch.setText(1, f"{c.get('role', 'Рабочее место')} · не в сети")
                            ch.setText(2, "🔴  офлайн")
                            ch.setForeground(2, QBrush(QColor("#ef4444")))
                        ch.setText(3, c_ip or "—")

            if point_has_online_comp:
                item.setText(2, "🟢  онлайн")
                item.setForeground(2, QBrush(QColor("#22c55e")))
            else:
                item.setText(2, "🔴  офлайн")
                item.setForeground(2, QBrush(QColor("#ef4444")))

            item.setExpanded(point_has_online_comp)
    # ==========================================================
    # Редактирование точек и компьютеров
    # ==========================================================
    def _points_edit_selected(self):
        items = self.points_tree.selectedItems()
        if not items:
            self.toast.show_message("Выберите точку или ПК", "warn")
            return
        self._points_edit_item(items[0])

    def _points_edit_item(self, item):
        if item is None:
            return

        point_id = self._points_item_point_id(item)
        if not point_id:
            return

        parent = item.parent()

        if parent is None:
            # Это точка
            point = self.store.get_point(point_id)
            if not point:
                return
            dlg = EditPointDialog(point, self)
            if dlg.exec() == EditPointDialog.Accepted and dlg.result_data:
                result = self.store.update_point(
                    point_id, **dlg.result_data
                )
                if result["ok"]:
                    self.toast.show_message(result["info"], "ok")
                    self._refresh_points()
                else:
                    self.toast.show_message(
                        f"Ошибка: {result['info']}", "err"
                    )
        else:
            # Это компьютер (JSON-хранилище точки)
            point = self.store.get_point(point_id)
            if not point:
                return
            computers = point.get("computers", [])
            idx = parent.indexOfChild(item)
            if idx < 0 or idx >= len(computers):
                return
            comp = computers[idx]

            dlg = EditComputerDialog(comp, point.get("name", ""), self)
            if dlg.exec() != EditComputerDialog.Accepted:
                return
            if not dlg.result_data:
                return

            action = dlg.result_data.get("action")
            new_computers = list(computers)

            if action == "delete":
                new_computers.pop(idx)
            else:
                new_computers[idx] = {
                    "name": dlg.result_data["name"],
                    "ip": dlg.result_data["ip"],
                    "role": dlg.result_data["role"],
                    "operator": dlg.result_data.get("operator", "—"),
                    "online": dlg.result_data["online"],
                }

            result = self.store.update_point(
                point_id, computers=new_computers
            )
            if result["ok"]:
                self.toast.show_message(
                    "Компьютер удалён" if action == "delete"
                    else "Компьютер сохранён", "ok"
                )
                self._refresh_points()
            else:
                self.toast.show_message(
                    f"Ошибка: {result['info']}", "err"
                )

    def _points_add_computer(self):
        items = self.points_tree.selectedItems()
        if not items:
            self.toast.show_message("Выберите точку", "warn")
            return

        item = items[0]
        if item.parent() is not None:
            item = item.parent()

        point_id = self._points_item_point_id(item)
        if not point_id:
            return

        point = self.store.get_point(point_id)
        if not point:
            return

        empty = {"name": "ПК-01", "ip": "", "role": "Основной оператор",
                 "operator": "—", "online": True}

        dlg = EditComputerDialog(empty, point.get("name", ""), self)
        if dlg.exec() != EditComputerDialog.Accepted:
            return
        if not dlg.result_data:
            return

        new_computers = list(point.get("computers", []))
        new_computers.append({
            "name": dlg.result_data["name"],
            "ip": dlg.result_data["ip"],
            "role": dlg.result_data["role"],
            "operator": dlg.result_data.get("operator", "—"),
            "online": dlg.result_data["online"],
        })

        result = self.store.update_point(
            point_id, computers=new_computers
        )
        if result["ok"]:
            self.toast.show_message("Компьютер добавлен", "ok")
            self._refresh_points()
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _points_item_point_id(self, item) -> str:
        try:
            text = item.text(0).strip()
            if " — " in text:
                return text.split(" — ")[0].strip()
            parent = item.parent()
            if parent is not None:
                ptext = parent.text(0).strip()
                if " — " in ptext:
                    return ptext.split(" — ")[0].strip()
        except Exception:
            pass
        return ""

    # ==================================================================
    # DEVICES (компьютеры / принтеры / сканеры)
    # ==================================================================
    def _page_devices(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        head = QHBoxLayout()
        tt = QLabel("Точки, компьютеры и оборудование (Сканер ↔ Принтер)")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_expand = QPushButton("▾ Развернуть всё")
        btn_expand.clicked.connect(lambda: self.devices_tree.expandAll())
        head.addWidget(btn_expand)

        btn_collapse = QPushButton("▸ Свернуть всё")
        btn_collapse.clicked.connect(lambda: self.devices_tree.collapseAll())
        head.addWidget(btn_collapse)

        btn_quick_bind = QPushButton("🔗 Быстрая привязка Сканер ↔ Принтер (этот ПК)")
        btn_quick_bind.setProperty("primary", True)
        btn_quick_bind.clicked.connect(self._open_master_devices_window)
        head.addWidget(btn_quick_bind)

        btn_refresh = QPushButton("🔄 Обновить")
        def _on_manual_refresh():
            btn_refresh.setEnabled(False)
            btn_refresh.setText("Обновление...")
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            try:
                from device_discovery import detect_local_printers, detect_local_scanners
                detect_local_printers(force_refresh=True)
                detect_local_scanners(force_refresh=True)
            except Exception:
                pass
            self._refresh_devices(force_probe=True)
            btn_refresh.setEnabled(True)
            btn_refresh.setText("🔄 Обновить")
            if hasattr(self, "toast"):
                self.toast.show_message("Статусы оборудования и ПК обновлены", "ok")
        btn_refresh.clicked.connect(_on_manual_refresh)
        head.addWidget(btn_refresh)

        v.addLayout(head)

        sub = QLabel(
            "Иерархия: точка → компьютер → (принтеры + сканеры). "
            "Двойной клик по узлу — редактирование."
        )
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        v.addWidget(sub)

        self.devices_tree = QTreeWidget()
        self.devices_tree.setHeaderLabels([
            "Объект", "Роль / Подключение", "Статус",
            "IP / Порт", "Дополнительно"
        ])
        self.devices_tree.setColumnWidth(0, 320)
        self.devices_tree.setColumnWidth(1, 240)
        self.devices_tree.setColumnWidth(2, 110)
        self.devices_tree.setColumnWidth(3, 160)
        self.devices_tree.setColumnWidth(4, 220)
        self.devices_tree.setAlternatingRowColors(False)
        self.devices_tree.setRootIsDecorated(True)
        # Стили дерева управляются централизованно через styles.py и apply_theme()

        self.devices_tree.itemDoubleClicked.connect(
            lambda item, col: self._devices_edit_item(item)
        )
        self.devices_tree.itemClicked.connect(
            lambda item, col: item.setExpanded(not item.isExpanded())
        )
        self.devices_tree.itemSelectionChanged.connect(
            self._devices_on_selection
        )

        v.addWidget(self.devices_tree, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        btn_add_pc = QPushButton("➕ Добавить ПК")
        btn_add_pc.setProperty("primary", True)
        btn_add_pc.clicked.connect(self._devices_add_computer)
        actions.addWidget(btn_add_pc)

        btn_add_printer = QPushButton("🖨 Добавить принтер")
        btn_add_printer.clicked.connect(self._devices_add_printer)
        actions.addWidget(btn_add_printer)

        btn_add_scanner = QPushButton("📡 Добавить сканер")
        btn_add_scanner.clicked.connect(self._devices_add_scanner)
        actions.addWidget(btn_add_scanner)

        actions.addStretch(1)
        v.addLayout(actions)

        return w

    def _start_async_remote_pc_probe(self, ips_to_check: list, force: bool = False):
        """Параллельно в фоне проверяет доступность удалённых ПК и сетевых принтеров без фриза UI."""
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        from PySide6.QtCore import QTimer

        if not hasattr(self, "_remote_pc_cache"):
            self._remote_pc_cache = {}
        if getattr(self, "_probe_running", False):
            return

        now = time.time()
        stale_ips = [
            ip for ip in set(ips_to_check)
            if ip and ip not in ("—", "") and (force or (now - self._remote_pc_cache.get(ip, (False, None, 0.0))[2]) > 15.0)
        ]
        if not stale_ips:
            return

        self._probe_running = True

        def _worker():
            changed = False
            try:
                from device_discovery import check_computer_online
                def _ping_one(target_ip):
                    try:
                        alive, _, lat = check_computer_online(target_ip, timeout_ms=250)
                        return target_ip, bool(alive), lat
                    except Exception:
                        return target_ip, False, None

                with ThreadPoolExecutor(max_workers=min(8, len(stale_ips))) as pool:
                    for tip, alive, lat in pool.map(_ping_one, stale_ips):
                        old_val = self._remote_pc_cache.get(tip)
                        self._remote_pc_cache[tip] = (alive, lat, time.time())
                        if old_val is None or old_val[0] != alive:
                            changed = True
            except Exception as e:
                log.debug("async remote probe: %s", e)
            finally:
                self._probe_running = False
                if changed:
                    try:
                        QTimer.singleShot(0, self._refresh_devices)
                    except Exception:
                        pass

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_devices(self, force_probe: bool = False):
        import socket
        import time
        from datetime import datetime

        self.devices_tree.clear()

        if not hasattr(self, "_remote_pc_cache"):
            self._remote_pc_cache = {}

        # 1. Быстрый кэшированный опрос локального оборудования
        try:
            from device_discovery import detect_local_printers, detect_local_scanners
            live_printers = detect_local_printers(connected_only=True, force_refresh=False) or []
            live_scanners = detect_local_scanners(connected_only=True, force_refresh=False) or []
        except Exception as e:
            log.debug("Ошибка опроса локальных устройств: %s", e)
            live_printers = []
            live_scanners = []

        live_pr_names = {p["name"].strip().lower() for p in live_printers if p.get("online")}
        live_sc_names = {str(s.get("device") or s.get("name") or "").strip().lower() for s in live_scanners}
        has_live_hid = any(s.get("connect_type") == "USB-HID" for s in live_scanners)
        active_coms = set()
        try:
            import serial.tools.list_ports
            active_coms = {cp.device.upper().rstrip(":") for cp in serial.tools.list_ports.comports()}
        except Exception:
            pass

        # 2. Роль и параметры запущенного ПК (с кэшированием IP)
        try:
            my_role = sync_mod.get_pc_role(self.store)
            my_node = sync_mod.get_pc_node_name(self.store).strip()
        except Exception:
            my_role = "master"
            my_node = socket.gethostname() or "PC-MAIN"

        is_master = (my_role == "master")
        real_hostname = socket.gethostname().strip()
        real_ip = getattr(self, "_cached_real_ip", None)
        if not real_ip:
            real_ip = "127.0.0.1"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.05)
                s.connect(("8.8.8.8", 80))
                real_ip = s.getsockname()[0]
                s.close()
            except Exception:
                try:
                    real_ip = socket.gethostbyname(real_hostname)
                except Exception:
                    real_ip = "127.0.0.1"
            self._cached_real_ip = real_ip

        local_ips = {"127.0.0.1", "localhost", real_ip.lower()}
        cur_pt_id = getattr(self.store, "current_point", "T-01") or "T-01"

        sync_folder = None
        try:
            sync_folder = sync_mod.get_sync_folder(self.store)
        except Exception:
            pass

        remote_ips_to_probe = []

        for point in self.store.points:
            point_item = QTreeWidgetItem(self.devices_tree)
            point_item.setText(0, f"🏢  {point['id']} — {point['name']}")
            point_item.setText(1, point.get("address", "—"))
            point_item.setText(3, real_ip if point["id"] == cur_pt_id else point.get("ip", "—"))
            point_item.setData(0, Qt.UserRole, ("point", point["id"]))

            f = point_item.font(0)
            f.setBold(True)
            f.setPointSize(f.pointSize() + 1)
            point_item.setFont(0, f)

            computers = self.store.list_computers(point_id=point["id"])

            if not computers:
                point_item.setText(2, "🔴  офлайн")
                point_item.setForeground(2, QBrush(QColor("#ef4444")))
                empty = QTreeWidgetItem(point_item)
                empty.setText(0, "    Нет компьютеров")
                empty.setForeground(0, QBrush(QColor("#64748b")))
                continue

            explicit_match_found = any(
                ((c.get("ip") or "").strip().lower() in local_ips)
                or ((c.get("name") or "").strip().lower() in (my_node.lower(), real_hostname.lower()))
                or ((c.get("sync_node") or "").strip().lower() in (my_node.lower(), real_hostname.lower()))
                for c in computers
            )

            point_has_online_comp = False

            for idx_c, comp in enumerate(computers):
                c_item = QTreeWidgetItem(point_item)
                c_item.setData(0, Qt.UserRole, ("computer", comp["id"]))
                cf = c_item.font(0)
                cf.setBold(True)
                c_item.setFont(0, cf)

                c_ip = (comp.get("ip") or "").strip()
                c_name = (comp.get("name") or "").strip()
                c_node = (comp.get("sync_node") or "").strip()

                is_local = (
                    (c_ip.lower() in local_ips and c_ip != "")
                    or (c_name.lower() in (my_node.lower(), real_hostname.lower()))
                    or (c_node.lower() in (my_node.lower(), real_hostname.lower()))
                    or (
                        not explicit_match_found
                        and point["id"] == cur_pt_id
                        and (c_name.lower() in ("пк-01", "pc-01", "master") or idx_c == 0)
                    )
                )

                c_online = False

                if is_local:
                    c_online = True
                    point_has_online_comp = True
                    display_pc = f"{c_name} ({real_hostname})" if real_hostname and real_hostname.lower() != c_name.lower() else c_name
                    role_badge = f"👑 Мастер-ПК · {comp.get('role', 'Основной оператор')}" if is_master else f"💻 Рабочий пост · {comp.get('role', 'Рабочее место')}"
                    c_item.setText(0, f"    💻  {display_pc}")
                    c_item.setText(1, role_badge)
                    c_item.setText(2, "🟢  онлайн")
                    c_item.setForeground(2, QBrush(QColor("#22c55e")))
                    c_item.setText(3, real_ip)
                    sync_state = "обмен вкл" if comp.get("sync_enabled", True) else "обмен выкл"
                    c_item.setText(4, f"узел: {my_node} · {sync_state}")
                else:
                    if c_ip and c_ip not in ("—", ""):
                        remote_ips_to_probe.append(c_ip)
                    cached = self._remote_pc_cache.get(c_ip)
                    c_online = bool(cached[0]) if cached else False
                    lat = cached[1] if cached else None
                    latency_info = f" ({int(lat) if lat else '<1'} мс)" if c_online and lat is not None else ""

                    recent_sync_str = ""
                    if sync_folder:
                        inbox_file = sync_mod.inbox_path(sync_folder, point["id"])
                        if inbox_file.exists():
                            try:
                                mtime = inbox_file.stat().st_mtime
                                age_sec = time.time() - mtime
                                if age_sec < 86400:
                                    t_str = datetime.fromtimestamp(mtime).strftime("%H:%M")
                                    recent_sync_str = f"обмен: {t_str}"
                                    if not c_online and age_sec < 300:
                                        c_online = True
                            except Exception:
                                pass

                    if c_online:
                        point_has_online_comp = True
                        c_item.setText(0, f"    💻  {c_name}")
                        c_item.setText(1, f"{comp.get('role', 'Рабочий пост')} · подключён")
                        c_item.setText(2, f"🟢  онлайн{latency_info}")
                        c_item.setForeground(2, QBrush(QColor("#22c55e")))
                    else:
                        c_item.setText(0, f"    💻  {c_name}")
                        c_item.setText(1, f"{comp.get('role', 'Рабочий пост')} · не в сети")
                        c_item.setText(2, "🔴  офлайн")
                        c_item.setForeground(2, QBrush(QColor("#ef4444")))

                    c_item.setText(3, c_ip or "—")
                    extra_info = [f"узел: {comp.get('sync_node', '—')}"]
                    if recent_sync_str:
                        extra_info.append(recent_sync_str)
                    elif comp.get("sync_enabled"):
                        extra_info.append("обмен вкл")
                    c_item.setText(4, " · ".join(extra_info))

                # --- Принтеры ---
                printers = self.store.list_printers_for_computer(comp["id"])
                shown_pr_names = set()
                for pr in printers:
                    p_item = QTreeWidgetItem(c_item)
                    p_name = (pr.get("name") or "").strip()
                    shown_pr_names.add(p_name.lower())
                    p_ip = (pr.get("ip") or "").strip()
                    p_port = pr.get("port")
                    p_online = False
                    p_status_text = "🔴  офлайн"
                    p_status_color = QColor("#ef4444")

                    if is_local:
                        if p_ip and p_ip != "—":
                            remote_ips_to_probe.append(p_ip)
                            p_cached = self._remote_pc_cache.get(p_ip)
                            p_online = bool(p_cached[0]) if p_cached else False
                            p_status_text = "🟢  онлайн" if p_online else "🔴  офлайн"
                            p_status_color = QColor("#22c55e") if p_online else QColor("#ef4444")
                        else:
                            p_low = p_name.lower()
                            p_online = (p_low in live_pr_names or any(lp in p_low or p_low in lp for lp in live_pr_names))
                            p_status_text = "🟢  онлайн" if p_online else "🔴  офлайн"
                            p_status_color = QColor("#22c55e") if p_online else QColor("#ef4444")
                    else:
                        if not c_online:
                            p_status_text = "🔴  офлайн"
                            p_status_color = QColor("#ef4444")
                        elif p_ip and p_ip != "—":
                            remote_ips_to_probe.append(p_ip)
                            p_cached = self._remote_pc_cache.get(p_ip)
                            p_online = bool(p_cached[0]) if p_cached else False
                            p_status_text = "🟢  онлайн" if p_online else "🔴  офлайн"
                            p_status_color = QColor("#22c55e") if p_online else QColor("#ef4444")
                        else:
                            p_online = bool(pr.get("online", True))
                            p_status_text = "🟢  онлайн" if p_online else "🔴  офлайн"
                            p_status_color = QColor("#22c55e") if p_online else QColor("#ef4444")

                    p_item.setText(0, f"        🖨  {p_name}")
                    p_item.setText(1, f"язык: {pr.get('lang', 'auto')}")
                    p_item.setText(2, p_status_text)
                    p_item.setForeground(2, QBrush(p_status_color))

                    target = p_ip or "USB"
                    if p_port and p_ip:
                        target = f"{p_ip}:{p_port}"
                    p_item.setText(3, target)
                    p_item.setText(4, f"{int(pr.get('width_mm', 58))}×{int(pr.get('height_mm', 40))} мм · {pr.get('dpi', 203)} DPI")
                    p_item.setData(0, Qt.UserRole, ("printer", pr["id"]))

                if is_local:
                    for lp in live_printers:
                        lp_name = (lp.get("name") or "").strip()
                        if lp_name and lp_name.lower() not in shown_pr_names and not any(
                            lp_name.lower() in sn or sn in lp_name.lower() for sn in shown_pr_names
                        ):
                            p_item = QTreeWidgetItem(c_item)
                            p_item.setText(0, f"        🖨  {lp_name}")
                            p_item.setText(1, f"язык: {lp.get('lang', 'auto')} · найден на ПК")
                            p_item.setText(2, "🟢  онлайн")
                            p_item.setForeground(2, QBrush(QColor("#22c55e")))
                            p_item.setText(3, lp.get("port", "USB"))
                            p_item.setText(4, "подключён сейчас")

                # --- Сканеры ---
                scanners = self.store.list_scanners_for_computer(comp["id"])
                shown_sc_names = set()
                for sc in scanners:
                    s_item = QTreeWidgetItem(c_item)
                    sc_name = (sc.get("name") or "").strip()
                    shown_sc_names.add(sc_name.lower())
                    sc_enabled = bool(sc.get("enabled", True))
                    sc_type = str(sc.get("connect_type") or "usb-hid").lower()
                    com_port = (sc.get("com_port") or "").upper().rstrip(":")
                    sc_ip = (sc.get("ip") or "").strip()
                    sc_port = sc.get("port")
                    s_status_text = "🔴  отключён"
                    s_status_color = QColor("#ef4444")

                    if not sc_enabled:
                        s_status_text = "🔴  отключён"
                        s_status_color = QColor("#ef4444")
                    elif is_local:
                        if sc_type in ("usb-com", "bt-spp"):
                            if com_port and com_port in active_coms:
                                s_status_text = f"🟢  активен ({com_port})"
                                s_status_color = QColor("#22c55e")
                            else:
                                s_status_text = "🔴  отключён"
                                s_status_color = QColor("#ef4444")
                        elif sc_type == "tcp":
                            remote_ips_to_probe.append(sc_ip)
                            s_cached = self._remote_pc_cache.get(sc_ip)
                            if sc_ip and s_cached and s_cached[0]:
                                s_status_text = "🟢  активен"
                                s_status_color = QColor("#22c55e")
                            else:
                                s_status_text = "🔴  отключён"
                                s_status_color = QColor("#ef4444")
                        else:
                            sc_dev_id = str(sc.get("device") or sc_name).lower()
                            if has_live_hid or any(k in sc_dev_id for k in live_sc_names):
                                s_status_text = "🟢  активен"
                                s_status_color = QColor("#22c55e")
                            else:
                                s_status_text = "🔴  отключён"
                                s_status_color = QColor("#ef4444")
                    else:
                        if not c_online:
                            s_status_text = "🔴  отключён"
                            s_status_color = QColor("#ef4444")
                        else:
                            s_status_text = "🟢  активен"
                            s_status_color = QColor("#22c55e")

                    s_item.setText(0, f"        📡  {sc_name}")
                    s_item.setText(1, f"Тип подключения: {sc.get('connect_type', 'USB-HID')}")
                    s_item.setText(2, s_status_text)
                    s_item.setForeground(2, QBrush(s_status_color))

                    if sc_type in ("usb-com", "bt-spp") and com_port:
                        target = com_port
                    elif sc_type == "tcp" and sc_ip:
                        target = f"{sc_ip}:{sc_port or 9001}"
                    else:
                        target = "USB-HID (клавиатура)"
                    s_item.setText(3, target)

                    if sc.get("printer_id"):
                        printer_name = "—"
                        for pr in printers:
                            if pr["id"] == sc["printer_id"]:
                                printer_name = pr["name"]
                                break
                        s_item.setText(4, f"→ {printer_name}")
                    else:
                        s_item.setText(4, "принтер не назначен")

                    s_item.setData(0, Qt.UserRole, ("scanner", sc["id"]))

                if is_local:
                    for ls in live_scanners:
                        ls_name = (ls.get("name") or "").strip()
                        if ls_name and ls_name.lower() not in shown_sc_names and not any(
                            ls_name.lower() in sn or sn in ls_name.lower() for sn in shown_sc_names
                        ):
                            s_item = QTreeWidgetItem(c_item)
                            s_item.setText(0, f"        📡  {ls_name}")
                            s_item.setText(1, f"Тип подключения: {ls.get('connect_type', 'USB-HID')} · найден на ПК")
                            s_item.setText(2, "🟢  активен")
                            s_item.setForeground(2, QBrush(QColor("#22c55e")))
                            s_item.setText(3, ls.get("device") or "USB")
                            s_item.setText(4, "подключён сейчас")

            # Итоговый статус точки в дереве (только зелёный или красный)
            if point_has_online_comp:
                point_item.setText(2, "🟢  онлайн")
                point_item.setForeground(2, QBrush(QColor("#22c55e")))
                point_item.setExpanded(True)
            else:
                point_item.setText(2, "🔴  офлайн")
                point_item.setForeground(2, QBrush(QColor("#ef4444")))

        # Запускаем фоновый опрос удалённых ПК без блокировки интерфейса
        if remote_ips_to_probe:
            self._start_async_remote_pc_probe(remote_ips_to_probe, force=force_probe)
    def _devices_on_selection(self):
        items = self.devices_tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if not data:
            return
        # Ничего не делаем — просто выделение

    def _devices_edit_item(self, item):
        if item is None:
            return
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        kind, obj_id = data

        if kind == "point":
            point = self.store.get_point(obj_id)
            if not point:
                return
            dlg = EditPointDialog(point, self)
            if dlg.exec() == EditPointDialog.Accepted and dlg.result_data:
                r = self.store.update_point(obj_id, **dlg.result_data)
                if r["ok"]:
                    self.toast.show_message(r["info"], "ok")
                    self._refresh_devices()
                else:
                    self.toast.show_message(f"Ошибка: {r['info']}", "err")

        elif kind == "computer":
            comp = self.store.get_computer(obj_id)
            if not comp:
                return
            point = self.store.get_point(comp["point_id"]) or {}
            dlg = EditComputerDialog(comp, point.get("name", ""), self)
            if dlg.exec() != EditComputerDialog.Accepted or not dlg.result_data:
                return
            if dlg.result_data.get("action") == "delete":
                r = self.store.delete_computer(obj_id)
            else:
                r = self.store.update_computer(obj_id, **dlg.result_data)
            if r["ok"]:
                self.toast.show_message(r["info"], "ok")
                self._refresh_devices()
            else:
                self.toast.show_message(f"Ошибка: {r['info']}", "err")

        elif kind == "printer":
            pr = self.store.get_printer(obj_id)
            if not pr:
                return
            comp = self.store.get_computer(pr["computer_id"]) or {}
            dlg = EditPrinterDialog(pr, comp.get("name", ""), self)
            if dlg.exec() != EditPrinterDialog.Accepted or not dlg.result_data:
                return
            if dlg.result_data.get("action") == "delete":
                r = self.store.delete_printer(obj_id)
            else:
                r = self.store.update_printer(obj_id, **dlg.result_data)
            if r["ok"]:
                self.toast.show_message(r["info"], "ok")
                self._refresh_devices()
            else:
                self.toast.show_message(f"Ошибка: {r['info']}", "err")

        elif kind == "scanner":
            sc = self.store.get_scanner(obj_id)
            if not sc:
                return
            comp = self.store.get_computer(sc["computer_id"]) or {}
            printers = self.store.list_printers_for_computer(sc["computer_id"])
            dlg = EditScannerDialog(sc, comp.get("name", ""), printers, self)
            if dlg.exec() != EditScannerDialog.Accepted or not dlg.result_data:
                return
            if dlg.result_data.get("action") == "delete":
                r = self.store.delete_scanner(obj_id)
            else:
                r = self.store.update_scanner(obj_id, **dlg.result_data)
            if r["ok"]:
                self.toast.show_message(r["info"], "ok")
                self._refresh_devices()
            else:
                self.toast.show_message(f"Ошибка: {r['info']}", "err")

    def _devices_get_selected(self):
        items = self.devices_tree.selectedItems()
        if not items:
            return (None, None)
        data = items[0].data(0, Qt.UserRole)
        if not data:
            return (None, None)
        return data

    def _devices_add_computer(self):
        kind, obj_id = self._devices_get_selected()

        if kind == "point":
            point_id = obj_id
        elif kind == "computer":
            comp = self.store.get_computer(obj_id)
            point_id = comp["point_id"] if comp else None
        else:
            point_id = None

        if not point_id:
            self.toast.show_message("Выберите точку или ПК в дереве", "warn")
            return

        point = self.store.get_point(point_id) or {}

        empty = {"name": "ПК-01", "ip": "", "role": "Основной оператор",
                 "sync_enabled": True, "online": True}
        dlg = EditComputerDialog(empty, point.get("name", ""), self)
        if dlg.exec() != EditComputerDialog.Accepted or not dlg.result_data:
            return

        r = self.store.create_computer(
            point_id=point_id,
            name=dlg.result_data["name"],
            ip=dlg.result_data["ip"],
            role=dlg.result_data["role"],
            sync_node=dlg.result_data.get("sync_node", ""),
            sync_enabled=dlg.result_data.get("sync_enabled", True),
            online=dlg.result_data.get("online", True),
        )
        if r["ok"]:
            self.toast.show_message(r["info"], "ok")
            self._refresh_devices()
        else:
            self.toast.show_message(f"Ошибка: {r['info']}", "err")

    def _devices_add_printer(self):
        kind, obj_id = self._devices_get_selected()

        if kind == "computer":
            computer_id = obj_id
        elif kind == "printer":
            pr = self.store.get_printer(obj_id)
            computer_id = pr["computer_id"] if pr else None
        elif kind == "scanner":
            sc = self.store.get_scanner(obj_id)
            computer_id = sc["computer_id"] if sc else None
        else:
            computer_id = None

        if not computer_id:
            self.toast.show_message("Выберите компьютер в дереве", "warn")
            return

        comp = self.store.get_computer(computer_id) or {}

        real_printers = [
            (pr.get("name", "") if isinstance(pr, dict) else str(pr))
            for pr in (list_printers() or [])
        ]
        real_printers = [n for n in real_printers if n]
        def_pr = default_printer()
        if isinstance(def_pr, dict):
            def_pr = def_pr.get("name", "")
        default_pr_name = def_pr or (real_printers[0] if real_printers else "Принтер этикеток")
        empty = {"name": default_pr_name, "ip": "", "port": 9100,
                 "lang": "auto", "width_mm": 58, "height_mm": 40,
                 "dpi": 203, "copies": 1, "online": True}
        dlg = EditPrinterDialog(empty, comp.get("name", ""), self)
        if dlg.exec() != EditPrinterDialog.Accepted or not dlg.result_data:
            return

        r = self.store.create_printer(
            computer_id=computer_id,
            name=dlg.result_data["name"],
            ip=dlg.result_data["ip"],
            port=dlg.result_data["port"],
            lang=dlg.result_data["lang"],
            width_mm=dlg.result_data["width_mm"],
            height_mm=dlg.result_data["height_mm"],
            dpi=dlg.result_data["dpi"],
            copies=dlg.result_data["copies"],
            online=dlg.result_data["online"],
        )
        if r["ok"]:
            self.toast.show_message(r["info"], "ok")
            self._refresh_devices()
        else:
            self.toast.show_message(f"Ошибка: {r['info']}", "err")

    def _devices_add_scanner(self):
        kind, obj_id = self._devices_get_selected()

        if kind == "computer":
            computer_id = obj_id
        elif kind == "scanner":
            sc = self.store.get_scanner(obj_id)
            computer_id = sc["computer_id"] if sc else None
        elif kind == "printer":
            pr = self.store.get_printer(obj_id)
            computer_id = pr["computer_id"] if pr else None
        else:
            computer_id = None

        if not computer_id:
            self.toast.show_message("Выберите компьютер в дереве", "warn")
            return

        comp = self.store.get_computer(computer_id) or {}
        printers = self.store.list_printers_for_computer(computer_id)

        empty = {"name": "Сканер M1", "operator": "",
                 "connect_type": "usb", "com_port": "", "bt_address": "",
                 "ip": "", "port": 9100, "printer_id": None, "enabled": True}
        dlg = EditScannerDialog(empty, comp.get("name", ""), printers, self)
        if dlg.exec() != EditScannerDialog.Accepted or not dlg.result_data:
            return

        r = self.store.create_scanner(
            computer_id=computer_id,
            name=dlg.result_data["name"],
            operator=dlg.result_data.get("operator", ""),
            printer_id=dlg.result_data.get("printer_id"),
            connect_type=dlg.result_data["connect_type"],
            com_port=dlg.result_data.get("com_port", ""),
            bt_address=dlg.result_data.get("bt_address", ""),
            ip=dlg.result_data.get("ip", ""),
            port=dlg.result_data.get("port", 9100),
            enabled=dlg.result_data.get("enabled", True),
        )
        if r["ok"]:
            self.toast.show_message(r["info"], "ok")
            self._refresh_devices()
        else:
            self.toast.show_message(f"Ошибка: {r['info']}", "err")

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

        btn_gtin_bind = QPushButton("🔗 Привязка к GTIN...")
        btn_gtin_bind.setToolTip("Управление связками GTIN-префиксов и шаблонов в отдельном окне")
        btn_gtin_bind.clicked.connect(self._open_gtin_binding_dialog)
        head.addWidget(btn_gtin_bind)

        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(14)

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

        center_col = QVBoxLayout()
        center_col.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)
        top_bar.addWidget(QLabel("Название:"))
        self.tpl_name_edit = QLineEdit()
        self.tpl_name_edit.setMinimumWidth(180)
        top_bar.addWidget(self.tpl_name_edit, stretch=1)

        top_bar.addSpacing(6)
        top_bar.addWidget(QLabel("Режим печати:"))
        self.tpl_mode_combo = QComboBox()
        self.tpl_mode_combo.addItem("ЧЗ + Русификатор (совмещённый)", "combined")
        self.tpl_mode_combo.addItem("ЧЗ + Русификатор (раздельный)", "separate")
        self.tpl_mode_combo.setFixedWidth(270)
        self.tpl_mode_combo.currentIndexChanged.connect(self._tpl_on_mode_changed)
        top_bar.addWidget(self.tpl_mode_combo)

        btn_save = QPushButton("💾 Сохранить шаблон")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._tpl_save)
        top_bar.addWidget(btn_save)

        btn_make_default = QPushButton("⭐ По умолчанию")
        btn_make_default.clicked.connect(self._tpl_make_default)
        top_bar.addWidget(btn_make_default)

        center_col.addLayout(top_bar)

        self.tpl_tabs = QTabWidget()
        self.tpl_tabs.setFixedHeight(86)
        self.tpl_tabs.currentChanged.connect(self._tpl_refresh_preview)

        self.tab_combined = self._build_template_tab("combined")
        self.tpl_tabs.addTab(self.tab_combined, "ЧЗ + Русификатор")

        self.tab_cz = self._build_template_tab("cz")
        self.tpl_tabs.addTab(self.tab_cz, "ЧЗ")

        self.tab_label = self._build_template_tab("label")
        self.tpl_tabs.addTab(self.tab_label, "Русификатор")

        center_col.addWidget(self.tpl_tabs)

        # Предпросмотр этикетки с панелью настройки шрифтов, стиля и сетки сверху — в центре экрана
        center_col.addWidget(self._build_template_preview(), stretch=1)

        row.addLayout(center_col, stretch=1)
        v.addLayout(row, stretch=1)

        # Резервные виджеты для безопасного вызова _refresh_bindings до открытия диалога
        self.bind_gtin = QLineEdit()
        self.bind_table = QTableWidget(0, 4)

        return w

    def _build_template_tab(self, tab_key: str) -> QWidget:
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)

        size_row.addWidget(QLabel("Ширина:"))
        w_spin = QDoubleSpinBox()
        w_spin.setRange(10, 500)
        w_spin.setDecimals(0)
        w_spin.setSuffix(" мм")
        w_spin.setValue(58)
        w_spin.setFixedWidth(110)
        w_spin.valueChanged.connect(self._tpl_refresh_preview)
        size_row.addWidget(w_spin)

        size_row.addWidget(QLabel("Высота:"))
        h_spin = QDoubleSpinBox()
        h_spin.setRange(10, 500)
        h_spin.setDecimals(0)
        h_spin.setSuffix(" мм")
        h_spin.setValue(40)
        h_spin.setFixedWidth(110)
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
        hint_lbl = QLabel("Кликните по элементу на этикетке ниже для настройки шрифта, стиля и позиции")
        hint_lbl.setStyleSheet("color:#64748b; font-size:12px;")
        size_row.addWidget(hint_lbl)
        v.addLayout(size_row)

        # Скрытая модель таблицы полей для хранения данных шаблона без загромождения центра экрана
        t = QTableWidget(0, 7, tab)
        t.setVisible(False)
        t.itemChanged.connect(lambda _: self._tpl_refresh_preview())
        t.itemSelectionChanged.connect(lambda tk=tab_key, tbl=t: self._tpl_on_table_row_selected(tk, tbl))

        if not hasattr(self, "_tpl_tab_widgets"):
            self._tpl_tab_widgets = {}
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
                mode_label = ("Совмещ." if tpl.get("print_mode") == "combined"
                              else "Раздел.")
                self.tpl_list.setItem(r, 1, cell(mode_label, "#93c5fd"))
        finally:
            self.tpl_list.blockSignals(False)

        if self.tpl_list.rowCount():
            self.tpl_list.selectRow(0)

        self._refresh_bindings()
        self._tpl_populate_shk_combo()

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
                item_name.setData(Qt.UserRole, {
                    "bold": bool(f.get("bold", False)),
                    "italic": bool(f.get("italic", False)),
                    "line_spacing": float(f.get("line_spacing") or 1.15),
                    "width": f.get("width"),
                    "height": f.get("height"),
                })
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

                spin_font = QSpinBox()
                spin_font.setRange(6, 40)
                spin_font.setValue(int(f.get("font_size", 12)))
                spin_font.setAlignment(Qt.AlignCenter)
                if is_dm:
                    spin_font.setEnabled(False)
                    spin_font.setValue(0)
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
                spin_size.valueChanged.connect(self._tpl_refresh_preview)
                t.setCellWidget(r, 4, spin_size)

                xy_text = f"X:{f.get('x', 0)} Y:{f.get('y', 0)}"
                item_xy = QTableWidgetItem(xy_text)
                item_xy.setFlags(item_xy.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, 5, item_xy)

                btn_rm = QPushButton("×")
                btn_rm.setFixedSize(28, 24)
                btn_rm.setStyleSheet(
                    "color:#ef4444; border:none; font-weight:700; "
                    "font-size:16px; padding:0; background:transparent;"
                )
                btn_rm.setCursor(Qt.PointingHandCursor)
                btn_rm.clicked.connect(
                    lambda _, k=tab_key, rr=r: self._tpl_remove_field(k, rr)
                )
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
        name, ok = QInputDialog.getText(
            self, "Новое поле",
            "Системное имя (например, country, manufacturer, batch):"
        )
        if not ok or not name.strip():
            return
        name = name.strip().lower().replace(" ", "_")

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
        t.setItem(r, 1, QTableWidgetItem(name))

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
        spin_size.valueChanged.connect(self._tpl_refresh_preview)
        t.setCellWidget(r, 4, spin_size)

        item_xy = QTableWidgetItem("X:0 Y:0")
        item_xy.setFlags(item_xy.flags() & ~Qt.ItemIsEditable)
        t.setItem(r, 5, item_xy)

        btn_rm = QPushButton("×")
        btn_rm.setFixedSize(28, 24)
        btn_rm.setStyleSheet(
            "color:#ef4444; border:none; font-weight:700; "
            "font-size:16px; padding:0; background:transparent;"
        )
        btn_rm.setCursor(Qt.PointingHandCursor)
        btn_rm.clicked.connect(
            lambda _, k=tab_key, rr=r: self._tpl_remove_field(k, rr)
        )
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
            size = (float(spin_size.value()) if spin_size and spin_size.value() > 0
                    else None)

            xy_text = t.item(r, 5).text() if t.item(r, 5) else "X:0 Y:0"
            x, y = 0, 0
            try:
                parts = xy_text.split()
                x = int(parts[0].split(":")[1])
                y = int(parts[1].split(":")[1])
            except Exception:
                pass

            all_fields.append({
                "name": name, "label": label,
                "required": required, "font_size": font_size,
                "size": size, "x": x, "y": y,
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
            wrap_chk = QWidget()
            wl = QHBoxLayout(wrap_chk)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.addWidget(chk)
            wl.setAlignment(Qt.AlignCenter)
            t.setCellWidget(r, 2, wrap_chk)

            spin_font = QSpinBox()
            spin_font.setRange(6, 40)
            spin_font.setValue(f["font_size"] if f["font_size"] else 12)
            spin_font.setAlignment(Qt.AlignCenter)
            if is_dm:
                spin_font.setEnabled(False)
                spin_font.setValue(0)
            spin_font.valueChanged.connect(self._tpl_refresh_preview)
            t.setCellWidget(r, 3, spin_font)

            spin_size = QDoubleSpinBox()
            spin_size.setRange(0, 200)
            spin_size.setDecimals(0)
            spin_size.setSuffix(" мм")
            spin_size.setValue(f["size"] if f["size"] else (20 if is_dm else 0))
            spin_size.setAlignment(Qt.AlignCenter)
            if not is_dm:
                spin_size.setEnabled(False)
            spin_size.valueChanged.connect(self._tpl_refresh_preview)
            t.setCellWidget(r, 4, spin_size)

            item_xy = QTableWidgetItem(f"X:{f['x']} Y:{f['y']}")
            item_xy.setFlags(item_xy.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, 5, item_xy)

            btn_rm = QPushButton("×")
            btn_rm.setFixedSize(28, 24)
            btn_rm.setStyleSheet(
                "color:#ef4444; border:none; font-weight:700; "
                "font-size:16px; padding:0; background:transparent;"
            )
            btn_rm.setCursor(Qt.PointingHandCursor)
            btn_rm.clicked.connect(
                lambda _, k=tab_key, rr=r: self._tpl_remove_field(k, rr)
            )
            wrap_rm = QWidget()
            wl2 = QHBoxLayout(wrap_rm)
            wl2.setContentsMargins(0, 0, 0, 0)
            wl2.addWidget(btn_rm)
            wl2.setAlignment(Qt.AlignCenter)
            t.setCellWidget(r, 6, wrap_rm)

        t.blockSignals(False)
        t.selectRow(new_idx)
        self._tpl_refresh_preview()


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
            size = None
            if name == "dataMatrix" and spin_size and spin_size.value() > 0:
                size = float(spin_size.value())

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

            extra = (
                t.item(r, 0).data(Qt.UserRole)
                if (t.item(r, 0) and isinstance(t.item(r, 0).data(Qt.UserRole), dict))
                else {}
            )
            field = {
                "name": name,
                "label": label,
                "required": required,
                "font_size": font_size,
                "bold": bool(extra.get("bold", False)),
                "italic": bool(extra.get("italic", False)),
                "line_spacing": float(extra.get("line_spacing") or 1.15),
                "width": extra.get("width"),
                "height": extra.get("height"),
                "x": x,
                "y": y,
            }
            if size is not None:
                field["size"] = size
            fields.append(field)
        return fields


    def _update_field_position(self, tab_key: str, row_idx: int,
                                x: int, y: int):
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if row_idx < 0 or row_idx >= t.rowCount():
            return

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
        """Обновить размер DataMatrix — через спинбокс колонки 4."""
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if row_idx < 0 or row_idx >= t.rowCount():
            return

        spin = t.cellWidget(row_idx, 4)
        if spin:
            spin.blockSignals(True)
            spin.setValue(float(size_mm))
            spin.blockSignals(False)

    def _update_field_font(self, tab_key: str, row_idx: int, font_size: int):
        """Обновить размер шрифта текстового поля."""
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if row_idx < 0 or row_idx >= t.rowCount():
            return

        spin = t.cellWidget(row_idx, 3)
        if spin:
            spin.blockSignals(True)
            spin.setValue(int(font_size))
            spin.blockSignals(False)
            self._tpl_update_font_badge(int(font_size))

    def _update_field_style(self, tab_key: str, row_idx: int, bold: Optional[bool] = None, italic: Optional[bool] = None, line_spacing: Optional[float] = None):
        """Обновить стиль шрифта (жирный / курсив / межстрочный интервал) текстового поля."""
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if row_idx < 0 or row_idx >= t.rowCount():
            return
        item = t.item(row_idx, 0)
        if not item:
            return
        extra = item.data(Qt.UserRole)
        if not isinstance(extra, dict):
            extra = {}
        else:
            extra = dict(extra)
        if bold is not None:
            extra["bold"] = bool(bold)
        if italic is not None:
            extra["italic"] = bool(italic)
        if line_spacing is not None:
            extra["line_spacing"] = float(line_spacing)
            if hasattr(self, "tpl_line_spacing_lbl"):
                self.tpl_line_spacing_lbl.setText(f"{float(line_spacing):.2f}")
        t.blockSignals(True)
        item.setData(Qt.UserRole, extra)
        t.blockSignals(False)

    def _update_field_dimensions(self, tab_key: str, row_idx: int, width_dots: int, height_dots: int):
        """Обновить заданную пользователем ширину и высоту рамки поля."""
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if row_idx < 0 or row_idx >= t.rowCount():
            return
        item = t.item(row_idx, 0)
        if not item:
            return
        extra = item.data(Qt.UserRole)
        if not isinstance(extra, dict):
            extra = {}
        else:
            extra = dict(extra)
        extra["width"] = int(width_dots)
        extra["height"] = int(height_dots)
        t.blockSignals(True)
        item.setData(Qt.UserRole, extra)
        t.blockSignals(False)

    def _update_field_label(self, tab_key: str, row_idx: int, new_label: str):
        """Обновить подпись текстового поля."""
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        if row_idx < 0 or row_idx >= t.rowCount():
            return
        t.blockSignals(True)
        item = t.item(row_idx, 1)
        if item is None:
            t.setItem(row_idx, 1, QTableWidgetItem(new_label))
        else:
            item.setText(new_label)
        t.blockSignals(False)

    def _tpl_add_field_active_tab(self):
        """Добавляет новое поле на активную вкладку шаблона через удобный выбор стандартных полей."""
        tab_idx = self.tpl_tabs.currentIndex() if hasattr(self, "tpl_tabs") else 0
        tab_key = ["combined", "cz", "label"][tab_idx] if 0 <= tab_idx < 3 else "combined"

        presets = [
            ("name_ru — Наименование товара", "name_ru", "Наименование"),
            ("model — Модель / Артикул", "model", "Модель"),
            ("importer — Импортёр", "importer", "Импортёр"),
            ("manufacturer — Производитель", "manufacturer", "Производитель"),
            ("exp_date — Срок годности", "exp_date", "Срок годности"),
            ("composition — Состав", "composition", "Состав"),
            ("material — Материал", "material", "Материал"),
            ("size_eu — Размер", "size_eu", "Размер"),
            ("country — Страна производства", "country", "Страна"),
            ("lot — Номер партии", "lot", "Партия"),
            ("sn — Серийный номер", "sn", "SN"),
            ("gtin — GTIN", "gtin", "GTIN"),
            ("qty — Количество", "qty", "Кол-во"),
            ("Своё текстовое поле...", "__custom__", ""),
        ]
        titles = [p[0] for p in presets]
        choice, ok = QInputDialog.getItem(
            self, "Добавить поле на этикетку",
            "Выберите поле для добавления на этикетку:",
            titles, 0, False
        )
        if not ok or not choice:
            return

        sel = next((p for p in presets if p[0] == choice), presets[0])
        if sel[1] == "__custom__":
            self._tpl_add_field(tab_key)
            return

        name, default_label = sel[1], sel[2]
        widgets = self._tpl_tab_widgets.get(tab_key)
        if not widgets:
            return
        t = widgets["fields"]
        r = t.rowCount()
        t.insertRow(r)

        item_name = QTableWidgetItem(name)
        item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
        item_name.setData(Qt.UserRole, {"bold": False, "italic": False})
        t.setItem(r, 0, item_name)
        t.setItem(r, 1, QTableWidgetItem("" if name == "name_ru" else default_label))

        chk = QCheckBox()
        wrap_chk = QWidget()
        wl = QHBoxLayout(wrap_chk)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(chk)
        wl.setAlignment(Qt.AlignCenter)
        t.setCellWidget(r, 2, wrap_chk)

        spin_font = QSpinBox()
        spin_font.setRange(6, 40)
        spin_font.setValue(10)
        spin_font.setAlignment(Qt.AlignCenter)
        spin_font.valueChanged.connect(self._tpl_refresh_preview)
        t.setCellWidget(r, 3, spin_font)

        spin_size = QDoubleSpinBox()
        spin_size.setRange(0, 200)
        spin_size.setDecimals(0)
        spin_size.setSuffix(" мм")
        spin_size.setValue(0)
        spin_size.setEnabled(False)
        spin_size.valueChanged.connect(self._tpl_refresh_preview)
        t.setCellWidget(r, 4, spin_size)

        # Размещаем новое поле внутри безопасной зоны со ступенчатым отступом по Y
        y_pos = min(220, 16 + r * 28)
        item_xy = QTableWidgetItem(f"X:16 Y:{y_pos}")
        item_xy.setFlags(item_xy.flags() & ~Qt.ItemIsEditable)
        t.setItem(r, 5, item_xy)

        if hasattr(self, "label_preview"):
            self.label_preview._selected = (tab_key, r)
            self.label_preview._notify_selection_changed()
        self._tpl_refresh_preview()


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

        combined_fields = config.get("combined", {}).get("fields", [])
        if not any(f["name"] == "dataMatrix" for f in combined_fields):
            self.toast.show_message(
                "В «ЧЗ + Русификатор» должно быть поле dataMatrix", "warn"
            )
            return

        cz_fields = config.get("cz", {}).get("fields", [])
        if not any(f["name"] == "dataMatrix" for f in cz_fields):
            self.toast.show_message(
                "В «ЧЗ» должно быть поле dataMatrix", "warn"
            )
            return

        result = self.store.update_template(
            code=tpl["code"], name=name, config=config,
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
        """Удобное создание нового шаблона этикетки с выбором пресетов размеров и режима."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Создание нового шаблона этикетки")
        dlg.resize(460, 320)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        hdr = QLabel("Параметры нового шаблона")
        hdr.setStyleSheet("font-size:15px; font-weight:700;")
        lay.addWidget(hdr)

        grid = QGridLayout()
        grid.setSpacing(10)

        grid.addWidget(QLabel("Название:"), 0, 0)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("например: Одежда 58х40 или Обувь ЧЗ 30х20")
        grid.addWidget(name_edit, 0, 1)

        grid.addWidget(QLabel("Размер этикетки:"), 1, 0)
        size_combo = QComboBox()
        size_combo.addItem("58 × 40 мм (стандарт термомаркера)", (58, 40))
        size_combo.addItem("58 × 30 мм (компактный)", (58, 30))
        size_combo.addItem("43 × 25 мм (малый формат)", (43, 25))
        size_combo.addItem("30 × 20 мм (только DataMatrix)", (30, 20))
        size_combo.addItem("Свой размер...", None)
        grid.addWidget(size_combo, 1, 1)

        custom_box = QWidget()
        c_lay = QHBoxLayout(custom_box)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(6)
        spin_w = QDoubleSpinBox(); spin_w.setRange(20, 150); spin_w.setValue(58); spin_w.setSuffix(" мм")
        spin_h = QDoubleSpinBox(); spin_h.setRange(15, 200); spin_h.setValue(40); spin_h.setSuffix(" мм")
        c_lay.addWidget(QLabel("Ш:"))
        c_lay.addWidget(spin_w)
        c_lay.addWidget(QLabel("В:"))
        c_lay.addWidget(spin_h)
        custom_box.setVisible(False)
        grid.addWidget(custom_box, 2, 1)

        def _on_size_change():
            data = size_combo.currentData()
            if data is None:
                custom_box.setVisible(True)
            else:
                custom_box.setVisible(False)
                spin_w.setValue(data[0])
                spin_h.setValue(data[1])
        size_combo.currentIndexChanged.connect(_on_size_change)

        grid.addWidget(QLabel("Режим печати:"), 3, 0)
        mode_combo = QComboBox()
        mode_combo.addItem("Совмещённая (ЧЗ DataMatrix + Русификатор)", "combined")
        mode_combo.addItem("Раздельная (только ЧЗ DataMatrix)", "separate")
        grid.addWidget(mode_combo, 3, 1)

        grid.addWidget(QLabel("Разрешение (DPI):"), 4, 0)
        dpi_combo = QComboBox()
        dpi_combo.addItem("203 DPI (стандарт для большинства принтеров)", 203)
        dpi_combo.addItem("300 DPI (высокая чёткость)", 300)
        grid.addWidget(dpi_combo, 4, 1)

        lay.addLayout(grid)
        lay.addStretch(1)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(dlg.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("Создать шаблон")
        btn_ok.setProperty("primary", True)
        btn_box.addWidget(btn_ok)
        lay.addLayout(btn_box)

        def _on_ok():
            title = name_edit.text().strip()
            if not title:
                QMessageBox.warning(dlg, "Ввод данных", "Введите название шаблона.")
                return

            w_val = spin_w.value()
            h_val = spin_h.value()
            sel_mode = mode_combo.currentData() or "combined"
            sel_dpi = dpi_combo.currentData() or 203

            import re, time
            translit_map = {
                'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
                'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
                'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
                'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
                'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
            }
            code_base = "".join(translit_map.get(ch, ch) for ch in title.lower())
            code_base = re.sub(r'[^a-z0-9_]+', '_', code_base).strip('_')
            code = f"{code_base[:16]}_{int(time.time()) % 10000}"

            dots_mm = sel_dpi / 25.4
            dm_sz_mm = round(min(w_val, h_val) * 0.45, 0)
            text_x_dots = int(round((dm_sz_mm + 4.0) * dots_mm))
            pad_dots = max(12, int(round(2.0 * dots_mm)))
            config = {
                "print_mode": sel_mode,
                "combined": {
                    "w": w_val, "h": h_val, "dpi": sel_dpi,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "size": dm_sz_mm, "x": pad_dots, "y": pad_dots},
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 9, "x": text_x_dots, "y": pad_dots},
                        {"name": "importer", "label": "Импортёр",
                         "required": False, "font_size": 8, "x": text_x_dots, "y": int(round(h_val * 0.45 * dots_mm))},
                    ],
                },
                "cz": {
                    "w": min(w_val, 30), "h": min(h_val, 30), "dpi": sel_dpi,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "size": min(w_val, h_val) * 0.7, "x": 3, "y": 3},
                    ],
                },
                "label": {
                    "w": w_val, "h": h_val, "dpi": sel_dpi,
                    "fields": [
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 12, "x": 5, "y": 5},
                    ],
                },
            }

            result = self.store.create_template(code=code, name=title, config=config)
            if result.get("ok"):
                self.toast.show_message(f"Шаблон «{title}» успешно создан", "ok")
                self._refresh_templates()
                dlg.accept()
            else:
                QMessageBox.critical(dlg, "Ошибка", result.get("info", "Не удалось создать шаблон"))

        btn_ok.clicked.connect(_on_ok)
        dlg.exec()

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
            bindings = self.store.list_bindings()
            tpls = {t["code"]: t["name"] for t in self.store.list_templates()}

            for b in bindings:
                r = self.bind_table.rowCount()
                self.bind_table.insertRow(r)
                self.bind_table.setItem(r, 0, cell(b["gtin_prefix"], mono=True))
                self.bind_table.setItem(
                    r, 1, cell(tpls.get(b["template_code"], b["template_code"]))
                )
                self.bind_table.setItem(r, 2, cell(str(b["priority"])))

                btn = QPushButton("×")
                btn.setFixedSize(32, 26)
                btn.setStyleSheet(
                    "color:#ef4444; border:none; font-weight:700; "
                    "font-size:18px; padding:0; background:transparent;"
                )
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(
                    lambda _, bid=b["id"]: self._tpl_bind_delete(bid)
                )
                wrap = QWidget()
                wl = QHBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 0, 0)
                wl.addWidget(btn)
                wl.setAlignment(Qt.AlignCenter)
                self.bind_table.setCellWidget(r, 3, wrap)
        except AttributeError:
            return

    def _tpl_bind_create(self):
        tpl = self._current_template()
        if not tpl:
            self.toast.show_message("Выберите шаблон", "warn")
            return
        prefix = self.bind_gtin.text().strip()
        if not prefix:
            self.toast.show_message("Введите GTIN-префикс", "warn")
            return
        result = self.store.create_binding(tpl["code"], prefix)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
            self.bind_gtin.clear()
            self._refresh_bindings()
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _tpl_bind_delete(self, binding_id: int):
        result = self.store.delete_binding(binding_id)
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
            self._refresh_bindings()
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    def _build_template_preview(self) -> QFrame:
        from label_preview import LabelPreviewWidget, _is_light_theme

        card = QFrame()
        card.setObjectName("card")

        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        # Верхняя строка: заголовок и выбор ШК товара для подстановки реального DataMatrix
        top_hdr = QHBoxLayout()
        top_hdr.setSpacing(10)

        title = QLabel("ПРЕДПРОСМОТР И НАСТРОЙКА ЭТИКЕТКИ (РЕАЛЬНЫЙ ДМ ПО ШК)")
        title.setStyleSheet(
            "color:#64748b; font-size:11px; font-weight:700; letter-spacing:1.2px;"
        )
        top_hdr.addWidget(title)
        top_hdr.addStretch(1)

        self.tpl_linked_dm_label = QLabel("ДМ: автовыбор из загруженной базы кодов")
        top_hdr.addWidget(self.tpl_linked_dm_label)
        v.addLayout(top_hdr)

        shk_row = QHBoxLayout()
        shk_row.setSpacing(8)
        shk_lbl = QLabel("ШК товара:")
        shk_lbl.setStyleSheet("color:#64748b; font-size:12px; font-weight:600;")
        shk_row.addWidget(shk_lbl)

        self.tpl_shk_combo = QComboBox()
        self.tpl_shk_combo.setEditable(True)
        self.tpl_shk_combo.setInsertPolicy(QComboBox.NoInsert)
        if self.tpl_shk_combo.lineEdit():
            self.tpl_shk_combo.lineEdit().setPlaceholderText("Выберите или отсканируйте ШК (EAN-13) / GTIN для подстановки ДМ...")
            self.tpl_shk_combo.lineEdit().returnPressed.connect(self._tpl_on_shk_selected)
        self.tpl_shk_combo.activated.connect(lambda _: self._tpl_on_shk_selected())
        shk_row.addWidget(self.tpl_shk_combo, stretch=1)

        btn_apply_shk = QPushButton("🔍 Найти ДМ")
        btn_apply_shk.setToolTip("Подтянуть настоящий DataMatrix из базы для указанного ШК")
        btn_apply_shk.setFixedWidth(104)
        btn_apply_shk.setStyleSheet(
            "QPushButton { background:#2563eb; color:#ffffff; font-weight:700; font-size:12px; "
            "padding:5px 10px; border-radius:6px; border:none; } "
            "QPushButton:hover { background:#1d4ed8; }"
        )
        btn_apply_shk.clicked.connect(self._tpl_on_shk_selected)
        shk_row.addWidget(btn_apply_shk)

        shk_row.addSpacing(8)
        lbl_sample = QLabel("Образец текста:")
        lbl_sample.setStyleSheet("color:#64748b; font-size:12px; font-weight:600;")
        shk_row.addWidget(lbl_sample)

        self.tpl_sample_combo = QComboBox()
        self.tpl_sample_combo.addItems(["Стандартный", "Короткий", "Длинный (тест)"])
        self.tpl_sample_combo.setFixedWidth(160)
        self.tpl_sample_combo.currentIndexChanged.connect(lambda idx: self.label_preview.set_sample_preset(idx))
        shk_row.addWidget(self.tpl_sample_combo)

        v.addLayout(shk_row)

        # Панель настройки размеров шрифтов, стиля, полей и сетки прямо над предпросмотром
        self.tpl_tools_panel = QFrame()
        tools_panel = self.tpl_tools_panel
        tp_lay = QHBoxLayout(tools_panel)
        tp_lay.setContentsMargins(10, 8, 10, 8)
        tp_lay.setSpacing(8)

        lbl_grid = QLabel("Сетка:")
        tp_lay.addWidget(lbl_grid)

        self.tpl_grid_combo = QComboBox()
        self.tpl_grid_combo.addItems(["1 мм", "2 мм", "5 мм", "Откл"])
        self.tpl_grid_combo.setFixedWidth(78)
        self.tpl_grid_combo.currentIndexChanged.connect(self._tpl_on_grid_changed)
        tp_lay.addWidget(self.tpl_grid_combo)

        btn_ruler = QPushButton("📏 Линейка")
        btn_ruler.setToolTip("Включить / выключить миллиметровую линейку сверху и слева")
        btn_ruler.setCheckable(True)
        btn_ruler.setChecked(True)
        btn_ruler.setFixedHeight(28)
        btn_ruler.clicked.connect(lambda: self.label_preview.toggle_ruler())
        tp_lay.addWidget(btn_ruler)

        btn_safe = QPushButton("🛡 Отступ 1.5 мм")
        btn_safe.setToolTip("Включить / выключить пунктир зоны безопасности (1.5 мм от края)")
        btn_safe.setCheckable(True)
        btn_safe.setChecked(True)
        btn_safe.setFixedHeight(28)
        btn_safe.clicked.connect(lambda: self.label_preview.toggle_safety_margin())
        tp_lay.addWidget(btn_safe)

        tp_lay.addSpacing(8)

        lbl_font = QLabel("Шрифт / размер:")
        tp_lay.addWidget(lbl_font)

        btn_font_minus = QPushButton("−")
        btn_font_minus.setToolTip("Уменьшить шрифт выбранного поля на 1 pt (или размер ДМ на 1 мм)")
        btn_font_minus.setFixedSize(30, 28)
        btn_font_minus.clicked.connect(lambda: self.label_preview.change_selected_font_size(-1))
        tp_lay.addWidget(btn_font_minus)

        self.tpl_font_size_lbl = QLabel("10 pt")
        self.tpl_font_size_lbl.setToolTip("Размер шрифта активного текстового поля (или размер DataMatrix)")
        self.tpl_font_size_lbl.setAlignment(Qt.AlignCenter)
        self.tpl_font_size_lbl.setFixedWidth(64)
        self.tpl_font_size_lbl.setFixedHeight(28)
        tp_lay.addWidget(self.tpl_font_size_lbl)

        btn_font_plus = QPushButton("+")
        btn_font_plus.setToolTip("Увеличить шрифт выбранного поля на 1 pt (или размер ДМ на 1 мм)")
        btn_font_plus.setFixedSize(30, 28)
        btn_font_plus.clicked.connect(lambda: self.label_preview.change_selected_font_size(+1))
        tp_lay.addWidget(btn_font_plus)

        self.tpl_bold_btn = QPushButton("B Жирный")
        self.tpl_bold_btn.setToolTip("Полужирный стиль шрифта для выбранного текстового поля (Ctrl+B)")
        self.tpl_bold_btn.setCheckable(True)
        self.tpl_bold_btn.setFixedHeight(28)
        self.tpl_bold_btn.clicked.connect(lambda: self.label_preview.toggle_selected_bold())
        tp_lay.addWidget(self.tpl_bold_btn)

        self.tpl_italic_btn = QPushButton("I Курсив")
        self.tpl_italic_btn.setToolTip("Курсивный стиль шрифта для выбранного текстового поля (Ctrl+I)")
        self.tpl_italic_btn.setCheckable(True)
        self.tpl_italic_btn.setFixedHeight(28)
        self.tpl_italic_btn.clicked.connect(lambda: self.label_preview.toggle_selected_italic())
        tp_lay.addWidget(self.tpl_italic_btn)

        tp_lay.addSpacing(6)

        btn_ls_minus = QPushButton("↕−")
        btn_ls_minus.setToolTip("Уменьшить межстрочный интервал на 0.1 (плотнее строки)")
        btn_ls_minus.setFixedSize(32, 28)
        btn_ls_minus.clicked.connect(lambda: self.label_preview.change_selected_line_spacing(-0.1))
        tp_lay.addWidget(btn_ls_minus)

        self.tpl_line_spacing_lbl = QLabel("1.15")
        self.tpl_line_spacing_lbl.setToolTip("Текущий межстрочный интервал (от 0.8 до 2.5)")
        self.tpl_line_spacing_lbl.setAlignment(Qt.AlignCenter)
        self.tpl_line_spacing_lbl.setFixedWidth(46)
        self.tpl_line_spacing_lbl.setFixedHeight(28)
        tp_lay.addWidget(self.tpl_line_spacing_lbl)

        btn_ls_plus = QPushButton("↕+")
        btn_ls_plus.setToolTip("Увеличить межстрочный интервал на 0.1 (свободнее строки)")
        btn_ls_plus.setFixedSize(32, 28)
        btn_ls_plus.clicked.connect(lambda: self.label_preview.change_selected_line_spacing(+0.1))
        tp_lay.addWidget(btn_ls_plus)

        tp_lay.addSpacing(8)

        btn_edit_lbl = QPushButton("✏ Подпись")
        btn_edit_lbl.setToolTip("Изменить подпись выбранного текстового поля")
        btn_edit_lbl.setFixedHeight(28)
        btn_edit_lbl.clicked.connect(lambda: self.label_preview.edit_selected_field_label())
        tp_lay.addWidget(btn_edit_lbl)

        btn_add_f = QPushButton("➕ Поле")
        btn_add_f.setToolTip("Добавить новое текстовое поле на этикетку")
        btn_add_f.setFixedHeight(28)
        btn_add_f.clicked.connect(self._tpl_add_field_active_tab)
        tp_lay.addWidget(btn_add_f)

        btn_del_f = QPushButton("🗑 Удалить поле")
        btn_del_f.setToolTip("Удалить выбранное текстовое поле с этикетки (Delete)")
        btn_del_f.setFixedHeight(28)
        btn_del_f.clicked.connect(lambda: self.label_preview.delete_selected_field())
        tp_lay.addWidget(btn_del_f)

        tp_lay.addStretch(1)
        v.addWidget(tools_panel)

        self.label_preview = LabelPreviewWidget(self)
        self.label_preview.setMinimumHeight(420)
        v.addWidget(self.label_preview, stretch=1)

        self.tpl_preview_size = QLabel("")
        self.tpl_preview_size.setStyleSheet("color:#64748b; font-size:12px;")
        self.tpl_preview_size.setAlignment(Qt.AlignCenter)
        v.addWidget(self.tpl_preview_size)

        self._apply_template_preview_theme(_is_light_theme())
        return card

    def _apply_template_preview_theme(self, is_lt: bool):
        """Применяет цвета светлой или тёмной темы к панели инструментов над предпросмотром."""
        if hasattr(self, "tpl_linked_dm_label"):
            if is_lt:
                self.tpl_linked_dm_label.setStyleSheet(
                    "color:#166534; font-family:Consolas,monospace; font-size:11px; font-weight:600; "
                    "background:#dcfce7; border:1px solid #bbf7d0; border-radius:6px; padding:4px 8px;"
                )
            else:
                self.tpl_linked_dm_label.setStyleSheet(
                    "color:#34d399; font-family:Consolas,monospace; font-size:11px; "
                    "background:#0d1117; border:1px solid #1e293b; border-radius:6px; padding:4px 8px;"
                )

        if hasattr(self, "tpl_tools_panel"):
            if is_lt:
                self.tpl_tools_panel.setStyleSheet(
                    "QFrame { background-color:#f1f5f9; border:1px solid #cbd5e1; border-radius:8px; } "
                    "QLabel { color:#334155; font-size:11px; font-weight:600; border:none; background:transparent; } "
                    "QComboBox { background-color:#ffffff; color:#0f172a; border:1px solid #cbd5e1; border-radius:6px; "
                    "padding:3px 8px; font-size:11px; min-height:22px; } "
                    "QComboBox:hover { border-color:#94a3b8; } "
                    "QComboBox::drop-down { border:none; width:16px; } "
                    "QPushButton { background-color:#ffffff; color:#1e293b; border:1px solid #cbd5e1; border-radius:6px; "
                    "padding:3px 10px; font-size:11px; font-weight:600; min-height:22px; } "
                    "QPushButton:hover { background-color:#e2e8f0; border-color:#94a3b8; color:#0f172a; } "
                    "QPushButton:checked { background-color:#2563eb; border-color:#1d4ed8; color:#ffffff; font-weight:700; }"
                )
            else:
                self.tpl_tools_panel.setStyleSheet(
                    "QFrame { background-color:#0d1117; border:1px solid #1e293b; border-radius:8px; } "
                    "QLabel { color:#94a3b8; font-size:11px; font-weight:600; border:none; background:transparent; } "
                    "QComboBox { background-color:#1e293b; color:#f1f5f9; border:1px solid #334155; border-radius:6px; "
                    "padding:3px 8px; font-size:11px; min-height:22px; } "
                    "QComboBox:hover { border-color:#64748b; } "
                    "QComboBox::drop-down { border:none; width:16px; } "
                    "QPushButton { background-color:#1e293b; color:#f1f5f9; border:1px solid #334155; border-radius:6px; "
                    "padding:3px 10px; font-size:11px; font-weight:600; min-height:22px; } "
                    "QPushButton:hover { background-color:#334155; border-color:#64748b; color:#ffffff; } "
                    "QPushButton:checked { background-color:#2563eb; border-color:#3b82f6; color:#ffffff; font-weight:700; }"
                )

        if hasattr(self, "tpl_font_size_lbl"):
            if is_lt:
                self.tpl_font_size_lbl.setStyleSheet(
                    "QLabel { background-color:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; "
                    "color:#1d4ed8; font-weight:700; font-size:11px; font-family:Consolas,monospace; padding:0px; }"
                )
            else:
                self.tpl_font_size_lbl.setStyleSheet(
                    "QLabel { background-color:#111827; border:1px solid #374151; border-radius:6px; "
                    "color:#38bdf8; font-weight:700; font-size:11px; font-family:Consolas,monospace; padding:0px; }"
                )

        if hasattr(self, "tpl_line_spacing_lbl"):
            if is_lt:
                self.tpl_line_spacing_lbl.setStyleSheet(
                    "QLabel { background-color:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; "
                    "color:#1d4ed8; font-weight:700; font-size:11px; font-family:Consolas,monospace; padding:0px; }"
                )
            else:
                self.tpl_line_spacing_lbl.setStyleSheet(
                    "QLabel { background-color:#111827; border:1px solid #374151; border-radius:6px; "
                    "color:#38bdf8; font-weight:700; font-size:11px; font-family:Consolas,monospace; padding:0px; }"
                )

    def _tpl_on_grid_changed(self, idx: int):
        steps = [1.0, 2.0, 5.0, 0.0]
        if hasattr(self, "label_preview") and 0 <= idx < len(steps):
            self.label_preview.set_grid_step(steps[idx])
            self.label_preview.update()

    def _tpl_update_font_badge(self, font_size: int):
        """Обновляет индикатор размера шрифта активного поля в панели инструментов."""
        if hasattr(self, "tpl_font_size_lbl"):
            self.tpl_font_size_lbl.setText(f"{font_size} pt")

    def _tpl_sync_selected_field_ui(self, f: Optional[ dict ]):
        """Синхронизирует кнопки размера шрифта, жирности и курсива с выбранным на холсте полем."""
        if not f:
            if hasattr(self, "tpl_font_size_lbl"):
                self.tpl_font_size_lbl.setText("-- pt")
            if hasattr(self, "tpl_bold_btn"):
                self.tpl_bold_btn.setChecked(False)
            if hasattr(self, "tpl_italic_btn"):
                self.tpl_italic_btn.setChecked(False)
            return

        if f.get("name") == "dataMatrix":
            sz = int(round(float(f.get("size") or 19)))
            if hasattr(self, "tpl_font_size_lbl"):
                self.tpl_font_size_lbl.setText(f"ДМ {sz}мм")
            if hasattr(self, "tpl_bold_btn"):
                self.tpl_bold_btn.setChecked(False)
            if hasattr(self, "tpl_italic_btn"):
                self.tpl_italic_btn.setChecked(False)
            if hasattr(self, "tpl_line_spacing_lbl"):
                self.tpl_line_spacing_lbl.setText("—")
        else:
            pt = int(f.get("font_size") or 10)
            ls = float(f.get("line_spacing") or 1.15)
            if hasattr(self, "tpl_font_size_lbl"):
                self.tpl_font_size_lbl.setText(f"{pt} pt")
            if hasattr(self, "tpl_bold_btn"):
                self.tpl_bold_btn.setChecked(bool(f.get("bold", False)))
            if hasattr(self, "tpl_italic_btn"):
                self.tpl_italic_btn.setChecked(bool(f.get("italic", False)))
            if hasattr(self, "tpl_line_spacing_lbl"):
                self.tpl_line_spacing_lbl.setText(f"{ls:.2f}")

    def _tpl_on_table_row_selected(self, tab_key: str, table: QTableWidget):
        """Синхронизирует выбор строки в модели полей с визуальным холстом и индикатором шрифта."""
        row = table.currentRow()
        if hasattr(self, "label_preview") and row >= 0:
            self.label_preview._selected = (tab_key, row)
            self.label_preview.update()
            fields = self._collect_fields_from_table(tab_key)
            if 0 <= row < len(fields):
                self._tpl_sync_selected_field_ui(fields[row])

    def _open_gtin_binding_dialog(self):
        """Открывает модальный диалог управления привязками шаблонов к GTIN-префиксам."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Привязка шаблонов к GTIN-префиксам")
        dlg.setMinimumWidth(560)
        dlg.setMinimumHeight(400)

        vl = QVBoxLayout(dlg)
        vl.setContentsMargins(18, 18, 18, 18)
        vl.setSpacing(12)

        desc = QLabel(
            "Настройте соответствие GTIN-префикса товара шаблону этикетки.\n"
            "При сканировании ШК система автоматически выберет нужный шаблон по совпадению префикса."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#94a3b8; font-size:12px;")
        vl.addWidget(desc)

        card_add = QFrame()
        card_add.setObjectName("card")
        cl = QHBoxLayout(card_add)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)

        cl.addWidget(QLabel("GTIN-префикс:"))
        self.bind_gtin = QLineEdit()
        self.bind_gtin.setPlaceholderText("4–14 цифр")
        self.bind_gtin.setFixedWidth(140)
        cl.addWidget(self.bind_gtin)

        btn_bind = QPushButton("🔗 Привязать к активному шаблону")
        btn_bind.clicked.connect(self._tpl_bind_create)
        cl.addWidget(btn_bind)
        cl.addStretch(1)
        vl.addWidget(card_add)

        self.bind_table = QTableWidget(0, 4)
        self.bind_table.setHorizontalHeaderLabels(["GTIN-префикс", "Шаблон", "Приоритет", ""])
        hb = self.bind_table.horizontalHeader()
        hb.setSectionResizeMode(0, QHeaderView.Fixed)
        hb.setSectionResizeMode(1, QHeaderView.Stretch)
        hb.setSectionResizeMode(2, QHeaderView.Fixed)
        hb.setSectionResizeMode(3, QHeaderView.Fixed)
        self.bind_table.setColumnWidth(0, 140)
        self.bind_table.setColumnWidth(2, 90)
        self.bind_table.setColumnWidth(3, 50)
        self.bind_table.verticalHeader().setVisible(False)
        self.bind_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bind_table.setShowGrid(False)
        vl.addWidget(self.bind_table, stretch=1)

        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(dlg.accept)
        btn_close.setFixedWidth(100)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        vl.addLayout(btn_row)

        self._refresh_bindings()
        dlg.exec()

    def _tpl_populate_shk_combo(self):
        """Заполняет выпадающий список ШК/GTIN в конструкторе шаблонов реальными связками ШК -> ДМ из БД."""
        if not hasattr(self, "tpl_shk_combo"):
            return
        current_val = self.tpl_shk_combo.currentData() or self.tpl_shk_combo.currentText().strip()
        self.tpl_shk_combo.blockSignals(True)
        try:
            self.tpl_shk_combo.clear()
            self.tpl_shk_combo.addItem("⚡ Авто (первый доступный ДМ из базы)", "")
            import db
            with db.get_conn() as conn:
                pool_stats = {}
                for pr in conn.execute(
                    "SELECT gtin, COUNT(*) AS cnt, "
                    "       SUM(CASE WHEN used = 0 THEN 1 ELSE 0 END) AS free_cnt, "
                    "       MAX(import_batch) AS batch_name "
                    "FROM pool_codes WHERE gtin IS NOT NULL AND gtin != '' "
                    "GROUP BY gtin"
                ).fetchall():
                    g_val = str(pr["gtin"] or "").strip()
                    if g_val:
                        pool_stats[g_val] = {
                            "total": pr["cnt"] or 0,
                            "free": pr["free_cnt"] or 0,
                            "batch": str(pr["batch_name"] or "").strip(),
                        }

                rows = conn.execute("SELECT foreign_gtin, cz_gtin FROM gtin_map ORDER BY foreign_gtin LIMIT 200").fetchall()
                seen_gtins = set()
                for r in rows:
                    shk = (r["foreign_gtin"] or "").strip()
                    cz_gtin = (r["cz_gtin"] or "").strip()
                    st = pool_stats.get(cz_gtin, {"free": 0, "batch": ""})
                    seen_gtins.add(cz_gtin)
                    pname = st.get("batch") or ""
                    cnt = st.get("free") or 0
                    label = f"ШК {shk} → GTIN {cz_gtin}"
                    if pname:
                        label += f" ({pname})"
                    label += f" [{cnt} ДМ]"
                    self.tpl_shk_combo.addItem(label, shk)

                for g, st in sorted(pool_stats.items(), key=lambda x: x[1]["free"], reverse=True)[:100]:
                    if g not in seen_gtins:
                        batch_str = f" ({st['batch']})" if st.get("batch") else ""
                        self.tpl_shk_combo.addItem(f"GTIN {g}{batch_str} [{st['free']} ДМ]", g)
        except Exception as e:
            log.error("_tpl_populate_shk_combo: %s", e)
        finally:
            self.tpl_shk_combo.blockSignals(False)

        if current_val:
            idx = self.tpl_shk_combo.findData(current_val)
            if idx >= 0:
                self.tpl_shk_combo.setCurrentIndex(idx)
        self._tpl_update_linked_dm_status()

    def _tpl_on_shk_selected(self):
        if not hasattr(self, "tpl_shk_combo") or not hasattr(self, "label_preview"):
            return
        shk = self.tpl_shk_combo.currentData()
        if shk is None or shk == "":
            raw_txt = self.tpl_shk_combo.currentText().strip()
            if raw_txt and not raw_txt.startswith("⚡"):
                # Извлекаем цифры ШК, если пользователь ввёл/отсканировал вручную
                import re
                m = re.search(r"(\d{8,14})", raw_txt)
                shk = m.group(1) if m else raw_txt
            else:
                shk = ""
        self.label_preview.set_shk_for_preview(shk)
        self._tpl_update_linked_dm_status()

    def _tpl_update_linked_dm_status(self):
        if not hasattr(self, "tpl_linked_dm_label") or not hasattr(self, "label_preview"):
            return
        try:
            dm_code, sn_val, gtin_val, matched_shk = self.label_preview._resolve_sample_dm()
            short_dm = dm_code[:38] + "..." if len(dm_code) > 38 else dm_code
            self.tpl_linked_dm_label.setText(
                f"ШК/GTIN: {matched_shk or gtin_val}  ·  SN: {sn_val}  ·  ДМ ({len(dm_code)} симв.): {short_dm}"
            )
        except Exception:
            pass


    def _tpl_refresh_preview(self):
        if hasattr(self, "_tpl_preview_timer"):
            self._tpl_preview_timer.start()

    def _tpl_refresh_preview_now(self):
        if hasattr(self, "label_preview"):
            self.label_preview._ensure_selected_field(prefer_text=True)
            self.label_preview.update()
        try:
            tab_key = ["combined", "cz", "label"][self.tpl_tabs.currentIndex()]
            widgets = self._tpl_tab_widgets.get(tab_key)
            if widgets:
                w_mm = widgets["w"].value()
                h_mm = widgets["h"].value()
                dpi = widgets["dpi"].currentText()
                self.tpl_preview_size.setText(
                    f"{int(w_mm)} × {int(h_mm)} мм · {dpi} DPI · Перетаскивание — мышь · Размер шрифта — колёсико или кнопки сверху"
                )
        except Exception:
            pass

    # ==================================================================
    # POOL (Пул кодов ЧЗ + Привязка ШК товара <-> GTIN ЧЗ gtin_map)
    # ==================================================================
    def _page_pool(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Склад кодов ЧЗ и привязка штрихкодов (ШК ↔ GTIN ЧЗ)")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_export = QPushButton("⬇ Экспорт пула CSV")
        btn_export.clicked.connect(self._export_pool)
        head.addWidget(btn_export)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_pool)
        head.addWidget(btn_refresh)
        v.addLayout(head)

        self.pool_tabs = QTabWidget()

        # ---------------- ВКЛАДКА 1: ПУЛ КОДОВ ЧЗ ----------------
        tab_pool = QWidget()
        vp = QVBoxLayout(tab_pool)
        vp.setContentsMargins(14, 14, 14, 14)
        vp.setSpacing(10)

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
        self.pool_count_label.setStyleSheet("color:#8b97a6; font-size:12px; font-weight:600;")
        filter_row.addWidget(self.pool_count_label)
        vp.addLayout(filter_row)

        toolbar2 = QHBoxLayout()
        toolbar2.setSpacing(8)

        btn_import_xlsx = QPushButton("📥 Импорт xlsx (ЦРПТ)")
        btn_import_xlsx.setProperty("primary", True)
        btn_import_xlsx.clicked.connect(self._import_codes_xlsx)
        toolbar2.addWidget(btn_import_xlsx)

        btn_import = QPushButton("⬆ Импорт CSV")
        btn_import.clicked.connect(self._import_pool)
        toolbar2.addWidget(btn_import)

        btn_template = QPushButton("📄 Шаблон CSV")
        btn_template.clicked.connect(self._template_pool)
        toolbar2.addWidget(btn_template)

        btn_inspect = QPushButton("🔍 Просмотр кодов GTIN")
        btn_inspect.clicked.connect(self._pool_inspect_selected_gtin)
        toolbar2.addWidget(btn_inspect)

        btn_reassign = QPushButton("⇄ Назначить точку")
        btn_reassign.clicked.connect(self._pool_reassign_point_for_gtin)
        toolbar2.addWidget(btn_reassign)

        btn_del_free = QPushButton("🗑 Удалить свободные коды GTIN")
        btn_del_free.clicked.connect(self._pool_delete_free_for_gtin)
        toolbar2.addWidget(btn_del_free)

        toolbar2.addStretch(1)
        vp.addLayout(toolbar2)

        hint_pool = QLabel("💡 Двойной клик по строке GTIN открывает список конкретных кодов и серийных номеров (SN).")
        hint_pool.setStyleSheet("color:#64748b; font-size:11px;")
        vp.addWidget(hint_pool)

        self.pool_table = table(
            ["GTIN ЧЗ", "Точка", "Всего", "Свободно", "Использовано", "Напечатано"]
        )
        self.pool_table.itemDoubleClicked.connect(lambda _it: self._pool_inspect_selected_gtin())
        vp.addWidget(self.pool_table, stretch=1)
        self.pool_tabs.addTab(tab_pool, "🎫 Пул кодов ЧЗ (pool_codes)")

        # ---------------- ВКЛАДКА 2: ПРИВЯЗКА ШК ТОВАРА <-> GTIN ЧЗ (gtin_map) ----------------
        tab_gmap = QWidget()
        vg = QVBoxLayout(tab_gmap)
        vg.setContentsMargins(14, 14, 14, 14)
        vg.setSpacing(10)

        gmap_desc = QLabel(
            "Сопоставление внешнего штрихкода товара/коробки (EAN-13, GTIN-14, артикул поставщика) "
            "с российским GTIN Честного Знака. Можно пропикать ШК товара сканером прямо в поле ниже."
        )
        gmap_desc.setWordWrap(True)
        gmap_desc.setStyleSheet("color:#8b97a6; font-size:12px;")
        vg.addWidget(gmap_desc)

        add_box = QFrame()
        add_box.setObjectName("card")
        ab_row = QHBoxLayout(add_box)
        ab_row.setContentsMargins(14, 10, 14, 10)
        ab_row.setSpacing(10)

        ab_row.addWidget(QLabel("ШК товара (EAN-13 / внешний):"))
        self.gmap_shk_edit = QLineEdit()
        self.gmap_shk_edit.setPlaceholderText("Отсканируйте или введите ШК товара (например, 4607012345678)")
        self.gmap_shk_edit.setMinimumWidth(260)
        ab_row.addWidget(self.gmap_shk_edit, stretch=1)

        ab_row.addWidget(QLabel("→  GTIN ЧЗ (14 цифр):"))
        self.gmap_cz_combo = QComboBox()
        self.gmap_cz_combo.setEditable(True)
        self.gmap_cz_combo.setMinimumWidth(220)
        ab_row.addWidget(self.gmap_cz_combo, stretch=1)

        btn_gmap_add = QPushButton("➕ Привязать ШК → GTIN ЧЗ")
        btn_gmap_add.setProperty("primary", True)
        btn_gmap_add.clicked.connect(self._gmap_add_binding)
        ab_row.addWidget(btn_gmap_add)
        vg.addWidget(add_box)

        gmap_tools = QHBoxLayout()
        gmap_tools.setSpacing(8)

        btn_gmap_import = QPushButton("⬆ Импорт связок CSV")
        btn_gmap_import.clicked.connect(self._gmap_import_csv)
        gmap_tools.addWidget(btn_gmap_import)

        btn_gmap_tpl = QPushButton("📄 Скачать шаблон CSV")
        btn_gmap_tpl.clicked.connect(self._gmap_template_csv)
        gmap_tools.addWidget(btn_gmap_tpl)

        btn_gmap_del = QPushButton("🗑 Удалить выбранную связку")
        btn_gmap_del.clicked.connect(self._gmap_delete_selected)
        gmap_tools.addWidget(btn_gmap_del)

        gmap_tools.addStretch(1)
        self.gmap_count_lbl = QLabel("Связок: 0")
        self.gmap_count_lbl.setStyleSheet("color:#8b97a6; font-size:12px; font-weight:600;")
        gmap_tools.addWidget(self.gmap_count_lbl)
        vg.addLayout(gmap_tools)

        self.gmap_table = table([
            "Внешний ШК товара / коробки (foreign_gtin)",
            "Российский GTIN ЧЗ (cz_gtin)",
            "Свободно кодов ЧЗ в пуле",
            "Всего кодов ЧЗ в пуле"
        ])
        vg.addWidget(self.gmap_table, stretch=1)
        self.pool_tabs.addTab(tab_gmap, "🔗 Привязка ШК товара ↔ GTIN ЧЗ (gtin_map)")

        # ---------------- ВКЛАДКА 3: ИСТОРИЯ ИМПОРТОВ ----------------
        tab_imp = self._page_imports()
        self.pool_tabs.addTab(tab_imp, "📥 История импортов")
        self.pool_tabs.currentChanged.connect(lambda idx: self._refresh_imports() if idx == 2 else None)

        v.addWidget(self.pool_tabs, stretch=1)
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
                t.setItem(r, 1, cell("Все точки"))
                t.setItem(r, 2, cell(p_data["total"], mono=True))
                t.setItem(r, 3, cell(p_data["free"], "#22c55e", mono=True))
                t.setItem(r, 4, cell(p_data["used"], "#f59e0b", mono=True))
                t.setItem(r, 5, cell(p_data["printed"], mono=True))

        try:
            total = self.store.count_pool(point)
            suffix = f" (точка {point})" if point else " (все точки)"
            self.pool_count_label.setText(f"Всего кодов в пуле: {total}{suffix}")
        except Exception:
            pass

        self._refresh_gtin_map_tab()

    def _refresh_gtin_map_tab(self):
        """Обновляет таблицу привязок ШК <-> GTIN ЧЗ и список доступных GTIN из пула."""
        if not hasattr(self, "gmap_table"):
            return

        import db
        pool_by_gtin = {}
        map_rows = []
        try:
            with db.get_conn() as conn:
                for r in conn.execute(
                    "SELECT gtin, COUNT(*) AS total, SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free "
                    "FROM pool_codes GROUP BY gtin ORDER BY gtin"
                ).fetchall():
                    pool_by_gtin[str(r["gtin"]).strip()] = {"total": r["total"], "free": r["free"]}

                for r in conn.execute(
                    "SELECT foreign_gtin, cz_gtin FROM gtin_map ORDER BY foreign_gtin"
                ).fetchall():
                    map_rows.append((str(r["foreign_gtin"]).strip(), str(r["cz_gtin"]).strip()))
        except Exception:
            for fg, cz in (getattr(self.store, "gtin_map", {}) or {}).items():
                map_rows.append((str(fg).strip(), str(cz).strip()))

        # Обновляем комбобокс GTIN ЧЗ
        if hasattr(self, "gmap_cz_combo"):
            cur_text = self.gmap_cz_combo.currentText().strip()
            self.gmap_cz_combo.blockSignals(True)
            self.gmap_cz_combo.clear()
            for gtin_k, st in pool_by_gtin.items():
                self.gmap_cz_combo.addItem(f"{gtin_k} (свободно: {st['free']})", gtin_k)
            if cur_text:
                self.gmap_cz_combo.setEditText(cur_text)
            self.gmap_cz_combo.blockSignals(False)

        t = self.gmap_table
        t.setRowCount(0)
        for fg, cz in map_rows:
            r = t.rowCount()
            t.insertRow(r)
            st = pool_by_gtin.get(cz, {"free": 0, "total": 0})
            t.setItem(r, 0, cell(fg, mono=True))
            t.setItem(r, 1, cell(cz, "#38bdf8", mono=True))
            t.setItem(r, 2, cell(st["free"], "#22c55e" if st["free"] > 0 else "#ef4444", mono=True))
            t.setItem(r, 3, cell(st["total"], mono=True))

        if hasattr(self, "gmap_count_lbl"):
            self.gmap_count_lbl.setText(f"Связок ШК ↔ GTIN ЧЗ: {len(map_rows)}")

    def _gmap_add_binding(self):
        """Добавляет связку ШК товара -> GTIN ЧЗ в базу данных и кэш Store."""
        shk = self.gmap_shk_edit.text().strip()
        cz_raw = self.gmap_cz_combo.currentData() or self.gmap_cz_combo.currentText().strip()
        if cz_raw and " (" in str(cz_raw):
            cz_raw = str(cz_raw).split(" (", 1)[0].strip()

        if not shk:
            self.toast.show_message("Введите или отсканируйте ШК товара", "warn")
            return
        if not cz_raw:
            self.toast.show_message("Укажите российский GTIN ЧЗ", "warn")
            return

        try:
            import db
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO gtin_map (foreign_gtin, cz_gtin) VALUES (?, ?)",
                    (shk, cz_raw)
                )
                conn.commit()
            if hasattr(self.store, "gtin_map") and isinstance(self.store.gtin_map, dict):
                self.store.gtin_map[shk] = cz_raw
            self.gmap_shk_edit.clear()
            self._refresh_gtin_map_tab()
            self.toast.show_message(f"Привязано: ШК {shk} → GTIN ЧЗ {cz_raw}", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка сохранения привязки: {e}", "err")

    def _gmap_delete_selected(self):
        """Удаляет выбранную связку из gtin_map."""
        row = self.gmap_table.currentRow()
        if row < 0:
            self.toast.show_message("Выберите связку в таблице для удаления", "warn")
            return
        fg_item = self.gmap_table.item(row, 0)
        if not fg_item:
            return
        fg = fg_item.text().strip()

        try:
            import db
            with db.get_conn() as conn:
                conn.execute("DELETE FROM gtin_map WHERE foreign_gtin = ?", (fg,))
                conn.commit()
            if hasattr(self.store, "gtin_map") and isinstance(self.store.gtin_map, dict):
                self.store.gtin_map.pop(fg, None)
            self._refresh_gtin_map_tab()
            self.toast.show_message(f"Связка для ШК {fg} удалена", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка удаления: {e}", "err")

    def _gmap_import_csv(self):
        result = import_gtin_map(self.store, self)
        if result.get("ok"):
            self._refresh_gtin_map_tab()
            self.toast.show_message(result.get("info", "Импорт связок выполнен"), "ok")
        elif result.get("info") != "Отменено":
            self.toast.show_message(f"Ошибка: {result.get('info')}", "err")

    def _gmap_template_csv(self):
        result = download_gtin_map_template(self)
        if result.get("ok"):
            self.toast.show_message(result.get("info", "Шаблон сохранён"), "ok")
        elif result.get("info") != "Отменено":
            self.toast.show_message(f"Ошибка: {result.get('info')}", "err")

    def _pool_selected_gtin(self) -> Optional[str]:
        row = self.pool_table.currentRow()
        if row < 0:
            return None
        it = self.pool_table.item(row, 0)
        return it.text().strip() if it else None

    def _pool_inspect_selected_gtin(self):
        """Открывает диалоговое окно со списком конкретных кодов ЧЗ и серийных номеров выбранного GTIN."""
        gtin = self._pool_selected_gtin()
        if not gtin:
            self.toast.show_message("Выберите строку GTIN в таблице пула", "warn")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Коды Честного Знака — GTIN {gtin}")
        dlg.resize(920, 560)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(QLabel(f"GTIN: {gtin}"))
        search_ed = QLineEdit()
        search_ed.setPlaceholderText("Поиск по серийному номеру (SN) или коду...")
        top.addWidget(search_ed, stretch=1)

        status_cmb = QComboBox()
        status_cmb.addItem("Все коды", "all")
        status_cmb.addItem("Только свободные (used=0)", "free")
        status_cmb.addItem("Использованные (used=1)", "used")
        status_cmb.addItem("Напечатанные (printed=1)", "printed")
        top.addWidget(status_cmb)

        btn_print_sel = QPushButton("🖨 Напечатать выбранный код")
        btn_print_sel.setProperty("primary", True)
        top.addWidget(btn_print_sel)
        lay.addLayout(top)

        codes_tbl = table(["ID", "Серия (SN)", "Точка", "Статус", "Напечатан", "Полный код ЧЗ (DataMatrix)"])

        def _load_codes():
            import db
            q = search_ed.text().strip()
            flt = status_cmb.currentData()
            sql = "SELECT id, sn, assigned_point, used, printed, code FROM pool_codes WHERE gtin = ?"
            params = [gtin]
            if flt == "free":
                sql += " AND used = 0"
            elif flt == "used":
                sql += " AND used = 1"
            elif flt == "printed":
                sql += " AND printed = 1"
            if q:
                sql += " AND (sn LIKE ? OR code LIKE ?)"
                params.extend([f"%{q}%", f"%{q}%"])
            sql += " ORDER BY id LIMIT 500"

            codes_tbl.setRowCount(0)
            with db.get_conn() as conn:
                for r_db in conn.execute(sql, tuple(params)).fetchall():
                    ri = codes_tbl.rowCount()
                    codes_tbl.insertRow(ri)
                    codes_tbl.setItem(ri, 0, cell(r_db["id"], mono=True))
                    codes_tbl.setItem(ri, 1, cell(r_db["sn"], mono=True))
                    codes_tbl.setItem(ri, 2, cell(r_db["assigned_point"] or "—", mono=True))
                    used_b = bool(r_db["used"])
                    codes_tbl.setItem(
                        ri, 3,
                        cell("Списан" if used_b else "Свободен", "#f59e0b" if used_b else "#22c55e")
                    )
                    pr_b = bool(r_db["printed"])
                    codes_tbl.setItem(
                        ri, 4,
                        cell("Да" if pr_b else "Нет", "#38bdf8" if pr_b else "#94a3b8")
                    )
                    code_clean = str(r_db["code"] or "").replace("\x1d", "[GS]")
                    it_code = cell(code_clean[:58] + ("…" if len(code_clean) > 58 else ""), mono=True)
                    it_code.setData(Qt.UserRole, r_db["code"])
                    it_code.setToolTip(code_clean)
                    codes_tbl.setItem(ri, 5, it_code)

        def _print_selected_code():
            cr = codes_tbl.currentRow()
            if cr < 0:
                self.toast.show_message("Выберите строку кода для печати", "warn")
                return
            it_c = codes_tbl.item(cr, 5)
            raw_code = it_c.data(Qt.UserRole) if it_c else ""
            if not raw_code:
                return
            cfg = dict(load_config() or {})
            cfg["_verified_cz_code"] = True
            res = print_code(raw_code, cfg)
            if isinstance(res, dict) and res.get("ok"):
                self.store.mark_printed(raw_code, point=self.store.current_point)
                _load_codes()
                self._refresh_pool()
                self.toast.show_message("Код отправлен на печать", "ok")
            else:
                info = res.get("info", "Ошибка") if isinstance(res, dict) else str(res)
                QMessageBox.warning(dlg, "Ошибка печати", str(info))

        search_ed.textChanged.connect(lambda _t: _load_codes())
        status_cmb.currentIndexChanged.connect(lambda _i: _load_codes())
        btn_print_sel.clicked.connect(_print_selected_code)
        lay.addWidget(codes_tbl, stretch=1)
        _load_codes()
        dlg.exec()

    def _pool_reassign_point_for_gtin(self):
        """Переназначает точку для свободных кодов выбранного GTIN."""
        gtin = self._pool_selected_gtin()
        if not gtin:
            self.toast.show_message("Выберите GTIN в таблице пула", "warn")
            return

        pt_ids = [p["id"] for p in self.store.points] or ["Т-01"]
        target_pt, ok = QInputDialog.getItem(
            self,
            "Переназначить точку для кодов ЧЗ",
            f"Выберите точку, на которую перенести свободные коды GTIN {gtin}:",
            pt_ids,
            0,
            False
        )
        if not ok or not target_pt:
            return

        try:
            import db
            with db.get_conn() as conn:
                cur = conn.execute(
                    "UPDATE pool_codes SET assigned_point = ? WHERE gtin = ? AND used = 0",
                    (target_pt, gtin)
                )
                updated = cur.rowcount
                conn.commit()
            self._refresh_pool()
            self.toast.show_message(f"Перенесено {updated} свободных кодов GTIN {gtin} на точку {target_pt}", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка переноса: {e}", "err")

    def _pool_delete_free_for_gtin(self):
        """Удаляет ошибочно загруженные свободные коды (used=0) для выбранного GTIN."""
        gtin = self._pool_selected_gtin()
        if not gtin:
            self.toast.show_message("Выберите GTIN в таблице пула для удаления свободных кодов", "warn")
            return

        ans = QMessageBox.question(
            self,
            "Удаление свободных кодов ЧЗ",
            f"Удалить все неиспользованные (свободные) коды для GTIN {gtin}?\n"
            f"Уже напечатанные/использованные коды затронуты не будут.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ans != QMessageBox.Yes:
            return

        try:
            import db
            with db.get_conn() as conn:
                cur = conn.execute("DELETE FROM pool_codes WHERE gtin = ? AND used = 0", (gtin,))
                deleted = cur.rowcount
                conn.commit()
            self._refresh_pool()
            self.toast.show_message(f"Удалено {deleted} свободных кодов для GTIN {gtin}", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка удаления: {e}", "err")

    def _pool_stats_by_point(self, point: str) -> list:
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
        from import_codes_window import ImportCodesWindow

        win = ImportCodesWindow(self.store, self)
        result = win.exec()
        if result == ImportCodesWindow.Accepted:
            self._refresh_pool()
            self._refresh_imports()

    # ==================================================================
    # IMPORTS (история импортов + откат ошибочной партии)
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

        btn_rollback = QPushButton("🗑 Удалить свободные коды выбранной партии")
        btn_rollback.clicked.connect(self._imports_delete_selected_batch)
        head.addWidget(btn_rollback)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_imports)
        head.addWidget(btn_refresh)

        v.addLayout(head)

        self.imports_table = table(
            ["Заказ / Партия", "Файл", "Точка", "Кодов", "Кто", "Когда"]
        )
        v.addWidget(self.imports_table, stretch=1)
        return w

    def _imports_delete_selected_batch(self):
        """Удаляет свободные (неиспользованные) коды выбранной партии импорта."""
        row = self.imports_table.currentRow()
        if row < 0:
            self.toast.show_message("Выберите партию импорта в таблице", "warn")
            return
        batch_item = self.imports_table.item(row, 0)
        file_item = self.imports_table.item(row, 1)
        batch_name = batch_item.text().strip() if batch_item else ""
        file_name = file_item.text().strip() if file_item else ""
        if not batch_name and not file_name:
            return

        ans = QMessageBox.question(
            self,
            "Откат партии импорта",
            f"Удалить все неиспользованные коды из партии «{batch_name or file_name}»?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ans != QMessageBox.Yes:
            return

        try:
            import db
            with db.get_conn() as conn:
                cur = conn.execute(
                    "DELETE FROM pool_codes WHERE used = 0 AND (import_batch = ? OR import_batch = ?)",
                    (batch_name, file_name)
                )
                deleted = cur.rowcount
                conn.commit()
            self._refresh_imports()
            self._refresh_pool()
            self.toast.show_message(f"Удалено {deleted} свободных кодов партии «{batch_name or file_name}»", "ok")
        except Exception as e:
            self.toast.show_message(f"Ошибка удаления партии: {e}", "err")

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
    # SYNC (MS SQL Server <-> Мастер-ПК [связующее звено] <-> Обычный ПК)
    # ==================================================================
    def _page_sync(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        head = QHBoxLayout()
        tt = QLabel("Синхронизация: MS SQL Сервер ↔ Мастер-ПК (шлюз) ↔ Рабочие ПК")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        head.addWidget(tt)
        head.addStretch(1)

        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_sync)
        head.addWidget(btn_refresh)
        v.addLayout(head)

        sub = QLabel(
            "Все коды (ШК и ДМ) хранятся на MS SQL сервере. Мастер-ПК выступает связующим звеном (шлюзом) "
            "между сервером и обычными ПК: обычный ПК нажимает кнопку запроса, выбирает нужный именованный "
            "JSON-файл партии, и этот файл вытягивается с сервера через Мастер-ПК на обычный ПК."
        )
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        sub.setWordWrap(True)
        v.addWidget(sub)

        # --- Карточка 1: Роль этого компьютера (Мастер-ПК или Обычный ПК) ---
        role_card = QFrame(); role_card.setObjectName("card")
        rc = QHBoxLayout(role_card)
        rc.setContentsMargins(16, 12, 16, 12)
        rc.setSpacing(10)

        rc.addWidget(QLabel("Роль этого ПК:"))
        self.sync_role_combo = QComboBox()
        self.sync_role_combo.addItem("👑 Мастер-ПК (Связующее звено между MS SQL сервером и обычными ПК)", "master")
        self.sync_role_combo.addItem("💻 Обычный ПК (Рабочий пост · вытягивает коды с сервера через Мастер-ПК)", "worker")
        self.sync_role_combo.setMinimumWidth(420)
        rc.addWidget(self.sync_role_combo)

        rc.addWidget(QLabel("Имя ПК:"))
        self.sync_pc_name_edit = QLineEdit()
        self.sync_pc_name_edit.setPlaceholderText("Например: POST-01 или MASTER-PC")
        self.sync_pc_name_edit.setFixedWidth(150)
        rc.addWidget(self.sync_pc_name_edit)

        rc.addWidget(QLabel("Целевая точка:"))
        self.sync_target_point_combo = QComboBox()
        self.sync_target_point_combo.setMinimumWidth(140)
        rc.addWidget(self.sync_target_point_combo)

        btn_save_role = QPushButton("💾 Применить роль")
        btn_save_role.setProperty("primary", True)
        btn_save_role.clicked.connect(self._sync_save_pc_role)
        rc.addWidget(btn_save_role)
        rc.addStretch(1)
        v.addWidget(role_card)

        # --- Карточка 2: Транзитный канал связи через Мастер-ПК ---
        folder_card = QFrame(); folder_card.setObjectName("card")
        fc = QHBoxLayout(folder_card)
        fc.setContentsMargins(16, 10, 16, 10)
        fc.setSpacing(8)

        fc.addWidget(QLabel("Транзитный канал Мастер-ПК (мост к серверу):"))
        self.sync_folder_edit = QLineEdit()
        self.sync_folder_edit.setPlaceholderText(
            r"например, \MASTER-PC\DotmarkSync или локальный путь транзита"
        )
        fc.addWidget(self.sync_folder_edit, stretch=1)

        btn_browse = QPushButton("📁 Выбрать")
        btn_browse.clicked.connect(self._sync_choose_folder)
        fc.addWidget(btn_browse)

        btn_save_folder = QPushButton("💾 Сохранить")
        btn_save_folder.clicked.connect(self._sync_save_folder)
        fc.addWidget(btn_save_folder)

        btn_open_folder = QPushButton("↗ Открыть")
        btn_open_folder.clicked.connect(self._sync_open_folder)
        fc.addWidget(btn_open_folder)
        v.addWidget(folder_card)

        # --- Карточка 3: Операции Обычного ПК и шлюза Мастер-ПК ---
        ops_card = QFrame(); ops_card.setObjectName("card")
        ops_layout = QVBoxLayout(ops_card)
        ops_layout.setContentsMargins(16, 12, 16, 12)
        ops_layout.setSpacing(10)

        # Главная кнопка для Обычного ПК (и старшего смены)
        worker_row = QHBoxLayout()
        worker_row.setSpacing(10)

        self.btn_open_json_picker = QPushButton(
            "📥 Запросить коды с сервера (выбрать JSON-файл через Мастер-ПК)..."
        )
        self.btn_open_json_picker.setProperty("primary", True)
        self.btn_open_json_picker.setMinimumHeight(38)
        self.btn_open_json_picker.setStyleSheet(
            "QPushButton { background:#3b6ea5; color:#ffffff; font-weight:700; font-size:13px; "
            "padding:8px 18px; border-radius:8px; } "
            "QPushButton:hover { background:#2e5988; }"
        )
        self.btn_open_json_picker.clicked.connect(self._sync_open_json_package_dialog)
        worker_row.addWidget(self.btn_open_json_picker, stretch=2)

        self.btn_worker_send_report = QPushButton(
            "📤 Отправить напечатанные коды на сервер (через Мастер-ПК)"
        )
        self.btn_worker_send_report.setMinimumHeight(38)
        self.btn_worker_send_report.clicked.connect(self._sync_worker_send_report)
        worker_row.addWidget(self.btn_worker_send_report, stretch=1)
        ops_layout.addLayout(worker_row)

        # Панель шлюза Мастер-ПК
        self.master_ops_widget = QWidget()
        master_row = QHBoxLayout(self.master_ops_widget)
        master_row.setContentsMargins(0, 0, 0, 0)
        master_row.setSpacing(8)

        btn_pull_sql_pkgs = QPushButton("🗄 Запросить с MS SQL сервера список партий (обновить шлюз Мастер-ПК)")
        btn_pull_sql_pkgs.setProperty("primary", True)
        btn_pull_sql_pkgs.clicked.connect(self._sync_master_pull_sql_packages)
        master_row.addWidget(btn_pull_sql_pkgs)

        btn_import_results = QPushButton("⬆ Переслать отчёты точек через Мастер-ПК на MS SQL сервер")
        btn_import_results.clicked.connect(self._sync_import_results)
        master_row.addWidget(btn_import_results)

        btn_full = QPushButton("🔁 Полный цикл транзита (Сервер ↔ Мастер-ПК ↔ Точки)")
        btn_full.clicked.connect(self._sync_full_cycle)
        master_row.addWidget(btn_full)
        master_row.addStretch(1)

        ops_layout.addWidget(self.master_ops_widget)
        v.addWidget(ops_card)

        # --- Таблица точек ---
        self.sync_table = table([
            "Точка", "Название", "Последний запрос с сервера",
            "Последний отчёт от точки", "Переслано на сервер"
        ])
        self.sync_table.setMinimumHeight(180)
        v.addWidget(self.sync_table, stretch=1)

        # --- Лог ---
        log_lbl = QLabel("ЖУРНАЛ ТРАНЗИТА (MS SQL СЕРВЕР ↔ МАСТЕР-ПК ↔ ОБЫЧНЫЙ ПК)")
        log_lbl.setStyleSheet(
            "color:#8b97a6; font-size:11px; font-weight:600; "
            "letter-spacing:1.5px;"
        )
        v.addWidget(log_lbl)

        self.sync_log = QPlainTextEdit()
        self.sync_log.setReadOnly(True)
        self.sync_log.setMinimumHeight(130)
        self.sync_log.setStyleSheet(
            "background:#0d1117; color:#c9d1d9; "
            "font-family:Consolas,monospace; font-size:12px; "
            "border:1px solid #2a3542; border-radius:8px; padding:8px;"
        )
        v.addWidget(self.sync_log)

        return w

    def _sync_log_write(self, text: str):
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self.sync_log.appendPlainText(f"[{stamp}] {text}")

    def _sync_save_pc_role(self):
        role = self.sync_role_combo.currentData() or "master"
        pc_name = self.sync_pc_name_edit.text().strip()
        target_pt = self.sync_target_point_combo.currentData() or "T-01"
        sync_mod.set_pc_role(self.store, role, pc_name)
        try:
            self.store.set_setting("worker_point_id", target_pt)
        except Exception:
            pass
        self._apply_pc_role_ui()
        self._refresh_sync()
        role_title = "👑 Мастер-ПК (шлюз к серверу)" if role == "master" else "💻 Обычный ПК (Рабочий пост)"
        self._sync_log_write(f"Установлена роль: {role_title} ({sync_mod.get_pc_node_name(self.store)}), точка: {target_pt}")
        self.toast.show_message(f"Режим сохранён: {role_title}", "ok")

    def _refresh_sync(self):
        try:
            folder = sync_mod.get_sync_folder(self.store)
        except Exception:
            folder = sync_mod._default_folder()
        self.sync_folder_edit.setText(str(folder))

        role = sync_mod.get_pc_role(self.store)
        pc_name = sync_mod.get_pc_node_name(self.store)
        if hasattr(self, "sync_role_combo"):
            idx = self.sync_role_combo.findData(role)
            if idx >= 0:
                self.sync_role_combo.setCurrentIndex(idx)
        if hasattr(self, "sync_pc_name_edit"):
            self.sync_pc_name_edit.setText(pc_name)
        if hasattr(self, "master_ops_widget"):
            self.master_ops_widget.setVisible(role == "master")

        if hasattr(self, "sync_target_point_combo"):
            saved_pt = ""
            try:
                saved_pt = self.store.get_setting("worker_point_id") or ""
            except Exception:
                pass
            self.sync_target_point_combo.blockSignals(True)
            self.sync_target_point_combo.clear()
            pts = getattr(self.store, "points", []) or []
            for p in pts:
                pid = p.get("id", "T-01")
                pname = p.get("name", pid)
                self.sync_target_point_combo.addItem(f"{pid} — {pname}", pid)
            if self.sync_target_point_combo.count() == 0:
                self.sync_target_point_combo.addItem("T-01 — Основной пост", "T-01")
            if saved_pt:
                pidx = self.sync_target_point_combo.findData(saved_pt)
                if pidx >= 0:
                    self.sync_target_point_combo.setCurrentIndex(pidx)
            self.sync_target_point_combo.blockSignals(False)

        t = self.sync_table
        t.setRowCount(0)
        try:
            rows = self.store.sync_status()
        except Exception as e:
            log.error("sync_status: %s", e)
            rows = []

        for r in rows:
            i = t.rowCount(); t.insertRow(i)
            t.setItem(i, 0, cell(r["point"], mono=True))
            t.setItem(i, 1, cell(r["name"]))
            t.setItem(i, 2, cell(r["last_export"], mono=True))
            t.setItem(i, 3, cell(r["last_report"], mono=True))
            t.setItem(i, 4, cell(r["last_import"], mono=True))

    def _sync_choose_folder(self):
        from PySide6.QtWidgets import QFileDialog
        current = self.sync_folder_edit.text().strip() or str(
            sync_mod._default_folder()
        )
        path = QFileDialog.getExistingDirectory(
            self, "Выберите транзитную папку шлюза Мастер-ПК", current
        )
        if path:
            self.sync_folder_edit.setText(path)

    def _sync_save_folder(self):
        path = self.sync_folder_edit.text().strip()
        if not path:
            self.toast.show_message("Укажите путь к транзитной папке", "warn")
            return
        try:
            from pathlib import Path as _Path
            folder = _Path(path)
            sync_mod.ensure_layout(folder)
        except Exception as e:
            self.toast.show_message(f"Не удалось создать папки: {e}", "err")
            return
        self.store.set_setting("sync_folder", path)
        self.toast.show_message("Транзитный канал Мастер-ПК сохранён", "ok")
        self._sync_log_write(f"Транзитный канал Мастер-ПК: {path}")
        self._refresh_sync()

    def _sync_open_folder(self):
        import os
        from pathlib import Path as _Path
        folder = _Path(
            self.sync_folder_edit.text().strip() or sync_mod._default_folder()
        )
        try:
            sync_mod.ensure_layout(folder)
            os.startfile(str(folder))
        except Exception as e:
            self.toast.show_message(f"Не удалось открыть: {e}", "err")

    def _sync_export_all(self):
        try:
            r = sync_mod.export_for_all_points(self.store)
        except Exception as e:
            log.error("export_for_all_points: %s", e)
            self._sync_log_write(f"ОШИБКА: {e}")
            self.toast.show_message(f"Ошибка: {e}", "err")
            return
        self._sync_log_write(r["info"])
        self.toast.show_message(r["info"].splitlines()[0], "ok")
        self._refresh_sync()

    def _sync_import_results(self):
        try:
            r = sync_mod.import_results_at_master(self.store)
        except Exception as e:
            log.error("import_results_at_master: %s", e)
            self._sync_log_write(f"ОШИБКА: {e}")
            self.toast.show_message(f"Ошибка: {e}", "err")
            return
        self._sync_log_write(r["info"])
        self.toast.show_message(r["info"].splitlines()[0], "ok")
        self._refresh_sync()

    def _sync_full_cycle(self):
        self._sync_log_write("── Полный цикл транзита (MS SQL Сервер ↔ Мастер-ПК ↔ Обычные ПК) ──")
        self._sync_export_all()
        self._sync_import_results()
        self._refresh_sync()

    def _sync_master_pull_sql_packages(self):
        """Шлюз Мастер-ПК: запросить актуальные партии с MS SQL Server и подготовить транзитные JSON-файлы (без сохранения в БД Мастер-ПК)."""
        try:
            res = sync_mod.pull_packages_from_mssql_via_master(
                self.store,
                save_to_master_db=False,
            )
        except Exception as e:
            self._sync_log_write(f"ОШИБКА связи с MS SQL: {e}")
            self.toast.show_message(f"Ошибка связи с сервером: {e}", "err")
            return

        self._sync_log_write(res.get("info", ""))
        if res.get("ok"):
            self.toast.show_message(
                f"На сервере доступно JSON-пакетов: {len(res.get('packages', []))} ({res.get('total_codes', 0)} кодов)",
                "ok"
            )
            self._refresh_sync()
        else:
            self.toast.show_message(res.get("info", "Не удалось получить список пакетов с сервера"), "warn")

    def _sync_master_create_named_package(self):
        """Совместимость: обновляет витрину пакетов с MS SQL сервера через Мастер-ПК."""
        self._sync_master_pull_sql_packages()

    def _sync_worker_send_report(self):
        """Обычный ПК: отправить отчёт о напечатанных кодах через Мастер-ПК на MS SQL сервер."""
        point_id = "T-01"
        if hasattr(self, "sync_target_point_combo") and self.sync_target_point_combo.currentData():
            point_id = self.sync_target_point_combo.currentData()
        try:
            res = sync_mod.export_results_from_point(self.store, point_id)
            self._sync_log_write(res.get("info", ""))
            self.toast.show_message(res.get("info", "Отчёт отправлен на сервер"), "ok" if res.get("ok") else "warn")
            self._refresh_sync()
        except Exception as e:
            self._sync_log_write(f"ОШИБКА отправки отчёта на сервер: {e}")
            self.toast.show_message(f"Ошибка: {e}", "err")

    def _sync_open_json_package_dialog(self):
        """
        Открывает окно выбора JSON-файла с кодами, хранящимися на MS SQL сервере.
        При открытии окна автоматически идёт запрос к MS SQL серверу через Мастер-ПК,
        раскрывается список понятных старшему смены JSON-файлов, и выбранный файл
        вытягивается с сервера через Мастер-ПК на обычный ПК.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("Запрос кодов с MS SQL сервера (через связующее звено Мастер-ПК)")
        dlg.resize(980, 600)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        hdr = QLabel("Выберите JSON-файл с кодами на сервере для отправки через Мастер-ПК на этот компьютер")
        hdr.setStyleSheet("font-size:16px; font-weight:700;")
        lay.addWidget(hdr)

        sub = QLabel(
            "Все коды хранятся на MS SQL сервере. В списке ниже показаны доступные на сервере JSON-файлы "
            "с понятными для старшего смены названиями (Заказ / Товар / ШК / Количество ДМ). "
            "Выберите нужный файл — он будет вытянут с сервера и отправлен через Мастер-ПК на этот ПК."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#8b97a6; font-size:12px;")
        lay.addWidget(sub)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        top_bar.addWidget(QLabel("Поиск по заказу / ШК:"))
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Введите название заказа, товар или ШК (например: 4607001234567)...")
        top_bar.addWidget(search_edit, stretch=1)

        btn_refresh_list = QPushButton("🔄 Обновить список с сервера (через Мастер-ПК)")
        top_bar.addWidget(btn_refresh_list)
        lay.addLayout(top_bar)

        pkg_table = table([
            "Имя JSON-файла на сервере",
            "Партия / Заказ",
            "ШК товара",
            "GTIN ЧЗ",
            "Кодов ДМ",
            "Обновлён с сервера",
            "Статус передачи через Мастер-ПК"
        ])
        pkg_table.setSelectionMode(QAbstractItemView.SingleSelection)
        pkg_table.setMinimumHeight(250)
        lay.addWidget(pkg_table, stretch=1)

        preview_lbl = QLabel("СОДЕРЖИМОЕ ВЫБРАННОГО JSON-ФАЙЛА НА СЕРВЕРЕ (ШК → ДМ):")
        preview_lbl.setStyleSheet("color:#8b97a6; font-size:11px; font-weight:600; letter-spacing:1.2px;")
        lay.addWidget(preview_lbl)

        preview_box = QPlainTextEdit()
        preview_box.setReadOnly(True)
        preview_box.setMaximumHeight(120)
        preview_box.setStyleSheet(
            "background:#0d1117; color:#93c5fd; font-family:Consolas,monospace; "
            "font-size:11px; border:1px solid #2a3542; border-radius:6px; padding:6px;"
        )
        lay.addWidget(preview_box)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        lbl_point = QLabel("Целевая точка этого ПК:")
        bottom_bar.addWidget(lbl_point)

        point_combo = QComboBox()
        pts = getattr(self.store, "points", []) or []
        for p in pts:
            pid = p.get("id", "T-01")
            point_combo.addItem(f"{pid} — {p.get('name', pid)}", pid)
        if point_combo.count() == 0:
            point_combo.addItem("T-01 — Основной пост", "T-01")
        if hasattr(self, "sync_target_point_combo") and self.sync_target_point_combo.currentIndex() >= 0:
            point_combo.setCurrentIndex(self.sync_target_point_combo.currentIndex())
        bottom_bar.addWidget(point_combo)

        bottom_bar.addStretch(1)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(dlg.reject)
        bottom_bar.addWidget(btn_cancel)

        btn_claim = QPushButton("📥 Вытянуть с сервера (через Мастер-ПК) на этот ПК")
        btn_claim.setProperty("primary", True)
        btn_claim.setMinimumHeight(36)
        btn_claim.setStyleSheet(
            "QPushButton { background:#2e8b69; color:#ffffff; font-weight:700; "
            "padding:8px 18px; border-radius:8px; } "
            "QPushButton:hover { background:#246e53; }"
        )
        bottom_bar.addWidget(btn_claim)
        lay.addLayout(bottom_bar)

        state = {"packages": [], "queried_once": False}

        def _populate_table(force_server_query: bool = False):
            do_query = force_server_query or (not state["queried_once"])
            state["queried_once"] = True
            q = search_edit.text().strip().lower()
            pkgs = sync_mod.list_available_json_packages(
                self.store,
                include_outbox=True,
                auto_query_server=do_query,
            )
            if q:
                pkgs = [
                    p for p in pkgs
                    if q in p["filename"].lower()
                    or q in p["batch_title"].lower()
                    or q in p["shk"].lower()
                    or q in p["gtin"].lower()
                ]
            state["packages"] = pkgs
            pkg_table.setRowCount(0)
            for p in pkgs:
                r = pkg_table.rowCount()
                pkg_table.insertRow(r)
                pkg_table.setItem(r, 0, cell(p["filename"], mono=True))
                pkg_table.setItem(r, 1, cell(p["batch_title"]))
                pkg_table.setItem(r, 2, cell(p["shk"], "#38bdf8", mono=True))
                pkg_table.setItem(r, 3, cell(p["gtin"], mono=True))
                pkg_table.setItem(r, 4, cell(f"{p['total_codes']} шт.", "#34d399", mono=True))
                pkg_table.setItem(r, 5, cell(str(p["created_at"])[:19], mono=True))
                if p.get("claimed_by"):
                    st_txt = f"Передан через Мастер-ПК → {p['claimed_by']} ({str(p.get('claimed_at', ''))[:16]})"
                    st_col = "#f59e0b"
                else:
                    st_txt = "На сервере (готов к вытягиванию через Мастер-ПК)"
                    st_col = "#34d399"
                pkg_table.setItem(r, 6, cell(st_txt, st_col))
            if pkg_table.rowCount() > 0:
                pkg_table.selectRow(0)
            else:
                preview_box.setPlainText(
                    "На MS SQL сервере не найдено доступных JSON-пакетов по заданному фильтру.\n"
                    "Проверьте настройки подключения к MS SQL Server на Мастер-ПК."
                )

        def _on_select():
            sel = pkg_table.selectionModel().selectedRows()
            if not sel:
                return
            idx = sel[0].row()
            if idx < 0 or idx >= len(state["packages"]):
                return
            pkg = state["packages"][idx]
            try:
                import json
                from pathlib import Path as _Path
                data = json.loads(_Path(pkg["path"]).read_text(encoding="utf-8"))
                codes = data.get("codes", [])
                lines = [
                    f"Файл на сервере: {pkg['filename']}  |  Партия: {pkg['batch_title']}  |  "
                    f"ШК: {pkg['shk']}  |  Всего кодов ДМ: {len(codes)}"
                ]
                for i, c in enumerate(codes[:5], 1):
                    lines.append(
                        f"  {i}. ШК={c.get('shk') or pkg['shk']} -> "
                        f"GTIN={c.get('gtin')} | SN={c.get('sn')} | ДМ={c.get('code')}"
                    )
                if len(codes) > 5:
                    lines.append(f"  ... и ещё {len(codes) - 5} кодов DataMatrix в этом файле.")
                preview_box.setPlainText("\n".join(lines))
            except Exception as e:
                preview_box.setPlainText(f"Ошибка чтения пакета: {e}")

        def _on_claim():
            sel = pkg_table.selectionModel().selectedRows()
            if not sel:
                QMessageBox.warning(dlg, "Выбор файла", "Выберите JSON-файл в таблице.")
                return
            idx = sel[0].row()
            if idx < 0 or idx >= len(state["packages"]):
                return
            pkg = state["packages"][idx]
            target_pt = point_combo.currentData() or "T-01"
            from pathlib import Path as _Path
            res = sync_mod.claim_and_import_json_package(
                self.store,
                package_path=_Path(pkg["path"]),
                point_id=target_pt,
            )
            self._sync_log_write(res.get("info", ""))
            if res.get("ok"):
                self.toast.show_message(
                    f"С сервера через Мастер-ПК получено {res.get('added', 0)} кодов ДМ ({pkg['filename']})",
                    "ok"
                )
                self._refresh_pool()
                self._refresh_sync()
                if hasattr(self, "_tpl_populate_shk_combo"):
                    self._tpl_populate_shk_combo()
                dlg.accept()
            else:
                QMessageBox.critical(dlg, "Ошибка вытягивания пакета", res.get("info", "Ошибка"))

        pkg_table.itemSelectionChanged.connect(_on_select)
        pkg_table.doubleClicked.connect(lambda _: _on_claim())
        search_edit.textChanged.connect(lambda _: _populate_table(force_server_query=False))
        btn_refresh_list.clicked.connect(lambda: _populate_table(force_server_query=True))
        btn_claim.clicked.connect(_on_claim)

        _populate_table(force_server_query=True)
        dlg.exec()

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

        self.disc_table = table([
            "GTIN", "Точка", "Кодов", "Сканов",
            "Использовано", "Свободно", "Статус"
        ])
        v.addWidget(self.disc_table, stretch=1)

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

            t.setItem(i, 2, cell(r["total"], mono=True))

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

        self._refresh_unprinted_codes(point)


    def _count_unprinted_by_gtin(self, point: str = None) -> dict:
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
        from PySide6.QtWidgets import QFileDialog
        import csv
        from datetime import datetime

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт ненапечатанных",
            f"dotmark_unprinted_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv",
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
            f"dotmark_discrepancies_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv",
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
        point_cfg = dict(get_printer_for_point(cfg, point) or cfg)
        point_cfg["_is_test_print"] = True

        test_gtin = "04607000000000"
        try:
            st_list = self.store.pool_stats()
            if st_list and st_list[0].get("gtin"):
                test_gtin = str(st_list[0]["gtin"]).zfill(14)[:14]
        except Exception:
            pass

        sample = "01" + test_gtin + "21" + "SN00000001" + "\x1d91TEST\x1d92SAMPLE01"
        try:
            result = print_code(sample, point_cfg)
        except Exception as e:
            result = {"ok": False, "info": f"{type(e).__name__}: {e}"}

        if result["ok"]:
            self.toast.show_message(f"Тест ({point}): {result['info']}", "ok")
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")

    # ==================================================================
    # BACKUP (Автобэкап раз в 2 недели / 14 дней + Автоудаление старше 6 месяцев / 180 дней)
    # ==================================================================
    def _page_backup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        tt = QLabel("Резервное копирование базы данных")
        tt.setStyleSheet("font-size:18px; font-weight:600;")
        v.addWidget(tt)

        sub = QLabel(
            f"Папка: {BACKUP_DIR}  ·  "
            f"Автоматическое создание бэкапа: раз в {AUTO_BACKUP_INTERVAL_DAYS} дней (2 недели)  ·  "
            f"Автоматическое удаление старых копий: старше {BACKUP_RETENTION_DAYS} дней (6 месяцев)"
        )
        sub.setStyleSheet("color:#34d399; font-size:12px; font-weight:500;")
        sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sub.setWordWrap(True)
        v.addWidget(sub)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        btn_create = QPushButton("💾 Создать бэкап сейчас")
        btn_create.setProperty("primary", True)
        btn_create.clicked.connect(self._backup_create)
        toolbar.addWidget(btn_create)

        btn_cleanup_6m = QPushButton(f"🧹 Удалить бэкапы старше 6 мес. ({BACKUP_RETENTION_DAYS} дн.)")
        btn_cleanup_6m.clicked.connect(self._backup_cleanup_old_files)
        toolbar.addWidget(btn_cleanup_6m)

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

        self.backup_table = QTableWidget(0, 5)
        self.backup_table.setHorizontalHeaderLabels(
            ["Имя файла", "Размер", "Дата создания", "Возраст", "Путь"]
        )
        header = self.backup_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
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
            age_days = f.get("age_days", 0)
            age_color = "#f59e0b" if age_days >= 150 else "#34d399"
            t.setItem(r, 0, cell(f["name"], mono=True))
            t.setItem(r, 1, cell(f"{f['size_kb']} КБ", mono=True))
            t.setItem(r, 2, cell(f["mtime"].strftime("%Y-%m-%d %H:%M:%S"), mono=True))
            t.setItem(r, 3, cell(f"{age_days} дн. (из {BACKUP_RETENTION_DAYS})", age_color, mono=True))
            t.setItem(r, 4, cell(f["path"], mono=True))

        self.backup_status.setText(
            f"Всего бэкапов: {len(files)}  ·  Автобэкап: каждые {AUTO_BACKUP_INTERVAL_DAYS} дн. (2 недели)  ·  "
            f"Автоудаление: старше {BACKUP_RETENTION_DAYS} дн. (6 месяцев)"
        )

        try:
            days = self.store.get_audit_retention_days()
            self.cleanup_days.setText(str(days))
        except Exception:
            self.cleanup_days.setText("180")

        last_cleanup = self.store.get_setting("last_cleanup")
        if last_cleanup:
            self.cleanup_status.setText(
                f"Последняя очистка: {last_cleanup}. "
                f"Записи старше {self.cleanup_days.text()} дней удаляются автоматически."
            )
        else:
            self.cleanup_status.setText(
                "Очистка ещё не выполнялась. "
                "Записи старше указанного срока удаляются автоматически."
            )

    def _backup_create(self):
        result = create_backup()
        if result["ok"]:
            self.toast.show_message(result["info"], "ok")
        else:
            self.toast.show_message(f"Ошибка: {result['info']}", "err")
        self._refresh_backup()

    def _backup_cleanup_old_files(self):
        res = cleanup_old_backups(BACKUP_RETENTION_DAYS)
        if res.get("deleted_count", 0) > 0:
            self.toast.show_message(res["info"], "ok")
        else:
            self.toast.show_message(
                f"Нет бэкапов старше {BACKUP_RETENTION_DAYS} дней (6 месяцев)", "ok"
            )
        self._refresh_backup()

    def _backup_restore(self):
        rows = self.backup_table.selectionModel().selectedRows()
        if not rows:
            self.toast.show_message("Выберите бэкап из списка", "warn")
            return

        idx = rows[0].row()
        name = self.backup_table.item(idx, 0).text()
        path_item = self.backup_table.item(idx, 4) or self.backup_table.item(idx, 3)
        path = path_item.text()

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

        default_name = f"dotmark_logs_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv"
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
        self.j_search.setPlaceholderText("Введите GTIN (14 цифр) или серийный номер (SN)…")
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
        default_name = f"dotmark_journal_{stamp}.csv"

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

        self.sec_title1 = QLabel("Пароль администратора")
        title1 = self.sec_title1
        title1.setStyleSheet("font-size:14px; font-weight:700;")
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

        self.sec_title2 = QLabel("Секретный вопрос (восстановление доступа)")
        title2 = self.sec_title2
        title2.setStyleSheet("font-size:14px; font-weight:700;")
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


    # ==================================================================
    # SQL SERVER (АСИНХРОННАЯ ИНТЕГРАЦИЯ БЕЗ ЗАВИСАНИЯ UI)
    # ==================================================================
    def _refresh_sql(self):
        """Обновление состояния страницы SQL Server при переключении на неё."""
        if hasattr(self, "sql_server_page"):
            try:
                queue_count = len(self.store.queue) if hasattr(self.store, "queue") else 0
                self.sql_server_page.lbl_queue_count.setText(f"{queue_count} в очереди")
            except Exception as e:
                log.error("Ошибка обновления страницы SQL Server: %s", e)

    def _handle_sql_sync(self, action_type: str):
        """
        Неблокирующая обработка команд синхронизации со страницы SQL Server в фоновом потоке:
        action_type: 'import', 'export_status', 'export_events', 'all'
        При импорте:
        1) Сохраняет полученные с MS SQL коды (ДМ + ШК) в локальную БД (pool_codes и gtin_map);
        2) Автоматически формирует понятные для старшего смены именованные JSON-файлы
           в папке Мастер-ПК (sync/master/packages/), чтобы обычные ПК могли их выбирать и скачивать.
        """
        import threading
        from PySide6.QtCore import QTimer
        from mssql import MSSQLManager, load_mssql_config

        cfg = load_mssql_config(self.store)
        self.sql_server_page.txt_log.append(f"[Обмен] Запуск фоновой операции: {action_type}...")

        def _worker():
            mgr = MSSQLManager(cfg)
            logs_out = []
            toasts_out = []
            try:
                if action_type in ("import", "all"):
                    mapping = cfg.get("mapping", {}).get("import", {})
                    table_name = mapping.get("table", "dbo.ChestnyZnakCodes")
                    codes = mgr.import_codes_from_sql(
                        mapping=mapping,
                        table=table_name,
                        limit=50000,
                        where_clause=mapping.get("where", "")
                    )
                    # Формируем транзитные именованные JSON-пакеты с MS SQL сервера через шлюз Мастер-ПК
                    # (Коды хранятся на сервере, а обычные ПК вытягивают нужный JSON-файл через Мастер-ПК)
                    pkg_res = sync_mod.create_named_json_packages_from_records(
                        records=codes,
                        store=self.store,
                        source_label="MS SQL Server (через Мастер-ПК)",
                    )
                    pkg_names = ", ".join(p["filename"] for p in pkg_res.get("packages", [])[:3])
                    logs_out.append(
                        f"[Шлюз MS SQL -> Мастер-ПК] На сервере подготовлено {len(pkg_res.get('packages', []))} "
                        f"JSON-пакетов ({len(codes)} кодов ДМ) для передачи на обычные ПК: {pkg_names}"
                    )
                    toasts_out.append((
                        f"Шлюз Мастер-ПК: подготовлено {len(pkg_res.get('packages', []))} JSON-пакетов с сервера ({len(codes)} кодов)",
                        "ok"
                    ))

                if action_type in ("export_status", "all"):
                    mapping = cfg.get("mapping", {}).get("status", {})
                    table_name = mapping.get("table", "dbo.ChestnyZnakCodes")
                    import db
                    with db.get_conn() as conn:
                        rows = conn.execute(
                            "SELECT code, gtin, sn, created_at FROM pool_codes WHERE printed = 1 OR used = 1"
                        ).fetchall()
                    used_codes = [
                        {
                            "code": r["code"],
                            "gtin": r["gtin"],
                            "sn": r["sn"],
                            "used_at": r["created_at"],
                            "operator": sync_mod.get_pc_node_name(self.store),
                        }
                        for r in rows
                    ]
                    res = mgr.export_code_statuses(mapping, table_name, used_codes)
                    logs_out.append(
                        f"[Статусы -> MS SQL] Выгружено напечатанных ДМ: {res.get('updated', 0)} (из {len(used_codes)})."
                    )
                    toasts_out.append((f"Статусы выгружены в MS SQL ({res.get('updated', 0)})", "ok"))

                if action_type in ("export_events", "all"):
                    mapping_ev = cfg.get("mapping", {}).get("events", {})
                    table_name_ev = mapping_ev.get("table", "dbo.MarkingEvents")
                    import db
                    events_to_export = []
                    with db.get_conn() as conn:
                        j_rows = conn.execute(
                            "SELECT id, action, details, point, operator, created_at FROM journal "
                            "ORDER BY id DESC LIMIT 500"
                        ).fetchall()
                    for jr in j_rows:
                        events_to_export.append({
                            "event_id": jr["id"],
                            "scanned_at": jr["created_at"],
                            "operator_name": jr["operator"] or sync_mod.get_pc_node_name(self.store),
                            "station_id": jr["point"] or "Т-01",
                            "barcode": jr["action"] or "",
                            "status": "SUCCESS",
                            "details": jr["details"] or "",
                        })
                    res_ev = mgr.export_marking_events(mapping_ev, table_name_ev, events_to_export)
                    logs_out.append(f"[События -> MS SQL] Журнал событий синхронизирован (добавлено в базу: {res_ev.get('inserted', 0)}).")
                    toasts_out.append((f"События выгружены в MS SQL ({res_ev.get('inserted', 0)})", "ok"))

            except Exception as e:
                log.error("Сбой обмена SQL Server: %s", e)
                logs_out.append(f"[ОШИБКА] Сбой операции: {e}")
                toasts_out.append((f"Ошибка обмена: {e}", "err"))

            def _apply_ui():
                for line_msg in logs_out:
                    self.sql_server_page.txt_log.append(line_msg)
                for t_msg, t_kind in toasts_out:
                    self.toast.show_message(t_msg, t_kind)
                self._refresh_pool()
                self._refresh_sync()
                if hasattr(self, "_tpl_populate_shk_combo"):
                    self._tpl_populate_shk_combo()

            QTimer.singleShot(0, _apply_ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _get_theme_patch_qss(self, theme_name: str) -> str:
        """Палитра приглушённой, спокойной светлой темы (Calm Nordic Sand / Stone) без резкой белизны."""
        is_lt = (theme_name == "light")
        if is_lt:
            return """
            /* Глобальный фон всех окон и контейнеров — спокойный приглушённый серо-бежевый камень */
            QMainWindow, QDialog, QStackedWidget {
                background-color: #e6eaef;
                color: #2d3748;
            }
            /* Все страницы и контейнеры внутри стека наследуют мягкий фон */
            QStackedWidget > QWidget {
                background-color: #e6eaef;
                color: #2d3748;
            }
            /* Левое навигационное меню (Сайдбар) */
            QFrame#sidebar {
                background-color: #dde3ea;
                border-right: 1px solid #cbd2dc;
            }
            QLabel#navSection {
                color: #64748b;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 10px 10px 4px 10px;
                background: transparent;
            }
            QPushButton#navBtn {
                background-color: transparent;
                color: #334155;
                border: none;
                border-radius: 8px;
                padding: 9px 12px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#navBtn:hover:!checked {
                background-color: #cfd7e2;
                color: #1e293b;
            }
            QPushButton#navBtn:checked {
                background-color: #3b6ea5;
                color: #f8fafc;
                font-weight: 700;
            }
            /* Карточки — тёплый матовый бумажный оттенок #f3f5f8 (нигде нет чистого #ffffff!) */
            QFrame#card {
                background-color: #f3f5f8;
                border: 1px solid #cfd6e0;
                border-radius: 12px;
            }
            /* Поля ввода, спинбоксы и комбобоксы */
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #ebeff4;
                color: #2d3748;
                border: 1px solid #c4cdd8;
                border-radius: 7px;
                padding: 5px 10px;
                selection-background-color: #3b6ea5;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                background-color: #f5f7fa;
                border: 1px solid #3b6ea5;
            }
            QComboBox QAbstractItemView {
                background-color: #f3f5f8;
                color: #2d3748;
                border: 1px solid #c4cdd8;
                border-radius: 6px;
                padding: 4px;
                selection-background-color: #d6e4f0;
                selection-color: #1e3a5f;
                outline: none;
            }
            /* Кнопки (вторичные — спокойные матовые, основные — благородный джинсовый синий) */
            QPushButton {
                background-color: #ebeff4;
                color: #2d3748;
                border: 1px solid #c4cdd8;
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #dfe5ec;
                border-color: #a8b4c4;
                color: #1e293b;
            }
            QPushButton:pressed {
                background-color: #d4dce6;
            }
            QPushButton[primary="true"] {
                background-color: #3b6ea5;
                color: #f8fafc;
                border: 1px solid #2e5988;
                font-weight: 700;
            }
            QPushButton[primary="true"]:hover {
                background-color: #2e5988;
                border-color: #23476e;
            }
            /* Вкладки QTabWidget */
            QTabWidget::pane {
                border: 1px solid #cfd6e0;
                border-radius: 10px;
                background-color: #f3f5f8;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #dde3ea;
                color: #4a5568;
                border: 1px solid #c4cdd8;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 7px 16px;
                margin-right: 4px;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #e6eaef;
                color: #2d3748;
            }
            QTabBar::tab:selected {
                background-color: #3b6ea5;
                color: #f8fafc;
                border-color: #3b6ea5;
                font-weight: 700;
            }
            /* Таблицы QTableWidget — приглушённый бумажный фон #f3f5f8 без слепящих пятен */
            QTableWidget {
                background-color: #f3f5f8;
                alternate-background-color: #ebeff4;
                color: #2d3748;
                border: 1px solid #cfd6e0;
                border-radius: 10px;
                gridline-color: #e2e7ee;
                selection-background-color: #d6e4f0;
                selection-color: #1e3a5f;
                outline: none;
            }
            QTableWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #e6eaef;
                color: #2d3748;
            }
            QTableWidget::item:selected {
                background-color: #d6e4f0;
                color: #1e3a5f;
                font-weight: 600;
            }
            QHeaderView::section:horizontal {
                background-color: #e4e9f0;
                color: #4a5568;
                font-size: 11px;
                font-weight: 700;
                padding: 8px 10px;
                border: none;
                border-bottom: 1px solid #cbd2dc;
                border-right: 1px solid #d8dfe8;
            }
            QTableWidget QTableCornerButton::section,
            QHeaderView::section:vertical {
                background-color: #e4e9f0;
                color: #64748b;
                border: none;
                border-right: 1px solid #cbd2dc;
                border-bottom: 1px solid #d8dfe8;
            }
            /* Группы QGroupBox */
            QGroupBox {
                background-color: #f3f5f8;
                border: 1px solid #cfd6e0;
                border-radius: 10px;
                margin-top: 12px;
                padding: 14px;
                color: #2d3748;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                color: #4a5568;
                font-weight: 700;
            }
            /* Чекбоксы */
            QCheckBox {
                color: #2d3748;
                spacing: 6px;
            }
            /* Скроллбары */
            QScrollBar:vertical {
                background: #e6eaef;
                width: 8px;
                margin: 2px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #b8c2cf;
                min-height: 24px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            /* Контекстные меню */
            QMenu {
                background-color: #f3f5f8;
                color: #2d3748;
                border: 1px solid #c4cdd8;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background-color: #3b6ea5;
                color: #ffffff;
            }
            """
        else:
            return """
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                selection-background-color: #3b6ea5;
                selection-color: #ffffff;
                outline: none;
            }
            QTableWidget QTableCornerButton::section,
            QHeaderView::section:vertical {
                background-color: #161c24;
                color: #8b97a6;
                border: none;
                border-right: 1px solid #2a3542;
                border-bottom: 1px solid #2a3542;
            }
            QGroupBox {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 10px;
                margin-top: 10px;
                padding: 14px;
                color: #f8fafc;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                color: #94a3b8;
                font-weight: 700;
            }
            """

    def apply_theme(self, theme_name: Optional[str] = None, refresh_data: bool = True):
        """Быстрое переключение темы оформления без тройной перекомпиляции QSS и без пересборки скрытых вкладок."""
        from PySide6.QtWidgets import QApplication
        from styles import get_qss, set_current_theme, is_light_theme

        if theme_name:
            t_str = set_current_theme(theme_name)
            is_lt = (t_str == "light")
        else:
            is_lt = is_light_theme()
            t_str = "light" if is_lt else "dark"

        # 1. Единоразово обновляем глобальный QSS приложения (если тема изменилась)
        try:
            app = QApplication.instance()
            if app is not None and getattr(app, "_applied_theme_name", None) != t_str:
                app.setStyleSheet(get_qss(t_str))
                app._applied_theme_name = t_str
        except Exception:
            pass

        # 2. Обновляем деревья Точки и ПК (points_tree и devices_tree) и локальные компоненты
        if is_lt:
            tree_qss = """
                QTreeWidget {
                    background-color: #f3f5f8;
                    border: 1px solid #cfd6e0;
                    border-radius: 10px;
                    color: #1e293b;
                    outline: none;
                }
                QTreeWidget::item {
                    padding: 9px 6px;
                    border-bottom: 1px solid #e2e8f0;
                    min-height: 26px;
                    color: #1e293b;
                }
                QTreeWidget::item:selected {
                    background-color: #d6e4f0;
                    color: #1e3a5f;
                    font-weight: 600;
                }
                QTreeWidget::item:hover:!selected {
                    background-color: #ebeff4;
                }
                QTreeWidget::branch { background: transparent; }
                QHeaderView::section {
                    background-color: #e4e9f0;
                    color: #475569;
                    padding: 10px 10px;
                    border: none;
                    border-bottom: 1px solid #cbd2dc;
                    font-size: 11px;
                    font-weight: 700;
                    letter-spacing: .5px;
                }
            """
        else:
            tree_qss = """
                QTreeWidget {
                    background-color: #161c24;
                    border: 1px solid #2a3542;
                    border-radius: 10px;
                    color: #e6edf3;
                    outline: none;
                }
                QTreeWidget::item {
                    padding: 9px 6px;
                    border-bottom: 1px solid #2a3542;
                    min-height: 26px;
                    color: #e6edf3;
                }
                QTreeWidget::item:selected {
                    background-color: rgba(59,130,246,.22);
                    color: #ffffff;
                }
                QTreeWidget::item:hover:!selected {
                    background-color: rgba(255,255,255,.03);
                }
                QTreeWidget::branch { background: transparent; }
                QHeaderView::section {
                    background-color: #161c24;
                    color: #8b97a6;
                    padding: 10px 10px;
                    border: none;
                    border-bottom: 1px solid #2a3542;
                    font-size: 11px;
                    font-weight: 600;
                    letter-spacing: .5px;
                }
            """
        for tree in (getattr(self, "points_tree", None), getattr(self, "devices_tree", None)):
            if tree is not None:
                tree.setStyleSheet(tree_qss)

        if hasattr(self, "_apply_template_preview_theme"):
            self._apply_template_preview_theme(is_lt)
        if hasattr(self, "label_preview"):
            self.label_preview.update()

        if hasattr(self, "sync_log"):
            if is_lt:
                self.sync_log.setStyleSheet(
                    "background:#ebeff4; color:#2d3748; font-family:Consolas,monospace; font-size:12px; "
                    "border:1px solid #cfd6e0; border-radius:8px; padding:10px;"
                )
            else:
                self.sync_log.setStyleSheet(
                    "background:#0d1117; color:#c9d1d9; font-family:Consolas,monospace; font-size:12px; "
                    "border:1px solid #2a3542; border-radius:8px; padding:10px;"
                )

        sec_col = "#2d3748" if is_lt else "#e6edf3"
        for lbl_s in (getattr(self, "sec_title1", None), getattr(self, "sec_title2", None)):
            if lbl_s is not None:
                lbl_s.setStyleSheet(f"color:{sec_col}; font-size:14px; font-weight:700;")

        self._apply_pc_role_ui()
        if hasattr(self, "sql_server_page") and hasattr(self.sql_server_page, "apply_theme"):
            self.sql_server_page.apply_theme(t_str)

        # 3. Обновляем ТОЛЬКО текущую открытую вкладку (вместо всех 6 вкладок одновременно)
        if refresh_data:
            try:
                cur_idx = self.stack.currentIndex()
                idx_to_page = {
                    0: "dashboard", 1: "queue", 2: "points", 3: "devices",
                    4: "templates", 5: "pool", 7: "printer", 8: "journal",
                    9: "logs", 10: "backup", 11: "security", 12: "discrepancies",
                    13: "sync", 14: "sql"
                }
                cur_page = idx_to_page.get(cur_idx, "dashboard")
                self.refresh(cur_page)
            except Exception:
                pass

        # 4. Синхронно обновляем тему окна оператора
        try:
            win = self.window()
            if hasattr(win, "operator") and hasattr(win.operator, "apply_theme"):
                win.operator.apply_theme(t_str)
        except Exception:
            pass

    def _toggle_theme(self):
        """Мгновенное переключение светлой и тёмной темы оформления."""
        from styles import is_light_theme, set_current_theme
        new_theme = "dark" if is_light_theme() else "light"
        set_current_theme(new_theme)
        self.apply_theme(new_theme, refresh_data=True)
        theme_ru = "Светлая" if new_theme == "light" else "Тёмная"
        self.toast.show_message(f"Тема переключена: {theme_ru}", "ok")