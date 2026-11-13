"""
Dotmark — Модуль управления печатью, динамического определения принтеров и генерации макетов по шаблонам (printer.py)

Ключевая логика:
1. Автоматически подтягивает активный шаблон этикетки из БД (`templates` + привязки `bindings` по префиксу GTIN
   или шаблон со статусом `⭐ По умолчанию` из вкладки «Шаблоны»).
2. Ширина (`w`), высота (`h`), разрешение (`dpi`) и координаты всех элементов (`dataMatrix` и текстовых полей)
   берутся напрямую из выбранного шаблона.
3. Поддерживает два полноценных конвейера печати по макету шаблона:
   - Команды термопринтеров ZPL / TSPL / EZPL: формирует `SIZE w mm, h mm` / `^PW` / `^LL`, вычисляет координаты
     в точках с учётом `dpi`, генерирует нативный GS1 DataMatrix (`^BX` / `DMATRIX`) или точный графический битмап
     матрицы ECC200 (`BITMAP` / `^GFA`) и выводит все текстовые поля шаблона с переносом строк.
   - Графическая печать через драйвер Windows (`windows` / `auto` GDI): рендерит макет шаблона один-в-один как
     в виджете предпросмотра (`LabelPreviewWidget`) в нужном `DPI` (включая DataMatrix, кириллицу «Русификатора»,
     жирный/курсивный шрифт, межстрочный интервал и перенос слов) и отправляет страницу через `QPrinter` / `QPainter`
     (либо сохраняет/передаёт битмап в спулер).
4. Проверяет реальное физическое подключение принтера перед печатью и поддерживает как локальные очереди
   Windows Spooler (`win32print`), так и прямую сетевую отправку по TCP/IP (порт 9100).
"""

import os
import re
import json
import socket
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

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

_PRINTER_CFG_CACHE: Optional[Dict[str, Any]] = None
_PRINTER_CFG_MTIME: float = -1.0


def load_config(force_reload: bool = False) -> Dict[str, Any]:
    """Мгновенно возвращает конфигурацию принтеров из кэша в памяти (перечитывает с диска только при изменении файла)."""
    global _PRINTER_CFG_CACHE, _PRINTER_CFG_MTIME
    if not CONFIG_FILE.exists():
        _PRINTER_CFG_CACHE = dict(DEFAULT_CONFIG)
        _PRINTER_CFG_MTIME = 0.0
        return dict(_PRINTER_CFG_CACHE)
    try:
        mtime = CONFIG_FILE.stat().st_mtime
        if not force_reload and _PRINTER_CFG_CACHE is not None and mtime == _PRINTER_CFG_MTIME:
            return dict(_PRINTER_CFG_CACHE)
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            merged = dict(DEFAULT_CONFIG)
            if isinstance(data, dict):
                merged.update(data)
            _PRINTER_CFG_CACHE = merged
            _PRINTER_CFG_MTIME = mtime
            return dict(merged)
    except Exception as e:
        log.error("Ошибка загрузки %s: %s", CONFIG_FILE, e)
        return dict(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> bool:
    """Сохраняет конфигурацию принтеров в JSON и мгновенно обновляет кэш в памяти."""
    global _PRINTER_CFG_CACHE, _PRINTER_CFG_MTIME
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        _PRINTER_CFG_CACHE = merged
        try:
            _PRINTER_CFG_MTIME = CONFIG_FILE.stat().st_mtime
        except Exception:
            _PRINTER_CFG_MTIME = 0.0
        return True
    except Exception as e:
        log.error("Ошибка сохранения %s: %s", CONFIG_FILE, e)
        return False


def detect_point(cfg: Optional[Dict[str, Any]] = None, store=None, *args, **kwargs) -> str:
    """Определяет идентификатор текущей рабочей точки (например, 'T-01')."""
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

    try:
        app_cfg_p = CONFIG_DIR / "config.json"
        if app_cfg_p.exists():
            app_cfg = json.loads(app_cfg_p.read_text(encoding="utf-8"))
            for k in ("worker_point_id", "current_point", "point"):
                val = app_cfg.get(k)
                if val and str(val).strip():
                    return str(val).strip()
    except Exception:
        pass

    try:
        host = socket.gethostname().upper()
        m = re.search(r"(?:T|P|POST|POINT)[-_]?(\d{1,2})", host)
        if m:
            return f"T-{int(m.group(1)):02d}"
    except Exception:
        pass

    return "T-01"


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


def autodetect(cfg: Optional[Dict[str, Any]] = None, save: bool = False, *args, **kwargs) -> Dict[str, Any]:
    """Автоматическое обнаружение физически подключенного принтера и точки."""
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

    res.setdefault("suggested", "")
    res.setdefault("printer", "")
    res.setdefault("printer_name", "")
    res.setdefault("ok", False)
    res.setdefault("connected", False)
    return res


autodetect_printer = autodetect
detect_printer = autodetect


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
    """Определяет принтер для точки с живой проверкой физического подключения."""
    if cfg is None:
        cfg = load_config()

    saved_point_pr = ""
    point_settings = cfg.get("printers", {}).get(point, {})
    if isinstance(point_settings, dict):
        saved_point_pr = point_settings.get("printer_name", "").strip()
    if not saved_point_pr:
        saved_point_pr = cfg.get("printer_name", "").strip()

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

    # Подтягиваем размеры и DPI из активного шаблона, чтобы они всегда были актуальными
    tpl_spec = get_active_template_spec()
    if tpl_spec:
        res["label_width_mm"] = float(tpl_spec.get("w") or 58)
        res["label_height_mm"] = float(tpl_spec.get("h") or 40)
        res["dpi"] = int(tpl_spec.get("dpi") or 203)
        res["template_name"] = tpl_spec.get("template_name", "По умолчанию")

    if connected_info and connected_info.get("connected"):
        live_name = connected_info["name"]
        saved_lang = str(res.get("printer_lang") or "auto").lower()
        live_lang = saved_lang if saved_lang not in ("", "auto") else (connected_info.get("lang") or detect_language(live_name))
        res["printer_name"] = live_name
        res["name"] = live_name
        res["printer_lang"] = live_lang
        res["lang"] = live_lang
        res["connected"] = True
        res["online"] = True
        res["status_text"] = f"🟢 {live_name}"
    else:
        res["printer_name"] = ""
        res["name"] = ""
        res["connected"] = False
        res["online"] = False
        res["status_text"] = "🔴 Принтер отключён"
        if saved_point_pr:
            res["last_saved_name"] = saved_point_pr

    return res


def set_printer_for_point(
    cfg: Dict[str, Any],
    point: str,
    printer_name_or_dict: Any,
    printer_lang: str = "auto"
) -> Dict[str, Any]:
    """Сохранить привязку принтера к точке (поддерживает и строку имени, и полный словарь настроек)."""
    if "printers" not in cfg or not isinstance(cfg.get("printers"), dict):
        cfg["printers"] = {}

    if isinstance(printer_name_or_dict, dict):
        cfg["printers"][point] = dict(printer_name_or_dict)
    else:
        cfg["printers"][point] = {
            "printer_name": str(printer_name_or_dict or "").strip(),
            "printer_lang": str(printer_lang or "auto").strip(),
        }
    save_config(cfg)
    return cfg


# ==============================================================================
# ЗАГРУЗКА АКТИВНОГО ШАБЛОНА ИЗ БД И ПОДСТАНОВКА ПОЛЕЙ ТОВАРА
# ==============================================================================

DEFAULT_TEMPLATE_FALLBACK: Dict[str, Any] = {
    "w": 58.0,
    "h": 40.0,
    "dpi": 203,
    "fields": [
        {"name": "dataMatrix", "label": "", "required": True, "font_size": 0, "size": 20.0, "x": 16, "y": 44},
        {"name": "name_ru", "label": "", "required": True, "font_size": 10, "bold": True, "x": 16, "y": 10, "width": 420, "height": 30},
        {"name": "gtin", "label": "GTIN", "required": True, "font_size": 9, "x": 188, "y": 54},
        {"name": "serial", "label": "SN", "required": True, "font_size": 9, "x": 188, "y": 82},
    ],
}


def resolve_template_for_gtin(gtin: str = "", store: Any = None, template_code: str = "") -> Dict[str, Any]:
    """
    Находит подходящий шаблон этикетки из БД (`templates` и `bindings`):
    1. Если задан `gtin` и в таблице `bindings` есть привязка по префиксу GTIN — берёт привязанный шаблон.
    2. Иначе берёт шаблон с флагом `is_default = 1` (⭐ По умолчанию).
    3. Если такого нет — берёт первый шаблон из таблицы `templates` или встроенный стандартный макет 58×40 мм.
    """
    templates: List[Dict[str, Any]] = []
    bindings: List[Dict[str, Any]] = []

    if store is not None:
        try:
            if hasattr(store, "list_templates"):
                templates = store.list_templates() or []
            if hasattr(store, "list_bindings"):
                bindings = store.list_bindings() or []
        except Exception:
            pass

    if not templates:
        try:
            import db
            with db.get_conn() as conn:
                has_tpl_tbl = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='templates'"
                ).fetchone()
                rows = (
                    conn.execute("SELECT code, name, print_mode, is_default, config_json FROM templates").fetchall()
                    if has_tpl_tbl else []
                )
                for r in rows:
                    rd = dict(r) if hasattr(r, "keys") else {
                        "code": r[0], "name": r[1], "print_mode": r[2], "is_default": r[3], "config_json": r[4]
                    }
                    try:
                        cfg_obj = json.loads(rd.get("config_json") or "{}")
                    except Exception:
                        cfg_obj = {}
                    templates.append({
                        "code": rd.get("code", ""),
                        "name": rd.get("name", "Шаблон"),
                        "print_mode": rd.get("print_mode") or cfg_obj.get("print_mode", "combined"),
                        "is_default": bool(rd.get("is_default")),
                        "config": cfg_obj,
                    })
                try:
                    has_bnd_tbl = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bindings'"
                    ).fetchone()
                    b_rows = (
                        conn.execute(
                            "SELECT gtin_prefix, template_code, priority FROM bindings ORDER BY priority DESC, LENGTH(gtin_prefix) DESC"
                        ).fetchall()
                        if has_bnd_tbl else []
                    )
                    for br in b_rows:
                        bd = dict(br) if hasattr(br, "keys") else {
                            "gtin_prefix": br[0], "template_code": br[1], "priority": br[2]
                        }
                        bindings.append(bd)
                except Exception:
                    pass
        except Exception as e:
            log.debug("Не удалось загрузить шаблоны из БД: %s", e)

    chosen_tpl: Optional[Dict[str, Any]] = None
    tpl_code_clean = (template_code or "").strip()
    gtin_clean = (gtin or "").strip()

    if tpl_code_clean and templates:
        for t in templates:
            if t.get("code") == tpl_code_clean:
                chosen_tpl = t
                break

    if chosen_tpl is None and gtin_clean and bindings and templates:
        tpl_by_code = {t.get("code"): t for t in templates if t.get("code")}
        sorted_b = sorted(
            bindings,
            key=lambda b: (int(b.get("priority") or 0), len(str(b.get("gtin_prefix") or ""))),
            reverse=True,
        )
        for b in sorted_b:
            pref = str(b.get("gtin_prefix") or "").strip()
            t_code = b.get("template_code")
            if pref and (gtin_clean.startswith(pref) or gtin_clean.lstrip("0").startswith(pref.lstrip("0"))):
                if t_code in tpl_by_code:
                    chosen_tpl = tpl_by_code[t_code]
                    break

    if chosen_tpl is None and templates:
        for t in templates:
            if t.get("is_default"):
                chosen_tpl = t
                break
        if chosen_tpl is None:
            chosen_tpl = templates[0]

    if chosen_tpl is None:
        return {
            "code": "default",
            "name": "Стандартный 58×40",
            "print_mode": "combined",
            "config": {"combined": dict(DEFAULT_TEMPLATE_FALLBACK)},
        }
    return chosen_tpl


def get_active_template_spec(gtin: str = "", store: Any = None, tab_override: str = "", template_code: str = "") -> Dict[str, Any]:
    """
    Возвращает готовую спецификацию макета для печати:
      {
        "template_code": ...,
        "template_name": ...,
        "print_mode": "combined" | "separate",
        "w": float (мм),
        "h": float (мм),
        "dpi": int,
        "fields": list,
        "secondary_section": Optional[dict],  # если print_mode == "separate" (вторая этикетка «Русификатор»)
      }
    """
    tpl = resolve_template_for_gtin(gtin=gtin, store=store, template_code=template_code)
    cfg = tpl.get("config") or {}
    mode = str(tpl.get("print_mode") or cfg.get("print_mode") or "combined").lower()

    if tab_override and tab_override in cfg and isinstance(cfg[tab_override], dict):
        primary_sec = cfg[tab_override]
        secondary_sec = None
    elif mode == "separate":
        primary_sec = cfg.get("cz") or cfg.get("combined") or DEFAULT_TEMPLATE_FALLBACK
        secondary_sec = cfg.get("label") if isinstance(cfg.get("label"), dict) else None
    else:
        primary_sec = cfg.get("combined") or cfg.get("cz") or DEFAULT_TEMPLATE_FALLBACK
        secondary_sec = None

    w_mm = float(primary_sec.get("w") or 58.0)
    h_mm = float(primary_sec.get("h") or 40.0)
    dpi = int(primary_sec.get("dpi") or 203)
    fields = list(primary_sec.get("fields") or DEFAULT_TEMPLATE_FALLBACK["fields"])

    return {
        "template_code": tpl.get("code", "default"),
        "template_name": tpl.get("name", "По умолчанию"),
        "print_mode": mode,
        "w": w_mm,
        "h": h_mm,
        "dpi": dpi,
        "fields": fields,
        "secondary_section": secondary_sec,
    }


def resolve_label_field_values(code: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """
    Собирает значения всех полей этикетки (`gtin`, `sn`/`serial`, `shk`, `name`/`name_ru`, `date` и др.)
    для конкретного печатаемого кода ЧЗ:
    1. Разбирает GS1 DataMatrix (`01<GTIN>21<SN>...`).
    2. Ищет название товара и внешний ШК в активном заказе оператора или в БД (`pool_codes`, `gtin_map`).
    3. Дополняет переданными метаданными из `cfg`.
    """
    from datetime import datetime
    from barcode import parse_datamatrix_gs1

    cfg = cfg or {}
    parsed = parse_datamatrix_gs1(code)
    gtin = str(cfg.get("gtin") or parsed.get("gtin") or "").strip()
    sn = str(cfg.get("sn") or cfg.get("serial") or parsed.get("sn") or "").strip()
    shk = str(cfg.get("barcode") or cfg.get("shk") or "").strip()
    prod_name = str(cfg.get("product_name") or cfg.get("title") or cfg.get("name_ru") or cfg.get("name") or "").strip()

    # Проверяем активную партию в окне оператора или БД, чтобы подтянуть реальное наименование товара и ШК
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            for w in app.topLevelWidgets():
                op = getattr(w, "operator", None)
                if op is not None:
                    b_info = getattr(op, "_active_batch_info", None)
                    if isinstance(b_info, dict):
                        if not prod_name and b_info.get("title"):
                            prod_name = str(b_info["title"]).strip()
                        if not shk and b_info.get("shk"):
                            shk = str(b_info["shk"]).strip()
                        if not gtin and b_info.get("gtin"):
                            gtin = str(b_info["gtin"]).strip()
                    break
    except Exception:
        pass

    if gtin and (not shk or not prod_name):
        try:
            import db
            with db.get_conn() as conn:
                if not shk:
                    row_g = conn.execute(
                        "SELECT foreign_gtin FROM gtin_map WHERE cz_gtin = ? OR LTRIM(cz_gtin, '0') = ? LIMIT 1",
                        (gtin, gtin.lstrip("0"))
                    ).fetchone()
                    if row_g:
                        shk = str(row_g[0] or "").strip()
        except Exception:
            pass

    if not shk and gtin:
        shk = gtin.lstrip("0") or gtin

    values: Dict[str, str] = {
        "gtin": gtin or "04607001234567",
        "shk": shk or gtin or "4607001234567",
        "sn": sn or "SN000001",
        "serial": sn or "SN000001",
        "name": prod_name,
        "name_ru": prod_name,
        "date": datetime.now().strftime("%d.%m.%Y"),
        "qty": "1 шт.",
    }
    for k, v in cfg.items():
        if isinstance(v, (str, int, float)) and k.lower() not in values:
            values[k.lower()] = str(v)
    return values


def _format_field_text(f: Dict[str, Any], field_values: Dict[str, str]) -> str:
    """Формирует итоговую строку текстового поля шаблона точно так же, как в LabelPreviewWidget."""
    fname = str(f.get("name") or "").strip()
    fname_l = fname.lower()
    flabel = str(f.get("label") or "").strip()
    val = str(field_values.get(fname_l) or "").strip()

    if fname_l in ("name", "name_ru"):
        return val or flabel
    if flabel and val:
        return f"{flabel}: {val}"
    if val:
        return val
    if "{" in flabel and "}" in flabel:
        try:
            return flabel.format_map({k: str(v) for k, v in field_values.items()})
        except Exception:
            pass
    return flabel


# ==============================================================================
# ГЕНЕРАЦИЯ КОМАНД ТЕРМОПРИНТЕРОВ (ZPL / TSPL / EZPL) ПО МАКЕТУ ШАБЛОНА
# ==============================================================================

def _matrix_to_bitmap_bytes(matrix: List[List[int]], target_dots: int, invert_for_tspl: bool = True) -> Tuple[int, int, bytes]:
    """
    Масштабирует битовую матрицу DataMatrix ECC200 до точного размера `target_dots` в точках принтера
    (кратного 8 по ширине для байтового выравнивания) и упаковывает в монохромный битмап:
    - для TSPL `BITMAP`: `0` = чёрная точка (печать), `1` = белая точка (фон);
    - для ZPL `^GFA`: `1` = чёрная точка, `0` = белая точка.
    Это гарантирует 100% читаемость GS1 DataMatrix с криптохвостом на любой прошивке принтера.
    """
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    if rows == 0 or cols == 0:
        return 0, 0, b""

    scale = max(2, round(target_dots / rows))
    actual_h = rows * scale
    raw_w = cols * scale
    width_bytes = (raw_w + 7) // 8
    actual_w = width_bytes * 8

    out = bytearray(width_bytes * actual_h)
    if invert_for_tspl:
        # В TSPL BITMAP бит 1 = белый (не жечь), бит 0 = чёрный (жечь точку)
        for i in range(len(out)):
            out[i] = 0xFF
        for r_idx, row in enumerate(matrix):
            for c_idx, val in enumerate(row):
                if val:
                    y0 = r_idx * scale
                    x0 = c_idx * scale
                    for dy in range(scale):
                        row_offset = (y0 + dy) * width_bytes
                        for dx in range(scale):
                            px = x0 + dx
                            out[row_offset + (px >> 3)] &= ~(1 << (7 - (px & 7)))
    else:
        # В ZPL ^GFA бит 1 = чёрный, бит 0 = белый
        for r_idx, row in enumerate(matrix):
            for c_idx, val in enumerate(row):
                if val:
                    y0 = r_idx * scale
                    x0 = c_idx * scale
                    for dy in range(scale):
                        row_offset = (y0 + dy) * width_bytes
                        for dx in range(scale):
                            px = x0 + dx
                            out[row_offset + (px >> 3)] |= (1 << (7 - (px & 7)))

    return width_bytes, actual_h, bytes(out)


def build_zpl_from_template(
    code: str,
    section: Dict[str, Any],
    field_values: Dict[str, str],
    copies: int = 1
) -> bytes:
    """
    Формирует команды языка ZPL II (Zebra) строго по координатам и размерам из шаблона:
    `^PW` (ширина в точках), `^LL` (высота в точках), `^CI28` (UTF-8 для кириллицы),
    `^BX` + `^GFA` для GS1 DataMatrix и `^FO` / `^A0N` / `^FB` для текстовых полей.
    """
    from barcode import encode_gs1_datamatrix_matrix, _normalize_gs1_raw

    w_mm = float(section.get("w") or 58.0)
    h_mm = float(section.get("h") or 40.0)
    dpi = int(section.get("dpi") or 203)
    fields = section.get("fields") or []

    w_dots = max(80, int(round(w_mm / 25.4 * dpi)))
    h_dots = max(80, int(round(h_mm / 25.4 * dpi)))

    lines: List[str] = [
        "^XA",
        "^CI28",
        f"^PW{w_dots}",
        f"^LL{h_dots}",
        "^LH0,0",
    ]

    norm_code = _normalize_gs1_raw(code).lstrip("\x1d")
    zpl_gs1 = "_1" + norm_code.replace("\x1d", "_1")

    for f in fields:
        fname = str(f.get("name") or "").strip()
        x_dots = max(0, int(f.get("x", 16)))
        y_dots = max(0, int(f.get("y", 16)))

        if fname == "dataMatrix":
            size_mm = float(f.get("size") or 19.0)
            size_dots = max(24, int(round(size_mm / 25.4 * dpi)))
            matrix = encode_gs1_datamatrix_matrix(code)
            mod_rows = max(18, len(matrix))
            module_h = max(3, round(size_dots / mod_rows))
            lines.append(f"^FO{x_dots},{y_dots}^BXN,{module_h},200,,,,_^FD{zpl_gs1}^FS")
        else:
            text_val = _format_field_text(f, field_values)
            if not text_val:
                continue
            font_pt = max(6, int(f.get("font_size") or 10))
            font_dots = max(14, int(round(font_pt * dpi / 72.0)))
            box_w = int(f.get("width") or max(80, w_dots - x_dots - 8))
            safe_txt = text_val.replace("^", " ").replace("~", " ")
            lines.append(f"^FO{x_dots},{y_dots}^A0N,{font_dots},{font_dots}^FB{box_w},3,2,L,0^FD{safe_txt}^FS")

    lines.append(f"^PQ{max(1, int(copies or 1))},0,1,Y")
    lines.append("^XZ")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def build_tspl_from_template(
    code: str,
    section: Dict[str, Any],
    field_values: Dict[str, str],
    copies: int = 1
) -> bytes:
    """
    Формирует команды языка TSPL / TSPL2 (TSC, Xprinter, Gprinter, АТОЛ) по макету шаблона:
    `SIZE w mm, h mm`, `CLS`, точный битмап матрицы ECC200 (`BITMAP x,y,width_bytes,height,0,<raw>`)
    + `DMATRIX` и текстовые поля `TEXT`.
    """
    from barcode import encode_gs1_datamatrix_matrix

    w_mm = float(section.get("w") or 58.0)
    h_mm = float(section.get("h") or 40.0)
    dpi = int(section.get("dpi") or 203)
    fields = section.get("fields") or []
    w_dots = max(80, int(round(w_mm / 25.4 * dpi)))

    out = bytearray()
    header = (
        f"SIZE {int(round(w_mm))} mm, {int(round(h_mm))} mm\r\n"
        f"GAP 2 mm, 0 mm\r\n"
        f"DIRECTION 1\r\n"
        f"CODEPAGE UTF-8\r\n"
        f"CLS\r\n"
    )
    out.extend(header.encode("utf-8"))

    for f in fields:
        fname = str(f.get("name") or "").strip()
        x_dots = max(0, int(f.get("x", 16)))
        y_dots = max(0, int(f.get("y", 16)))

        if fname == "dataMatrix":
            size_mm = float(f.get("size") or 19.0)
            size_dots = max(24, int(round(size_mm / 25.4 * dpi)))
            matrix = encode_gs1_datamatrix_matrix(code)
            w_bytes, actual_h, bmp_data = _matrix_to_bitmap_bytes(matrix, size_dots, invert_for_tspl=True)
            if w_bytes > 0 and actual_h > 0:
                cmd_head = f"BITMAP {x_dots},{y_dots},{w_bytes},{actual_h},0,".encode("ascii")
                out.extend(cmd_head)
                out.extend(bmp_data)
                out.extend(b"\r\n")
        else:
            text_val = _format_field_text(f, field_values)
            if not text_val:
                continue
            font_pt = max(6, int(f.get("font_size") or 10))
            mul = 2 if font_pt >= 14 else 1
            box_w = int(f.get("width") or max(80, w_dots - x_dots - 8))
            approx_char_w = 12 * mul
            max_chars = max(10, box_w // approx_char_w)

            # Перенос длинных строк по ширине рамки из шаблона
            words = text_val.split()
            lines_wrapped: List[str] = []
            cur_l = ""
            for w_word in words:
                cand = f"{cur_l} {w_word}".strip()
                if len(cand) <= max_chars or not cur_l:
                    cur_l = cand
                else:
                    lines_wrapped.append(cur_l)
                    cur_l = w_word
            if cur_l:
                lines_wrapped.append(cur_l)

            line_step = 24 * mul + 4
            for idx_l, l_str in enumerate(lines_wrapped[:4]):
                safe_t = l_str.replace('"', "'")
                cy = y_dots + idx_l * line_step
                out.extend(f'TEXT {x_dots},{cy},"3",0,{mul},{mul},"{safe_t}"\r\n'.encode("utf-8"))

    out.extend(f"PRINT {max(1, int(copies or 1))},1\r\n".encode("ascii"))
    return bytes(out)


def build_ezpl_from_template(
    code: str,
    section: Dict[str, Any],
    field_values: Dict[str, str],
    copies: int = 1
) -> bytes:
    """Формирует команды языка EZPL (Godex) по координатам и размерам из шаблона."""
    w_mm = int(round(float(section.get("w") or 58.0)))
    h_mm = int(round(float(section.get("h") or 40.0)))
    dpi = int(section.get("dpi") or 203)
    fields = section.get("fields") or []

    lines: List[str] = [
        f"^Q{h_mm},2",
        f"^W{w_mm}",
        "^H10",
        f"^P{max(1, int(copies or 1))}",
        "^S4",
        "^L",
    ]
    clean_code = (code or "").strip().lstrip("\x1d")
    for f in fields:
        fname = str(f.get("name") or "").strip()
        x_dots = max(0, int(f.get("x", 16)))
        y_dots = max(0, int(f.get("y", 16)))
        if fname == "dataMatrix":
            size_mm = float(f.get("size") or 19.0)
            size_dots = max(24, int(round(size_mm / 25.4 * dpi)))
            mul = max(3, min(10, round(size_dots / 26)))
            lines.append(f"XRB{x_dots},{y_dots},{mul},0,{len(clean_code)}")
            lines.append(clean_code)
        else:
            text_val = _format_field_text(f, field_values)
            if not text_val:
                continue
            safe_t = text_val.replace("\r", " ").replace("\n", " ")
            lines.append(f"AC,{x_dots},{y_dots},1,1,0,0,{safe_t}")

    lines.append("E")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


# ==============================================================================
# ГРАФИЧЕСКИЙ РЕНДЕРИНГ МАКЕТА 1-В-1 КАК НА ПРЕДПРОСМОТРЕ (ДЛЯ WINDOWS ДРАЙВЕРА)
# ==============================================================================

def render_template_section_to_qimage(
    code: str,
    section: Dict[str, Any],
    field_values: Dict[str, str],
):
    """
    Рендерит этикетку в `QImage` с физическим разрешением `dpi` из шаблона
    один-в-один как на холсте `LabelPreviewWidget`:
    - точные миллиметровые размеры `w × h` в точках `dpi`;
    - чёткий матричный `DataMatrix` без размытия (`Qt.FastTransformation`);
    - все текстовые поля («Наименование», «GTIN», «SN», «Русификатор») с жирным/курсивным
      начертанием, межстрочным интервалом и переносом строк внутри рамки.
    """
    from PySide6.QtCore import Qt, QRect
    from PySide6.QtGui import QImage, QPainter, QColor, QFont, QFontMetrics, QPen
    from barcode import render_datamatrix

    w_mm = float(section.get("w") or 58.0)
    h_mm = float(section.get("h") or 40.0)
    dpi = int(section.get("dpi") or 203)
    fields = section.get("fields") or []

    w_dots = max(60, int(round(w_mm / 25.4 * dpi)))
    h_dots = max(60, int(round(h_mm / 25.4 * dpi)))
    sm_dots = max(2, int(round(1.0 / 25.4 * dpi)))

    img = QImage(w_dots, h_dots, QImage.Format_RGB32)
    img.fill(QColor(255, 255, 255))

    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    left_px = sm_dots
    right_px = w_dots - sm_dots
    top_px = sm_dots
    bottom_px = h_dots - sm_dots

    # 1. Отрисовка DataMatrix (если присутствует в секции шаблона)
    for f in fields:
        if f.get("name") != "dataMatrix":
            continue
        x_dots = int(f.get("x", 16))
        y_dots = int(f.get("y", 16))
        size_mm = float(f.get("size") or 19.0)
        size_dots = max(20, int(round(size_mm / 25.4 * dpi)))

        max_dm = max(20, min(right_px - left_px, bottom_px - top_px))
        ssz = max(20, min(max_dm, size_dots))
        sx = max(left_px, min(x_dots, max(left_px, right_px - ssz)))
        sy = max(top_px, min(y_dots, max(top_px, bottom_px - ssz)))

        pix = render_datamatrix(code, size=ssz)
        if not pix.isNull():
            painter.drawPixmap(QRect(sx, sy, ssz, ssz), pix)

    # 2. Отрисовка текстовых полей шаблона
    for f in fields:
        if f.get("name") == "dataMatrix":
            continue
        text_str = _format_field_text(f, field_values)
        if not text_str:
            continue

        x_dots = int(f.get("x", 16))
        y_dots = int(f.get("y", 16))
        saved_w_dots = int(f["width"]) if ("width" in f and f["width"] is not None) else None
        saved_h_dots = int(f["height"]) if ("height" in f and f["height"] is not None) else None

        font_size_pt = max(6, int(f.get("font_size") or 10))
        target_font_px = max(8, int(round(font_size_pt * dpi / 72.0)))
        is_bold = bool(f.get("bold", False))
        is_italic = bool(f.get("italic", False))
        line_spacing = float(f.get("line_spacing") or 1.15)

        sx = x_dots
        sy = y_dots

        def _layout_text(px_size: int):
            f_obj = QFont("Arial")
            f_obj.setBold(is_bold)
            f_obj.setItalic(is_italic)
            f_obj.setPixelSize(max(8, px_size))
            fm_obj = QFontMetrics(f_obj)

            if saved_w_dots is not None and saved_w_dots > 20:
                bw = max(24, saved_w_dots)
            else:
                avail_natural = max(32, right_px - sx)
                words = text_str.split()
                max_word_w = max((fm_obj.horizontalAdvance(w) for w in words), default=0) + 8
                single_line_w = fm_obj.horizontalAdvance(text_str) + 8
                bw = min(avail_natural, max(max_word_w, single_line_w))

            bw = min(bw, right_px - left_px)
            lh = max(10, int(round(fm_obj.height() * line_spacing)))
            lines_out: List[str] = []
            cur_l = ""
            max_line_w = max(12, bw - 6)
            for raw_para in text_str.splitlines() or [text_str]:
                for word in raw_para.split():
                    test_l = f"{cur_l} {word}".strip()
                    if fm_obj.horizontalAdvance(test_l) <= max_line_w:
                        cur_l = test_l
                    else:
                        if cur_l:
                            lines_out.append(cur_l)
                            cur_l = ""
                        if fm_obj.horizontalAdvance(word) > max_line_w:
                            chunk = ""
                            for ch in word:
                                if fm_obj.horizontalAdvance(chunk + ch) <= max_line_w or not chunk:
                                    chunk += ch
                                else:
                                    lines_out.append(chunk)
                                    chunk = ch
                            cur_l = chunk
                        else:
                            cur_l = word
                if cur_l:
                    lines_out.append(cur_l)
                    cur_l = ""
            nat_h = max(fm_obj.height() + 4, len(lines_out) * lh + 4)
            return f_obj, fm_obj, bw, lh, lines_out, nat_h

        min_px = max(8, int(round(6 * dpi / 72.0)))
        cur_px = target_font_px
        font, fm, box_w, line_h, wrapped_lines, natural_h = _layout_text(cur_px)

        if saved_h_dots is not None and saved_h_dots > 14:
            target_box_h = min(max(18, saved_h_dots), bottom_px - top_px)
            while natural_h > target_box_h and cur_px > min_px:
                cur_px = max(min_px, cur_px - max(1, (cur_px - min_px) // 4 or 1))
                font, fm, box_w, line_h, wrapped_lines, natural_h = _layout_text(cur_px)
            box_h = target_box_h
        else:
            max_avail_h = max(18, bottom_px - max(top_px, min(sy, bottom_px - 18)))
            while natural_h > max_avail_h and cur_px > min_px:
                cur_px = max(min_px, cur_px - max(1, (cur_px - min_px) // 4 or 1))
                font, fm, box_w, line_h, wrapped_lines, natural_h = _layout_text(cur_px)
            box_h = min(natural_h, bottom_px - top_px)

        sx = max(left_px, min(sx, right_px - box_w))
        sy = max(top_px, min(sy, bottom_px - box_h))
        ascent = fm.ascent()

        box_h = max(box_h, fm.height() + 6)
        rect = QRect(sx, sy, box_w, box_h)
        painter.save()
        painter.setClipRect(rect, Qt.IntersectClip)
        painter.setFont(font)
        painter.setPen(QPen(QColor(0, 0, 0)))

        cur_y = sy + ascent + 2
        for l_idx, line in enumerate(wrapped_lines):
            if l_idx > 0 and (cur_y - ascent) >= (sy + box_h):
                break
            painter.drawText(sx + 2, cur_y, line)
            cur_y += line_h
        painter.restore()

    painter.end()
    return img


def _qimage_to_mono_raster(img) -> Tuple[int, int, bytes, bool]:
    """Конвертирует QImage в 1-битный монохромный растр (bytes_per_row, height, raw_bytes, zero_is_white)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    mono = img.convertToFormat(QImage.Format_Mono, Qt.ThresholdDither | Qt.AvoidDither)
    w = mono.width()
    h = mono.height()
    bpr = (w + 7) // 8
    out = bytearray()
    for y in range(h):
        ptr = mono.constScanLine(y)
        out.extend(bytes(ptr[:bpr]))
    c0 = mono.color(0) & 0xFFFFFF
    zero_is_white = (c0 == 0xFFFFFF)
    return bpr, h, bytes(out), zero_is_white


def build_wysiwyg_zpl_from_template(
    code: str,
    section: Dict[str, Any],
    field_values: Dict[str, str],
    copies: int = 1,
) -> bytes:
    """Рендерит макет 1-в-1 в QImage и упаковывает в ZPL ^GFA (полная поддержка кириллицы и шрифтов)."""
    img = render_template_section_to_qimage(code, section, field_values)
    bpr, h, raw, zero_is_white = _qimage_to_mono_raster(img)
    total_bytes = bpr * h
    if not zero_is_white:
        raw = bytes(b ^ 0xFF for b in raw)
    hex_data = raw.hex().upper()
    w_dots = img.width()
    zpl = (
        f"^XA\n"
        f"^CI28\n"
        f"^PW{w_dots}\n"
        f"^LL{h}\n"
        f"^FO0,0^GFA,{total_bytes},{total_bytes},{bpr},{hex_data}^FS\n"
        f"^PQ{max(1, int(copies))},0,1,Y\n"
        f"^XZ\n"
    )
    return zpl.encode("utf-8")


def build_wysiwyg_tspl_from_template(
    code: str,
    section: Dict[str, Any],
    field_values: Dict[str, str],
    copies: int = 1,
) -> bytes:
    """Рендерит макет 1-в-1 в QImage и упаковывает в TSPL BITMAP (полная поддержка кириллицы и шрифтов)."""
    img = render_template_section_to_qimage(code, section, field_values)
    bpr, h, raw, zero_is_white = _qimage_to_mono_raster(img)
    if zero_is_white:
        raw = bytes(b ^ 0xFF for b in raw)
    w_mm = float(section.get("w") or 58.0)
    h_mm = float(section.get("h") or 40.0)
    header = (
        f"SIZE {w_mm:.1f} mm, {h_mm:.1f} mm\r\n"
        f"GAP 2 mm, 0 mm\r\n"
        f"DIRECTION 1\r\n"
        f"CLS\r\n"
        f"BITMAP 0,0,{bpr},{h},0,"
    ).encode("ascii")
    footer = f"\r\nPRINT {max(1, int(copies))},1\r\n".encode("ascii")
    return header + raw + footer


def _print_sections_via_windows_gdi(
    printer_name: str,
    code: str,
    sections: List[Dict[str, Any]],
    field_values: Dict[str, str],
    copies: int = 1,
) -> Dict[str, Any]:
    """
    Отправляет срендеренный макет этикетки (1 или 2 секции при раздельном режиме «ЧЗ + Русификатор»)
    через системный драйвер принтера Windows (`QPrinter` + `QPainter`) с точным размером страницы в мм.
    """
    from PySide6.QtCore import QSizeF, QRectF, QMarginsF, Qt
    from PySide6.QtGui import QPageSize, QPageLayout, QPainter
    from PySide6.QtPrintSupport import QPrinter

    first_sec = sections[0]
    w_mm = float(first_sec.get("w") or 58.0)
    h_mm = float(first_sec.get("h") or 40.0)
    dpi = int(first_sec.get("dpi") or 203)

    qprinter = QPrinter(QPrinter.HighResolution)
    qprinter.setPrinterName(printer_name)
    qprinter.setDocName("Dotmark Label")
    qprinter.setResolution(dpi)
    qprinter.setCopyCount(max(1, int(copies or 1)))
    qprinter.setFullPage(True)

    page_size = QPageSize(QSizeF(w_mm, h_mm), QPageSize.Millimeter, f"{int(w_mm)}x{int(h_mm)}mm", QPageSize.ExactMatch)
    qprinter.setPageSize(page_size)
    qprinter.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)

    if not qprinter.isValid():
        return {
            "ok": False,
            "info": f"Принтер «{printer_name}» недоступен для графической печати Windows.",
            "error_code": "PRINTER_INVALID",
        }

    painter = QPainter()
    if not painter.begin(qprinter):
        return {
            "ok": False,
            "info": f"Не удалось открыть задание печати на принтере «{printer_name}».",
            "error_code": "SPOOLER_ERROR",
        }

    try:
        for idx, sec in enumerate(sections):
            if idx > 0:
                sec_w = float(sec.get("w") or w_mm)
                sec_h = float(sec.get("h") or h_mm)
                if abs(sec_w - w_mm) > 0.5 or abs(sec_h - h_mm) > 0.5:
                    qprinter.setPageSize(
                        QPageSize(QSizeF(sec_w, sec_h), QPageSize.Millimeter, f"{int(sec_w)}x{int(sec_h)}mm", QPageSize.ExactMatch)
                    )
                qprinter.newPage()

            img = render_template_section_to_qimage(code, sec, field_values)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            target_rect = painter.viewport()
            sec_w_mm = float(sec.get("w") or w_mm)
            sec_h_mm = float(sec.get("h") or h_mm)
            actual_dpi = max(72, int(qprinter.resolution() or dpi))
            phys_w = sec_w_mm / 25.4 * actual_dpi
            phys_h = sec_h_mm / 25.4 * actual_dpi
            # Если принтер оставил большой лист (например А4 у лазерного принтера), печатаем этикетку 1-в-1 в её реальных миллиметрах
            if target_rect.width() > phys_w * 1.4 or target_rect.height() > phys_h * 1.4:
                margin_px = 5.0 / 25.4 * actual_dpi
                draw_rect = QRectF(margin_px, margin_px, phys_w, phys_h)
            else:
                draw_rect = QRectF(target_rect)
            painter.drawImage(draw_rect, img, QRectF(img.rect()))
    finally:
        painter.end()

    return {
        "ok": True,
        "info": f"Этикетка ({int(w_mm)}×{int(h_mm)} мм, {dpi} DPI) напечатана на «{printer_name}»",
    }


def _send_raw_bytes_to_printer(pr_name: str, pr_ip: str, pr_port: int, raw_payload: bytes) -> Dict[str, Any]:
    """Отправляет сформированные байты команд ZPL / TSPL / EZPL по TCP/IP или через Windows Spooler."""
    if pr_ip and pr_ip != "—":
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.5)
            sock.connect((pr_ip, int(pr_port or 9100)))
            sock.sendall(raw_payload)
            sock.close()
            return {"ok": True, "info": f"Отправлено по сети {pr_ip}:{pr_port}"}
        except Exception as e:
            return {"ok": False, "info": f"Ошибка сетевой печати ({pr_ip}:{pr_port}): {e}", "error_code": "NET_ERROR"}

    import platform
    if platform.system() != "Windows":
        return {"ok": False, "info": f"Печать через Windows Spooler недоступна на этой ОС ({pr_name})"}

    import win32print
    h = win32print.OpenPrinter(pr_name)
    try:
        info2 = win32print.GetPrinter(h, 2)
        attrs = info2.get("Attributes", 0)
        st = info2.get("Status", 0)
        if (attrs & getattr(win32print, "PRINTER_ATTRIBUTE_WORK_OFFLINE", 0)) or (st & win32print.PRINTER_STATUS_OFFLINE):
            return {
                "ok": False,
                "info": f"Принтер «{pr_name}» находится в статусе «Отключен» в Windows.",
                "error_code": "PRINTER_OFFLINE",
            }

        job = win32print.StartDocPrinter(h, 1, ("Dotmark Label", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, raw_payload)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
        return {"ok": True, "info": f"Отправлено на принтер «{pr_name}»"}
    finally:
        win32print.ClosePrinter(h)


def print_code(code: str, cfg: Optional[Dict[str, Any]] = None, *args, **kwargs) -> Dict[str, Any]:
    """
    Печать настоящей этикетки строго по активному макету из вкладки «Шаблоны»:
    1. Проверяет реальное физическое подключение принтера.
    2. Определяет GTIN из кода ЧЗ и загружает соответствующий шаблон из БД (по привязке GTIN или шаблон по умолчанию).
    3. Берёт из шаблона ширину (`w`), высоту (`h`), `dpi` и список полей (`fields`).
    4. Для режимов `zpl` / `tspl` / `ezpl` генерирует нативные команды принтера по координатам шаблона;
       для режима `windows` (и `auto` на обычных/GDI принтерах) рендерит макет 1-в-1 как на предпросмотре
       и отправляет через драйвер Windows (`QPrinter`).
    """
    if cfg is None:
        cfg = load_config()

    pr_name = (cfg.get("printer_name") or cfg.get("name") or "").strip()
    pr_ip = (cfg.get("printer_ip") or cfg.get("ip") or "").strip()
    pr_port = int(cfg.get("printer_port") or cfg.get("port") or 9100)
    is_conn = cfg.get("connected", True) and cfg.get("online", True)

    if (not is_conn or not pr_name) and not pr_ip:
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
        # 1. Подготавливаем значения полей этикетки (GTIN, SN, ШК, Наименование товара)
        field_values = resolve_label_field_values(code, cfg)
        gtin_val = field_values.get("gtin", "")

        # 2. Загружаем активный шаблон этикетки из БД
        tpl_spec = get_active_template_spec(gtin=gtin_val, store=cfg.get("_store"), template_code=str(cfg.get("template_code") or ""))
        primary_section = {
            "w": tpl_spec["w"],
            "h": tpl_spec["h"],
            "dpi": tpl_spec["dpi"],
            "fields": tpl_spec["fields"],
        }
        sections = [primary_section]
        if tpl_spec.get("print_mode") == "separate" and isinstance(tpl_spec.get("secondary_section"), dict):
            sections.append(tpl_spec["secondary_section"])

        copies = max(1, int(cfg.get("copies") or 1))
        lang = str(cfg.get("printer_lang") or cfg.get("lang") or "auto").lower().strip()
        if lang == "auto":
            lang = detect_language(pr_name) if pr_name else ("tspl" if pr_ip else "windows")

        # 3. Если выбран режим Windows-драйвера (или auto определил GDI-принтер) и печать локальная:
        #    рендерим макет 1-в-1 как в LabelPreviewWidget и отправляем через QPrinter
        if lang == "windows" and not pr_ip:
            res_gdi = _print_sections_via_windows_gdi(
                printer_name=pr_name,
                code=code,
                sections=sections,
                field_values=field_values,
                copies=copies,
            )
            if res_gdi.get("ok"):
                res_gdi["template_name"] = tpl_spec.get("template_name")
                return res_gdi
            log.warning("Fallback после QPrinter GDI на RAW-печать (%s): %s", pr_name, res_gdi.get("info"))

        # 4. Для термопринтеров в режимах ZPL / TSPL / EZPL формируем команды принтера:
        #    для ZPL и TSPL по умолчанию используем WYSIWYG-битмап 1-в-1 (гарантирует кириллицу и точные отступы)
        use_wysiwyg = bool(cfg.get("wysiwyg_bitmap", True))
        payload_parts: List[bytes] = []
        for sec in sections:
            if lang == "zpl":
                if use_wysiwyg:
                    payload_parts.append(build_wysiwyg_zpl_from_template(code, sec, field_values, copies=copies))
                else:
                    payload_parts.append(build_zpl_from_template(code, sec, field_values, copies=copies))
            elif lang == "ezpl":
                payload_parts.append(build_ezpl_from_template(code, sec, field_values, copies=copies))
            else:
                if use_wysiwyg:
                    payload_parts.append(build_wysiwyg_tspl_from_template(code, sec, field_values, copies=copies))
                else:
                    payload_parts.append(build_tspl_from_template(code, sec, field_values, copies=copies))

        full_payload = b"".join(payload_parts)
        res_raw = _send_raw_bytes_to_printer(pr_name, pr_ip, pr_port, full_payload)
        if res_raw.get("ok"):
            res_raw["info"] = (
                f"{res_raw['info']} · Шаблон «{tpl_spec.get('template_name')}» "
                f"({int(tpl_spec['w'])}×{int(tpl_spec['h'])} мм, {tpl_spec['dpi']} DPI, {lang.upper()})"
            )
        return res_raw

    except Exception as e:
        log.error("Ошибка печати на %s: %s", pr_name, e)
        return {"ok": False, "info": f"Сбой отправки на принтер: {e}"}
