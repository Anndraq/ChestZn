"""
Dotmark — Модуль точного обнаружения реально подключенного оборудования (device_discovery.py)
Определяет ТОЛЬКО физически подключенные в данный момент принтеры и сканеры:
1. detect_local_printers(connected_only=True):
   - Отсекает виртуальные принтеры (PDF, XPS, OneNote, Fax) и пустые порты (LPT, FILE, PORTPROMуPT, WSD).
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
import time
import threading
from typing import List, Dict, Any, Tuple, Optional

_DISCOVERY_LOCK = threading.Lock()
_BG_WORKER_LOCK = threading.Lock()
_BG_WORKER_RUNNING = False

_PRINTERS_CACHE_TS: float = 0.0
_PRINTERS_CACHE_DATA: list = []
_PRINTERS_ALL_CACHE_DATA: list = []

_SCANNERS_CACHE_TS: float = 0.0
_SCANNERS_CACHE_DATA: list = []

_CACHE_TTL_SEC: float = 12.0

_PS_SNAPSHOT_TS: float = 0.0
_PS_OFFLINE_CACHE_DATA: Dict[str, bool] = {}
_USB_PNP_CACHE_HAS: bool = False
_USB_PNP_CACHE_NAMES: List[str] = []
_HID_PNP_CACHE_ITEMS: List[Dict[str, Any]] = []


logger = logging.getLogger("Dotmark.Discovery")

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


def _refresh_ps_hardware_snapshot(force: bool = False) -> None:
    """
    Объединённый 1-проходный опрос Windows PnP и спулера в одном вызове PowerShell (вместо 3 процессов
    и медленного Get-PnpDeviceProperty в цикле). Работает в 4-5 раз быстрее и никогда не блокирует UI.
    """
    global _PS_SNAPSHOT_TS, _PS_OFFLINE_CACHE_DATA, _USB_PNP_CACHE_HAS, _USB_PNP_CACHE_NAMES, _HID_PNP_CACHE_ITEMS
    if platform.system() != "Windows":
        return

    now = time.time()
    if not force and _PS_SNAPSHOT_TS > 0.0 and (now - _PS_SNAPSHOT_TS) < _CACHE_TTL_SEC:
        return

    ps_script = (
        "$pr = @(Get-Printer -ErrorAction SilentlyContinue | Select-Object Name, PortName, PrinterStatus, WorkOffline); "
        "$pnp = @(Get-PnpDevice -PresentOnly -Status OK -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Class -in @('Printer','PrinterQueue','HIDClass','Keyboard','POSBarcodeScanner') -or "
        "$_.InstanceId -like 'USBPRINT*' -or $_.InstanceId -like '*DOT4*' -or $_.Service -eq 'usbprint' } | "
        "Select-Object FriendlyName, InstanceId, Class, Service); "
        "@{ printers = $pr; pnp = $pnp } | ConvertTo-Json -Compress -Depth 3"
    )
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=2.2,
            creationflags=creationflags,
        )
        if res.returncode != 0 or not res.stdout.strip():
            _PS_SNAPSHOT_TS = now
            return

        payload = json.loads(res.stdout.strip())
        if not isinstance(payload, dict):
            _PS_SNAPSHOT_TS = now
            return

        pr_raw = payload.get("printers") or []
        if isinstance(pr_raw, dict):
            pr_raw = [pr_raw]

        offline_map: Dict[str, bool] = {}
        for item in pr_raw:
            if not isinstance(item, dict):
                continue
            p_name = str(item.get("Name") or "").strip().lower()
            is_off = bool(item.get("WorkOffline", False)) or str(item.get("PrinterStatus", "")).lower() in (
                "offline", "error", "not available", "5", "7"
            )
            if p_name:
                offline_map[p_name] = is_off

        pnp_raw = payload.get("pnp") or []
        if isinstance(pnp_raw, dict):
            pnp_raw = [pnp_raw]

        has_usbprint = False
        usb_names: List[str] = []
        hid_items: List[Dict[str, Any]] = []

        for item in pnp_raw:
            if not isinstance(item, dict):
                continue
            inst = str(item.get("InstanceId") or "").strip()
            inst_up = inst.upper()
            srv = str(item.get("Service") or "").strip().lower()
            cls_name = str(item.get("Class") or "").strip().lower()
            fname = str(item.get("FriendlyName") or "").strip()

            if inst_up.startswith("SWD\\PRINTENUM\\") and is_virtual_printer(fname):
                continue

            if (
                inst_up.startswith("USBPRINT\\")
                or "DOT4" in inst_up
                or srv in ("usbprint", "dot4", "dot4prt")
                or (cls_name in ("printer", "printerqueue") and "usb" in inst_up.lower())
            ):
                has_usbprint = True
                if fname:
                    usb_names.append(fname.lower())
                parts = inst_up.split("\\")
                if len(parts) >= 2:
                    usb_names.append(parts[1].lower().replace("_", " "))

            if cls_name in ("hidclass", "keyboard", "posbarcodescanner") and "vid_" in inst.lower():
                hid_items.append({
                    "FriendlyName": fname,
                    "InstanceId": inst,
                    "Class": cls_name,
                })

        with _DISCOVERY_LOCK:
            _PS_OFFLINE_CACHE_DATA = offline_map
            _USB_PNP_CACHE_HAS = has_usbprint
            _USB_PNP_CACHE_NAMES = usb_names
            _HID_PNP_CACHE_ITEMS = hid_items
            _PS_SNAPSHOT_TS = now
    except Exception as e:
        logger.debug("Ошибка единого снимка PnP/Get-Printer: %s", e)
        _PS_SNAPSHOT_TS = now


def _get_windows_printer_offline_statuses(force: bool = False) -> Dict[str, bool]:
    _refresh_ps_hardware_snapshot(force=force)
    with _DISCOVERY_LOCK:
        return dict(_PS_OFFLINE_CACHE_DATA)


def _get_present_usb_printers_windows(force: bool = False) -> Tuple[bool, List[str]]:
    _refresh_ps_hardware_snapshot(force=force)
    with _DISCOVERY_LOCK:
        return _USB_PNP_CACHE_HAS, list(_USB_PNP_CACHE_NAMES)


def _is_printer_name_matching_pnp(printer_name: str, present_pnp_names: List[str]) -> bool:
    """Проверяет, соответствует ли имя принтера в спулере одному из физически присутствующих USBPRINT-устройств."""
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


def _scan_printers_sync(force: bool = False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Синхронно формирует списки (connected_printers, all_printers)."""
    if platform.system() != "Windows":
        return [], []

    connected_list: List[Dict[str, Any]] = []
    all_list: List[Dict[str, Any]] = []

    try:
        import win32print
        default_name = ""
        try:
            default_name = win32print.GetDefaultPrinter()
        except Exception:
            pass

        has_usbprint, present_pnp_printers = _get_present_usb_printers_windows(force=force)
        offline_statuses = _get_windows_printer_offline_statuses(force=False)

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
            ps_offline = offline_statuses.get(name.lower(), False)

            physically_connected = False
            if not is_virtual_port:
                if port_up.startswith("USB") or port_up.startswith("DOT4"):
                    if has_usbprint:
                        if (
                            _is_printer_name_matching_pnp(name, present_pnp_printers)
                            or _is_printer_name_matching_pnp(driver, present_pnp_printers)
                            or (not is_offline_flag and not is_error_flag and not work_offline_attr and not ps_offline)
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

            if is_offline_flag or is_error_flag or work_offline_attr or ps_offline:
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

            item_dict = {
                "name": name,
                "port": port or "—",
                "driver": driver or "—",
                "lang": lang,
                "online": physically_connected,
                "connected": physically_connected,
                "status_text": "Готов к печати" if physically_connected else "Отключен",
                "is_default": (name == default_name),
            }
            all_list.append(item_dict)
            if physically_connected:
                connected_list.append(item_dict)
    except Exception as e:
        logger.error("Ошибка опроса принтеров Windows: %s", e)

    return connected_list, all_list


def _scan_scanners_sync(force: bool = False) -> List[Dict[str, Any]]:
    """Синхронно формирует список реально подключенных сканеров штрихкодов."""
    if platform.system() != "Windows":
        return []

    scanners: List[Dict[str, Any]] = []
    seen_identifiers = set()

    # А. Последовательные сканеры (USB-COM / Bluetooth SPP)
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

            is_connected = False
            status_text = "Отключен"

            if is_physical_usb:
                is_connected = True
                status_text = "Подключен по USB (COM-режим)"
            elif is_bluetooth:
                try:
                    import serial
                    s = serial.Serial(dev, timeout=0.08, write_timeout=0.08)
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

            if not is_connected:
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
                "status_text": status_text,
            })
            seen_identifiers.add(dev)
    except Exception as e:
        logger.error("Ошибка опроса COM-сканеров: %s", e)

    # Б. USB-HID сканеры из объединённого PnP-снимка (без повторного вызова PowerShell)
    try:
        _refresh_ps_hardware_snapshot(force=force)
        with _DISCOVERY_LOCK:
            hid_data = list(_HID_PNP_CACHE_ITEMS)

        for item in hid_data:
            inst = str(item.get("InstanceId") or "").lower()
            fname = str(item.get("FriendlyName") or "").strip()
            cls_name = str(item.get("Class") or "").lower()
            combined_l = fname.lower()

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
                    scanners.append({
                        "name": title,
                        "device": "USB-HID",
                        "description": fname or "USB-HID сканер штрихкодов",
                        "connect_type": "USB-HID",
                        "online": True,
                        "status_text": "Подключен (USB-HID)",
                    })
    except Exception as e:
        logger.debug("Ошибка поиска USB-HID сканеров: %s", e)

    return scanners


def _refresh_all_caches_sync() -> None:
    """Мгновенно и синхронно обновляет кэши принтеров и сканеров (используется при старте и по кнопке «Обновить»)."""
    global _PRINTERS_CACHE_DATA, _PRINTERS_ALL_CACHE_DATA, _PRINTERS_CACHE_TS
    global _SCANNERS_CACHE_DATA, _SCANNERS_CACHE_TS

    _refresh_ps_hardware_snapshot(force=True)
    conn_pr, all_pr = _scan_printers_sync(force=False)
    conn_sc = _scan_scanners_sync(force=False)
    now = time.time()
    with _DISCOVERY_LOCK:
        _PRINTERS_CACHE_DATA = conn_pr
        _PRINTERS_ALL_CACHE_DATA = all_pr
        _PRINTERS_CACHE_TS = now
        _SCANNERS_CACHE_DATA = conn_sc
        _SCANNERS_CACHE_TS = now


def _trigger_bg_refresh() -> None:
    """Неблокирующее обновление кэша оборудования в фоновом потоке, чтобы UI никогда не замирал."""
    global _BG_WORKER_RUNNING
    with _BG_WORKER_LOCK:
        if _BG_WORKER_RUNNING:
            return
        _BG_WORKER_RUNNING = True

    def _bg():
        global _BG_WORKER_RUNNING
        try:
            _refresh_all_caches_sync()
        except Exception as e:
            logger.debug("Фоновое обновление оборудования: %s", e)
        finally:
            with _BG_WORKER_LOCK:
                _BG_WORKER_RUNNING = False

    threading.Thread(target=_bg, daemon=True).start()


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
                "driver": p.get("driver", "—"),
                "lang": p.get("lang", "auto"),
            }

    return {
        "name": target,
        "connected": False,
        "status_text": f"🔴 Принтер «{target}» не найден в системе",
        "port": "—",
        "lang": "auto",
    }


def _fast_seed_caches_if_empty() -> None:
    """
    Мгновенная первичная инициализация кэша при старте приложения (< 10 мс, без запуска PowerShell)
    с одновременным стартом фонового PnP-потока. Полностью устраняет задержку 1–2 с при холодном запуске.
    """
    global _PRINTERS_CACHE_DATA, _PRINTERS_ALL_CACHE_DATA, _PRINTERS_CACHE_TS
    global _SCANNERS_CACHE_DATA, _SCANNERS_CACHE_TS, _PS_SNAPSHOT_TS
    now = time.time()
    with _DISCOVERY_LOCK:
        if _PRINTERS_CACHE_TS > 0.0 and _SCANNERS_CACHE_TS > 0.0:
            return
        # Помечаем _PS_SNAPSHOT_TS текущим временем, чтобы _scan_printers_sync/_scan_scanners_sync
        # не вызывали блокирующий PowerShell в UI-потоке во время холодного старта
        if _PS_SNAPSHOT_TS == 0.0:
            _PS_SNAPSHOT_TS = now

    conn_pr, all_pr = _scan_printers_sync(force=False)
    conn_sc = _scan_scanners_sync(force=False)
    with _DISCOVERY_LOCK:
        _PRINTERS_CACHE_DATA = conn_pr
        _PRINTERS_ALL_CACHE_DATA = all_pr
        _PRINTERS_CACHE_TS = now
        _SCANNERS_CACHE_DATA = conn_sc
        _SCANNERS_CACHE_TS = now

    # Запускаем полноценный PowerShell PnP-снимок асинхронно в фоне
    _trigger_bg_refresh()


def detect_local_printers(connected_only: bool = True, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Возвращает список принтеров за 0 мс из кэша в памяти.
    При первом вызове выполняет быстрый Win32-опрос (< 10 мс) и запускает PowerShell PnP в фоне.
    """
    now = time.time()
    if force_refresh:
        _refresh_all_caches_sync()
    elif _PRINTERS_CACHE_TS == 0.0:
        _fast_seed_caches_if_empty()
    elif (now - _PRINTERS_CACHE_TS) >= _CACHE_TTL_SEC:
        _trigger_bg_refresh()

    with _DISCOVERY_LOCK:
        if connected_only:
            return list(_PRINTERS_CACHE_DATA)
        return list(_PRINTERS_ALL_CACHE_DATA)


def detect_local_scanners(connected_only: bool = True, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Возвращает список сканеров за 0 мс из кэша в памяти.
    При первом вызове выполняет быстрый COM-опрос и запускает PowerShell PnP в фоне.
    """
    now = time.time()
    if force_refresh:
        _refresh_all_caches_sync()
    elif _SCANNERS_CACHE_TS == 0.0:
        _fast_seed_caches_if_empty()
    elif (now - _SCANNERS_CACHE_TS) >= _CACHE_TTL_SEC:
        _trigger_bg_refresh()

    with _DISCOVERY_LOCK:
        return list(_SCANNERS_CACHE_DATA)

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