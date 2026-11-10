"""
Неблокирующая проверка состояния приложения при запуске и в реальном времени (startup_check.py).
Оптимизировано:
- Статические проверки (ключ шифрования, БД, Ghostscript) кэшируются в памяти и не запускают
  дочерние процессы (gswin64c) повторно в фоновом цикле.
- Проверки принтера и сканера мгновенно (< 0.1 мс) читают асинхронный кэш device_discovery
  без блокировки главного потока Qt UI.
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

import db
import crypto

CRITICAL = "critical"      # блокирует запуск
WARNING  = "warning"       # можно продолжать

_CACHED_KEY_CHECK: Optional[Dict[str, Any]] = None
_CACHED_DB_CHECK: Optional[Dict[str, Any]] = None
_CACHED_GS_CHECK: Optional[Dict[str, Any]] = None


def check_encryption_key(force: bool = False) -> dict:
    """Проверить, что ключ шифрования есть и подходит к БД (кэшируется после первой успешной проверки)."""
    global _CACHED_KEY_CHECK
    if _CACHED_KEY_CHECK is not None and not force and _CACHED_KEY_CHECK.get("status") == "ok":
        return _CACHED_KEY_CHECK

    key_file = crypto.KEY_FILE
    db_file  = crypto.DB_FILE

    if not key_file.exists() and not db_file.exists():
        res = {
            "name": "Ключ шифрования",
            "status": "ok",
            "severity": None,
            "message": "Будет создан при первом запуске",
            "hint": "",
        }
        _CACHED_KEY_CHECK = res
        return res

    if not key_file.exists() and db_file.exists():
        return {
            "name": "Ключ шифрования",
            "status": "error",
            "severity": CRITICAL,
            "message": "Файл ключа отсутствует, но БД существует",
            "hint": f"Файл ключа: {key_file}\n"
                    f"Файл БД: {db_file}\n\n"
                    f"Восстановите .keyfile из резервной копии.\n"
                    f"НЕ удаляйте БД — данные ещё можно восстановить.",
            "doc": "database.md",
            "url_label": "Как восстановить ключ",
        }

    try:
        key = crypto.load_key()
    except crypto.KeyCorruptedError as e:
        return {
            "name": "Ключ шифрования",
            "status": "error",
            "severity": CRITICAL,
            "message": "Файл ключа повреждён",
            "hint": f"Файл: {key_file}\n\n{e}",
            "doc": "database.md",
        }

    if key is None:
        return {
            "name": "Ключ шифрования",
            "status": "error",
            "severity": CRITICAL,
            "message": "Не удалось загрузить ключ",
            "hint": f"Файл: {key_file}\n\nВосстановите .keyfile из резервной копии.",
            "doc": "database.md",
        }

    if db_file.exists():
        if not crypto.key_matches_db(key):
            return {
                "name": "Ключ шифрования",
                "status": "error",
                "severity": CRITICAL,
                "message": "Ключ не подходит к базе данных",
                "hint": f"Файл ключа: {key_file}\nФайл БД: {db_file}\n\n"
                        f"Ключ был скопирован с другого компьютера или база данных создана с другим ключом.",
                "doc": "database.md",
            }

    res = {
        "name": "Ключ шифрования",
        "status": "ok",
        "severity": None,
        "message": "Загружен и подходит к БД",
        "hint": "",
    }
    _CACHED_KEY_CHECK = res
    return res


def check_database(force: bool = False) -> dict:
    """Проверить, что БД открывается и доступна (кэшируется после первой успешной проверки)."""
    global _CACHED_DB_CHECK
    if _CACHED_DB_CHECK is not None and not force and _CACHED_DB_CHECK.get("status") == "ok":
        return _CACHED_DB_CHECK

    try:
        db_path = db.DB_FILE
        if not db_path.exists():
            res = {
                "name": "База данных",
                "status": "ok",
                "severity": None,
                "message": "Будет создана при первом запуске",
                "hint": "",
            }
            _CACHED_DB_CHECK = res
            return res

        with db.get_conn() as conn:
            conn.execute("SELECT 1").fetchone()

        res = {
            "name": "База данных",
            "status": "ok",
            "severity": None,
            "message": "Доступна",
            "hint": "",
        }
        _CACHED_DB_CHECK = res
        return res
    except Exception as e:
        return {
            "name": "База данных",
            "status": "error",
            "severity": CRITICAL,
            "message": f"Не удалось открыть: {type(e).__name__}",
            "hint": f"{e}\n\nВозможные причины:\n• ключ шифрования не подходит;\n• файл БД повреждён;\n• нет прав на папку %APPDATA%\\ChestZn.\n\nОбратитесь к администратору.",
            "doc": "database.md",
            "requires_admin": True,
        }


def check_printer(force_refresh: bool = False) -> dict:
    """Мгновенная неблокирующая проверка принтера через кэш device_discovery."""
    try:
        from device_discovery import detect_local_printers
        from printer import load_config

        connected = detect_local_printers(connected_only=True, force_refresh=force_refresh)
        if connected:
            pr_name = connected[0].get("name", "Принтер")
            return {
                "name": "Принтер",
                "status": "ok",
                "severity": None,
                "message": f"Подключён: {pr_name}",
                "hint": "",
            }

        cfg = load_config()
        saved_name = cfg.get("printer_name", "").strip()
        hint_text = "Принтер отключён от питания или USB." if saved_name else "Подключите USB-кабель и включите питание."

        return {
            "name": "Принтер",
            "status": "warning",
            "severity": WARNING,
            "message": "Не подключён (все принтеры выключены)",
            "hint": hint_text,
            "doc": "printer.md",
        }
    except Exception as e:
        return {
            "name": "Принтер",
            "status": "warning",
            "severity": WARNING,
            "message": "Не подключён",
            "hint": str(e),
            "doc": "printer.md",
        }


def check_scanner(force_refresh: bool = False) -> dict:
    """Мгновенная неблокирующая проверка сканера через кэш device_discovery."""
    try:
        from device_discovery import detect_local_scanners
        scanners = detect_local_scanners(connected_only=True, force_refresh=force_refresh)
        if scanners:
            sc_name = scanners[0].get("name", "Сканер ШК")
            conn_type = scanners[0].get("connect_type", "")
            type_str = f" ({conn_type})" if conn_type else ""
            return {
                "name": "Сканер",
                "status": "ok",
                "severity": None,
                "message": f"Подключён: {sc_name}{type_str}",
                "hint": "",
            }

        return {
            "name": "Сканер",
            "status": "warning",
            "severity": WARNING,
            "message": "Не подключён (сканер не обнаружен)",
            "hint": "Подключите USB-сканер штрихкодов или активируйте Bluetooth SPP / COM-порт.",
            "doc": "problems.md",
        }
    except Exception as e:
        return {
            "name": "Сканер",
            "status": "warning",
            "severity": WARNING,
            "message": "Не подключён",
            "hint": str(e),
            "doc": "problems.md",
        }


def check_ghostscript(force: bool = False) -> dict:
    """Проверка наличия Ghostscript (результат кэшируется в памяти, чтобы не вызывать subprocess повторно)."""
    global _CACHED_GS_CHECK
    if _CACHED_GS_CHECK is not None and not force:
        return _CACHED_GS_CHECK

    gs = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")

    if not gs and os.name == "nt":
        for base in [r"C:\Program Files\gs", r"C:\Program Files (x86)\gs"]:
            if os.path.isdir(base):
                for sub in sorted(os.listdir(base), reverse=True):
                    for exe in ("gswin64c.exe", "gswin32c.exe"):
                        candidate = os.path.join(base, sub, "bin", exe)
                        if os.path.isfile(candidate):
                            gs = candidate
                            break
                    if gs:
                        break

    if gs:
        _CACHED_GS_CHECK = {
            "name": "Ghostscript",
            "status": "ok",
            "severity": None,
            "message": f"Найден ({Path(gs).name})",
            "hint": gs,
        }
    else:
        _CACHED_GS_CHECK = {
            "name": "Ghostscript",
            "status": "warning",
            "severity": WARNING,
            "message": "Не найден",
            "hint": "Ghostscript используется для разбора EPS-файлов с DataMatrix.",
            "doc": "install-gs.md",
            "url": "https://www.ghostscript.com/releases/gsdnld.html",
            "url_label": "Скачать Ghostscript",
        }
    return _CACHED_GS_CHECK


def check_all(force_refresh: bool = False) -> list:
    """Вернуть список результатов всех проверок (< 0.1 мс из кэша в штатном режиме)."""
    return [
        check_encryption_key(force=force_refresh),
        check_database(force=force_refresh),
        check_printer(force_refresh=force_refresh),
        check_scanner(force_refresh=force_refresh),
        check_ghostscript(force=force_refresh),
    ]


def has_critical(problems: list) -> bool:
    return any(p.get("severity") == CRITICAL for p in problems)


def has_warnings(problems: list) -> bool:
    return any(p.get("severity") == WARNING for p in problems)


def has_any_problems(problems: list) -> bool:
    return any(p.get("status") != "ok" for p in problems)