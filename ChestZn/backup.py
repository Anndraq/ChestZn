"""
Резервное копирование БД ChestZn.
Безопасный бэкап работающей SQLite через sqlite3.backup().
"""
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import db
from logger import get_logger

log = get_logger("backup")


BACKUP_DIR = db.APP_DIR / "backups"
MAX_BACKUPS = 30
AUTO_BACKUP_INTERVAL_HOURS = 24


def ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def make_backup_path() -> Path:
    ensure_backup_dir()
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return BACKUP_DIR / f"chestzn_{ts}.db"


def create_backup() -> dict:
    """
    Создать бэкап работающей БД.
    Возвращает {ok, path, name, size_kb, info}
    """
    src = db.DB_FILE
    if not src.exists():
        log.error("Бэкап не создан: файл БД не найден (%s)", src)
        return {"ok": False, "info": "Файл БД не найден"}

    dst = make_backup_path()
    try:
        # Безопасный бэкап через sqlite3 — работает при запущенном приложении
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        size_kb = dst.stat().st_size // 1024
        log.info("Бэкап создан: %s (%s КБ)", dst.name, size_kb)

        cleanup_old_backups()

        return {
            "ok": True,
            "path": str(dst),
            "name": dst.name,
            "size_kb": size_kb,
            "info": f"Создан {dst.name} ({size_kb} КБ)",
        }
    except Exception as e:
        log.error("Ошибка бэкапа: %s", e)
        return {"ok": False, "info": str(e)}


def list_backups() -> list:
    """Список бэкапов, свежие сверху."""
    ensure_backup_dir()
    files = []
    for p in BACKUP_DIR.glob("chestzn_*.db"):
        try:
            st = p.stat()
            files.append({
                "path": str(p),
                "name": p.name,
                "size_kb": st.st_size // 1024,
                "mtime": datetime.fromtimestamp(st.st_mtime),
            })
        except Exception:
            pass
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


def cleanup_old_backups():
    """Удалить лишние бэкапы, оставить MAX_BACKUPS последних."""
    files = list_backups()
    if len(files) <= MAX_BACKUPS:
        return
    deleted = 0
    for f in files[MAX_BACKUPS:]:
        try:
            os.remove(f["path"])
            deleted += 1
        except Exception as e:
            log.error("Не удалось удалить %s: %s", f["name"], e)
    if deleted:
        log.info("Удалено старых бэкапов: %s", deleted)


def last_backup_time() -> Optional[datetime]:
    """Время последнего бэкапа или None."""
    files = list_backups()
    return files[0]["mtime"] if files else None


def auto_backup_if_needed() -> Optional[dict]:
    """Сделать бэкап, если прошло больше AUTO_BACKUP_INTERVAL_HOURS."""
    last = last_backup_time()
    if last is None:
        log.info("Автобэкап: бэкапов ещё нет — создаём первый")
        return create_backup()

    hours_passed = (datetime.now() - last).total_seconds() / 3600
    if hours_passed >= AUTO_BACKUP_INTERVAL_HOURS:
        log.info("Автобэкап: прошло %.1f ч — создаём", hours_passed)
        return create_backup()
    else:
        log.debug("Автобэкап: пропускаем (прошло %.1f ч)", hours_passed)
        return None


def restore_backup(backup_path: str) -> dict:
    """
    Восстановить БД из бэкапа.
    Требует перезапуска приложения после.
    Текущая БД сохраняется как chestzn_before_restore_*.db.
    """
    src = Path(backup_path)
    if not src.exists():
        log.error("Восстановление: файл не найден (%s)", backup_path)
        return {"ok": False, "info": "Файл бэкапа не найден"}

    if not src.name.startswith("chestzn_"):
        log.warning("Восстановление: подозрительное имя файла (%s)", src.name)

    dst = db.DB_FILE

    try:
        # 1. Сохраняем текущую БД на всякий случай
        if dst.exists():
            safety = BACKUP_DIR / (
                f"chestzn_before_restore_"
                f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
            )
            shutil.copy2(str(dst), str(safety))
            log.info("Текущая БД сохранена: %s", safety.name)

        # 2. Удаляем WAL/SHM — иначе конфликт с новым файлом
        for suffix in ("-wal", "-shm"):
            f = Path(str(dst) + suffix)
            if f.exists():
                try:
                    f.unlink()
                    log.debug("Удалён %s", f.name)
                except Exception as e:
                    log.warning("Не удалось удалить %s: %s", f.name, e)

        # 3. Копируем бэкап на место основной БД
        shutil.copy2(str(src), str(dst))

        size_kb = dst.stat().st_size // 1024
        log.info("БД восстановлена из %s (%s КБ)", src.name, size_kb)

        return {
            "ok": True,
            "info": f"Восстановлено из {src.name} ({size_kb} КБ). "
                    f"Перезапустите приложение.",
        }

    except Exception as e:
        log.error("Ошибка восстановления: %s", e)
        return {"ok": False, "info": str(e)}