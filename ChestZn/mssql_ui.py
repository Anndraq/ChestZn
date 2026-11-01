r"""
ChestZn — Пользовательский интерфейс настройки и управления интеграцией с MS SQL Server (mssql_ui.py).
Включает:
1. SQLServerConfigDialog — модальный диалог настройки реквизитов, сопоставления колонок (ДМ, ШК/GTIN, Серия, Заказ/Партия)
   и предпросмотра запросов (Dry Run).
2. SQLServerAdminPage — страница 'SQL Server' в админ-панели (выгрузка кодов ШК+ДМ с сервера, формирование
   именованных JSON-пакетов для обычных ПК и выгрузка отчётов о печати обратно в MS SQL Server).
"""

from typing import Dict, Any, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QTabWidget,
    QGroupBox, QTextEdit, QMessageBox, QFileDialog, QSpinBox
)

from mssql import MSSQLManager, load_mssql_config, save_mssql_config


class SQLServerConfigDialog(QDialog):
    """
    Модальное окно настройки подключения к MS SQL Server и сопоставления таблиц/колонок (ШК ↔ ДМ).
    """
    settings_saved = Signal(dict)

    def __init__(self, current_config: Optional[Dict[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка интеграции с Microsoft SQL Server")
        self.setMinimumSize(780, 620)
        self.config = current_config or load_mssql_config()
        self._cached_tables = []

        self._init_ui()
        self._load_config_to_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.tab_conn = QWidget()
        self._setup_connection_tab()
        self.tabs.addTab(self.tab_conn, "🔌 Реквизиты подключения")

        self.tab_mapping = QWidget()
        self._setup_mapping_tab()
        self.tabs.addTab(self.tab_mapping, "🗂 Сопоставление таблиц и полей (ШК ↔ ДМ)")

        self.tab_preview = QWidget()
        self._setup_preview_tab()
        self.tabs.addTab(self.tab_preview, "🔍 Предпросмотр SQL (Dry Run)")

        btn_layout = QHBoxLayout()

        self.btn_export_tpl = QPushButton("📄 Скачать шаблон структуры (JSON)")
        self.btn_export_tpl.clicked.connect(self._on_export_template)
        btn_layout.addWidget(self.btn_export_tpl)

        self.btn_import_tpl = QPushButton("📂 Загрузить шаблон структуры")
        self.btn_import_tpl.clicked.connect(self._on_import_template)
        btn_layout.addWidget(self.btn_import_tpl)

        btn_layout.addStretch()

        self.btn_save = QPushButton("💾 Сохранить настройки")
        self.btn_save.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;")
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        main_layout.addLayout(btn_layout)

    def _setup_connection_tab(self):
        layout = QVBoxLayout(self.tab_conn)

        mock_box = QGroupBox("Режим развертывания (Автономная проверка / Боевой сервер)")
        mock_layout = QVBoxLayout(mock_box)
        self.chk_mock_mode = QCheckBox(
            "Включить тестовый режим эмуляции MS SQL Server (Mock Mode — 3 готовые партии с парами ШК и ДМ)"
        )
        self.chk_mock_mode.setToolTip(
            "Позволяет дома проверить вытягивание кодов с сервера, создание JSON-пакетов и выбор их на обычном ПК без живого MS SQL"
        )
        self.chk_mock_mode.toggled.connect(self._on_mock_mode_toggled)
        mock_layout.addWidget(self.chk_mock_mode)
        layout.addWidget(mock_box)

        form_box = QGroupBox("Параметры сервера MS SQL")
        form_layout = QFormLayout(form_box)

        self.txt_server = QLineEdit()
        self.txt_server.setPlaceholderText("например: 192.168.1.100 или SRV-SQL\\SQLEXPRESS")
        form_layout.addRow("Сервер (Host / Instance):", self.txt_server)

        self.txt_database = QLineEdit()
        self.txt_database.setPlaceholderText("например: ChestnyZnakDB")
        form_layout.addRow("База данных:", self.txt_database)

        self.cmb_driver = QComboBox()
        drivers = MSSQLManager.get_available_drivers()
        self.cmb_driver.addItems(drivers if drivers else MSSQLManager.DEFAULT_DRIVERS)
        form_layout.addRow("Драйвер ODBC:", self.cmb_driver)

        self.cmb_auth = QComboBox()
        self.cmb_auth.addItem("Windows Authentication (Trusted_Connection)", "windows")
        self.cmb_auth.addItem("SQL Server Authentication (Логин и пароль)", "sql")
        self.cmb_auth.currentIndexChanged.connect(self._on_auth_type_changed)
        form_layout.addRow("Тип аутентификации:", self.cmb_auth)

        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("sa или имя пользователя SQL")
        form_layout.addRow("Логин SQL:", self.txt_user)

        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        self.txt_password.setPlaceholderText("Пароль (шифруется через Windows DPAPI)")
        form_layout.addRow("Пароль SQL:", self.txt_password)

        self.cmb_encrypt = QComboBox()
        self.cmb_encrypt.addItem("Отключено (Encrypt=no, TrustServerCertificate=yes) — рекомендуется в LAN", "no")
        self.cmb_encrypt.addItem("Опционально (Encrypt=yes, TrustServerCertificate=yes)", "optional")
        self.cmb_encrypt.addItem("Строгое шифрование (Encrypt=yes, TrustServerCertificate=no)", "strict")
        form_layout.addRow("Режим шифрования TLS:", self.cmb_encrypt)

        self.spn_timeout = QSpinBox()
        self.spn_timeout.setRange(2, 60)
        self.spn_timeout.setValue(10)
        self.spn_timeout.setSuffix(" сек.")
        form_layout.addRow("Таймаут подключения:", self.spn_timeout)

        layout.addWidget(form_box)

        test_box = QHBoxLayout()
        self.btn_test_conn = QPushButton("⚡ Проверить подключение")
        self.btn_test_conn.clicked.connect(self._on_test_connection)
        test_box.addWidget(self.btn_test_conn)

        self.lbl_test_result = QLabel("Статус подключения: не проверялся")
        self.lbl_test_result.setStyleSheet("color: #888;")
        test_box.addWidget(self.lbl_test_result)
        test_box.addStretch()

        layout.addLayout(test_box)
        layout.addStretch()

    def _setup_mapping_tab(self):
        layout = QVBoxLayout(self.tab_mapping)

        top_schema_layout = QHBoxLayout()
        self.btn_fetch_schema = QPushButton("🔄 Загрузить список таблиц с сервера")
        self.btn_fetch_schema.clicked.connect(self._on_fetch_schema_clicked)
        top_schema_layout.addWidget(self.btn_fetch_schema)

        self.lbl_schema_status = QLabel("Схема: таблицы не загружены")
        top_schema_layout.addWidget(self.lbl_schema_status)
        top_schema_layout.addStretch()
        layout.addLayout(top_schema_layout)

        mapping_tabs = QTabWidget()
        layout.addWidget(mapping_tabs)

        # 1. Импорт кодов (ДМ + ШК + Партия)
        tab_imp = QWidget()
        layout_imp = QFormLayout(tab_imp)
        self.cmb_imp_table = QComboBox()
        self.cmb_imp_table.setEditable(True)
        layout_imp.addRow("Таблица источников кодов на сервере:", self.cmb_imp_table)

        self.cmb_imp_code = QComboBox()
        self.cmb_imp_code.setEditable(True)
        layout_imp.addRow("Колонка DataMatrix / ДМ (обязательно):", self.cmb_imp_code)

        self.cmb_imp_gtin = QComboBox()
        self.cmb_imp_gtin.setEditable(True)
        layout_imp.addRow("Колонка ШК товара (EAN-13 / GTIN):", self.cmb_imp_gtin)

        self.cmb_imp_sn = QComboBox()
        self.cmb_imp_sn.setEditable(True)
        layout_imp.addRow("Колонка Серийного номера (SN):", self.cmb_imp_sn)

        self.cmb_imp_order = QComboBox()
        self.cmb_imp_order.setEditable(True)
        layout_imp.addRow("Колонка Заказа / Названия партии (для имени JSON):", self.cmb_imp_order)

        self.txt_imp_where = QLineEdit()
        self.txt_imp_where.setPlaceholderText("например: is_printed = 0")
        layout_imp.addRow("Условие отбора WHERE:", self.txt_imp_where)

        mapping_tabs.addTab(tab_imp, "1. Загрузка ШК и ДМ (SELECT)")

        # 2. Выгрузка статусов печати
        tab_status = QWidget()
        layout_status = QFormLayout(tab_status)
        self.cmb_stat_table = QComboBox()
        self.cmb_stat_table.setEditable(True)
        layout_status.addRow("Таблица обновления кодов:", self.cmb_stat_table)

        self.cmb_stat_code = QComboBox()
        self.cmb_stat_code.setEditable(True)
        layout_status.addRow("Ключевая колонка ДМ (WHERE):", self.cmb_stat_code)

        self.cmb_stat_printed = QComboBox()
        self.cmb_stat_printed.setEditable(True)
        layout_status.addRow("Колонка флага печати (is_printed):", self.cmb_stat_printed)

        self.cmb_stat_date = QComboBox()
        self.cmb_stat_date.setEditable(True)
        layout_status.addRow("Колонка даты печати (printed_at):", self.cmb_stat_date)

        self.cmb_stat_status = QComboBox()
        self.cmb_stat_status.setEditable(True)
        layout_status.addRow("Колонка статуса (status_code):", self.cmb_stat_status)

        mapping_tabs.addTab(tab_status, "2. Статус печати (UPDATE)")

        # 3. Выгрузка событий
        tab_ev = QWidget()
        layout_ev = QFormLayout(tab_ev)
        self.cmb_ev_table = QComboBox()
        self.cmb_ev_table.setEditable(True)
        layout_ev.addRow("Таблица журнала событий:", self.cmb_ev_table)

        self.txt_ev_id = QLineEdit("event_id")
        layout_ev.addRow("Колонка ID события:", self.txt_ev_id)

        self.txt_ev_time = QLineEdit("scanned_at")
        layout_ev.addRow("Колонка даты/времени:", self.txt_ev_time)

        self.txt_ev_op = QLineEdit("operator_name")
        layout_ev.addRow("Колонка поста / сканера:", self.txt_ev_op)

        self.txt_ev_station = QLineEdit("station_id")
        layout_ev.addRow("Колонка точки:", self.txt_ev_station)

        self.txt_ev_barcode = QLineEdit("barcode")
        layout_ev.addRow("Колонка штрихкода:", self.txt_ev_barcode)

        self.txt_ev_status = QLineEdit("status")
        layout_ev.addRow("Колонка результата (status):", self.txt_ev_status)

        mapping_tabs.addTab(tab_ev, "3. Журнал событий (INSERT)")

    def _setup_preview_tab(self):
        layout = QVBoxLayout(self.tab_preview)

        btn_gen = QPushButton("⚡ Сгенерировать SQL-запросы на основе текущих настроек")
        btn_gen.clicked.connect(self._generate_sql_preview)
        layout.addWidget(btn_gen)

        self.txt_preview_sql = QTextEdit()
        self.txt_preview_sql.setReadOnly(True)
        self.txt_preview_sql.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px; background-color: #1e1e1e; color: #d4d4d4;"
        )
        layout.addWidget(self.txt_preview_sql)

    def _on_mock_mode_toggled(self, checked: bool):
        if checked:
            self.txt_server.setText("MOCK")
            self.txt_server.setEnabled(False)
            self.cmb_auth.setEnabled(False)
            self.txt_user.setEnabled(False)
            self.txt_password.setEnabled(False)
        else:
            if self.txt_server.text() == "MOCK":
                self.txt_server.setText("")
            self.txt_server.setEnabled(True)
            self.cmb_auth.setEnabled(True)
            self._on_auth_type_changed()

    def _on_auth_type_changed(self):
        is_sql = (self.cmb_auth.currentData() == "sql") and not self.chk_mock_mode.isChecked()
        self.txt_user.setEnabled(is_sql)
        self.txt_password.setEnabled(is_sql)

    def _collect_config_from_ui(self) -> Dict[str, Any]:
        cfg = dict(self.config)
        cfg["mock_mode"] = self.chk_mock_mode.isChecked()
        cfg["server"] = "MOCK" if cfg["mock_mode"] else self.txt_server.text().strip()
        cfg["database"] = self.txt_database.text().strip() or "ChestnyZnakDB"
        cfg["driver"] = self.cmb_driver.currentText()
        cfg["auth_type"] = self.cmb_auth.currentData()
        cfg["user"] = self.txt_user.text().strip()
        if self.txt_password.text():
            cfg["password_encrypted"] = MSSQLManager._encrypt_password(self.txt_password.text())
        cfg["encryption"] = self.cmb_encrypt.currentData()
        cfg["timeout"] = self.spn_timeout.value()

        cfg["mapping"] = {
            "import": {
                "table": self.cmb_imp_table.currentText().strip() or "dbo.ChestnyZnakCodes",
                "code_col": self.cmb_imp_code.currentText().strip() or "mark_code",
                "gtin_col": self.cmb_imp_gtin.currentText().strip() or "gtin",
                "sn_col": self.cmb_imp_sn.currentText().strip() or "serial_number",
                "order_col": self.cmb_imp_order.currentText().strip() or "order_id",
                "where": self.txt_imp_where.text().strip(),
            },
            "status": {
                "table": self.cmb_stat_table.currentText().strip() or "dbo.ChestnyZnakCodes",
                "code_col": self.cmb_stat_code.currentText().strip() or "mark_code",
                "is_printed_col": self.cmb_stat_printed.currentText().strip() or "is_printed",
                "printed_at_col": self.cmb_stat_date.currentText().strip() or "printed_at",
                "status_col": self.cmb_stat_status.currentText().strip() or "status_code",
            },
            "events": {
                "table": self.cmb_ev_table.currentText().strip() or "dbo.MarkingEvents",
                "event_id": self.txt_ev_id.text().strip() or "event_id",
                "scanned_at": self.txt_ev_time.text().strip() or "scanned_at",
                "operator_name": self.txt_ev_op.text().strip() or "operator_name",
                "station_id": self.txt_ev_station.text().strip() or "station_id",
                "barcode": self.txt_ev_barcode.text().strip() or "barcode",
                "status": self.txt_ev_status.text().strip() or "status",
            },
        }
        return cfg

    def _load_config_to_ui(self):
        cfg = self.config
        is_mock = cfg.get("mock_mode", False) or cfg.get("server", "").upper() == "MOCK"
        self.chk_mock_mode.setChecked(is_mock)
        self.txt_server.setText(cfg.get("server", ""))
        self.txt_database.setText(cfg.get("database", "ChestnyZnakDB"))

        idx = self.cmb_driver.findText(cfg.get("driver", ""))
        if idx >= 0:
            self.cmb_driver.setCurrentIndex(idx)

        auth_val = cfg.get("auth_type", "windows")
        idx_auth = self.cmb_auth.findData(auth_val)
        if idx_auth >= 0:
            self.cmb_auth.setCurrentIndex(idx_auth)

        self.txt_user.setText(cfg.get("user", ""))
        self.spn_timeout.setValue(int(cfg.get("timeout", 10)))

        enc_val = cfg.get("encryption", "no")
        idx_enc = self.cmb_encrypt.findData(enc_val)
        if idx_enc >= 0:
            self.cmb_encrypt.setCurrentIndex(idx_enc)

        m = cfg.get("mapping", {})
        m_imp = m.get("import", {})
        self.cmb_imp_table.setCurrentText(m_imp.get("table", "dbo.ChestnyZnakCodes"))
        self.cmb_imp_code.setCurrentText(m_imp.get("code_col", "mark_code"))
        self.cmb_imp_gtin.setCurrentText(m_imp.get("gtin_col", "gtin"))
        self.cmb_imp_sn.setCurrentText(m_imp.get("sn_col", "serial_number"))
        self.cmb_imp_order.setCurrentText(m_imp.get("order_col", "order_id"))
        self.txt_imp_where.setText(m_imp.get("where", "is_printed = 0"))

        m_stat = m.get("status", {})
        self.cmb_stat_table.setCurrentText(m_stat.get("table", "dbo.ChestnyZnakCodes"))
        self.cmb_stat_code.setCurrentText(m_stat.get("code_col", "mark_code"))
        self.cmb_stat_printed.setCurrentText(m_stat.get("is_printed_col", "is_printed"))
        self.cmb_stat_date.setCurrentText(m_stat.get("printed_at_col", "printed_at"))
        self.cmb_stat_status.setCurrentText(m_stat.get("status_col", "status_code"))

        m_ev = m.get("events", {})
        self.cmb_ev_table.setCurrentText(m_ev.get("table", "dbo.MarkingEvents"))

        self._on_mock_mode_toggled(is_mock)

    def _on_test_connection(self):
        cfg = self._collect_config_from_ui()
        mgr = MSSQLManager(cfg)
        self.lbl_test_result.setText("Подключение...")
        ok, msg = mgr.test_connection()
        if ok:
            self.lbl_test_result.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.lbl_test_result.setText("✓ " + msg)
            QMessageBox.information(self, "Тест подключения", msg)
        else:
            self.lbl_test_result.setStyleSheet("color: #f44336; font-weight: bold;")
            self.lbl_test_result.setText("✗ " + msg)
            QMessageBox.critical(self, "Ошибка подключения", msg)

    def _on_fetch_schema_clicked(self):
        cfg = self._collect_config_from_ui()
        mgr = MSSQLManager(cfg)
        try:
            tables = mgr.get_tables()
            self._cached_tables = [f"{t[0]}.{t[1]}" for t in tables]
            for cmb in (self.cmb_imp_table, self.cmb_stat_table, self.cmb_ev_table):
                cmb.clear()
                for tbl in self._cached_tables:
                    cmb.addItem(tbl)

            self.lbl_schema_status.setText(f"✓ Загружено {len(self._cached_tables)} таблиц")
            self.lbl_schema_status.setStyleSheet("color: #4caf50;")
            QMessageBox.information(self, "Схема", f"Успешно получено таблиц с сервера: {len(self._cached_tables)}")
        except Exception as e:
            QMessageBox.critical(self, "Сбой загрузки схемы", str(e))

    def _generate_sql_preview(self):
        cfg = self._collect_config_from_ui()
        mgr = MSSQLManager(cfg)
        m = cfg["mapping"]

        try:
            sql_imp, _ = mgr.build_import_sql(
                m["import"], m["import"]["table"], limit=50000, where_clause=m["import"].get("where", "")
            )
            sql_stat = mgr.build_status_update_sql(m["status"], m["status"]["table"])
            sql_ev, _ = mgr.build_event_insert_sql(m["events"], m["events"]["table"])

            lines = [
                "-- ========================================================",
                "-- 1. ЗАПРОС ЗАГРУЗКИ ШК И DATAMATRIX (SELECT)",
                "-- ========================================================",
                f"{sql_imp};",
                "",
                "-- ========================================================",
                "-- 2. ЗАПРОС ОБНОВЛЕНИЯ СТАТУСА ПЕЧАТИ (UPDATE)",
                "-- ========================================================",
                f"{sql_stat};",
                "",
                "-- ========================================================",
                "-- 3. ЗАПРОС ВЫГРУЗКИ ЖУРНАЛА СОБЫТИЙ (INSERT)",
                "-- ========================================================",
                f"{sql_ev};",
            ]
            self.txt_preview_sql.setPlainText("\n".join(lines))
        except Exception as e:
            self.txt_preview_sql.setPlainText(f"-- Ошибка построения запроса: {e}")

    def _on_export_template(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Сохранить шаблон структуры", "mssql_template.json", "JSON (*.json)"
        )
        if filename:
            try:
                MSSQLManager.export_structure_template(filename)
                QMessageBox.information(self, "Шаблон сохранен", f"Файл шаблона успешно экспортирован:\n{filename}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка экспорта", str(e))

    def _on_import_template(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Загрузить шаблон структуры", "", "JSON (*.json)")
        if filename:
            try:
                data = MSSQLManager.load_structure_template(filename)
                imp = data.get("import_codes_mapping", {})
                self.cmb_imp_table.setCurrentText(imp.get("source_table", ""))
                self.cmb_imp_code.setCurrentText(imp.get("code_col", ""))
                self.cmb_imp_gtin.setCurrentText(imp.get("gtin_col", ""))
                self.cmb_imp_sn.setCurrentText(imp.get("sn_col", ""))
                self.cmb_imp_order.setCurrentText(imp.get("order_col", "order_id"))

                stat = data.get("code_status_mapping", {})
                self.cmb_stat_table.setCurrentText(stat.get("target_table", ""))
                self.cmb_stat_code.setCurrentText(stat.get("code_col", ""))
                self.cmb_stat_printed.setCurrentText(stat.get("is_printed_col", ""))
                self.cmb_stat_date.setCurrentText(stat.get("printed_at_col", ""))
                self.cmb_stat_status.setCurrentText(stat.get("status_col", ""))

                QMessageBox.information(self, "Шаблон применен", "Данные сопоставления успешно загружены из файла!")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка импорта", str(e))

    def _on_save_clicked(self):
        cfg = self._collect_config_from_ui()
        self.settings_saved.emit(cfg)
        self.accept()


class SQLServerAdminPage(QWidget):
    """
    Страница панели администратора 'SQL Server' (доступна на Мастер-ПК).
    """
    sync_requested = Signal(str)  # 'import', 'build_packages', 'export_status', 'export_events', 'all'

    def __init__(self, config_store=None, parent=None):
        super().__init__(parent)
        self.store = config_store
        self._init_ui()
        self._refresh_labels_from_cfg()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)

        header_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        lbl_title = QLabel("Интеграция с Microsoft SQL Server (Мастер-ПК)")
        lbl_title.setStyleSheet("font-size: 20px; font-weight: bold;")
        lbl_subtitle = QLabel(
            "Загрузка кодов маркировки (ШК ↔ ДМ) с сервера, формирование JSON-пакетов для обычных ПК и выгрузка отчётов"
        )
        lbl_subtitle.setStyleSheet("color: #8b97a6; font-size: 13px;")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch()

        self.btn_open_settings = QPushButton("⚙ Параметры подключения и таблиц")
        self.btn_open_settings.setProperty("primary", True)
        self.btn_open_settings.setStyleSheet("padding: 8px 16px; font-size: 13px;")
        self.btn_open_settings.clicked.connect(self._open_settings_dialog)
        header_layout.addWidget(self.btn_open_settings)
        layout.addLayout(header_layout)

        status_box = QGroupBox("Состояние соединения с MS SQL Server")
        status_layout = QGridLayout(status_box)

        status_layout.addWidget(QLabel("Сервер:"), 0, 0)
        self.lbl_cur_server = QLabel("MOCK (Режим эмуляции)")
        self.lbl_cur_server.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.lbl_cur_server, 0, 1)

        status_layout.addWidget(QLabel("База данных:"), 0, 2)
        self.lbl_cur_db = QLabel("ChestnyZnakDB")
        self.lbl_cur_db.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.lbl_cur_db, 0, 3)

        status_layout.addWidget(QLabel("Текущий статус:"), 1, 0)
        self.lbl_conn_status = QLabel("● Готов к обмену")
        self.lbl_conn_status.setStyleSheet("color: #22c55e; font-weight: bold;")
        status_layout.addWidget(self.lbl_conn_status, 1, 1)

        status_layout.addWidget(QLabel("Напечатано / в очереди:"), 1, 2)
        self.lbl_queue_count = QLabel("0 в очереди")
        status_layout.addWidget(self.lbl_queue_count, 1, 3)

        layout.addWidget(status_box)

        actions_box = QGroupBox("Операции обмена с MS SQL Server")
        actions_layout = QVBoxLayout(actions_box)
        actions_layout.setSpacing(12)

        btns_row = QHBoxLayout()
        self.btn_import_codes = QPushButton("📥 1. Вытянуть ШК + ДМ с сервера и создать JSON-пакеты")
        self.btn_import_codes.setProperty("primary", True)
        self.btn_import_codes.setStyleSheet("padding: 10px; font-size: 13px; font-weight: 600;")
        self.btn_import_codes.clicked.connect(lambda: self.sync_requested.emit("import"))
        btns_row.addWidget(self.btn_import_codes)

        self.btn_export_status = QPushButton("📤 2. Выгрузить статусы напечатанных ДМ")
        self.btn_export_status.setStyleSheet("padding: 10px; font-size: 13px;")
        self.btn_export_status.clicked.connect(lambda: self.sync_requested.emit("export_status"))
        btns_row.addWidget(self.btn_export_status)

        self.btn_export_events = QPushButton("📤 3. Выгрузить журнал сканирований")
        self.btn_export_events.setStyleSheet("padding: 10px; font-size: 13px;")
        self.btn_export_events.clicked.connect(lambda: self.sync_requested.emit("export_events"))
        btns_row.addWidget(self.btn_export_events)

        self.btn_full_sync = QPushButton("⚡ Полный цикл обмена")
        self.btn_full_sync.setStyleSheet(
            "background-color: #1976d2; color: white; font-weight: bold; padding: 10px 18px; font-size: 13px;"
        )
        self.btn_full_sync.clicked.connect(lambda: self.sync_requested.emit("all"))
        btns_row.addWidget(self.btn_full_sync)

        actions_layout.addLayout(btns_row)

        auto_row = QHBoxLayout()
        self.chk_auto_sync = QCheckBox("Автоматический фоновый обмен каждые:")
        self.spn_auto_interval = QSpinBox()
        self.spn_auto_interval.setRange(1, 120)
        self.spn_auto_interval.setValue(5)
        self.spn_auto_interval.setSuffix(" мин.")
        auto_row.addWidget(self.chk_auto_sync)
        auto_row.addWidget(self.spn_auto_interval)
        auto_row.addStretch()
        actions_layout.addLayout(auto_row)

        layout.addWidget(actions_box)

        log_box = QGroupBox("Лог операций обмена с MS SQL Server")
        log_layout = QVBoxLayout(log_box)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #0d1117; color: #c9d1d9; font-family: Consolas; font-size: 12px;")
        self.txt_log.append(
            "[Система] Модуль MS SQL Server готов. Нажмите «1. Вытянуть ШК + ДМ с сервера», "
            "чтобы загрузить коды в пул Мастер-ПК и сформировать именованные JSON-пакеты для обычных ПК."
        )
        log_layout.addWidget(self.txt_log)
        layout.addWidget(log_box)

    def _refresh_labels_from_cfg(self):
        cfg = load_mssql_config(self.store)
        server_name = "MOCK (Тестовая эмуляция)" if cfg.get("mock_mode") else (cfg.get("server") or "Не указан")
        self.lbl_cur_server.setText(server_name)
        self.lbl_cur_db.setText(cfg.get("database", "ChestnyZnakDB"))

    def _open_settings_dialog(self):
        curr_cfg = load_mssql_config(self.store)
        dlg = SQLServerConfigDialog(curr_cfg, self)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.exec()

    def _on_settings_saved(self, new_cfg: dict):
        save_mssql_config(self.store, new_cfg)
        self._refresh_labels_from_cfg()
        server_name = "MOCK (Тестовая эмуляция)" if new_cfg.get("mock_mode") else new_cfg.get("server", "Не указан")
        self.txt_log.append(f"[Настройки] Сохранены параметры сервера: {server_name} / {new_cfg.get('database')}")