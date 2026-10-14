"""
ChestZn — Модуль точного обнаружения реально подключенного оборудования (device_discovery.py)
Определяет ТОЛЬКО физически подключенные в данный момент принтеры и сканеры:
1. detect_local_printers(connected_only=True):
   - Отсекает виртуальные принтеры (PDF, XPS, OneNote, Fax) и пустые порты (LPT, FILE, PORTPROMPT, WSD).
   - Для USB-принтеров (USB001, USB002...) проверяет через Windows PnP (Get-PnpDevice -PresentOnly -Status OK),
     действительно ли принтер прямо сейчас физически воткнут в USB и включён. Старые установленные драйверы
     отключённых принтеров (например, офисный Xerox/HP/Canon без кабеля) НЕ выводятся.
   - Для сетевых принтеров проверяет доступность по TCP-порту (по умолчанию 9100).
2. detect_local_scanners(connected_only=True):
   - USB-COM сканеры (физические USB-Serial устройства, воткнутые в USB в данный момент).
   - Bluetooth SPP сканеры (только с активным радиоканалом DSR/CD).
   - USB-HID сканеры (только настоящие сканеры штрихкодов по VID производителей сканеров или USB BusReportedDeviceDesc,
     не путая их с обычными клавиатурами и мышами Holtek/Telink/MosArt).
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

# Строгий список Vendor ID настоящих производителей сканеров штрихкодов (для USB-HID и USB-COM)
# Исключены чипы обычных клавиатур и мышей (04d9 Holtek, 1d57, 248a Telink, 062a MosArt)
BARCODE_SCANNER_HID_VENDORS = {
    "05e0": "Zebra / Motorola / Symbol",
    "0c2e": "Honeywell",
    "0536": "Honeywell / Metrologic",
    "05f9": "Datalogic",
    "1eab": "Newland",
    "0525": "Netum Barcode",
    "28e9": "Mindeo / GD32 Scanner",
    "2dd6": "Mertech / Superlead",
    "23d0": "Urovo / iData",
}

# Для режима USB-COM (Virtual Serial Port) также учитываем мосты USB-UART
SCANNER_COM_VENDORS = {
    **BARCODE_SCANNER_HID_VENDORS,
    "1a86": "WCH (CH340/CH341 USB-COM сканер)",
    "10c4": "Silicon Labs (CP210x USB-COM сканер)",
    "0483": "STM (Virtual COM сканер)",
    "0403": "FTDI (USB-COM сканер)",
    "067b": "Prolific (USB-COM сканер)",
}

# Совместимый алиас
SCANNER_KNOWN_VENDORS = SCANNER_COM_VENDORS

VIRTUAL_PRINTER_KEYWORDS = [
    "pdf", "xps", "onenote", "fax", "root print queue",
    "microsoft print to pdf", "send to onenote", "document writer",
    "fineprint", "pdffactory", "cute writer", "anydesk", "teamviewer"
]

SCANNER_NAME_KEYWORDS = [
    "barcode", "bar code", "scanner", "сканер", "штрих",
    "honeywell", "zebra", "symbol", "datalogic", "newland",
    "netum", "mindeo", "mertech", "urovo", "opticon", "cipherlab",
    "pos hid", "hid pos"
]


def is_virtual_printer(printer_name: str) -> bool:
    name_l = (printer_name or "").lower().strip()
    return any(k in name_l for k in VIRTUAL_PRINTER_KEYWORDS)


def _get_present_usb_printers_windows() -> Tuple[bool, List[str]]:
    """
    Опрашивает Windows PnP (Get-PnpDevice -PresentOnly -Status OK), чтобы узнать,
    какие физические USB-принтеры прямо сейчас воткнуты в ПК и включены.
    Возвращает (has_any_usbprint_device, list_of_present_printer_names_lower).
    """
    if platform.system() != "Windows":
        return False, []

    try:
        ps_script = (
            "Get-PnpDevice -PresentOnly -Status OK -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Class -in @('Printer', 'PrinterQueue') -or "
            "$_.InstanceId -like 'USBPRINT*' -or $_.Service -eq 'usbprint' } | "
            "Select-Object -Property FriendlyName, InstanceId, Class, Service | ConvertTo-Json -Compress"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=2.5,
            creationflags=creationflags
        )
        if res.returncode != 0 or not res.stdout.strip():
            return False, []

        raw = json.loads(res.stdout.strip())
        if isinstance(raw, dict):
            raw = [raw]

        has_usbprint = False
        names: List[str] = []
        for item in raw:
            inst = str(item.get("InstanceId") or "").upper()
            srv = str(item.get("Service") or "").lower()
            cls_name = str(item.get("Class") or "").lower()
            fname = str(item.get("FriendlyName") or "").strip()

            if inst.startswith("SWD\\PRINTENUM\\") and is_virtual_printer(fname):
                continue

            if inst.startswith("USBPRINT\\") or srv == "usbprint" or (cls_name == "printer" and "usb" in inst.lower()):
                has_usbprint = True
                if fname:
                    names.append(fname.lower())
                # В InstanceId вида USBPRINT\ZEBRA_TECHNOLOGIES_ZTC_ZD421\... также закодирована модель
                parts = inst.split("\\")
                if len(parts) >= 2:
                    names.append(parts[1].lower().replace("_", " "))

        return has_usbprint, names
    except Exception as e:
        logger.debug("Ошибка проверки физического подключения USB-принтеров: %s", e)
        return False, []


def _is_printer_name_matching_pnp(printer_name: str, present_pnp_names: List[str]) -> bool:
    """Проверяет, соответствует ли имя принтера в спулере одному из физически присутствующих USBPRINT-устройств."""
    if not present_pnp_names:
        return False
    p_low = printer_name.lower().strip()
    p_tokens = [t for t in re.split(r"[\s\-_/()]+", p_low) if len(t) >= 3 and t not in ("usb", "copy", "копия", "series", "printer")]
    for pnp in present_pnp_names:
        if p_low in pnp or pnp in p_low:
            return True
        if p_tokens and sum(1 for tok in p_tokens if tok in pnp) >= min(2, len(p_tokens)):
            return True
    return False


# ---------------------------------------------------------------------------
# 1. ОБНАРУЖЕНИЕ ТОЛЬКО ФИЗИЧЕСКИ ПОДКЛЮЧЕННЫХ В ДАННЫЙ МОМЕНТ ПРИНТЕРОВ
# ---------------------------------------------------------------------------

def detect_local_printers(connected_only: bool = True) -> List[Dict[str, Any]]:
    printers: List[Dict[str, Any]] = []

    if platform.system() != "Windows":
        return []

    try:
        import win32print
        default_name = ""
        try:
            default_name = win32print.GetDefaultPrinter()
        except Exception:
            pass

        has_usbprint, present_pnp_printers = _get_present_usb_printers_windows()

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printer_info_list = win32print.EnumPrinters(flags, None, 2)

        for p_info in printer_info_list:
            name = (p_info.get("pPrinterName") or "").strip()
            port = (p_info.get("pPortName") or "").strip()
            driver = (p_info.get("pDriverName") or "").strip()
            status_flags = p_info.get("Status", 0)
            attributes = p_info.get("Attributes", 0)

            if not name or is_virtual_printer(name):
                continue

            port_up = port.upper()
            # Отсекаем заведомо виртуальные и пустые порты
            if connected_only and any(
                port_up.startswith(prefix)
                for prefix in ("PORTPROMPT", "FILE:", "NUL", "SHRFAX", "FAX", "LPT", "XPS")
            ):
                continue

            is_offline_flag = bool(status_flags & win32print.PRINTER_STATUS_OFFLINE)
            is_error_flag = bool(status_flags & (win32print.PRINTER_STATUS_ERROR | win32print.PRINTER_STATUS_NOT_AVAILABLE))
            work_offline_attr = bool(attributes & getattr(win32print, "PRINTER_ATTRIBUTE_WORK_OFFLINE", 0))

            # Проверяем реальное физическое подключение в данный момент
            physically_connected = False

            if port_up.startswith("USB"):
                # Для USB-принтера: он подключён ТОЛЬКО если в системе сейчас есть живое USBPRINT-устройство
                if has_usbprint and (
                    _is_printer_name_matching_pnp(name, present_pnp_printers)
                    or _is_printer_name_matching_pnp(driver, present_pnp_printers)
                ):
                    physically_connected = True
                else:
                    physically_connected = False
            elif port_up.startswith("COM"):
                # Последовательный принтер — проверяем наличие порта
                try:
                    import serial.tools.list_ports
                    active_coms = {cp.device.upper() for cp in serial.tools.list_ports.comports()}
                    physically_connected = port_up.rstrip(":") in active_coms
                except Exception:
                    physically_connected = False
            else:
                # Сетевой принтер по IP (например, 192.168.1.150 или IP_192.168.1.150)
                m_ip = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", port)
                if m_ip:
                    ip_addr = m_ip.group(1)
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(0.35)
                        physically_connected = (sock.connect_ex((ip_addr, 9100)) == 0)
                        sock.close()
                    except Exception:
                        physically_connected = False

            if is_offline_flag or is_error_flag or work_offline_attr:
                physically_connected = False

            if connected_only and not physically_connected:
                continue

            name_lower = name.lower() + " " + driver.lower()
            if "zebra" in name_lower or "zpl" in name_lower:
                lang = "zpl"
            elif any(k in name_lower for k in ["tsc", "tspl", "xprinter", "gprinter", "atol", "атол"]):
                lang = "tspl"
            elif "godex" in name_lower or "ezpl" in name_lower:
                lang = "ezpl"
            else:
                lang = "auto"

            printers.append({
                "name": name,
                "port": port or "—",
                "driver": driver or "—",
                "lang": lang,
                "online": physically_connected,
                "status_text": "Готов к печати" if physically_connected else "Отключен",
                "is_default": (name == default_name)
            })

    except Exception as e:
        logger.error("Ошибка опроса принтеров Windows: %s", e)

    return printers


# ---------------------------------------------------------------------------
# 2. ОБНАРУЖЕНИЕ ТОЛЬКО ФИЗИЧЕСКИ ПОДКЛЮЧЕННЫХ В ДАННЫЙ МОМЕНТ СКАНЕРОВ
# ---------------------------------------------------------------------------

def detect_local_scanners(connected_only: bool = True) -> List[Dict[str, Any]]:
    """
    Возвращает список ТОЛЬКО реально подключенных в данный момент сканеров штрихкодов.
    Если ни один сканер не подключен, возвращает пустой список [].
    """
    scanners: List[Dict[str, Any]] = []
    seen_identifiers = set()

    if platform.system() != "Windows":
        return []

    # -----------------------------------------------------------------------
    # А. Поиск последовательных сканеров (USB-COM / Bluetooth SPP)
    # -----------------------------------------------------------------------
    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()

        for port in ports:
            dev = port.device
            desc = port.description or ""
            hwid = port.hwid or ""

            desc_l = desc.lower()
            hwid_l = hwid.lower()

            # 1. Отсекаем системные порты материнской платы (ACPI\PNP0501)
            if "pnp0501" in hwid_l or "communications port" in desc_l or "последовательный порт" in desc_l:
                if "usb" not in hwid_l and "bluetooth" not in desc_l:
                    continue

            is_physical_usb = ("usb" in hwid_l or "usb" in desc_l or "vid_" in hwid_l) and "bthenum" not in hwid_l
            is_bluetooth = ("bluetooth" in desc_l or "bthenum" in hwid_l)

            # 2. Отсекаем пустые виртуальные входящие порты Bluetooth Windows
            if is_bluetooth and ("_localmfg" in hwid_l or "localmfg" in hwid_l):
                continue

            detected_vendor = None
            for vid, vname in SCANNER_COM_VENDORS.items():
                if f"vid_{vid}" in hwid_l:
                    detected_vendor = vname
                    break

            is_connected = False
            status_text = "Отключен"

            if is_physical_usb:
                is_connected = True
                status_text = "Подключен по USB (COM-режим)"
            elif is_bluetooth:
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
                    if "access is denied" in err_str or "permission" in err_str or "отказано в доступе" in err_str:
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
    # Б. Поиск настоящих USB-HID сканеров штрихкодов (без обычных клавиатур и мышей)
    # -----------------------------------------------------------------------
    try:
        ps_cmd = (
            "Get-PnpDevice -PresentOnly -Status OK -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Class -in @('HIDClass', 'Keyboard', 'POSBarcodeScanner') -and $_.InstanceId -like '*VID_*' } | "
            "ForEach-Object { "
            "  $busDesc = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_BusReportedDeviceDesc' -ErrorAction SilentlyContinue).Data; "
            "  [PSCustomObject]@{ FriendlyName = $_.FriendlyName; InstanceId = $_.InstanceId; Class = $_.Class; BusDesc = $busDesc } "
            "} | ConvertTo-Json -Compress"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=creationflags
        )
        if res.returncode == 0 and res.stdout.strip():
            hid_data = json.loads(res.stdout.strip())
            if isinstance(hid_data, dict):
                hid_data = [hid_data]

            for item in hid_data:
                inst = str(item.get("InstanceId") or "").lower()
                fname = str(item.get("FriendlyName") or "").strip()
                bdesc = str(item.get("BusDesc") or "").strip()
                cls_name = str(item.get("Class") or "").lower()

                combined_l = f"{fname} {bdesc}".lower()

                # Игнорируем мыши, аудио, системные контроллеры
                if any(w in combined_l for w in ["mouse", "мышь", "audio", "headset", "consumer control", "system controller"]):
                    continue

                # 1. Проверяем только строгий список VID производителей сканеров штрихкодов
                vendor_name = None
                for vid, vtitle in BARCODE_SCANNER_HID_VENDORS.items():
                    if f"vid_{vid}" in inst:
                        vendor_name = vtitle
                        break

                # 2. Или если устройство в USB BusReportedDeviceDesc / FriendlyName явно называет себя сканером
                is_named_scanner = (
                    cls_name == "posbarcodescanner"
                    or any(k in combined_l for k in SCANNER_NAME_KEYWORDS)
                )

                if vendor_name or is_named_scanner:
                    label_detail = bdesc or vendor_name or fname
                    title = f"USB-сканер ({label_detail})"
                    vid_pid_match = re.search(r"(vid_[0-9a-f]{4}&pid_[0-9a-f]{4})", inst)
                    dedup_key = vid_pid_match.group(1) if vid_pid_match else title

                    if dedup_key not in seen_identifiers:
                        seen_identifiers.add(dedup_key)
                        scanners.append({
                            "name": title,
                            "device": "USB-HID",
                            "description": bdesc or fname or "USB-HID сканер штрихкодов",
                            "connect_type": "USB-HID",
                            "online": True,
                            "status_text": "Подключен (USB-HID)"
                        })

    except Exception as e:
        logger.debug("Ошибка поиска USB-HID сканеров: %s", e)

    return scanners


# ---------------------------------------------------------------------------
# 3. СЕТЕВОЙ МОНИТОРИНГ КОМПЬЮТЕРОВ СКЛАДА (PING / TCP CHECK)
# ---------------------------------------------------------------------------

def check_computer_online(ip_or_host: str, timeout_ms: int = 600) -> Tuple[bool, str, Optional[float]]:
    ip = (ip_or_host or "").strip()
    if not ip or ip == "—":
        return False, "IP-адрес не указан", None

    if ip.lower() in ("127.0.0.1", "localhost", socket.gethostname().lower()):
        return True, "Локальный узел (мастер)", 0.1

    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if platform.system() == "Windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
        else:
            timeout_sec = max(1, timeout_ms // 1000)
            cmd = ["ping", "-c", "1", "-W", str(timeout_sec), ip]

        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=2, creationflags=creationflags
        )
        if res.returncode == 0:
            latency = None
            m = re.search(r'(?:time|время)[=<]\s*(\d+)\s*ms', res.stdout, re.IGNORECASE)
            if m:
                latency = float(m.group(1))
            return True, f"В сети ({latency or '<1'} мс)", latency
    except Exception:
        pass

    for port in (445, 139, 80, 5000):
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
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Добавить компьютер склада по IP")
            self.setMinimumWidth(460)
            self.result_data = None
            self._init_ui()

        def _init_ui(self):
            layout = QVBoxLayout(self)
            layout.setSpacing(12)

            grp = QGroupBox("Параметры сетевого узла (ПК оператора)")
            form = QFormLayout(grp)

            self.txt_name = QLineEdit()
            self.txt_name.setPlaceholderText("Например: СКЛАД-ПК-02")

            self.txt_ip = QLineEdit()
            self.txt_ip.setPlaceholderText("Например: 192.168.1.55")

            self.txt_role = QLineEdit("Пост упаковки и маркировки")

            self.chk_sync = QCheckBox("Включить автоматический обмен кодами ЧЗ с этим ПК")
            self.chk_sync.setChecked(True)

            form.addRow("Имя компьютера:", self.txt_name)
            form.addRow("IP-адрес в сети:", self.txt_ip)
            form.addRow("Назначение / Роль:", self.txt_role)
            form.addRow("", self.chk_sync)
            layout.addWidget(grp)

            test_row = QHBoxLayout()
            self.btn_ping = QPushButton("📡 Проверить доступность по IP")
            self.btn_ping.clicked.connect(self._on_test_ping)
            self.lbl_ping_status = QLabel("Статус не проверен")
            self.lbl_ping_status.setStyleSheet("color: #888;")
            test_row.addWidget(self.btn_ping)
            test_row.addWidget(self.lbl_ping_status, 1)
            layout.addLayout(test_row)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            self.btn_save = QPushButton("💾 Добавить компьютер")
            self.btn_save.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 14px;")
            self.btn_save.clicked.connect(self._on_save)
            self.btn_cancel = QPushButton("Отмена")
            self.btn_cancel.clicked.connect(self.reject)
            btn_row.addWidget(self.btn_save)
            btn_row.addWidget(self.btn_cancel)
            layout.addLayout(btn_row)

        def _on_test_ping(self):
            ip = self.txt_ip.text().strip()
            if not ip:
                QMessageBox.warning(self, "Внимание", "Введите IP-адрес для проверки")
                return
            self.lbl_ping_status.setText("Проверка связи...")
            self.lbl_ping_status.setStyleSheet("color: #f59e0b;")
            online, msg, _ = check_computer_online(ip)
            if online:
                self.lbl_ping_status.setText(f"🟢 {msg}")
                self.lbl_ping_status.setStyleSheet("color: #22c55e; font-weight: bold;")
            else:
                self.lbl_ping_status.setText(f"🔴 {msg}")
                self.lbl_ping_status.setStyleSheet("color: #ef4444; font-weight: bold;")

        def _on_save(self):
            name = self.txt_name.text().strip()
            ip = self.txt_ip.text().strip()
            if not name or not ip:
                QMessageBox.warning(self, "Ошибка", "Заполните имя компьютера и его IP-адрес")
                return
            online, status_msg, _ = check_computer_online(ip)
            self.result_data = {
                "name": name,
                "ip": ip,
                "role": self.txt_role.text().strip() or "Пост упаковки",
                "sync_enabled": self.chk_sync.isChecked(),
                "online": online,
                "status_text": status_msg
            }
            self.accept()

except ImportError:
    pass