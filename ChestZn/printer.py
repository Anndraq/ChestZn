"""
Dotmark — Модуль управления печатью и динамического определения принтеров (printer.py)

Ключевая логика:
1. НЕ запоминает отключённый принтер: если ранее настроенный принтер физически выключен
   или отсоединён (например, Xerox WorkCentre 3215 в статусе «Отключен»), система
   не рапортует зелёным «всё готово».
2. Динамически подтягивает название реально подключенного в данный момент принтера:
   - Если к ПК подключён термопринтер (TSC, Godex, Xprinter, Zebra) или включён офисный принтер —
     автоматически подставляет его имя в точку и даёт зелёный статус «🟢 [Имя принтера]».
   - Если в данный момент ни один принтер физически не подключён к ПК — возвращает
     статус «🔴 Принтер отключён» и блокирует печать с понятным сообщением об ошибке.
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from logger import get_logger
log = get_logger("printer")

# Файл конфигурации принтеров в %APPDATA%\ChestZn\printer_config.json
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
CONFIG_FILE = CONFIG_DIR / "printer_config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "printer_name": "",
    "printer_ip": "",
    "printer_port": 9100,
    "printer_lang": "auto",
    "label_width_mm": 58,
    "label_height_mm": 40,
    "dpi": 203,
    "copies": 1,
    "printers": {},
}


def load_config() -> Dict[str, Any]:
    """Загрузить общую конфигурацию принтеров из JSON."""
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            merged = dict(DEFAULT_CONFIG)
            merged.update(data)
            return merged
    except Exception as e:
        log.error("Ошибка загрузки %s: %s", CONFIG_FILE, e)
        return dict(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> bool:
    """Сохранить конфигурацию принтеров в JSON."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log.error("Ошибка сохранения %s: %s", CONFIG_FILE, e)
        return False


def detect_point(cfg: Optional[Dict[str, Any]] = None, store=None, *args, **kwargs) -> str:
    """
    Определяет идентификатор текущей рабочей точки (например, 'T-01'):
    1. Из настроек store (current_point, worker_point_id).
    2. Из переданного или загруженного словаря cfg.
    3. По имени хоста / компьютера в сети.
    4. Если не определено — возвращает 'T-01' по умолчанию.
    """
    if store is not None:
        try:
            pt = getattr(store, "current_point", None)
            if pt and str(pt).strip():
                return str(pt).strip()
            if hasattr(store, "get_setting"):
                val = store.get_setting("worker_point_id") or store.get_setting("current_point")
                if val and str(val).strip():
                    return str(val).strip()
        except Exception:
            pass

    if cfg is None:
        cfg = load_config()

    if isinstance(cfg, dict):
        for k in ("current_point", "point", "point_id", "station_id"):
            val = cfg.get(k)
            if val and str(val).strip():
                return str(val).strip()

    import socket
    try:
        host = socket.gethostname().upper()
        import re
        m = re.search(r"(?:T|P|POST|POINT)[-_]?(\d{1,2})", host)
        if m:
            return f"T-{int(m.group(1)):02d}"
    except Exception:
        pass

    return "T-01"


def autodetect(cfg: Optional[Dict[str, Any]] = None, save: bool = False, *args, **kwargs) -> Dict[str, Any]:
    """
    Автоматическое обнаружение физически подключенного принтера и точки:
    1. Опрашивает device_discovery на наличие живого принтера.
    2. Если принтер подключён — возвращает suggested=имя_принтера, ok=True.
    3. Если ни один принтер не подключён — возвращает suggested="", ok=False.
    4. Предоставляет все ключи, ожидаемые main.py (suggested, printer, point, ok, connected).
    """
    if cfg is None:
        cfg = load_config()

    res = dict(cfg or {})
    curr_point = detect_point(res)
    res["point"] = curr_point

    try:
        from device_discovery import get_currently_connected_printer
        live = get_currently_connected_printer(force_refresh=False)
        if live and live.get("connected"):
            pr_name = live["name"]
            pr_lang = live.get("lang") or detect_language(pr_name)
            res["suggested"] = pr_name
            res["printer"] = pr_name
            res["printer_name"] = pr_name
            res["name"] = pr_name
            res["printer_lang"] = pr_lang
            res["lang"] = pr_lang
            res["connected"] = True
            res["online"] = True
            res["ok"] = True
            res["status_text"] = f"🟢 {pr_name}"
            if save:
                save_config(res)
        else:
            res["suggested"] = ""
            res["printer"] = ""
            res["printer_name"] = ""
            res["name"] = ""
            res["connected"] = False
            res["online"] = False
            res["ok"] = False
            res["status_text"] = "🔴 Принтер отключён"
    except Exception as e:
        log.debug("autodetect: %s", e)
        res["suggested"] = ""
        res["printer"] = ""
        res["printer_name"] = ""
        res["name"] = ""
        res["connected"] = False
        res["online"] = False
        res["ok"] = False
        res["status_text"] = "🔴 Принтер отключён"

    # Гарантируем наличие всех возможных полей для совместимости с main.py
    res.setdefault("suggested", "")
    res.setdefault("printer", "")
    res.setdefault("printer_name", "")
    res.setdefault("ok", False)
    res.setdefault("connected", False)
    return res


# Алиасы для полной обратной совместимости с main.py
autodetect_printer = autodetect
detect_printer = autodetect


def detect_language(name: str) -> str:
    """Определить язык управления принтером (zpl, tspl, ezpl, windows)."""
    low = (name or "").lower()
    if any(k in low for k in ["zebra", "zpl", "zd420", "zd421", "zt230", "zt410"]):
        return "zpl"
    if any(k in low for k in ["tsc", "tspl", "xprinter", "gprinter", "te200", "te210", "da200", "atol", "атол"]):
        return "tspl"
    if any(k in low for k in ["godex", "ezpl", "g500", "g300", "rt700"]):
        return "ezpl"
    return "windows"


def list_printers() -> List[Dict[str, Any]]:
    """Список принтеров с актуальным статусом физического подключения."""
    try:
        from device_discovery import detect_local_printers
        return detect_local_printers(connected_only=False, force_refresh=False)
    except Exception:
        import platform
        if platform.system() == "Windows":
            try:
                import win32print
                res = []
                flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
                for p in win32print.EnumPrinters(flags, None, 2):
                    res.append({
                        "name": p.get("pPrinterName", ""),
                        "port": p.get("pPortName", ""),
                        "online": False,
                        "status_text": "Статус неизвестен",
                    })
                return res
            except Exception:
                pass
        return []


def default_printer() -> str:
    """Вернуть имя принтера по умолчанию в Windows."""
    try:
        import win32print
        return win32print.GetDefaultPrinter() or ""
    except Exception:
        return ""


def get_printer_for_point(cfg: Optional[Dict[str, Any]], point: str, *args, **kwargs) -> Dict[str, Any]:
    """
    Определяет принтер для точки с ЖИВОЙ ПРОВЕРКОЙ ФИЗИЧЕСКОГО ПОДКЛЮЧЕНИЯ:
    1. Не запоминает отключённый принтер: если настроенный принтер (например, Xerox)
       в Windows отключён, функция НЕ рапортует, что всё готово.
    2. Автоматически подтягивает название РЕАЛЬНО ПОДКЛЮЧЕННОГО принтера:
       - Если подключён TSC, Xprinter, Zebra или Xerox включён — подставляет его имя
         и возвращает connected=True.
       - Если ни один принтер не подключён — возвращает connected=False, printer_name="",
         status_text="🔴 Принтер отключён".
    """
    if cfg is None:
        cfg = load_config()

    saved_point_pr = ""
    point_settings = cfg.get("printers", {}).get(point, {})
    if isinstance(point_settings, dict):
        saved_point_pr = point_settings.get("printer_name", "").strip()
    if not saved_point_pr:
        saved_point_pr = cfg.get("printer_name", "").strip()

    # Опрашиваем реальное подключение принтеров через device_discovery
    connected_info: Optional[Dict[str, Any]] = None
    try:
        from device_discovery import get_currently_connected_printer
        connected_info = get_currently_connected_printer(
            preferred_name=saved_point_pr,
            force_refresh=False
        )
    except Exception as e:
        log.debug("Ошибка get_currently_connected_printer: %s", e)

    res = dict(cfg)
    if isinstance(point_settings, dict):
        res.update(point_settings)

    if connected_info and connected_info.get("connected"):
        live_name = connected_info["name"]
        live_lang = connected_info.get("lang") or detect_language(live_name)
        res["printer_name"] = live_name
        res["name"] = live_name
        res["printer_lang"] = live_lang
        res["lang"] = live_lang
        res["connected"] = True
        res["online"] = True
        res["status_text"] = f"🟢 {live_name}"
        log.info("[Dotmark] Принтер для точки %s: 🟢 %s (%s)", point, live_name, live_lang)
    else:
        # Прямо сейчас ни один принтер не подключён (как на фото пользователя)
        res["printer_name"] = ""
        res["name"] = ""
        res["connected"] = False
        res["online"] = False
        res["status_text"] = "🔴 Принтер отключён"
        if saved_point_pr:
            res["last_saved_name"] = saved_point_pr
            log.warning("[Dotmark] Принтер для точки %s: 🔴 ОТКЛЮЧЁН (ранее был «%s», но сейчас отключён в Windows)", point, saved_point_pr)
        else:
            log.warning("[Dotmark] Принтер для точки %s: 🔴 ОТКЛЮЧЁН (нет физически подключенных принтеров)", point)

    return res


def set_printer_for_point(cfg: Dict[str, Any], point: str, printer_name: str, printer_lang: str = "auto") -> Dict[str, Any]:
    """Сохранить привязку принтера к точке."""
    if "printers" not in cfg:
        cfg["printers"] = {}
    cfg["printers"][point] = {
        "printer_name": printer_name,
        "printer_lang": printer_lang,
    }
    save_config(cfg)
    return cfg


def print_code(code: str, cfg: Optional[Dict[str, Any]] = None, *args, **kwargs) -> Dict[str, Any]:
    """
    Отправка задания на печать с предварительной проверкой физического подключения:
    Если принтер отключён — блокирует отправку и возвращает понятную ошибку.
    """
    if cfg is None:
        cfg = load_config()

    pr_name = (cfg.get("printer_name") or cfg.get("name") or "").strip()
    is_conn = cfg.get("connected", True) and cfg.get("online", True)

    # Двойная проверка живого статуса принтера перед отправкой байт
    if not is_conn or not pr_name:
        try:
            from device_discovery import get_currently_connected_printer
            live = get_currently_connected_printer(preferred_name=pr_name, force_refresh=False)
            if live and live.get("connected"):
                pr_name = live["name"]
                is_conn = True
                cfg["printer_name"] = pr_name
            else:
                return {
                    "ok": False,
                    "info": "Печать невозможна: принтер отключён или выключен. Подключите принтер к ПК и включите питание.",
                    "error_code": "PRINTER_OFFLINE",
                }
        except Exception:
            if not pr_name:
                return {
                    "ok": False,
                    "info": "Печать невозможна: принтер не подключён.",
                    "error_code": "PRINTER_OFFLINE",
                }

    try:
        import platform
        if platform.system() == "Windows":
            import win32print
            h = win32print.OpenPrinter(pr_name)
            try:
                # Проверяем реальный статус открытого принтера
                info2 = win32print.GetPrinter(h, 2)
                attrs = info2.get("Attributes", 0)
                st = info2.get("Status", 0)
                if (attrs & getattr(win32print, "PRINTER_ATTRIBUTE_WORK_OFFLINE", 0)) or (st & win32print.PRINTER_STATUS_OFFLINE):
                    return {
                        "ok": False,
                        "info": f"Принтер «{pr_name}» находится в статусе «Отключен» в Windows.",
                        "error_code": "PRINTER_OFFLINE",
                    }

                # Формируем документ и отправляем RAW данные
                job = win32print.StartDocPrinter(h, 1, ("Dotmark Label", None, "RAW"))
                try:
                    win32print.StartPagePrinter(h)
                    raw_bytes = code.encode("utf-8") if isinstance(code, str) else bytes(code)
                    win32print.WritePrinter(h, raw_bytes)
                    win32print.EndPagePrinter(h)
                finally:
                    win32print.EndDocPrinter(h)
                return {"ok": True, "info": f"Отправлено на принтер «{pr_name}»"}
            finally:
                win32print.ClosePrinter(h)
        else:
            return {"ok": True, "info": f"[Mock] Отправлено на принтер «{pr_name}»"}
    except Exception as e:
        log.error("Ошибка печати на %s: %s", pr_name, e)
        return {"ok": False, "info": f"Сбой отправки на принтер: {e}"}
