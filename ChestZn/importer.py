"""
Импорт данных ChestZn из CSV.
UTF-8 с BOM и разделителем ';' — но принимает и ',' и ';'.
"""
import csv
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from logger import get_logger

log = get_logger("importer")


SUPPORTED_DELIMITERS = [";", ",", "\t"]


def _detect_delimiter(sample: str) -> str:
    """Определить разделитель по первой строке."""
    counts = {d: sample.count(d) for d in SUPPORTED_DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ";"


def _read_csv(path: str) -> list:
    """
    Прочитать CSV. Возвращает список dict'ов по заголовкам.
    Поддерживает UTF-8 (с BOM и без), разделители ';' ',' '\t'.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        first_line = f.readline()
        f.seek(0)
        delim = _detect_delimiter(first_line)

        reader = csv.DictReader(f, delimiter=delim)
        # Нормализуем заголовки: lower, strip, замена пробелов на _
        rows = []
        for raw in reader:
            row = {}
            for k, v in raw.items():
                if k is None:
                    continue
                key = k.strip().lower().replace(" ", "_")
                row[key] = (v or "").strip()
            rows.append(row)
    return rows


def _ask_open_path(parent, title: str) -> str:
    path, _ = QFileDialog.getOpenFileName(
        parent, title, "", "CSV / текстовые файлы (*.csv *.txt);;Все файлы (*)"
    )
    return path


def _ask_save_path(parent, default_name: str, title: str) -> str:
    path, _ = QFileDialog.getSaveFileName(
        parent, title, default_name, "CSV (*.csv);;Все файлы (*)"
    )
    return path


# ==========================================================
# Импорт пула кодов ЧЗ
# ==========================================================

POOL_TEMPLATE = (
    "gtin;code;sn\n"
    "04600123456782;(01)04600123456782(21)SN0001(91)ABCD(92)XYZ12345;SN0001\n"
    "04600123456782;(01)04600123456782(21)SN0002(91)EFGH(92)XYZ67890;SN0002\n"
)


def download_pool_template(parent=None) -> dict:
    """Скачать шаблон CSV для импорта пула ЧЗ."""
    path = _ask_save_path(parent, "chestzn_pool_template.csv", "Сохранить шаблон")
    if not path:
        return {"ok": False, "info": "Отменено"}
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(POOL_TEMPLATE)
        return {"ok": True, "info": f"Шаблон сохранён: {path}"}
    except Exception as e:
        log.error("Ошибка шаблона: %s", e)
        return {"ok": False, "info": str(e)}


def import_pool_codes(store, parent=None) -> dict:
    """
    Импортировать CSV с кодами ЧЗ в пул.
    Возвращает {ok, info, added, skipped, errors}.
    """
    path = _ask_open_path(parent, "Выберите CSV с кодами ЧЗ")
    if not path:
        return {"ok": False, "info": "Отменено"}

    try:
        rows = _read_csv(path)
    except Exception as e:
        log.error("Ошибка чтения CSV: %s", e)
        return {"ok": False, "info": f"Ошибка чтения: {e}"}

    if not rows:
        return {"ok": False, "info": "Файл пустой"}

    # Проверка заголовков
    if "gtin" not in rows[0] or "code" not in rows[0]:
        return {
            "ok": False,
            "info": "Не найдены колонки gtin и code. "
                    "Скачайте шаблон и заполните его.",
        }

    result = store.bulk_import_pool_codes(rows)

    # Дополняем сообщение ошибками
    if result["errors"]:
        preview = "\n".join(result["errors"][:5])
        if len(result["errors"]) > 5:
            preview += f"\n...и ещё {len(result['errors']) - 5}"
        result["info"] += f"\n\nОшибки:\n{preview}"

    log.info("Импорт пула ЧЗ: %s", result["info"])
    return result


# ==========================================================
# Импорт маппинга GTIN
# ==========================================================

GTIN_MAP_TEMPLATE = (
    "foreign_gtin;cz_gtin\n"
    "04600123456782;04600123456782\n"
    "04600123456775;04600123456775\n"
    "04600123456768;04600123456768\n"
)


def download_gtin_map_template(parent=None) -> dict:
    """Скачать шаблон CSV для маппинга GTIN."""
    path = _ask_save_path(parent, "chestzn_gtin_map_template.csv",
                          "Сохранить шаблон")
    if not path:
        return {"ok": False, "info": "Отменено"}
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(GTIN_MAP_TEMPLATE)
        return {"ok": True, "info": f"Шаблон сохранён: {path}"}
    except Exception as e:
        log.error("Ошибка шаблона: %s", e)
        return {"ok": False, "info": str(e)}


def import_gtin_map(store, parent=None) -> dict:
    """
    Импортировать CSV с маппингом GTIN.
    Возвращает {ok, info, added, updated, errors}.
    """
    path = _ask_open_path(parent, "Выберите CSV с маппингом GTIN")
    if not path:
        return {"ok": False, "info": "Отменено"}

    try:
        rows = _read_csv(path)
    except Exception as e:
        log.error("Ошибка чтения CSV: %s", e)
        return {"ok": False, "info": f"Ошибка чтения: {e}"}

    if not rows:
        return {"ok": False, "info": "Файл пустой"}

    if "foreign_gtin" not in rows[0] or "cz_gtin" not in rows[0]:
        return {
            "ok": False,
            "info": "Не найдены колонки foreign_gtin и cz_gtin. "
                    "Скачайте шаблон и заполните его.",
        }

    result = store.bulk_import_gtin_map(rows)

    if result["errors"]:
        preview = "\n".join(result["errors"][:5])
        if len(result["errors"]) > 5:
            preview += f"\n...и ещё {len(result['errors']) - 5}"
        result["info"] += f"\n\nОшибки:\n{preview}"

    log.info("Импорт маппинга GTIN: %s", result["info"])
    return result