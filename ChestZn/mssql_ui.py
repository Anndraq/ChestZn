"""
Интерфейс управления интеграцией с Microsoft SQL Server (mssql_ui.py).
Поддерживает обе темы без невидимого текста.
"""
from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QLineEdit, QPushButton, QCheckBox, QSpinBox,
    QPlainTextEdit, QMessageBox, QGridLayout
)
from PySide6.QtCore import Qt, Signal

from toast import Toast


class SQLServerAdminPage(QWidget):
    sync_requested = Signal(str)

    def __init__(self, config_store=None, parent=None):
        super().__init__(parent)
        self.store = config_store
        self.toast = Toast(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # Заголовок раздела
        head = QHBoxLayout()
        title = QLabel("Интеграция с Microsoft SQL Server (Мастер-ПК)")
        title.setProperty("heading", True)
        head.addWidget(title)
        head.addStretch(1)

        btn_params = QPushButton("⚙ Параметры подключения и таблиц")
        btn_params.setProperty("primary", True)
        btn_params.clicked.connect(self._open_connection_params)
        head.addWidget(btn_params)
        root.addLayout(head)

        sub_desc = QLabel("Загрузка кодов маркировки (ШК ↔ ДМ) с сервера, формирование JSON-пакетов для обычных ПК и выгрузка отчётов")
        sub_desc.setStyleSheet("color: #64748b; font-size: 13px;")
        root.addWidget(sub_desc)

        # 1. Карточка статуса соединения
        sec1_title = QLabel("СОСТОЯНИЕ СОЕДИНЕНИЯ С MS SQL SERVER")
        sec1_title.setStyleSheet("color: #475569; font-weight: 700; font-size: 12px; letter-spacing: 1.2px;")
        root.addWidget(sec1_title)

        status_card = QFrame()
        status_card.setObjectName("card")
        sc_layout = QGridLayout(status_card)
        sc_layout.setContentsMargins(18, 14, 18, 14)
        sc_layout.setHorizontalSpacing(24)
        sc_layout.setVerticalSpacing(10)

        sc_layout.addWidget(QLabel("Сервер:"), 0, 0)
        self.lbl_server = QLabel("MOCK (Тестовая эмуляция)")
        self.lbl_server.setStyleSheet("font-weight: 700;")
        sc_layout.addWidget(self.lbl_server, 0, 1)

        sc_layout.addWidget(QLabel("База данных:"), 0, 2)
        self.lbl_db = QLabel("ChestnyZnakDB")
        self.lbl_db.setStyleSheet("font-weight: 700;")
        sc_layout.addWidget(self.lbl_db, 0, 3)

        sc_layout.addWidget(QLabel("Текущий статус:"), 1, 0)
        self.lbl_status = QLabel("🟢 Готов к обмену")
        self.lbl_status.setStyleSheet("color: #10b981; font-weight: 700;")
        sc_layout.addWidget(self.lbl_status, 1, 1)

        sc_layout.addWidget(QLabel("Напечатано / в очереди:"), 1, 2)
        self.lbl_queue = QLabel("0 в очереди")
        self.lbl_queue.setStyleSheet("font-weight: 700;")
        sc_layout.addWidget(self.lbl_queue, 1, 3)

        root.addWidget(status_card)

        # 2. Карточка операций обмена
        sec2_title = QLabel("ОПЕРАЦИИ ОБМЕНА С MS SQL SERVER")
        sec2_title.setStyleSheet("color: #475569; font-weight: 700; font-size: 12px; letter-spacing: 1.2px;")
        root.addWidget(sec2_title)

        ops_card = QFrame()
        ops_card.setObjectName("card")
        ops_layout = QVBoxLayout(ops_card)
        ops_layout.setContentsMargins(18, 14, 18, 14)
        ops_layout.setSpacing(12)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_pull = QPushButton("📥 1. Вытянуть ШК + ДМ с сервера и создать JSON-пакеты")
        btn_pull.setProperty("primary", True)
        btn_pull.clicked.connect(lambda: self._handle_action("pull"))
        btn_row.addWidget(btn_pull)

        btn_push_status = QPushButton("📤 2. Выгрузить статусы напечатанных ДМ")
        btn_push_status.clicked.connect(lambda: self._handle_action("push_status"))
        btn_row.addWidget(btn_push_status)

        btn_push_scans = QPushButton("📤 3. Выгрузить журнал сканирований")
        btn_push_scans.clicked.connect(lambda: self._handle_action("push_scans"))
        btn_row.addWidget(btn_push_scans)

        btn_full = QPushButton("⚡ Полный цикл обмена")
        btn_full.setStyleSheet(
            "background-color: #0284c7; color: #ffffff; font-weight: 700; border-radius: 8px; padding: 8px 16px;"
        )
        btn_full.clicked.connect(lambda: self._handle_action("full"))
        btn_row.addWidget(btn_full)

        ops_layout.addLayout(btn_row)

        auto_row = QHBoxLayout()
        auto_row.setSpacing(8)

        self.auto_sync_chk = QCheckBox("Автоматический фоновый обмен каждые:")
        self.auto_sync_chk.setChecked(False)
        auto_row.addWidget(self.auto_sync_chk)

        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 120)
        self.spin_interval.setValue(5)
        self.spin_interval.setSuffix(" мин.")
        self.spin_interval.setFixedWidth(110)
        auto_row.addWidget(self.spin_interval)

        auto_row.addStretch(1)
        ops_layout.addLayout(auto_row)

        root.addWidget(ops_card)

        # 3. Консоль журнала обмена
        sec3_title = QLabel("ЛОГ ОПЕРАЦИЙ ОБМЕНА С MS SQL SERVER")
        sec3_title.setStyleSheet("color: #475569; font-weight: 700; font-size: 12px; letter-spacing: 1.2px;")
        root.addWidget(sec3_title)

        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0f172a;
                color: #e2e8f0;
                font-family: Consolas, monospace;
                font-size: 13px;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        self.log_console.appendPlainText("[Система] Модуль MS SQL Server готов к работе. Нажмите «1. Вытянуть ШК + ДМ с сервера» для начала.")
        root.addWidget(self.log_console, stretch=1)

    def _open_connection_params(self):
        QMessageBox.information(self, "Параметры MS SQL", "Настройки подключения (Host, DB, Login) сохранены в конфигурации системы.")

    def _handle_action(self, action: str):
        actions_map = {
            "pull": "Загрузка ШК и кодов маркировки с сервера",
            "push_status": "Выгрузка напечатанных марок на сервер",
            "push_scans": "Выгрузка журнала сканирования на сервер",
            "full": "Полная синхронизация с MS SQL Server",
        }
        desc = actions_map.get(action, action)
        self.log_console.appendPlainText(f"[Обмен] Запуск: {desc}...")
        self.sync_requested.emit(action)
        self.toast.show_message(desc, "ok")