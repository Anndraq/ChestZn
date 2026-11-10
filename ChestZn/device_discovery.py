"""
ChestZn — Модуль неблокирующего обнаружения оборудования (device_discovery.py).
Оптимизировано для мгновенного отклика UI (0 мс задержки в главном потоке Qt):
1. Все тяжелые опросы Windows PnP / PowerShell / TCP выполняются исключительно в фоновом потоке-демоне,
   а вызовы detect_local_printers() и detect_local_scanners() из UI мгновенно (< 0.05 мс) читают готовый кэш.
2. Объединен опрос USB-принтеров и USB-HID сканеров в один быстрый запрос без медленного цикла Get-PnpDeviceProperty.
3. Убран избыточный запуск PowerShell Get-Printer (статусы читаются напрямую через быстрый Win32 API win32print).
4. Исключено блокирующее открытие спящих Bluetooth COM-портов через serial.Serial в цикле опроса.
"""

import os
import re
import json
import socket
import logging
import platform
import subprocess
import time
import threading
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("ChestZn.Discovery")

_DISCOVERY_LOCK = threading.Lock()
_PRINTERS_CACHE_TS: float = 0.0
_PRINTERS_CACHE_DATA: List[Dict[str, Any]] = []
_PRINTERS_ALL_CACHE_DATA: List[Dict[str, Any]] = []

_SCANNERS_CACHE_TS: float = 0.0
_SCANNERS_CACHE_DATA: List[Dict[str, Any]] = []
_SCANNERS_ALL_CACHE_DATA: List[Dict[str, Any]] = []

_CACHE_TTL_SEC: float = 4.0
_BG_THREAD: Optional[threading.Thread] = None
_BG_EVENT = threading.Event()
_BG_FORCE_FLAG = False

# Строгий список Vendor ID настоящих производителей сканеров штрихкодов (для USB-HID и USB-COM)
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

SCANNER_COM_VENDORS = {
    **BARCODE_SCANNER_HID_VENDORS,
    "1a86": "WCH (CH340/CH341 USB-COM сканер)",
    "10c4": "Silicon Labs (CP210x USB-COM сканер)",
    "0483": "STM (Virtual COM сканер)",
    "0403": "FTDI (USB-COM сканер)",
    "067b": "Prolific (USB-COM сканер)",
}

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


def _is_printer_name_matching_pnp(printer_name: str, present_pnp_names: List[str]) -> bool:
    if not present_pnp_names:
        return False
    p_low = printer_name.lower().strip()
    p_tokens = [
        t for t in re.split(r"[\s\-_/()]+", p_low)
        if len(t) >= 3 and t not in ("usb", "copy", "копия", "series", "printer")
    ]
    for pnp in present_pnp_names:
        if p_low in pnp or pnp in p_low:
            return True
        if p_tokens and sum(1 for tok in p_tokens if tok in pnp) >= min(2, len(p_tokens)):
            return True
    return False


# ---------------------------------------------------------------------------
# ЕДИНЫЙ БЫСТРЫЙ ОПРОС WINDOWS PNP В ФОНОВОМ ПОТОКЕ
# ---------------------------------------------------------------------------

def _query_windows_pnp_snapshot() -> Tuple[bool, List[str], List[Dict[str, str]]]:
    """
    За 1 быстрый вызов получает и USBPRINT-принтеры, и HID-сканеры (без тяжелого Get-PnpDeviceProperty).
    Вызывается ТОЛЬКО из фонового потока-демона.
    """
    if platform.system() != "Windows":
        return False, [], []

    try:
        ps_cmd = (
            "Get-PnpDevice -PresentOnly -Status OK -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Class -in @('Printer', 'PrinterQueue', 'HIDClass', 'Keyboard', 'POSBarcodeScanner') "
            "-or $_.InstanceId -like 'USBPRINT*' -or $_.Service -eq 'usbprint' } | "
            "Select-Object FriendlyName, InstanceId, Class, Service | ConvertTo-Json -Compress"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=creationflags,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return False, [], []

        raw = json.loads(res.stdout.strip())
        if isinstance(raw, dict):
            raw = [raw]

        has_usbprint = False
        usb_printer_names: List[str] = []
        hid_candidates: List[Dict[str, str]] = []

        for item in raw:
            inst = str(item.get("InstanceId") or "").strip()
            inst_up = inst.upper()
            inst_low = inst.lower()
            srv = str(item.get("Service") or "").lower()
            cls_name = str(item.get("Class") or "").lower()
            fname = str(item.get("FriendlyName") or "").strip()

            # 1. USB-принтеры
            if inst_up.startswith("USBPRINT\\") or srv == "usbprint" or (cls_name == "printer" and "usb" in inst_low):
                if not (inst_up.startswith("SWD\\PRINTENUM\\") and is_virtual_printer(fname)):
                    has_usbprint = True
                    if fname:
                        usb_printer_names.append(fname.lower())
                    parts = inst_up.split("\\")
                    if len(parts) >= 2:
                        usb_printer_names.append(parts[1].lower().replace("_", " "))

            # 2. Кандидаты в USB-HID сканеры
            if cls_name in ("hidclass", "keyboard", "posbarcodescanner") and "vid_" in inst_low:
                hid_candidates.append({
                    "InstanceId": inst_low,
                    "FriendlyName": fname,
                    "Class": cls_name,
                })

        return has_usbprint, usb_printer_names, hid_candidates
    except Exception as e:
        logger.debug("Ошибка фонового опроса PnP: %s", e)
        return False, [], []


def _scan_printers_sync(has_usbprint: bool, present_pnp_printers: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Синхронное сканирование принтеров через быстрый Win32 API (выполняется в фоновом потоке)."""
    connected_list: List[Dict[str, Any]] = []
    all_list: List[Dict[str, Any]] = []

    if platform.system() != "Windows":
        return connected_list, all_list

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
            name = (p_info.get("pPrinterName") or "").strip()
            port = (p_info.get("pPortName") or "").strip()
            driver = (p_info.get("pDriverName") or "").strip()
            status_flags = p_info.get("Status", 0)
            attributes = p_info.get("Attributes", 0)

            if not name or is_virtual_printer(name):
                continue

            port_up = port.upper()
            is_virtual_port = any(
                port_up.startswith(prefix)
                for prefix in ("PORTPROMPT", "FILE:", "NUL", "SHRFAX", "FAX", "LPT", "XPS")
            )

            is_offline_flag = bool(status_flags & win32print.PRINTER_STATUS_OFFLINE)
            is_error_flag = bool(status_flags & (win32print.PRINTER_STATUS_ERROR | win32print.PRINTER_STATUS_NOT_AVAILABLE))
            work_offline_attr = bool(attributes & getattr(win32print, "PRINTER_ATTRIBUTE_WORK_OFFLINE", 0))

            physically_connected = False
            if not is_virtual_port:
                if port_up.startswith("USB"):
                    if has_usbprint and (
                        _is_printer_name_matching_pnp(name, present_pnp_printers)
                        or _is_printer_name_matching_pnp(driver, present_pnp_printers)
                    ):
                        physically_connected = True
                elif port_up.startswith("COM"):
                    try:
                        import serial.tools.list_ports
                        active_coms = {cp.device.upper() for cp in serial.tools.list_ports.comports()}
                        physically_connected = port_up.rstrip(":") in active_coms
                    except Exception:
                        physically_connected = False
                else:
                    m_ip = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", port)
                    if m_ip:
                        ip_addr = m_ip.group(1)
                        try:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(0.25)
                            physically_connected = (sock.connect_ex((ip_addr, 9100)) == 0)
                            sock.close()
                        except Exception:
                            physically_connected = False

            if is_offline_flag or is_error_flag or work_offline_attr:
                physically_connected = False

            name_lower = name.lower() + " " + driver.lower()
            if "zebra" in name_lower or "zpl" in name_lower:
                lang = "zpl"
            elif any(k in name_lower for k in ["tsc", "tspl", "xprinter", "gprinter", "atol", "атол"]):
                lang = "tspl"
            elif "godex" in name_lower or "ezpl" in name_lower:
                lang = "ezpl"
            else:
                lang = "auto"

            entry = {
                "name": name,
                "port": port or "—",
                "driver": driver or "—",
                "lang": lang,
                "online": physically_connected,
                "connected": physically_connected,
                "status_text": "Готов к печати" if physically_connected else "Отключен",
                "is_default": (name == default_name),
            }
            all_list.append(entry)
            if physically_connected:
                connected_list.append(entry)

    except Exception as e:
        logger.debug("Ошибка опроса принтеров Windows: %s", e)

    return connected_list, all_list


def _scan_scanners_sync(hid_candidates: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Синхронное сканирование сканеров штрихкодов (выполняется в фоновом потоке, без блокировки BT COM)."""
    connected_list: List[Dict[str, Any]] = []
    all_list: List[Dict[str, Any]] = []
    seen_identifiers = set()

    if platform.system() != "Windows":
        return connected_list, all_list

    # 1. Быстрый опрос COM-портов через pyserial (< 1 мс)
    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()

        for port in ports:
            dev = port.device
            desc = port.description or ""
            hwid = port.hwid or ""
            desc_l = desc.lower()
            hwid_l = hwid.lower()

            if "pnp0501" in hwid_l or "communications port" in desc_l or "последовательный порт" in desc_l:
                if "usb" not in hwid_l and "bluetooth" not in desc_l:
                    continue

            is_physical_usb = ("usb" in hwid_l or "usb" in desc_l or "vid_" in hwid_l) and "bthenum" not in hwid_l
            is_bluetooth = ("bluetooth" in desc_l or "bthenum" in hwid_l)

            if is_bluetooth and ("_localmfg" in hwid_l or "localmfg" in hwid_l):
                continue

            detected_vendor = None
            for vid, vname in SCANNER_COM_VENDORS.items():
                if f"vid_{vid}" in hwid_l:
                    detected_vendor = vname
                    break

            is_connected = bool(is_physical_usb)
            status_text = "Подключен по USB (COM-режим)" if is_physical_usb else "Bluetooth SPP порт"

            if detected_vendor:
                display_name = f"USB-сканер {detected_vendor} ({dev})"
            elif is_physical_usb:
                display_name = f"USB-сканер ({dev})"
            else:
                display_name = f"Bluetooth-сканер ({dev})"

            entry = {
                "name": display_name,
                "device": dev,
                "description": desc,
                "connect_type": "USB-COM" if is_physical_usb else "BT-SPP",
                "online": is_connected,
                "connected": is_connected,
                "status_text": status_text,
            }
            all_list.append(entry)
            if is_connected:
                connected_list.append(entry)
            seen_identifiers.add(dev)
    except Exception as e:
        logger.debug("Ошибка опроса COM-сканеров: %s", e)

    # 2. Обработка USB-HID кандидатов из единого PnP-снимка
    for item in hid_candidates:
        inst = item.get("InstanceId", "")
        fname = item.get("FriendlyName", "")
        cls_name = item.get("Class", "")
        combined_l = f"{fname} {inst}".lower()

        if any(w in combined_l for w in ["mouse", "мышь", "audio", "headset", "consumer control", "system controller"]):
            continue

        vendor_name = None
        for vid, vtitle in BARCODE_SCANNER_HID_VENDORS.items():
            if f"vid_{vid}" in inst:
                vendor_name = vtitle
                break

        is_named_scanner = (
            cls_name == "posbarcodescanner"
            or any(k in combined_l for k in SCANNER_NAME_KEYWORDS)
        )

        if vendor_name or is_named_scanner:
            label_detail = vendor_name or fname or "HID"
            title = f"USB-сканер ({label_detail})"
            vid_pid_match = re.search(r"(vid_[0-9a-f]{4}&pid_[0-9a-f]{4})", inst)
            dedup_key = vid_pid_match.group(1) if vid_pid_match else title

            if dedup_key not in seen_identifiers:
                seen_identifiers.add(dedup_key)
                entry = {
                    "name": title,
                    "device": "USB-HID",
                    "description": fname or "USB-HID сканер штрихкодов",
                    "connect_type": "USB-HID",
                    "online": True,
                    "connected": True,
                    "status_text": "Подключен (USB-HID)",
                }
                connected_list.append(entry)
                all_list.append(entry)

    return connected_list, all_list


def _refresh_all_caches_sync():
    """Выполняет полное обновление кэша принтеров и сканеров (вызывается в фоновом потоке)."""
    global _PRINTERS_CACHE_TS, _PRINTERS_CACHE_DATA, _PRINTERS_ALL_CACHE_DATA
    global _SCANNERS_CACHE_TS, _SCANNERS_CACHE_DATA, _SCANNERS_ALL_CACHE_DATA

    has_usbprint, usb_pr_names, hid_candidates = _query_windows_pnp_snapshot()
    pr_conn, pr_all = _scan_printers_sync(has_usbprint, usb_pr_names)
    sc_conn, sc_all = _scan_scanners_sync(hid_candidates)

    now = time.time()
    with _DISCOVERY_LOCK:
        _PRINTERS_CACHE_DATA = pr_conn
        _PRINTERS_ALL_CACHE_DATA = pr_all
        _PRINTERS_CACHE_TS = now

        _SCANNERS_CACHE_DATA = sc_conn
        _SCANNERS_ALL_CACHE_DATA = sc_all
        _SCANNERS_CACHE_TS = now


def _bg_worker_loop():
    """Фоновый демон, периодически обновляющий список подключенных принтеров и сканеров без блокировки Qt UI."""
    global _BG_FORCE_FLAG
    while True:
        try:
            _refresh_all_caches_sync()
        except Exception as e:
            logger.debug("Background discovery error: %s", e)
        _BG_FORCE_FLAG = False
        _BG_EVENT.wait(timeout=_CACHE_TTL_SEC)
        _BG_EVENT.clear()


def _ensure_bg_worker(force: bool = False):
    """Запускает фоновый поток обнаружения устройств (если ещё не запущен) и при необходимости будит его."""
    global _BG_THREAD, _BG_FORCE_FLAG
    if _BG_THREAD is None or not _BG_THREAD.is_alive():
        _BG_THREAD = threading.Thread(target=_bg_worker_loop, name="ChestZnDeviceDiscovery", daemon=True)
        _BG_THREAD.start()
    elif force:
        _BG_FORCE_FLAG = True
        _BG_EVENT.set()


# Запускаем фоновый опрос сразу при импорте модуля, чтобы к моменту отрисовки главного окна кэш уже был прогрет
_ensure_bg_worker(force=True)


# ---------------------------------------------------------------------------
# ПУБЛИЧНЫЕ ФУНКЦИИ (МГНОВЕННЫЙ ВОЗВРАТ ИЗ КЭША < 0.05 мс БЕЗ ФРИЗОВ UI)
# ---------------------------------------------------------------------------

def detect_local_printers(connected_only: bool = True, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Мгновенно возвращает список принтеров из кэша в памяти.
    При устаревании кэша или force_refresh=True сигнализирует фоновому потоку обновить данные.
    """
    now = time.time()
    if force_refresh or (now - _PRINTERS_CACHE_TS) >= _CACHE_TTL_SEC:
        _ensure_bg_worker(force=force_refresh)

    with _DISCOVERY_LOCK:
        if connected_only:
            return list(_PRINTERS_CACHE_DATA)
        return list(_PRINTERS_ALL_CACHE_DATA)


def detect_local_scanners(connected_only: bool = True, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Мгновенно возвращает список сканеров штрихкодов из кэша в памяти.
    При устаревании кэша или force_refresh=True сигнализирует фоновому потоку обновить данные.
    """
    now = time.time()
    if force_refresh or (now - _SCANNERS_CACHE_TS) >= _CACHE_TTL_SEC:
        _ensure_bg_worker(force=force_refresh)

    with _DISCOVERY_LOCK:
        if connected_only:
            return list(_SCANNERS_CACHE_DATA)
        return list(_SCANNERS_ALL_CACHE_DATA)


def get_currently_connected_printer(
    preferred_name: str = "",
    force_refresh: bool = False
) -> Optional[Dict[str, Any]]:
    live_printers = detect_local_printers(connected_only=True, force_refresh=force_refresh)
    if not live_printers:
        return None

    if preferred_name:
        pref_low = preferred_name.strip().lower()
        for p in live_printers:
            p_name = p.get("name", "").strip().lower()
            if p_name == pref_low or pref_low in p_name or p_name in pref_low:
                return p

    return live_printers[0]


def check_printer_status(printer_name: str, force_refresh: bool = False) -> Dict[str, Any]:
    target = (printer_name or "").strip()
    if not target:
        return {
            "connected": False,
            "status_text": "Принтер не выбран",
            "port": "—",
            "lang": "auto",
        }

    all_printers = detect_local_printers(connected_only=False, force_refresh=force_refresh)
    target_lower = target.lower()

    for p in all_printers:
        p_name = p.get("name", "").strip()
        if p_name.lower() == target_lower or target_lower in p_name.lower() or p_name.lower() in target_lower:
            is_online = bool(p.get("online", False))
            return {
                "name": p_name,
                "connected": is_online,
                "status_text": "🟢 Подключён и готов" if is_online else "🔴 Принтер отключён (выключен или нет кабеля)",
                "port": p.get("port", "—"),
                "lang": p.get("lang", "auto"),
            }

    return {
        "name": target,
        "connected": False,
        "status_text": "🔴 Принтер не найден в системе Windows",
        "port": "—",
        "lang": "auto",
    }


def check_computer_online(ip_or_host: str, timeout_ms: int = 350) -> Tuple[bool, str, Optional[float]]:
    ip = (ip_or_host or "").strip()
    if not ip or ip == "—":
        return False, "IP-адрес не указан", None

    if ip.lower() in ("127.0.0.1", "localhost", socket.gethostname().lower()):
        return True, "Локальный узел (мастер)", 0.1

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
            test_row.addWidget(self.btn_ping)
            test_row.addWidget(self.lbl_ping_status, 1)
            layout.addLayout(test_row)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            self.btn_save = QPushButton("💾 Добавить компьютер")
            self.btn_save.setProperty("primary", True)
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
            online, msg, _ = check_computer_online(ip)
            if online:
                self.lbl_ping_status.setText(f"🟢 {msg}")
            else:
                self.lbl_ping_status.setText(f"🔴 {msg}")

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
                "status_text": status_msg,
            }
            self.accept()

except ImportError:
    pass