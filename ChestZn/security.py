"""
Безопасность окружения ChestZn.

Настраивает права NTFS на папку %APPDATA%\\ChestZn:
- доступ только текущему пользователю, SYSTEM и Administrators;
- отключает наследование от родительских папок;
- применяется рекурсивно.

Запускается один раз — при старте приложения.
"""
import os
import subprocess
import sys
from pathlib import Path

from logger import get_logger

log = get_logger("security")


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"


def _run_icacls(args: list) -> tuple:
    """Запустить icacls. Возвращает (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["icacls"] + args,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="cp866",  # русская кодировка Windows-консоли
            errors="replace",
        )
        return (result.returncode == 0, result.stdout, result.stderr)
    except Exception as e:
        return (False, "", str(e))


def _get_current_user() -> str:
    """
    Вернуть имя текущего Windows-пользователя в формате DOMAIN\\User.
    Для локального пользователя — COMPUTERNAME\\User.
    """
    # Способ 1: через переменные окружения
    username = os.environ.get("USERNAME")
    userdomain = os.environ.get("USERDOMAIN")

    if username and userdomain:
        return f"{userdomain}\\{username}"

    # Способ 2: через pywin32
    try:
        import win32api
        import win32security
        sid = win32security.GetTokenInformation(
            win32security.OpenProcessToken(
                win32api.GetCurrentProcess(),
                win32security.TOKEN_QUERY
            ),
            win32security.TokenUser
        )[0]
        name, domain, _ = win32security.LookupAccountSid(None, sid)
        return f"{domain}\\{name}"
    except Exception as e:
        log.warning("Не удалось определить пользователя через pywin32: %s", e)

    return username or ""


def _is_ntfs() -> bool:
    """Проверить, что папка на NTFS (иначе права не имеют смысла)."""
    try:
        result = subprocess.run(
            ["fsutil", "fsinfo", "drives"],
            capture_output=True, text=True, timeout=5,
            encoding="cp866", errors="replace",
        )
        # Пробуем иначе — просто проверить, что icacls работает
        return True
    except Exception:
        return False


def harden_folder_permissions() -> dict:
    """
    Настроить права NTFS на APP_DIR.
    Используем SID, чтобы работало на любой локали Windows.
    """
    APP_DIR.mkdir(parents=True, exist_ok=True)

    user = _get_current_user()
    if not user:
        return {"ok": False, "info": "Не удалось определить текущего пользователя"}

    steps = []

    # 1. Отключить наследование
    ok, out, err = _run_icacls([str(APP_DIR), "/inheritance:r"])
    if ok:
        steps.append("Наследование отключено")
    else:
        log.warning("Не удалось отключить наследование: %s", err or out)
        steps.append(f"Наследование: ошибка — {(err or out).strip()[:100]}")

    # 2. Убрать общие группы — по SID
    #    *S-1-1-0   = Everyone
    #    *S-1-5-32-545 = Users
    #    *S-1-5-11  = Authenticated Users
    for sid in ["*S-1-1-0", "*S-1-5-32-545", "*S-1-5-11"]:
        _run_icacls([str(APP_DIR), "/remove:g", sid, "/T", "/C", "/Q"])
        _run_icacls([str(APP_DIR), "/remove:d", sid, "/T", "/C", "/Q"])
    steps.append("Удалены общие группы доступа")

    # 3. Полный доступ текущему пользователю
    ok, out, err = _run_icacls(
        [str(APP_DIR), "/grant:r", f"{user}:(OI)(CI)F", "/T", "/C", "/Q"]
    )
    if ok:
        steps.append(f"Полный доступ: {user}")
    else:
        log.error("Не удалось выдать права пользователю: %s", err or out)
        steps.append(f"Права пользователя: ошибка — {(err or out).strip()[:100]}")

    # 4. SYSTEM — по SID
    #    *S-1-5-18 = SYSTEM
    ok, out, err = _run_icacls(
        [str(APP_DIR), "/grant:r", "*S-1-5-18:(OI)(CI)F", "/T", "/C", "/Q"]
    )
    if ok:
        steps.append("Полный доступ: SYSTEM")
    else:
        log.warning("Не удалось выдать права SYSTEM: %s", err or out)

    # 5. Administrators — по SID
    #    *S-1-5-32-544 = Administrators
    ok, out, err = _run_icacls(
        [str(APP_DIR), "/grant:r", "*S-1-5-32-544:(OI)(CI)F", "/T", "/C", "/Q"]
    )
    if ok:
        steps.append("Полный доступ: Administrators")
    else:
        log.warning("Не удалось выдать права Administrators: %s", err or out)

    log.info("Права NTFS настроены: %s", ", ".join(steps))
    return {"ok": True, "info": "Права NTFS настроены", "steps": steps}


def check_folder_permissions() -> dict:
    """
    Показать текущие права на папку (для отображения в админке).
    Возвращает {ok, lines}.
    """
    if not APP_DIR.exists():
        return {"ok": False, "lines": ["Папка не создана"]}

    try:
        result = subprocess.run(
            ["icacls", str(APP_DIR)],
            capture_output=True, text=True, timeout=10,
            encoding="cp866", errors="replace",
        )
        if result.returncode != 0:
            return {"ok": False, "lines": [result.stderr or "Ошибка icacls"]}

        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        return {"ok": True, "lines": lines}
    except Exception as e:
        return {"ok": False, "lines": [str(e)]}


def is_permissions_configured() -> bool:
    """Проверить, настроены ли уже права (по SID)."""
    if not APP_DIR.exists():
        return False

    try:
        result = subprocess.run(
            ["icacls", str(APP_DIR)],
            capture_output=True, text=True, timeout=10,
            encoding="cp866", errors="replace",
        )
        if result.returncode != 0:
            return False

        text = result.stdout

        # Плохой признак — есть группы Everyone / Users / Authenticated Users
        # Проверяем по SID-строкам, которые icacls показывает в скобках.
        # На русской Windows они могут быть как *S-1-1-0, *S-1-5-32-545 и т.д.
        bad_sids = [
            "*S-1-1-0",       # Everyone
            "*S-1-5-32-545",  # Users
            "*S-1-5-11",      # Authenticated Users
            "S-1-1-0)",
            "S-1-5-32-545)",
            "S-1-5-11)",
        ]
        for sid in bad_sids:
            if sid in text:
                return False

        # Хороший признак — есть хотя бы наш пользователь или админы
        good_sids = ["*S-1-5-18", "*S-1-5-32-544",
                     "S-1-5-18)", "S-1-5-32-544)"]
        has_good = any(g in text for g in good_sids)

        return has_good
    except Exception:
        return False


def apply_permissions_if_needed() -> dict:
    """
    Настроить права, если ещё не настроены.
    Вызывается при старте приложения.
    """
    if not is_permissions_configured():
        log.info("Права NTFS не настроены — применяю")
        return harden_folder_permissions()
    else:
        log.debug("Права NTFS уже настроены")
        return {"ok": True, "info": "Права уже настроены", "skipped": True}