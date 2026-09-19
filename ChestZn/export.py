"""
Экспорт данных ChestZn в CSV.
UTF-8 с BOM, разделитель ';' — для корректного открытия в Excel.
"""
import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

import db
from logger import get_logger

log = get_logger("export")


# Разделитель ';' — Excel в RU-локали понимает его по умолчанию
CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"    # UTF-8 with BOM


def _ask_save_path(parent, default_name: str, title: str) -> str:
    """Спросить путь сохранения. Пустая строка — отмена."""
    path, _ = QFileDialog.getSaveFileName(
        parent,
        title,
        default_name,
        "CSV файлы (*.csv);;Все файлы (*)"
    )
    return path


def _write_csv(path: str, headers: list, rows: list) -> int:
    """Записать CSV. Возвращает количество строк (без заголовка)."""
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.writer(f, delimiter=CSV_DELIMITER)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
    return len(rows)


def export_audit(parent=None) -> dict:
    """Экспорт журнала операций."""
    default = f"chestzn_journal_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv"
    path = _ask_save_path(parent, default, "Экспорт журнала операций")
    if not path:
        return {"ok": False, "info": "Отменено"}

    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT time, action, point, operator, gtin, sn, result, created_at "
                "FROM audit ORDER BY id DESC"
            ).fetchall()

        headers = ["Время", "Действие", "Точка", "Оператор",
                   "GTIN", "Серия", "Результат", "Создано"]
        data = [[r["time"], r["action"], r["point"], r["operator"],
                 r["gtin"], r["sn"], r["result"], r["created_at"]]
                for r in rows]

        n = _write_csv(path, headers, data)
        log.info("Экспорт журнала: %s строк → %s", n, path)
        return {"ok": True, "info": f"Экспортировано {n} строк", "count": n}

    except Exception as e:
        log.error("Ошибка экспорта журнала: %s", e)
        return {"ok": False, "info": str(e)}


def export_pool(parent=None) -> dict:
    """Экспорт пула кодов ЧЗ."""
    default = f"chestzn_pool_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv"
    path = _ask_save_path(parent, default, "Экспорт пула кодов")
    if not path:
        return {"ok": False, "info": "Отменено"}

    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, gtin, sn, code, used, printed, point, printed_at, created_at "
                "FROM pool_codes ORDER BY id"
            ).fetchall()

        headers = ["ID", "GTIN", "Серия", "Код ЧЗ", "Использован",
                   "Напечатан", "Точка", "Напечатан в", "Создан"]
        data = [
            [r["id"], r["gtin"], r["sn"], r["code"],
             "да" if r["used"] else "нет",
             "да" if r["printed"] else "нет",
             r["point"] or "", r["printed_at"] or "", r["created_at"]]
            for r in rows
        ]

        n = _write_csv(path, headers, data)
        log.info("Экспорт пула: %s строк → %s", n, path)
        return {"ok": True, "info": f"Экспортировано {n} строк", "count": n}

    except Exception as e:
        log.error("Ошибка экспорта пула: %s", e)
        return {"ok": False, "info": str(e)}


def export_queue(parent=None) -> dict:
    """Экспорт очереди синхронизации."""
    default = f"chestzn_queue_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.csv"
    path = _ask_save_path(parent, default, "Экспорт очереди")
    if not path:
        return {"ok": False, "info": "Отменено"}

    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, time, gtin, sn, point, operator, status, created_at "
                "FROM queue ORDER BY id"
            ).fetchall()

        headers = ["ID", "Время", "GTIN", "Серия", "Точка",
                   "Оператор", "Статус", "Создано"]
        data = [
            [r["id"], r["time"], r["gtin"], r["sn"], r["point"] or "",
             r["operator"] or "", r["status"], r["created_at"]]
            for r in rows
        ]

        n = _write_csv(path, headers, data)
        log.info("Экспорт очереди: %s строк → %s", n, path)
        return {"ok": True, "info": f"Экспортировано {n} строк", "count": n}

    except Exception as e:
        log.error("Ошибка экспорта очереди: %s", e)
        return {"ok": False, "info": str(e)}