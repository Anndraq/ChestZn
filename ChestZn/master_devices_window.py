"""
ChestZn — Интерфейс управления сетевыми узлами и физическими устройствами (master_devices_window.py)
Отображает ТОЛЬКО реально подключенные принтеры и сканеры (без пустых портов и виртуальных принтеров).
Включает интерактивный тест мгновенного распознавания сканера ("Сканируйте штрихкод").
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QGridLayout, QTreeWidget, QTreeWidgetItem, QLabel, QPushButton,
    QCheckBox, QLineEdit, QHeaderView, QGroupBox, QMessageBox, QFrame
)

from device_discovery import (
    detect_local_printers, detect_local_scanners,
    check_computer_online, AddComputerByIPDialog
)

logger = logging.getLogger("ChestZn.MasterDevices")

CONFIG_PATH = Path("devices_config.json")


class MasterDevicesWidget(QWidget):
    """
    Панель управления сетевыми компьютерами и физическим оборудованием Мастер-ПК
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = self._load_config()
        self._init_ui()
        self.refresh_all()

    def _load_config(self) -> Dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Ошибка загрузки конфигурации: %s", e)

        return {
            "master_node": "MASTER-PC",
            "active_only_filter": True,
            "ping_timeout_ms": 600,
            "computers": [
                {"id": 1, "name": "МАСТЕР-ПК (Этот компьютер)", "ip": "127.0.0.1", "role": "Сервер маркировки", "sync_enabled": True}
            ]
        }

    def _save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Ошибка сохранения конфигурации: %s", e)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        head_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        lbl_title = QLabel("Управление оборудованием и компьютерами склада")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        lbl_sub = QLabel("Отображение только реально подключенных принтеров, сканеров и онлайн-компьютеров")
        lbl_sub.setStyleSheet("font-size: 12px; color: #888;")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        head_layout.addLayout(title_box)
        head_layout.addStretch()

        self.btn_add_ip = QPushButton("➕ Добавить компьютер по IP")
        self.btn_add_ip.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 7px 14px;")
        self.btn_add_ip.clicked.connect(self._on_add_computer_ip)
        head_layout.addWidget(self.btn_add_ip)

        self.btn_refresh = QPushButton("🔄 Опросить устройства и сеть")
        self.btn_refresh.setStyleSheet("padding: 7px 14px;")
        self.btn_refresh.clicked.connect(self.refresh_all)
        head_layout.addWidget(self.btn_refresh)

        layout.addLayout(head_layout)

        # Панель быстрого теста сканирования
        test_scan_bar = QFrame()
        test_scan_bar.setStyleSheet("background-color: #1a222d; border: 1px solid #2a3542; border-radius: 6px; padding: 6px;")
        ts_layout = QHBoxLayout(test_scan_bar)
        ts_layout.setContentsMargins(10, 4, 10, 4)
        
        ts_label = QLabel("⚡ Проверить сканер:")
        ts_label.setStyleSheet("font-weight: bold; color: #93c5fd;")
        ts_layout.addWidget(ts_label)

        self.txt_test_scan = QLineEdit()
        self.txt_test_scan.setPlaceholderText("Кликните сюда и отсканируйте любой штрихкод для проверки...")
        self.txt_test_scan.returnPressed.connect(self._on_test_barcode_scanned)
        ts_layout.addWidget(self.txt_test_scan, stretch=1)

        self.lbl_scan_feedback = QLabel("Ожидание сканирования...")
        self.lbl_scan_feedback.setStyleSheet("color: #888; font-size: 11px;")
        ts_layout.addWidget(self.lbl_scan_feedback)

        layout.addWidget(test_scan_bar)

        filter_bar = QHBoxLayout()
        self.chk_online_only = QCheckBox("Показывать только актуальные (онлайн) компьютеры")
        self.chk_online_only.setChecked(self.config.get("active_only_filter", True))
        self.chk_online_only.toggled.connect(self._on_filter_toggled)
        filter_bar.addWidget(self.chk_online_only)

        filter_bar.addStretch()

        self.lbl_summary = QLabel("Опрос устройств...")
        self.lbl_summary.setStyleSheet("color: #aaa; font-size: 12px;")
        filter_bar.addWidget(self.lbl_summary)

        layout.addLayout(filter_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            "Узел / Устройство",
            "Порт / IP-адрес",
            "Статус",
            "Тип",
            "Сведения"
        ])
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 180)
        self.tree.setColumnWidth(2, 140)
        self.tree.setColumnWidth(3, 160)
        self.tree.setColumnWidth(4, 220)
        self.tree.setAlternatingRowColors(False)
        self.tree.setStyleSheet("""
            QTreeWidget {
                background: #161c24;
                border: 1px solid #2a3542;
                border-radius: 8px;
                color: #e6edf3;
            }
            QTreeWidget::item {
                padding: 7px 5px;
                border-bottom: 1px solid #232c37;
            }
            QTreeWidget::item:selected {
                background: rgba(59,130,246,0.3);
            }
            QHeaderView::section {
                background: #1b232e;
                color: #8b97a6;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #2a3542;
                font-weight: bold;
            }
        """)

        layout.addWidget(self.tree, stretch=1)

    def _on_filter_toggled(self, checked: bool):
        self.config["active_only_filter"] = checked
        self._save_config()
        self.refresh_all()

    def _on_test_barcode_scanned(self):
        text = self.txt_test_scan.text().strip()
        if text:
            self.lbl_scan_feedback.setText(f"✓ Успешно принят штрихкод: {text}")
            self.lbl_scan_feedback.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.txt_test_scan.clear()

    def refresh_all(self):
        """Опрос только реально подключенных устройств и проверка сети"""
        self.tree.clear()

        connected_printers = detect_local_printers(connected_only=True)
        connected_scanners = detect_local_scanners(connected_only=True)

        online_count = 0
        total_count = len(self.config.get("computers", []))

        for comp in self.config.get("computers", []):
            ip = comp.get("ip", "")
            is_online, msg, latency = check_computer_online(ip, timeout_ms=self.config.get("ping_timeout_ms", 600))
            comp["online"] = is_online
            comp["last_status"] = msg

            if is_online:
                online_count += 1
            elif self.chk_online_only.isChecked():
                continue

            comp_item = QTreeWidgetItem(self.tree)
            comp_item.setText(0, f"💻  {comp.get('name')}")
            comp_item.setText(1, ip)
            comp_item.setText(2, "🟢  онлайн" if is_online else "🔴  офлайн")
            comp_item.setText(3, comp.get("role", "Складской узел"))
            comp_item.setText(4, msg)

            font = comp_item.font(0)
            font.setBold(True)
            comp_item.setFont(0, font)

            if is_online:
                comp_item.setForeground(2, QBrush(QColor("#22c55e")))
            else:
                comp_item.setForeground(2, QBrush(QColor("#ef4444")))

            # Для локального ПК выводим только реально подключенные устройства
            if ip in ("127.0.0.1", "localhost"):
                # Принтеры (только реально подключенные)
                if connected_printers:
                    for pr in connected_printers:
                        p_item = QTreeWidgetItem(comp_item)
                        p_item.setText(0, f"    🖨  {pr['name']}")
                        p_item.setText(1, pr["port"])
                        p_item.setText(2, "🟢  готов" if pr["online"] else "🟡  " + pr["status_text"])
                        p_item.setText(3, f"Термопринтер ({pr['lang'].upper()})")
                        p_item.setText(4, "По умолчанию" if pr.get("is_default") else pr["driver"])
                        p_item.setForeground(2, QBrush(QColor("#22c55e")))
                else:
                    empty_p = QTreeWidgetItem(comp_item)
                    empty_p.setText(0, "    🖨  Подключенные принтеры не найдены")
                    empty_p.setForeground(0, QBrush(QColor("#888")))

                # Сканеры (только реально подключенные, без пустых портов)
                if connected_scanners:
                    for sc in connected_scanners:
                        s_item = QTreeWidgetItem(comp_item)
                        s_item.setText(0, f"    📡  {sc['name']}")
                        s_item.setText(1, sc["device"])
                        s_item.setText(2, "🟢  подключен" if sc["online"] else "⚪  отключен")
                        s_item.setText(3, sc["connect_type"])
                        s_item.setText(4, sc["description"])
                        s_item.setForeground(2, QBrush(QColor("#22c55e")))
                else:
                    empty_s = QTreeWidgetItem(comp_item)
                    empty_s.setText(0, "    📡  Подключенные сканеры не найдены")
                    empty_s.setForeground(0, QBrush(QColor("#888")))

            comp_item.setExpanded(is_online)

        self.lbl_summary.setText(f"ПК в сети: {online_count} из {total_count} | Подключено принтеров: {len(connected_printers)}, сканеров: {len(connected_scanners)}")

    def _on_add_computer_ip(self):
        dlg = AddComputerByIPDialog(parent=self)
        if dlg.exec() == AddComputerByIPDialog.Accepted and dlg.result_data:
            data = dlg.result_data
            data["id"] = len(self.config.get("computers", [])) + 1
            self.config.setdefault("computers", []).append(data)
            self._save_config()
            self.refresh_all()
            QMessageBox.information(self, "Успешно", f"Компьютер «{data['name']}» ({data['ip']}) добавлен!")


class MasterDevicesMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChestZn — Монитор подключенного оборудования")
        self.resize(1020, 640)
        self.widget = MasterDevicesWidget(self)
        self.setCentralWidget(self.widget)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MasterDevicesMainWindow()
    win.show()
    sys.exit(app.exec())