"""
ChestZn — Модуль полной очистки проекта и базы данных от демонстрационных заглушек (clean_demo_stubs.py).

Что делает этот скрипт при запуске `python clean_demo_stubs.py`:

1. Очищает код `ChestZn/store.py` (с сохранением резервной копии `store.py.bak`):
   - Удаляет из `parse_foreign_barcode(raw)` зашитый демо-GTIN `04600123456782`,
     «Наушники беспроводные BT-500», модель «BT-500», импортёра «ООО Ромашка, г. Москва»,
     фейковую партию `LOT-2025-04`, срок годности `12.2026` и генератор случайных серийников `random.choices`.
   - Заменяет `parse_foreign_barcode(raw)` на боевой парсер (EAN-13, GTIN-14, GS1-128, DataMatrix).
   - Отключает в `_ensure_seed_data(self)` автозасев базы 90 фейковыми кодами ЧЗ (`DEMO-T-01`, `DEMO-T-02`)
     и 3 демо-привязками в `gtin_map` (`04600123456782`, `04600123456775`, `04600123456768`).
   - Убирает из `_DEFAULT_POINTS` несуществующие принтеры (`Zebra ZD421`, `TSC TE200`, `Godex G500`),
     фейковые IP (`10.0.1.15`, `10.0.2.15`, `10.0.3.15`, `10.0.1.10`, `10.0.1.11`, `10.0.2.10`)
     и вымышленные ФИО (`Иванов И.И.`, `Петрова А.С.`, `Сидоров В.П.`).

2. Очищает существующую базу данных SQLite (`%APPDATA%\\ChestZn\\chestzn.db`):
   - Удаляет демо-партии кодов ЧЗ (`import_batch IN ('DEMO-T-01', 'DEMO-T-02')`)
     и демо-GTIN (`04600123456782`, `04600123456775`, `04600123456768`) из `pool_codes` и `gtin_map`.
   - Удаляет сгенерированные демо-принтеры (`Zebra ZD421`, `TSC TE200`, `Godex G500` с IP `10.0.x.15`)
     и демо-компьютеры (`10.0.1.10`, `10.0.1.11`, `10.0.2.10`) из таблиц `printers`, `computers` и `points`.
"""

import os
import re
import shutil
import sqlite3
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("ChestZn.CleanStubs")

DEMO_GTINS = ("04600123456782", "04600123456775", "04600123456768")
DEMO_BATCHES = ("DEMO-T-01", "DEMO-T-02")
DEMO_PRINTER_NAMES = ("Zebra ZD421", "TSC TE200", "Godex G500")
DEMO_IPS = ("10.0.1.15", "10.0.2.15", "10.0.3.15", "10.0.1.10", "10.0.1.11", "10.0.2.10")
DEMO_OPERATORS = ("Иванов И.И.", "Петрова А.С.", "Сидоров В.П.")


# Чистая боевая реализация parse_foreign_barcode для замены в store.py
CLEAN_PARSE_FOREIGN_BARCODE_CODE = '''def parse_foreign_barcode(raw: str) -> dict:
    """
    Боевой разбор отсканированного штрихкода товара (EAN-13, GTIN-14, GS1-128, DataMatrix)
    без демонстрационных заглушек (без 04600123456782, «Наушников BT-500» и «ООО Ромашка»).
    """
    raw_s = (raw or "").strip("\\r\\n\\t ")
    if len(raw_s) > 3 and raw_s.startswith("]") and raw_s[1:3] in ("d2", "d1", "C1", "E0", "E4", "I1", "Q3"):
        raw_s = raw_s[3:].strip()

    s = raw_s.replace("_x001D_", "\\x1d").lstrip("\\x1d")

    gtin = ""
    sn = "—"
    lot = "—"
    exp = "—"
    btype = "EAN-13"

    if s:
        # 1. Скобочный формат GS1: (01)04607012345678(21)SN...
        m_br = re.search(r"\\(01\\)\\s*(\\d{8,14})(?:\\s*\\(21\\)\\s*([^\\(\\x1d\\r\\n]+))?", s)
        if m_br:
            btype = "GS1-128"
            g_raw = m_br.group(1)
            gtin = g_raw.zfill(14) if len(g_raw) == 13 else g_raw
            if m_br.group(2):
                sn = m_br.group(2).strip()

        # 2. Бесскобочный GS1 DataMatrix / GS1-128: 01 + 14 цифр + 21 + SN
        if not gtin:
            m01 = re.search(r"(?:^|\\x1d)01(\\d{14})", s)
            if m01:
                btype = "GS1-128"
                gtin = m01.group(1)
                after_01 = s[m01.end():]
                m21 = re.match(r"^21([^\\x1d\\r\\n]{1,30})", after_01)
                if m21:
                    sn = m21.group(1).strip()

        m10 = re.search(r"(?:\\(10\\)|\\x1d10)([^\\(\\x1d\\r\\n]{1,20})", s)
        if m10:
            lot = m10.group(1).strip()

        m17 = re.search(r"(?:\\(17\\)|\\x1d17)(\\d{6})", s)
        if m17:
            d = m17.group(1)
            yy, mm = d[:2], d[2:4]
            exp = f"{mm}.20{yy}"

        # 3. Линейный штрихкод EAN-13 / GTIN-14 / EAN-8 / внешний артикул
        if not gtin:
            if s.isdigit():
                if len(s) == 13:
                    btype = "EAN-13"
                    gtin = "0" + s
                elif len(s) == 14:
                    btype = "GTIN-14"
                    gtin = s
                elif len(s) in (8, 12):
                    btype = "EAN-8" if len(s) == 8 else "UPC-A"
                    gtin = s.zfill(14)
                else:
                    gtin = s
            else:
                gtin = s

    return {
        "raw": raw_s,
        "shk": raw_s,
        "type": btype,
        "gtin": gtin,
        "serial": sn,
        "sn": sn,
        "lot": lot,
        "exp": exp,
        "country": "—",
        "name": f"Товар (GTIN {gtin})" if gtin else "Неизвестный ШК",
        "model": gtin or "—",
        "importer": "—",
    }
'''

# Чистая боевая реализация _ensure_seed_data (не засоряет БД фейковыми кодами)
CLEAN_ENSURE_SEED_DATA_CODE = '''    def _ensure_seed_data(self):
        """Демо-заполнение отключено: база работает только с реальными кодами ЧЗ и привязками."""
        return
'''


def _find_store_py_path(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Находит файл ChestZn/store.py в проекте."""
    candidates = []
    if start_dir:
        candidates.extend([
            start_dir / "store.py",
            start_dir / "ChestZn" / "store.py",
        ])
    here = Path(__file__).resolve().parent
    candidates.extend([
        here / "store.py",
        here / "ChestZn" / "store.py",
        Path.cwd() / "store.py",
        Path.cwd() / "ChestZn" / "store.py",
    ])
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _find_sqlite_db_paths() -> List[Path]:
    """Находит все файлы базы данных chestzn.db (в %APPDATA%\\ChestZn и локальной папке)."""
    paths: List[Path] = []
    try:
        import db as db_mod
        if hasattr(db_mod, "DB_PATH"):
            p = Path(getattr(db_mod, "DB_PATH"))
            if p.exists():
                paths.append(p)
    except Exception:
        pass

    appdata = os.environ.get("APPDATA")
    if appdata:
        p_app = Path(appdata) / "ChestZn" / "chestzn.db"
        if p_app.exists() and p_app not in paths:
            paths.append(p_app)

    home_db = Path.home() / "ChestZn" / "chestzn.db"
    if home_db.exists() and home_db not in paths:
        paths.append(home_db)

    for local_cand in (
        Path(__file__).resolve().parent / "chestzn.db",
        Path.cwd() / "chestzn.db",
        Path.cwd() / "ChestZn" / "chestzn.db",
    ):
        if local_cand.exists() and local_cand not in paths:
            paths.append(local_cand)

    return paths


def patch_store_py_file(store_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Точечно очищает файл ChestZn/store.py от демо-заглушек, сохраняя все методы класса Store.
    Создаёт резервную копию store.py.bak перед первым изменением.
    """
    target = store_path or _find_store_py_path()
    if target is None or not target.exists():
        return {"ok": False, "patched": False, "info": "Файл store.py не найден"}

    try:
        original_text = target.read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "patched": False, "info": f"Ошибка чтения {target}: {e}"}

    text = original_text
    changes: List[str] = []

    # 1. Заменяем демо-функцию parse_foreign_barcode в конце store.py
    # Используем срез по индексам m.start(), чтобы re.sub не интерпретировал backslash-последовательности в Python 3.14
    if "Наушники беспроводные BT-500" in text or "ООО «Ромашка»" in text or 'gtin = "04600123456782"' in text:
        pattern_parse = re.compile(
            r"^def parse_foreign_barcode\s*\([^)]*\).*?\Z",
            re.DOTALL | re.MULTILINE
        )
        m_parse = pattern_parse.search(text)
        if m_parse:
            text = text[:m_parse.start()] + CLEAN_PARSE_FOREIGN_BARCODE_CODE.strip() + "\n"
            changes.append("parse_foreign_barcode очищена от демо-GTIN 04600123456782, «Наушников BT-500» и «ООО Ромашка»")

    # 2. Отключаем автозасев фейковых кодов ЧЗ в _ensure_seed_data
    if "DEMO-T-01" in text or "04600123456775" in text:
        pattern_seed = re.compile(
            r"    def _ensure_seed_data\(self\):.*?(?=\n    def _make_cz\(self|\n    def [a-zA-Z0-9_]+\(self)",
            re.DOTALL
        )
        m_seed = pattern_seed.search(text)
        if m_seed:
            text = text[:m_seed.start()] + CLEAN_ENSURE_SEED_DATA_CODE.rstrip() + text[m_seed.end():]
            changes.append("_ensure_seed_data отключена (удалён автозасев 90 кодов DEMO-T-01 / DEMO-T-02)")

    # 3. Очищаем _DEFAULT_POINTS от фейковых принтеров Zebra ZD421 / TSC TE200 / Godex G500 и IP 10.0.x.x
    for fake_pr in DEMO_PRINTER_NAMES:
        if f'"printer": "{fake_pr}"' in text:
            text = text.replace(f'"printer": "{fake_pr}"', '"printer": ""')
            changes.append(f"Удалён демо-принтер {fake_pr} из _DEFAULT_POINTS")
    for fake_ip in ("10.0.1.15", "10.0.2.15", "10.0.3.15"):
        if f'"ip": "{fake_ip}"' in text:
            text = text.replace(f'"ip": "{fake_ip}"', '"ip": ""')
    for fake_op in DEMO_OPERATORS:
        if f'"operator": "{fake_op}"' in text:
            text = text.replace(f'"operator": "{fake_op}"', '"operator": "Оператор"')
        if f'self.current_operator = "{fake_op}"' in text:
            text = text.replace(f'self.current_operator = "{fake_op}"', 'self.current_operator = "Оператор"')
            changes.append("Заменён дефолтный оператор «Иванов И.И.» на «Оператор»")

    # Очищаем вложенные демо-компьютеры в _DEFAULT_POINTS (через lambda во избежание парсинга \ в Python 3.14)
    text_no_demo_comps = re.sub(
        r'"computers":\s*\[\s*\{[^]]*?10\.0\.[12]\.10[^]]*?\}\s*,?\s*\]',
        lambda _m: '"computers": []',
        text,
        flags=re.DOTALL
    )
    if text_no_demo_comps != text:
        text = text_no_demo_comps
        changes.append("Удалены демо-компьютеры (10.0.1.10 / 10.0.1.11 / 10.0.2.10) из _DEFAULT_POINTS")

    # 4. Сбрасываем фейковый счётчик вызовов ГИС МТ calls_today=1284
    if '"calls_today": 1284' in text:
        text = text.replace('"calls_today": 1284', '"calls_today": 0')
        changes.append("Сброшен фейковый счётчик ГИС МТ calls_today=1284")

    if text != original_text:
        bak_path = target.with_suffix(".py.bak")
        if not bak_path.exists():
            try:
                shutil.copy2(target, bak_path)
            except Exception:
                pass
        target.write_text(text, encoding="utf-8")
        logger.info("Файл %s успешно очищен от заглушек (%d изменений)", target, len(changes))
        return {"ok": True, "patched": True, "path": str(target), "changes": changes}

    return {"ok": True, "patched": False, "path": str(target), "changes": [], "info": "Заглушки уже удалены"}


def clean_sqlite_demo_data(db_path: Optional[Path] = None, conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """
    Удаляет ранее засеянные демонстрационные данные из таблиц SQLite (chestzn.db):
    - фейковые коды ЧЗ из pool_codes (DEMO-T-01, DEMO-T-02, 04600123456782, 04600123456775, 04600123456768)
    - фейковые маппинги из gtin_map
    - фейковые принтеры (Zebra ZD421, TSC TE200, Godex G500 с IP 10.0.x.15) и ПК (10.0.x.10/11)
    """
    stats = {
        "pool_codes_deleted": 0,
        "gtin_map_deleted": 0,
        "printers_deleted": 0,
        "computers_deleted": 0,
        "points_cleaned": 0,
    }

    own_conn = False
    if conn is None:
        if db_path is None:
            paths = _find_sqlite_db_paths()
            if not paths:
                return stats
            db_path = paths[0]
        conn = sqlite3.connect(str(db_path))
        own_conn = True

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}

        # 1. Удаляем демо-коды из pool_codes
        if "pool_codes" in tables:
            cur.execute(
                "DELETE FROM pool_codes WHERE import_batch IN ('DEMO-T-01', 'DEMO-T-02') "
                "OR (gtin IN ('04600123456782', '04600123456775', '04600123456768') AND code LIKE '%91EE06%')"
            )
            stats["pool_codes_deleted"] += max(0, cur.rowcount)

        # 2. Удаляем демо-связки из gtin_map
        if "gtin_map" in tables:
            cur.execute(
                "DELETE FROM gtin_map WHERE foreign_gtin IN ('04600123456782', '04600123456775', '04600123456768') "
                "AND cz_gtin = foreign_gtin"
            )
            stats["gtin_map_deleted"] += max(0, cur.rowcount)

        # 3. Удаляем демо-товары из gtin_catalog (если есть)
        if "gtin_catalog" in tables:
            cur.execute(
                "DELETE FROM gtin_catalog WHERE gtin IN ('04600123456782', '04600123456775', '04600123456768')"
            )

        # 4. Удаляем фейковые принтеры из таблицы printers
        if "printers" in tables:
            cur.execute(
                "DELETE FROM printers WHERE "
                "(name IN ('Zebra ZD421', 'TSC TE200', 'Godex G500') AND ip IN ('10.0.1.15', '10.0.2.15', '10.0.3.15', ''))"
            )
            stats["printers_deleted"] += max(0, cur.rowcount)

        # 5. Удаляем фейковые компьютеры (ПК-01 10.0.1.10, ПК-02 10.0.1.11, ПК-01 10.0.2.10)
        if "computers" in tables:
            cur.execute(
                "DELETE FROM computers WHERE ip IN ('10.0.1.10', '10.0.1.11', '10.0.2.10') "
                "AND name IN ('ПК-01', 'ПК-02')"
            )
            stats["computers_deleted"] += max(0, cur.rowcount)

        # 6. Очищаем демо-поля в таблице points
        if "points" in tables:
            cur.execute(
                "UPDATE points SET printer = '', ip = '' "
                "WHERE printer IN ('Zebra ZD421', 'TSC TE200', 'Godex G500') "
                "AND ip IN ('10.0.1.15', '10.0.2.15', '10.0.3.15')"
            )
            stats["points_cleaned"] += max(0, cur.rowcount)
            cur.execute(
                "UPDATE points SET operator = 'Оператор' "
                "WHERE operator IN ('Иванов И.И.', 'Петрова А.С.', 'Сидоров В.П.')"
            )
            cur.execute(
                "UPDATE points SET computers_json = '[]' "
                "WHERE computers_json LIKE '%10.0.1.10%' OR computers_json LIKE '%10.0.2.10%'"
            )

        conn.commit()
    except Exception as e:
        logger.debug("Ошибка очистки демо-данных в SQLite: %s", e)
    finally:
        if own_conn:
            conn.close()

    return stats


def purge_all_demo_stubs(store_instance: Optional[Any] = None) -> Dict[str, Any]:
    """
    Комплексная очистка проекта:
    1. Патчит файл store.py на диске.
    2. Очищает базу данных chestzn.db от демо-кодов и фейковых устройств.
    3. Обновляет кэш в живом экземпляре Store (gtin_map, points, current_operator).
    """
    patch_res = patch_store_py_file()

    db_totals = {
        "pool_codes_deleted": 0,
        "gtin_map_deleted": 0,
        "printers_deleted": 0,
        "computers_deleted": 0,
        "points_cleaned": 0,
    }

    for db_p in _find_sqlite_db_paths():
        st = clean_sqlite_demo_data(db_path=db_p)
        for k, v in st.items():
            db_totals[k] = db_totals.get(k, 0) + v

    if store_instance is not None:
        try:
            for demo_g in DEMO_GTINS:
                if hasattr(store_instance, "gtin_map") and isinstance(store_instance.gtin_map, dict):
                    if store_instance.gtin_map.get(demo_g) == demo_g:
                        store_instance.gtin_map.pop(demo_g, None)
            if getattr(store_instance, "current_operator", "") in DEMO_OPERATORS:
                store_instance.current_operator = "Оператор"
            if hasattr(store_instance, "_load_points"):
                store_instance.points = store_instance._load_points()
        except Exception as e:
            logger.debug("Ошибка обновления живого экземпляра Store: %s", e)

    return {
        "store_py": patch_res,
        "db_cleaned": db_totals,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    report = purge_all_demo_stubs()
    print("=" * 68)
    print("ОТЧЁТ ОЧИСТКИ ПРОЕКТА CHESTZN ОТ ДЕМО-ЗАГЛУШЕК")
    print("=" * 68)
    sp = report["store_py"]
    print(f"Файл store.py: {sp.get('path', 'не найден')}")
    if sp.get("changes"):
        for ch in sp["changes"]:
            print(f"  [✓] {ch}")
    else:
        print(f"  [i] {sp.get('info', 'Изменений не требуется')}")

    dbc = report["db_cleaned"]
    print("\nОчистка базы данных SQLite (chestzn.db):")
    print(f"  - Удалено демо-кодов ЧЗ из pool_codes : {dbc['pool_codes_deleted']}")
    print(f"  - Удалено демо-связок из gtin_map      : {dbc['gtin_map_deleted']}")
    print(f"  - Удалено фейковых принтеров           : {dbc['printers_deleted']}")
    print(f"  - Удалено фейковых компьютеров         : {dbc['computers_deleted']}")
    print(f"  - Очищено демо-точек в points          : {dbc['points_cleaned']}")
    print("=" * 68)