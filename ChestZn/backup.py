"""
Резервное копирование SQLite базы данных ChestZn.
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

APP_DIR = db.APP_DIR
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
    if not db.DB_PATH.exists():
        log.warning("Бэкап не создан: файл БД не найден (%s)", db.DB_PATH)
        return {"ok": False, "info": "Файл БД не найден"}

    prefix = "chestzn_auto" if is_auto else "chestzn"
    target = make_backup_path(prefix=prefix)
    try:
        src = sqlite3.connect(str(db.DB_PATH), timeout=10.0)
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

    # Всегда сохраняем самый свежий бэкап (files[0]), проверяем остальные
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

    log.info("Автобэкап не требуется: с последнего бэкапа прошло %.1f дн. из %d дн.", age_days, interval_days)
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

    try:
        if db.DB_PATH.exists():
            ensure_backup_dir()
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safety = BACKUP_DIR / f"chestzn_before_restore_{stamp}.db"
            shutil.copy2(str(db.DB_PATH), str(safety))
            log.info("Текущая БД сохранена перед восстановлением: %s", safety.name)

        for suffix in ("-wal", "-shm"):
            aux = Path(str(db.DB_PATH) + suffix)
            if aux.exists():
                try:
                    aux.unlink()
                except Exception as e:
                    log.warning("Не удалось удалить %s: %s", aux.name, e)

        shutil.copy2(str(src_path), str(db.DB_PATH))
        size_kb = max(1, db.DB_PATH.stat().st_size // 1024)
        log.info("БД восстановлена из %s (%s КБ)", src_path.name, size_kb)
        return {
            "ok": True,
            "info": f"Восстановлено из {src_path.name} ({size_kb} КБ). Перезапустите приложение.",
        }
    except Exception as e:
        log.error("Ошибка восстановления: %s", e)
        return {"ok": False, "info": str(e)}