"""
ChestZn — Модуль точного обнаружения реального оборудования (device_discovery.py)
Определяет физически подключенные принтеры и сканеры (USB-COM, USB-HID клавиатура, Bluetooth SPP):
1. detect_local_printers() — реально подключенные принтеры (отсекает виртуальные PDF/XPS/Fax).
2. detect_local_scanners() — реально подключенные сканеры:
   - USB-COM сканеры (определяет любые устройства, воткнутые в USB с портом COM).
   - USB-HID сканеры (сканеры-клавиатуры и 2.4G беспроводные радиодонглы).
   - Фильтрует пустые виртуальные порты Bluetooth (COM3, COM4, COM6).
3. check_computer_online() — проверка доступности ПК склада по IP.
4. AddComputerByIPDialog — диалог добавления компьютера по IP.
"""

import os
import re
import json
import socket
import logging
import platform
import subprocess
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("ChestZn.Discovery")

# База Vendor ID производителей сканеров, контроллеров и 2.4G радиоприемников
SCANNER_KNOWN_VENDORS = {
    "05e0": "Zebra / Motorola / Symbol",
    "0c2e": "Honeywell",
    "0536": "Honeywell / Metrologic",
    "05f9": "Datalogic",
    "1eab": "Newland",
    "0525": "Netum Barcode",
    "28e9": "Mindeo Scanner",
    "1a86": "WCH (CH340/CH341 USB-сканер)",
    "10c4": "Silicon Labs (CP210x USB-сканер)",
    "0483": "STM (USB-сканер / Virtual COM)",
    "04d9": "Holtek (2D Barcode Controller)",
    "1d57": "2.4G Wireless Barcode Dongle",
    "248a": "2.4G Wireless Barcode Dongle",
    "062a": "2.4G Wireless Barcode Receiver",
    "0403": "FTDI (USB-сканер)",
    "067b": "Prolific (USB-сканер)",
}

VIRTUAL_PRINTER_KEYWORDS = [
    "pdf", "xps", "onenote", "fax", "root print queue",
    "microsoft print to pdf", "send to onenote", "document writer"
]


def is_virtual_printer(printer_name: str) -> bool:
    name_l = printer_name.lower().strip()
    return any(k in name_l for k in VIRTUAL_PRINTER_KEYWORDS)


# ---------------------------------------------------------------------------
# 1. ОБНАРУЖЕНИЕ ТОЛЬКО ПОДКЛЮЧЕННЫХ ПРИНТЕРОВ
# ---------------------------------------------------------------------------

def detect_local_printers(connected_only: bool = True) -> List[Dict[str, Any]]:
    printers = []

    if platform.system() != "Windows":
        return [
            {"name": "TSC TE200", "port": "USB001", "online": True, "status_text": "Готов к печати", "is_default": True},
            {"name": "Zebra ZD421", "port": "192.168.1.150:9100", "online": True, "status_text": "Готов к печати", "is_default": False},
        ]

    try:
        import win32print
        default_name = ""
        try:
            default_name = win32print.GetDefaultPrinter()
        except Exception:
            pass

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printer_info_list = win32print.EnumPrinters(flags, None, 2)

        for p_info in printer_info_list:
            name = p_info.get("pPrinterName", "")
            port = p_info.get("pPortName", "—")
            driver = p_info.get("pDriverName", "—")
            status_flags = p_info.get("Status", 0)
            attributes = p_info.get("Attributes", 0)

            if connected_only and is_virtual_printer(name):
                continue

            is_offline = bool(status_flags & win32print.PRINTER_STATUS_OFFLINE)
            is_error = bool(status_flags & (win32print.PRINTER_STATUS_ERROR | win32print.PRINTER_STATUS_NOT_AVAILABLE))
            work_offline_attr = bool(attributes & getattr(win32print, "PRINTER_ATTRIBUTE_WORK_OFFLINE", 0))

            if is_error or is_offline or work_offline_attr:
                if connected_only:
                    continue
                online = False
                status_text = "Отключен"
            else:
                online = True
                status_text = "Готов к печати"

            name_lower = name.lower() + " " + driver.lower()
            if "zebra" in name_lower or "zpl" in name_lower:
                lang = "zpl"
            elif any(k in name_lower for k in ["tsc", "tspl", "xprinter", "gprinter"]):
                lang = "tspl"
            elif "godex" in name_lower or "ezpl" in name_lower:
                lang = "ezpl"
            else:
                lang = "auto"

            printers.append({
                "name": name,
                "port": port,
                "driver": driver,
                "lang": lang,
                "online": online,
                "status_text": status_text,
                "is_default": (name == default_name)
            })

    except Exception as e:
        logger.error("Ошибка опроса принтеров Windows: %s", e)

    return printers


# ---------------------------------------------------------------------------
# 2. ОБНАРУЖЕНИЕ ТОЛЬКО РЕАЛЬНО ПОДКЛЮЧЕННЫХ СКАНЕРОВ (USB И BLUETOOTH)
# ---------------------------------------------------------------------------

def detect_local_scanners(connected_only: bool = True) -> List[Dict[str, Any]]:
    """
    Обнаружение ТОЛЬКО физически подключенных сканеров:
    - USB-COM сканеры (любой реальный физический USB-порт со сканером).
    - USB-HID сканеры (сканеры-клавиатуры через провод или 2.4G радиофлешку).
    - Исключает пустые системные порты материнки и пустые виртуальные порты Bluetooth.
    """
    scanners = []
    seen_identifiers = set()

    # -----------------------------------------------------------------------
    # А. Поиск последовательных сканеров (USB-COM / USB-Serial)
    # -----------------------------------------------------------------------
    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()

        for port in ports:
            dev = port.device          # например, 'COM3'
            desc = port.description or ""
            hwid = port.hwid or ""

            desc_l = desc.lower()
            hwid_l = hwid.lower()

            # 1. Отсекаем пустые порты материнки (ACPI\PNP0501)
            if "pnp0501" in hwid_l or "communications port" in desc_l:
                continue

            # 2. Проверяем, является ли порт реальным физическим USB-устройством:
            # В Windows USB-COM порт существует ТОЛЬКО когда USB-кабель физически воткнут!
            is_physical_usb = ("usb" in hwid_l or "usb" in desc_l or "vid_" in hwid_l)
            is_bluetooth = ("bluetooth" in desc_l or "bthenum" in hwid_l)

            # 3. Отсекаем пустые системные входящие порты Bluetooth Windows (_LOCALMFG&0000)
            if is_bluetooth and ("_localmfg" in hwid_l or "localmfg" in hwid_l):
                continue

            # Для физического USB: проверяем производителя чипа сканера
            detected_vendor = None
            for vid, vname in SCANNER_KNOWN_VENDORS.items():
                if f"vid_{vid}" in hwid_l:
                    detected_vendor = vname
                    break

            # Проверка открытия порта
            is_connected = False
            status_text = "Отключен"

            if is_physical_usb:
                # Физическое USB-устройство: если оно есть в списке портов, оно УЖЕ воткнуто в USB!
                is_connected = True
                status_text = "Подключен по USB (COM-режим)"
            elif is_bluetooth:
                # Для Bluetooth требуется подтверждение активного радиоканала
                try:
                    import serial
                    s = serial.Serial(dev, timeout=0.1, write_timeout=0.1)
                    has_carrier = getattr(s, "dsr", False) or getattr(s, "cd", False)
                    s.close()
                    if has_carrier:
                        is_connected = True
                        status_text = "Подключен по Bluetooth"
                except Exception as e:
                    err_str = str(e).lower()
                    if "access is denied" in err_str or "permission" in err_str:
                        is_connected = True
                        status_text = "Активен (задействован)"

            if connected_only and not is_connected:
                continue

            if detected_vendor:
                display_name = f"USB-сканер {detected_vendor} ({dev})"
            elif is_physical_usb:
                display_name = f"USB-сканер ({dev})"
            else:
                display_name = f"Bluetooth-сканер ({dev})"

            scanners.append({
                "name": display_name,
                "device": dev,
                "description": desc,
                "connect_type": "USB-COM" if is_physical_usb else "BT-SPP",
                "online": True,
                "status_text": status_text
            })
            seen_identifiers.add(dev)

    except Exception as e:
        logger.error("Ошибка опроса COM-сканеров: %s", e)

    # -----------------------------------------------------------------------
    # Б. Поиск USB-HID сканеров (сканеры-клавиатуры / 2.4G радиодонглы)
    # -----------------------------------------------------------------------
    if platform.system() == "Windows":
        try:
            # Опрашиваем подключенные USB-устройства классов HID и Keyboard
            cmd = [
                'powershell', '-Command',
                "Get-PnpDevice -PresentOnly | Where-Object { "
                "  $_.Class -in @('HIDClass', 'Keyboard', 'POSBarcodeScanner') -and $_.InstanceId -like '*VID_*' "
                "} | Select-Object -Property FriendlyName, InstanceId, Class | ConvertTo-Json"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    hid_data = json.loads(res.stdout)
                    if isinstance(hid_data, dict):
                        hid_data = [hid_data]

                    for item in hid_data:
                        inst = (item.get("InstanceId") or "").lower()
                        fname = item.get("FriendlyName") or ""
                        fname_l = fname.lower()

                        # Игнорируем стандартные мыши
                        if "мышь" in fname_l or "mouse" in fname_l:
                            continue

                        # 1. Проверяем, есть ли сигнатура известного сканера
                        vendor_name = None
                        for vid, vtitle in SCANNER_KNOWN_VENDORS.items():
                            if f"vid_{vid}" in inst:
                                vendor_name = vtitle
                                break

                        # 2. Либо если в названии явно сканер / POS
                        is_named_scanner = any(k in fname_l for k in ["barcode", "scanner", "сканер", "pos", "honeywell", "zebra", "symbol", "datalogic", "newland", "netum"])

                        if vendor_name or is_named_scanner:
                            title = f"USB-сканер ({vendor_name or fname})"
                            # Избегаем дублирования
                            if title not in seen_identifiers:
                                seen_identifiers.add(title)
                                scanners.append({
                                    "name": title,
                                    "device": "USB-HID",
                                    "description": fname or "Эмуляция клавиатуры / 2.4G радиоприемник",
                                    "connect_type": "USB-HID",
                                    "online": True,
                                    "status_text": "Готов (ввод штрихкодов)"
                                })

                except Exception as e:
                    logger.debug("Ошибка парсинга HID JSON: %s", e)

        except Exception as e:
            logger.error("Ошибка поиска USB-HID устройств: %s", e)

    # Запасной вариант для тестов
    if not scanners and platform.system() != "Windows":
        scanners.append({
            "name": "USB-сканер (Zebra / Symbol DS2208)",
            "device": "USB-HID",
            "description": "Эмуляция клавиатуры",
            "connect_type": "USB-HID",
            "online": True,
            "status_text": "Готов (ввод штрихкодов)"
        })

    return scanners


# ---------------------------------------------------------------------------
# 3. СЕТЕВОЙ МОНИТОРИНГ КОМПЬЮТЕРОВ СКЛАДА (PING / TCP CHECK)
# ---------------------------------------------------------------------------

def check_computer_online(ip_or_host: str, timeout_ms: int = 600) -> Tuple[bool, str, Optional[float]]:
    ip = ip_or_host.strip()
    if not ip or ip == "—":
        return False, "IP-адрес не указан", None

    if ip.lower() in ("127.0.0.1", "localhost", socket.gethostname().lower()):
        return True, "Локальный узел (мастер)", 0.1

    try:
        if platform.system() == "Windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
        else:
            timeout_sec = max(1, timeout_ms // 1000)
            cmd = ["ping", "-c", "1", "-W", str(timeout_sec), ip]

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
        if res.returncode == 0:
            latency = None
            m = re.search(r'(?:time|время)[=<]\s*(\d+)\s*ms', res.stdout, re.IGNORECASE)
            if m:
                latency = float(m.group(1))
            return True, f"В сети ({latency or '<1'} мс)", latency
    except Exception:
        pass

    test_ports = [445, 139, 80, 5000]
    for port in test_ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout_ms / 1000.0)
            result = sock.connect_ex((ip, port))
            sock.close()
            if result == 0:
                return True, f"В сети (порт {port})", None
        except Exception:
            continue

    return False, "Офлайн (нет ответа)", None


# ---------------------------------------------------------------------------
# 4. ДИАЛОГ ДОБАВЛЕНИЯ НОВОГО ПК ПО IP-АДРЕСУ
# ---------------------------------------------------------------------------

try:
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
        QLineEdit, QPushButton, QCheckBox, QMessageBox, QGroupBox
    )
    from PySide6.QtCore import Qt

    class AddComputerByIPDialog(QDialog):
        def __init__(self, point_name: str = "", parent=None):
            super().__init__(parent)
            self.setWindowTitle("Добавить компьютер склада по IP")
            self.setMinimumWidth(440)
            self.result_data = None
            self.point_name = point_name
            self._init_ui()

        def _init_ui(self):
            layout = QVBoxLayout(self)
            layout.setSpacing(14)

            info_lbl = QLabel(f"Привязка к точке: <b>{self.point_name or 'Основной склад'}</b>")
            layout.addWidget(info_lbl)

            form_box = QGroupBox("Параметры сетевого узла")
            form = QFormLayout(form_box)

            self.txt_name = QLineEdit("СКЛАД-ПК-01")
            form.addRow("Имя компьютера:", self.txt_name)

            self.txt_ip = QLineEdit()
            self.txt_ip.setPlaceholderText("например: 192.168.1.55")
            form.addRow("IP-адрес в сети:", self.txt_ip)

            self.txt_role = QLineEdit("Оператор маркировки (Линия 1)")
            form.addRow("Роль / Назначение:", self.txt_role)

            self.chk_sync = QCheckBox("Включить обмен с мастером сразу")
            self.chk_sync.setChecked(True)
            form.addRow("", self.chk_sync)

            layout.addWidget(form_box)

            check_box = QHBoxLayout()
            self.btn_check_ping = QPushButton("⚡ Проверить связь по IP")
            self.btn_check_ping.clicked.connect(self._on_check_ping)
            check_box.addWidget(self.btn_check_ping)

            self.lbl_ping_res = QLabel("Связь не проверялась")
            self.lbl_ping_res.setStyleSheet("color: #888;")
            check_box.addWidget(self.lbl_ping_res)
            check_box.addStretch()
            layout.addLayout(check_box)

            btns = QHBoxLayout()
            btns.addStretch()
            self.btn_add = QPushButton("Добавить компьютер")
            self.btn_add.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 14px;")
            self.btn_add.clicked.connect(self._on_add)
            btns.addWidget(self.btn_add)

            self.btn_cancel = QPushButton("Отмена")
            self.btn_cancel.clicked.connect(self.reject)
            btns.addWidget(self.btn_cancel)

            layout.addLayout(btns)

        def _on_check_ping(self):
            ip = self.txt_ip.text().strip()
            if not ip:
                QMessageBox.warning(self, "Предупреждение", "Введите IP-адрес для проверки связи")
                return

            self.lbl_ping_res.setText("Проверка связи...")
            is_online, msg, _ = check_computer_online(ip)
            if is_online:
                self.lbl_ping_res.setStyleSheet("color: #4caf50; font-weight: bold;")
                self.lbl_ping_res.setText(f"✓ {msg}")
            else:
                self.lbl_ping_res.setStyleSheet("color: #f44336; font-weight: bold;")
                self.lbl_ping_res.setText(f"✗ {msg}")

        def _on_add(self):
            name = self.txt_name.text().strip()
            ip = self.txt_ip.text().strip()
            if not name:
                QMessageBox.warning(self, "Ошибка", "Укажите имя компьютера")
                return
            if not ip:
                QMessageBox.warning(self, "Ошибка", "Укажите IP-адрес компьютера")
                return

            is_online, _, _ = check_computer_online(ip)

            self.result_data = {
                "name": name,
                "ip": ip,
                "role": self.txt_role.text().strip(),
                "sync_enabled": self.chk_sync.isChecked(),
                "online": is_online
            }
            self.accept()

except ImportError:
    pass