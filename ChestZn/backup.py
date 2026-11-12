"""
Резервное копирование SQLite базы данных Dotmark.
- Автоматическое создание бэкапа: раз в 2 недели (14 дней = 336 часов).
- Автоматическое удаление старых бэкапов: раз в полгода (удаляются бэкапы старше 6 месяцев = 180 дней).
"""
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import db
from logger import get_logger
log = get_logger("backup")


def get_app_dir() -> Path:
    if hasattr(db, "APP_DIR") and getattr(db, "APP_DIR"):
        return Path(getattr(db, "APP_DIR"))
    return Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"


def get_db_path() -> Path:
    """Надёжно определяет путь к активной базе данных Dotmark."""
    for attr in ("DB_PATH", "DB_FILE", "db_path", "DATABASE_PATH"):
        if hasattr(db, attr) and getattr(db, attr):
            p = Path(getattr(db, attr))
            if p.exists():
                return p

    # Пробуем узнать точный путь через активное подключение к SQLite
    try:
        if hasattr(db, "get_conn"):
            with db.get_conn() as conn:
                for row in conn.execute("PRAGMA database_list").fetchall():
                    # row: (seq, name, file)
                    db_name = row[1] if isinstance(row, (list, tuple)) else row["name"]
                    db_file = row[2] if isinstance(row, (list, tuple)) else row["file"]
                    if db_name == "main" and db_file:
                        return Path(db_file)
    except Exception:
        pass

    return get_app_dir() / "chestzn.db"


APP_DIR = get_app_dir()
BACKUP_DIR = APP_DIR / "backups"
MAX_BACKUPS = 50
AUTO_BACKUP_INTERVAL_DAYS = 14
AUTO_BACKUP_INTERVAL_HOURS = AUTO_BACKUP_INTERVAL_DAYS * 24  # 336 часов (2 недели)
BACKUP_RETENTION_DAYS = 180  # 6 месяцев (полгода)


def ensure_backup_dir() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def make_backup_path(prefix: str = "chestzn") -> Path:
    ensure_backup_dir()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return BACKUP_DIR / f"{prefix}_{stamp}.db"


def create_backup(is_auto: bool = False) -> Dict[str, Any]:
    """
    Создать бэкап работающей БД через sqlite3.backup().
    После создания автоматически проверяет и удаляет бэкапы старше 6 месяцев (180 дней).
    Возвращает {ok, path, name, size_kb, deleted_old, info}.
    """
    db_file = get_db_path()
    if not db_file.exists():
        log.warning("Бэкап не создан: файл БД не найден (%s)", db_file)
        return {"ok": False, "info": f"Файл БД не найден ({db_file.name})"}

    prefix = "chestzn_auto" if is_auto else "chestzn"
    target = make_backup_path(prefix=prefix)
    try:
        src = sqlite3.connect(str(db_file), timeout=10.0)
        dst = sqlite3.connect(str(target))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

        size_kb = max(1, target.stat().st_size // 1024)
        kind_label = "Автобэкап (раз в 2 недели)" if is_auto else "Бэкап"
        log.info("%s создан: %s (%s КБ)", kind_label, target.name, size_kb)

        cleanup_res = cleanup_old_backups(max_age_days=BACKUP_RETENTION_DAYS)
        deleted_old = cleanup_res.get("deleted_count", 0)

        try:
            if hasattr(db, "get_conn"):
                with db.get_conn() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_auto_backup', ?)",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
                    )
                    conn.commit()
        except Exception:
            pass

        extra_msg = f" · удалено старых (>6 мес.): {deleted_old}" if deleted_old > 0 else ""
        return {
            "ok": True,
            "path": str(target),
            "name": target.name,
            "size_kb": size_kb,
            "deleted_old": deleted_old,
            "info": f"Создан {target.name} ({size_kb} КБ){extra_msg}",
        }
    except Exception as e:
        log.error("Ошибка бэкапа: %s", e)
        return {"ok": False, "info": str(e)}


def list_backups() -> List[Dict[str, Any]]:
    """Список всех файлов бэкапов, отсортированный от самых новых к старым."""
    ensure_backup_dir()
    files = []
    now = datetime.now()
    for p in BACKUP_DIR.glob("chestzn_*.db"):
        try:
            stat = p.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)
            age_days = max(0, (now - mtime).days)
            files.append({
                "name": p.name,
                "path": str(p),
                "size_kb": max(1, stat.st_size // 1024),
                "mtime": mtime,
                "age_days": age_days,
            })
        except Exception:
            pass
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def cleanup_old_backups(max_age_days: int = BACKUP_RETENTION_DAYS) -> Dict[str, Any]:
    """
    Автоматически удаляет бэкапы, которым уже исполнилось 6 месяцев (max_age_days = 180 дней) и более,
    а также ограничивает общее число файлов при превышении MAX_BACKUPS.
    Всегда оставляет как минимум 1 самый свежий бэкап.
    """
    files = list_backups()
    if len(files) <= 1:
        return {"ok": True, "deleted_count": 0, "deleted_names": [], "info": "Удаление не требуется"}

    cutoff = datetime.now() - timedelta(days=max_age_days)
    deleted_names = []

    for idx, f in enumerate(files[1:], start=1):
        should_delete_by_age = f["mtime"] <= cutoff
        should_delete_by_count = idx >= MAX_BACKUPS
        if should_delete_by_age or should_delete_by_count:
            try:
                Path(f["path"]).unlink(missing_ok=True)
                deleted_names.append(f["name"])
            except Exception as e:
                log.warning("Не удалось удалить старый бэкап %s: %s", f["name"], e)

    if deleted_names:
        log.info("Автоудаление старых бэкапов (>%s дн.): удалено %s шт.", max_age_days, len(deleted_names))
        try:
            if hasattr(db, "get_conn"):
                with db.get_conn() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_backup_cleanup', ?)",
                        (f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (удалено: {len(deleted_names)})",)
                    )
                    conn.commit()
        except Exception:
            pass

    return {
        "ok": True,
        "deleted_count": len(deleted_names),
        "deleted_names": deleted_names,
        "info": f"Удалено бэкапов старше {max_age_days} дн. (6 мес.): {len(deleted_names)}",
    }


def last_backup_time() -> Optional[datetime]:
    """Время создания самого свежего бэкапа или None."""
    files = list_backups()
    if not files:
        return None
    return files[0]["mtime"]


def auto_backup_if_needed(
    interval_days: int = AUTO_BACKUP_INTERVAL_DAYS,
    retention_days: int = BACKUP_RETENTION_DAYS,
) -> Optional[Dict[str, Any]]:
    """
    Проверяет:
    1. Есть ли бэкапы старше 6 месяцев (180 дней) — если есть, удаляет их.
    2. Прошло ли 2 недели (14 дней) с момента последнего бэкапа — если прошло (или бэкапов нет),
       автоматически создаёт новый бэкап.
    """
    try:
        cleanup_old_backups(max_age_days=retention_days)
    except Exception as e:
        log.warning("Ошибка плановой очистки старых бэкапов: %s", e)

    last = last_backup_time()
    if last is None:
        log.info("Автобэкап (14 дн.): бэкапов ещё нет — создаём первый")
        return create_backup(is_auto=True)

    age_days = (datetime.now() - last).total_seconds() / (3600.0 * 24.0)
    if age_days >= float(interval_days):
        log.info("Автобэкап: прошло %.1f дн. (порог %d дн.) — создаём новый бэкап", age_days, interval_days)
        return create_backup(is_auto=True)

    return None


def restore_backup(backup_path: str) -> Dict[str, Any]:
    """
    Восстановить БД из выбранного бэкапа.
    Текущая БД предварительно сохраняется как chestzn_before_restore_*.db.
    """
    src_path = Path(backup_path)
    if not src_path.exists():
        log.error("Восстановление: файл не найден (%s)", backup_path)
        return {"ok": False, "info": "Файл бэкапа не найден"}

    target_db = get_db_path()
    try:
        if target_db.exists():
            ensure_backup_dir()
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safety = BACKUP_DIR / f"chestzn_before_restore_{stamp}.db"
            shutil.copy2(str(target_db), str(safety))
            log.info("Текущая БД сохранена перед восстановлением: %s", safety.name)

        for suffix in ("-wal", "-shm"):
            aux = Path(str(target_db) + suffix)
            if aux.exists():
                try:
                    aux.unlink()
                except Exception as e:
                    log.warning("Не удалось удалить %s: %s", aux.name, e)

        shutil.copy2(str(src_path), str(target_db))
        size_kb = max(1, target_db.stat().st_size // 1024)
        log.info("БД восстановлена из %s (%s КБ)", src_path.name, size_kb)
        return {
            "ok": True,
            "info": f"Восстановлено из {src_path.name} ({size_kb} КБ). Перезапустите приложение.",
        }
    except Exception as e:
        log.error("Ошибка восстановления: %s", e)
        return {"ok": False, "info": str(e)}
