"""
Универсальный модуль печати.
Поддерживает ZPL, TSPL, EZPL и печать через Windows-драйвер.
Автоопределение принтера и языка по имени модели.

Конфигурация (config.json) поддерживает:
  - ОДИН принтер (старый формат): printer_name, printer_ip, printer_lang, ...
  - НЕСКОЛЬКО принтеров по точкам (новый формат):
      "point": "T-01",
      "printers": {
        "T-01": {"printer_name": "...", "printer_lang": "zpl"},
        "T-02": {"printer_name": "...", "printer_lang": "tspl"}
      }

Функция get_printer_for_point(cfg, point) возвращает настройки
принтера для указанной точки или fallback на общие.
"""
import re
import os
import json
import socket
from pathlib import Path

try:
    import win32print
    import win32ui
    from PIL import Image, ImageWin
    _HAS_WIN = True
except ImportError:
    _HAS_WIN = False


# ============================================================
# Настройки
# ============================================================

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    # Точка (гибрид: авто + fallback)
    "point": "",
    "point_auto": True,
    "point_map": {},     # {"PC-LINE-01": "T-01", ...}

    # Принтеры по точкам (новый формат)
    "printers": {},      # {"T-01": {"printer_name": ..., "printer_lang": ...}}

    # Общий принтер (fallback, старый формат)
    "printer_name": "",
    "printer_ip": "",
    "printer_port": 9100,
    "printer_lang": "auto",

    # Параметры этикетки (общие)
    "label_width_mm": 58,
    "label_height_mm": 40,
    "dpi": 203,
    "copies": 1,
}


# ============================================================
# Санитайз SN для bwipp (без скобок и запрещённых символов)
# ============================================================

def _sanitize_sn_for_bwipp(sn: str) -> str:
    """
    bwipp требует CSET 82 и не терпит скобок в SN.
    Убираем всё, что ломает парсер.
    """
    if not sn:
        return sn
    allowed = set(
        "0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "!\"%&'*+,-./:;<=>?_ "   # ← БЕЗ ( )
    )
    return "".join(ch for ch in sn if ch in allowed)


# ============================================================
# Конфиг
# ============================================================

def load_config() -> dict:
    """Прочитать config.json. Объединить с DEFAULT_CONFIG."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    """Сохранить config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ============================================================
# Принтер для конкретной точки
# ============================================================

def get_printer_for_point(cfg: dict, point: str) -> dict:
    """
    Вернуть настройки принтера для указанной точки.

    Логика:
      1. Если в cfg["printers"][point] есть — мержим поверх общих.
      2. Иначе — возвращаем общий принтер (fallback).
    """
    result = dict(cfg)  # копия общих настроек

    printers = cfg.get("printers") or {}
    if point and point in printers:
        point_cfg = printers[point] or {}
        result.update(point_cfg)
        result["_point_source"] = f"printers[{point}]"
    else:
        result["_point_source"] = "fallback"

    return result


def set_printer_for_point(cfg: dict, point: str, printer_cfg: dict) -> dict:
    """Сохранить настройки принтера для точки в cfg."""
    printers = dict(cfg.get("printers") or {})
    printers[point] = dict(printer_cfg)
    cfg["printers"] = printers
    return cfg


# ============================================================
# Автоопределение языка по имени принтера
# ============================================================

_LANG_BY_BRAND = {
    "zpl":  ["zebra", "zdesigner", "zd4", "zd2", "zt2", "zt4", "gk420", "gx420"],
    "tspl": ["tsc", "te200", "te300", "tx200", "tx300", "ttp"],
    "ezpl": ["godex", "g500", "g530", "rt7", "ez"],
}


def detect_language(printer_name: str) -> str:
    """Определить язык по имени принтера. По умолчанию — windows."""
    if not printer_name:
        return "windows"
    name = printer_name.lower()
    for lang, markers in _LANG_BY_BRAND.items():
        for m in markers:
            if m in name:
                return lang
    return "windows"


# ============================================================
# Список принтеров Windows
# ============================================================

def list_printers() -> list:
    if not _HAS_WIN:
        return []
    printers = []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for p in win32print.EnumPrinters(flags, None, 2):
        printers.append({
            "name": p["pPrinterName"],
            "port": p["pPortName"],
            "driver": p["pDriverName"],
        })
    return printers


def default_printer() -> str:
    if not _HAS_WIN:
        return ""
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        return ""


# ============================================================
# Формирование команд
# ============================================================

def _mm_to_dots(mm: float, dpi: int) -> int:
    return int(round(mm / 25.4 * dpi))


def build_zpl(code: str, cfg: dict) -> bytes:
    dpi = cfg["dpi"]
    w = _mm_to_dots(cfg["label_width_mm"], dpi)
    h = _mm_to_dots(cfg["label_height_mm"], dpi)
    copies = cfg["copies"]
    zpl = (
        f"^XA\n"
        f"^PW{w}\n"
        f"^LL{h}\n"
        f"^CI28\n"
        f"^FO20,20\n"
        f"^BXN,{max(4, dpi // 40)},200,200,_,~\n"
        f"^FD{code}^FS\n"
        f"^PQ{copies}\n"
        f"^XZ\n"
    )
    return zpl.encode("utf-8")


def build_tspl(code: str, cfg: dict) -> bytes:
    dpi = cfg["dpi"]
    copies = cfg["copies"]
    cell = max(4, dpi // 40)
    tspl = (
        f"SIZE {cfg['label_width_mm']} mm,{cfg['label_height_mm']} mm\n"
        f"GAP 2 mm,0\n"
        f"DIRECTION 1\n"
        f"CLS\n"
        f"DMATRIX 20,20,{cell},{cell},0,\"{code}\"\n"
        f"PRINT {copies},1\n"
    )
    return tspl.encode("utf-8")


def build_ezpl(code: str, cfg: dict) -> bytes:
    dpi = cfg["dpi"]
    cell = max(4, dpi // 40)
    copies = cfg["copies"]
    ezpl = (
        f"^Q{cfg['label_height_mm']},3\n"
        f"^W{_mm_to_dots(cfg['label_width_mm'], dpi)}\n"
        f"^H10\n"
        f"^P{copies}\n"
        f"^S4\n"
        f"^L\n"
        f"DMATRIX 20,20,{cell},{cell},0,\"{code}\"\n"
        f"E\n"
    )
    return ezpl.encode("utf-8")


# ============================================================
# Отправка
# ============================================================

def _send_tcp(ip: str, port: int, data: bytes, timeout: float = 3.0):
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.sendall(data)


def _send_windows_raw(printer_name: str, data: bytes):
    if not _HAS_WIN:
        raise RuntimeError("pywin32 не установлен")
    handle = win32print.OpenPrinter(printer_name)
    try:
        job = win32print.StartDocPrinter(handle, 1, ("ChestZn Label", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def _render_datamatrix_png(code: str, size_mm: float = 30.0,
                           dpi: int = 203) -> str:
    """Отрендерить DataMatrix в PNG-файл. Автоматически конвертирует в GS1-формат."""
    import treepoem
    import tempfile

    def to_gs1_paren(c):
        if not c:
            return c
        s = str(c).strip()
        s = s.replace("_x001D_", "\x1d")

        m = re.match(
            r"^01(\d{14})21(.+?)(?:\x1d)?91([^\x1d]+)(?:\x1d)?92(.+)$",
            s, re.DOTALL,
        )
        if m:
            gtin, sn, verify, crypto = m.groups()
            sn = _sanitize_sn_for_bwipp(sn)
            verify = _sanitize_sn_for_bwipp(verify)
            crypto = _sanitize_sn_for_bwipp(crypto)
            return f"(01){gtin}(21){sn}(91){verify}(92){crypto}"

        m = re.match(r"^01(\d{14})21(.+)$", s, re.DOTALL)
        if m:
            gtin, sn = m.groups()
            sn = _sanitize_sn_for_bwipp(sn)
            return f"(01){gtin}(21){sn}"

        m = re.match(r"^01(\d{14})$", s)
        if m:
            return f"(01){m.group(1)}"

        return s

    gs1_code = to_gs1_paren(code)

    size_px = int(round(size_mm / 25.4 * dpi))
    size_px = max(size_px, 200)

    img = treepoem.generate_barcode(
        barcode_type="gs1datamatrix",
        data=gs1_code,
    )
    img = img.convert("RGB")
    img = img.resize((size_px, size_px))

    fd, path = tempfile.mkstemp(suffix=".png", prefix="cz_label_")
    os.close(fd)
    img.save(path, "PNG")
    return path


def _print_image_windows(printer_name: str, image_path: str):
    if not _HAS_WIN:
        raise RuntimeError("pywin32 не установлен")
    hprinter = win32print.OpenPrinter(printer_name)
    try:
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer_name)
        hdc.StartDoc(image_path)
        hdc.StartPage()
        img = Image.open(image_path)
        pw = hdc.GetDeviceCaps(110)
        ph = hdc.GetDeviceCaps(111)
        dib = ImageWin.Dib(img)
        dib.draw(hdc.GetHandleOutput(), (0, 0, pw, ph))
        hdc.EndPage()
        hdc.EndDoc()
    finally:
        win32print.ClosePrinter(hprinter)


# ============================================================
# Публичный API
# ============================================================

def print_code(code: str, cfg: dict = None) -> dict:
    """
    Печать одного кода.
    cfg — настройки (уже могут быть для конкретной точки).
    Если cfg не передан — берётся общий config.json.
    """
    if cfg is None:
        cfg = load_config()

    lang = cfg.get("printer_lang", "auto")
    if lang == "auto":
        lang = detect_language(cfg.get("printer_name", ""))

    ip = cfg.get("printer_ip", "")
    port = cfg.get("printer_port", 9100)

    try:
        # --- Сетевой принтер (IP) ---
        if lang in ("zpl", "tspl", "ezpl") and ip:
            if lang == "zpl":
                data = build_zpl(code, cfg)
            elif lang == "tspl":
                data = build_tspl(code, cfg)
            else:
                data = build_ezpl(code, cfg)
            _send_tcp(ip, port, data)
            return {"ok": True, "info": f"Отправлено на {ip}:{port} ({lang})"}

        # --- Локальный принтер с поддержкой ZPL/TSPL/EZPL ---
        if lang in ("zpl", "tspl", "ezpl") and cfg.get("printer_name"):
            if lang == "zpl":
                data = build_zpl(code, cfg)
            elif lang == "tspl":
                data = build_tspl(code, cfg)
            else:
                data = build_ezpl(code, cfg)
            _send_windows_raw(cfg["printer_name"], data)
            return {
                "ok": True,
                "info": f"Отправлено на {cfg['printer_name']} ({lang})",
            }

        # --- Офисный принтер: PNG через драйвер Windows ---
        if cfg.get("printer_name"):
            png_path = _render_datamatrix_png(
                code,
                size_mm=min(cfg.get("label_width_mm", 58),
                            cfg.get("label_height_mm", 40)) * 0.7,
                dpi=cfg.get("dpi", 203),
            )
            try:
                _print_image_windows(cfg["printer_name"], png_path)
                return {
                    "ok": True,
                    "info": f"Отправлено на {cfg['printer_name']} (windows / PNG)",
                }
            finally:
                try:
                    os.remove(png_path)
                except Exception:
                    pass

        return {"ok": False, "info": "Принтер не выбран"}

    except Exception as e:
        return {"ok": False, "info": f"{type(e).__name__}: {e}"}


def autodetect() -> dict:
    """Найти принтеры Windows, вернуть все + лучший вариант."""
    printers = list_printers()
    default = default_printer()

    suggestions = []
    for p in printers:
        lang = detect_language(p["name"])
        suggestions.append({
            "name": p["name"],
            "port": p["port"],
            "lang": lang,
            "is_default": p["name"] == default,
            "is_network": p["port"].upper().startswith("IP_"),
        })

    def score(s):
        sc = 0
        if s["lang"] in ("zpl", "tspl", "ezpl"): sc += 10
        if s["is_network"]: sc += 5
        if s["is_default"]: sc += 3
        return -sc

    suggestions.sort(key=score)
    return {
        "all": suggestions,
        "suggested": suggestions[0] if suggestions else None,
    }


# ============================================================
# Определение точки на этой машине
# ============================================================

def detect_point(cfg: dict = None) -> str:
    """
    Определить точку для текущего компьютера.

    Логика:
      1. Если cfg["point"] задан и НЕ пустой — берём его.
      2. Если cfg["point_auto"] и COMPUTERNAME в cfg["point_map"] — берём оттуда.
      3. Иначе — "" (не задана).
    """
    if cfg is None:
        cfg = load_config()

    # Явное указание точки
    explicit = (cfg.get("point") or "").strip()
    if explicit:
        return explicit

    # Авто по имени компьютера
    if cfg.get("point_auto"):
        hostname = os.environ.get("COMPUTERNAME", "").strip()
        point_map = cfg.get("point_map") or {}
        if hostname and hostname in point_map:
            return point_map[hostname]

    return ""