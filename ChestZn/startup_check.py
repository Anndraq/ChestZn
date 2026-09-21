"""
Проверка состояния приложения при запуске.
Возвращает список проблем: критичных (блокируют работу)
и предупреждений (можно продолжать).

Каждая проблема содержит:
- name           — название (например, "Ghostscript")
- status         — ok / warning / error
- severity       — None / warning / critical
- message        — краткое описание (например, "Не найден")
- hint           — подробная подсказка
- doc            — файл руководства из docs/ (install-gs.md, ...)
- url            — ссылка на сайт (для скачивания и т.п.)
- url_label      — текст кнопки-ссылки
- requires_admin — True, если для действия нужен пароль админа
"""
import os
import shutil
import subprocess
from pathlib import Path

import db
import crypto


# ---------- Типы проблем ----------
CRITICAL = "critical"      # блокирует запуск
WARNING  = "warning"       # можно продолжать


# ==================================================================
# ПРОВЕРКА 1: Ключ шифрования
# ==================================================================
def check_encryption_key() -> dict:
    """
    Проверить, что ключ шифрования есть и подходит к БД.
    Критическая проверка — без ключа БД недоступна.
    """
    key_file = crypto.KEY_FILE
    db_file  = crypto.DB_FILE

    # 1. Ситуация «нет ключа, нет БД» — это первый запуск, всё ок
    if not key_file.exists() and not db_file.exists():
        return {
            "name": "Ключ шифрования",
            "status": "ok",
            "severity": None,
            "message": "Будет создан при первом запуске",
            "hint": "",
        }

    # 2. Нет ключа, но есть БД — критическая ошибка
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

    # 3. Файл ключа есть — проверим, что он расшифровывается
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
            "hint": f"Файл: {key_file}\n\n"
                    f"Восстановите .keyfile из резервной копии.",
            "doc": "database.md",
        }

    # 4. Ключ расшифрован — проверим, что подходит к БД
    if db_file.exists():
        if not crypto.key_matches_db(key):
            return {
                "name": "Ключ шифрования",
                "status": "error",
                "severity": CRITICAL,
                "message": "Ключ не подходит к базе данных",
                "hint": f"Файл ключа: {key_file}\n"
                        f"Файл БД: {db_file}\n\n"
                        f"Ключ был скопирован с другого компьютера\n"
                        f"или база данных создана с другим ключом.",
                "doc": "database.md",
            }

    return {
        "name": "Ключ шифрования",
        "status": "ok",
        "severity": None,
        "message": "Загружен и подходит к БД",
        "hint": "",
    }


# ==================================================================
# ПРОВЕРКА 2: База данных
# ==================================================================
def check_database() -> dict:
    """
    Проверить, что БД открывается и доступна.
    Критическая проверка — без БД работать нельзя.
    """
    try:
        db_path = db.DB_FILE
        if not db_path.exists():
            # Если и ключа нет, и БД нет — это первый запуск
            return {
                "name": "База данных",
                "status": "ok",
                "severity": None,
                "message": "Будет создана при первом запуске",
                "hint": "",
            }

        with db.get_conn() as conn:
            conn.execute("SELECT 1").fetchone()

        return {
            "name": "База данных",
            "status": "ok",
            "severity": None,
            "message": "Доступна",
            "hint": "",
        }
    except Exception as e:
        return {
            "name": "База данных",
            "status": "error",
            "severity": CRITICAL,
            "message": f"Не удалось открыть: {type(e).__name__}",
            "hint": f"{e}\n\n"
                    f"Возможные причины:\n"
                    f"• ключ шифрования не подходит;\n"
                    f"• файл БД повреждён;\n"
                    f"• нет прав на папку %APPDATA%\\ChestZn.\n\n"
                    f"Обратитесь к администратору.",
            "doc": "database.md",
            "requires_admin": True,
        }


# ==================================================================
# ПРОВЕРКА 3: Принтер
# ==================================================================
def check_printer() -> dict:
    """Принтер настроен? Предупреждение."""
    try:
        from printer import load_config
        cfg = load_config()
        name = cfg.get("printer_name", "").strip()
        ip   = cfg.get("printer_ip", "").strip()

        if name or ip:
            target = name if name else f"{ip}:{cfg.get('printer_port', 9100)}"
            return {
                "name": "Принтер",
                "status": "ok",
                "severity": None,
                "message": f"Настроен: {target}",
                "hint": "",
            }

        return {
            "name": "Принтер",
            "status": "warning",
            "severity": WARNING,
            "message": "Не настроен",
            "hint": "Автопечать работать не будет.\n"
                    "Откройте: Админ → Принтер → Выбрать → Сохранить.",
            "doc": "printer.md",
        }
    except Exception as e:
        return {
            "name": "Принтер",
            "status": "warning",
            "severity": WARNING,
            "message": f"Ошибка проверки: {type(e).__name__}",
            "hint": f"{e}",
            "doc": "printer.md",
        }


# ==================================================================
# ПРОВЕРКА 4: Ghostscript
# ==================================================================
def check_ghostscript() -> dict:
    """Ghostscript доступен? Предупреждение."""
    gs = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")

    if not gs:
        candidates = []
        for root in [
            Path(r"C:\Program Files\gs"),
            Path(r"C:\Program Files (x86)\gs"),
        ]:
            if root.exists():
                for sub in root.iterdir():
                    if sub.is_dir():
                        candidates.append(sub / "bin" / "gswin64c.exe")
                        candidates.append(sub / "bin" / "gswin32c.exe")

        for c in candidates:
            if c.exists():
                gs = str(c)
                break

    if not gs:
        return {
            "name": "Ghostscript",
            "status": "warning",
            "severity": WARNING,
            "message": "Не найден",
            "hint": "DataMatrix не будет рендериться.\n"
                    "Установите Ghostscript 10.x — подробнее в руководстве.",
            "doc": "install-gs.md",
            "url": "https://www.ghostscript.com/releases/gsdnld.html",
            "url_label": "Скачать Ghostscript",
        }

    try:
        result = subprocess.run(
            [gs, "--version"],
            capture_output=True,
            timeout=5,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        version = (result.stdout or "").strip().splitlines()[0] if result.stdout else "?"
        return {
            "name": "Ghostscript",
            "status": "ok",
            "severity": None,
            "message": f"Версия {version}",
            "hint": gs,
        }
    except Exception as e:
        return {
            "name": "Ghostscript",
            "status": "warning",
            "severity": WARNING,
            "message": f"Не запускается: {type(e).__name__}",
            "hint": f"{e}\n\nПуть: {gs}",
            "doc": "install-gs.md",
            "url": "https://www.ghostscript.com/releases/gsdnld.html",
            "url_label": "Скачать Ghostscript",
        }


# ==================================================================
# ОБЩАЯ ПРОВЕРКА
# ==================================================================
def check_all() -> list:
    """Вернуть список результатов."""
    return [
        check_encryption_key(),
        check_database(),
        check_printer(),
        check_ghostscript(),
    ]


def has_critical(problems: list) -> bool:
    return any(p.get("severity") == CRITICAL for p in problems)


def has_warnings(problems: list) -> bool:
    return any(p.get("severity") == WARNING for p in problems)


def has_any_problems(problems: list) -> bool:
    return any(p.get("status") != "ok" for p in problems)


# ==================================================================
# ДЛЯ ТЕСТА ИЗ КОМАНДНОЙ СТРОКИ
# ==================================================================
if __name__ == "__main__":
    print("=== Проверка состояния приложения ===\n")
    for p in check_all():
        icon = {"ok": "✓", "warning": "⚠", "error": "❌"}.get(p["status"], "?")
        print(f"{icon} {p['name']}: {p['message']}")
        if p.get("hint"):
            for line in p["hint"].splitlines():
                print(f"    {line}")
        if p.get("doc"):
            print(f"    [руководство: {p['doc']}]")
        if p.get("url"):
            print(f"    [ссылка: {p['url']}]")
        print()

    problems = check_all()
    if has_critical(problems):
        print(">>> КРИТИЧЕСКИЕ ПРОБЛЕМЫ — запуск должен быть заблокирован")
    elif has_warnings(problems):
        print(">>> Есть предупреждения — можно работать")
    else:
        print(">>> Всё в порядке")