"""Синхронизация мастер ↔ точки через общую папку (JSON-файлы).

Схема:
    sync/master/outbox/to_T-01.json   # мастер → точка
    sync/master/inbox/from_T-01.json  # точка → мастер
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from logger import get_logger

log = get_logger("sync")

SYNC_VERSION = 1


# ==========================================================
# Пути
# ==========================================================
def _default_folder() -> Path:
    base = Path(__file__).resolve().parent
    return base / "sync"


def get_sync_folder(store=None) -> Path:
    """Папка обмена. Переопределяется через settings['sync_folder']."""
    if store is not None:
        try:
            custom = store.get_setting("sync_folder")
            if custom:
                return Path(custom)
        except Exception:
            pass
    return _default_folder()


def ensure_layout(folder: Path) -> None:
    (folder / "master" / "outbox").mkdir(parents=True, exist_ok=True)
    (folder / "master" / "inbox").mkdir(parents=True, exist_ok=True)


def outbox_path(folder: Path, point_id: str) -> Path:
    return folder / "master" / "outbox" / f"to_{point_id}.json"


def inbox_path(folder: Path, point_id: str) -> Path:
    return folder / "master" / "inbox" / f"from_{point_id}.json"


# ==========================================================
# Атомарная запись / чтение
# ==========================================================
def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("Не удалось прочитать %s: %s", path, e)
        return None


# ==========================================================
# Мастер → точка
# ==========================================================
def export_for_point(store, point_id: str,
                     folder: Optional[Path] = None,
                     only_new: bool = True) -> dict:
    """Выгрузить коды, назначенные точке, в outbox/to_{point}.json."""
    folder = folder or get_sync_folder(store)
    ensure_layout(folder)

    since = None
    if only_new:
        since = store.get_setting(f"last_export_{point_id}")

    try:
        codes = store.get_codes_for_export(point_id, since=since)
    except Exception as e:
        log.error("export_for_point(%s): %s", point_id, e)
        return {"ok": False, "info": str(e), "count": 0}

    data = {
        "version": SYNC_VERSION,
        "direction": "master_to_point",
        "point": point_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(codes),
        "codes": codes,
    }

    try:
        _write_json(outbox_path(folder, point_id), data)
    except Exception as e:
        log.error("export_for_point: запись %s", e)
        return {"ok": False, "info": f"Ошибка записи: {e}", "count": 0}

    # запоминаем момент выгрузки
    store.set_setting(
        f"last_export_{point_id}",
        datetime.now().isoformat(timespec="seconds")
    )

    return {"ok": True,
            "info": f"{point_id}: выгружено кодов — {len(codes)}",
            "count": len(codes)}


def export_for_all_points(store,
                          folder: Optional[Path] = None,
                          only_new: bool = True) -> dict:
    folder = folder or get_sync_folder(store)
    ensure_layout(folder)

    total = 0
    lines = []
    for p in store.points:
        r = export_for_point(store, p["id"], folder, only_new=only_new)
        lines.append(f"  {p['id']}: {r.get('count', 0)}")
        if r["ok"]:
            total += r.get("count", 0)

    return {"ok": True,
            "info": f"Всего выгружено: {total}\n" + "\n".join(lines),
            "count": total}


# ==========================================================
# Точка → мастер
# ==========================================================
def export_results_from_point(store, point_id: str,
                              folder: Optional[Path] = None,
                              only_new: bool = True) -> dict:
    """Точка пишет результаты печати в inbox/from_{point}.json."""
    folder = folder or get_sync_folder(store)
    ensure_layout(folder)

    since = None
    if only_new:
        since = store.get_setting(f"last_report_{point_id}")

    try:
        printed = store.get_printed_for_master(point_id, since=since)
    except Exception as e:
        log.error("export_results_from_point(%s): %s", point_id, e)
        return {"ok": False, "info": str(e), "count": 0}

    data = {
        "version": SYNC_VERSION,
        "direction": "point_to_master",
        "point": point_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(printed),
        "printed": printed,
    }

    try:
        _write_json(inbox_path(folder, point_id), data)
    except Exception as e:
        return {"ok": False, "info": f"Ошибка записи: {e}", "count": 0}

    store.set_setting(
        f"last_report_{point_id}",
        datetime.now().isoformat(timespec="seconds")
    )

    return {"ok": True,
            "info": f"{point_id}: отправлено записей — {len(printed)}",
            "count": len(printed)}


# ==========================================================
# Импорт
# ==========================================================
def import_from_master(store, point_id: str,
                       folder: Optional[Path] = None) -> dict:
    """Точка читает outbox/to_{point}.json и применяет у себя."""
    folder = folder or get_sync_folder(store)

    data = _read_json(outbox_path(folder, point_id))
    if not data:
        return {"ok": True, "info": "Нет файла от мастера", "count": 0}

    codes = data.get("codes", [])
    try:
        added = store.apply_incoming_codes(point_id, codes)
    except Exception as e:
        log.error("import_from_master(%s): %s", point_id, e)
        return {"ok": False, "info": str(e), "count": 0}

    return {"ok": True,
            "info": f"{point_id}: принято кодов — {added}",
            "count": added}


def import_results_at_master(store,
                             folder: Optional[Path] = None) -> dict:
    """Мастер читает все inbox/*.json и применяет результаты."""
    folder = folder or get_sync_folder(store)
    inbox = folder / "master" / "inbox"
    if not inbox.exists():
        return {"ok": True, "info": "Нет входящих файлов", "count": 0}

    total = 0
    lines = []
    for p in sorted(inbox.glob("from_*.json")):
        # from_T-01.json → T-01
        name = p.stem
        point_id = name[5:] if name.startswith("from_") else name

        data = _read_json(p)
        if not data:
            continue

        printed = data.get("printed", [])
        try:
            applied = store.apply_incoming_printed(point_id, printed)
        except Exception as e:
            log.error("import_results_at_master(%s): %s", point_id, e)
            applied = 0

        lines.append(f"  {point_id}: {applied}")
        total += applied

    return {"ok": True,
            "info": f"Всего обновлено: {total}\n" + "\n".join(lines),
            "count": total}