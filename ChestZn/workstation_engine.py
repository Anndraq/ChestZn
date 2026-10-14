"""
ChestZn — Высокопроизводительный многопоточный диспетчер рабочих мест (workstation_engine.py)
Архитектурный принцип: 1 ПК = N независимых пар «Сканер_i <-> Принтер_i» (N сканеров = N принтеров).

Ключевая логика работы при сканировании ШК:
1. Перехват сканирования из любого источника:
   - Потоки COM / Bluetooth SPP / TCP сканеров (_ScannerWorkerThread, с поддержкой сканеров как с CR/LF, так и без суффикса).
   - Поле ввода и клавиатурный ввод USB-HID сканера в OperatorWindow (_GlobalScanEventFilter + хук parse_foreign_barcode).
2. Сравнение отсканированного ШК со всеми кодами Честного Знака в базе (resolve_and_claim_cz_code):
   - Сравнивает ШК с таблицей сопоставления gtin_map, колонкой pool_codes.gtin (13/14 цифр, без ведущих нулей)
     и напрямую с полными строками кодов ЧЗ pool_codes.code (извлекая GTIN из структуры 01<GTIN>21<SN>).
3. Действие по результату сравнения:
   - Если найден связанный свободный код ЧЗ (used = 0) -> резервирует его (used = 1) и гарантированно отправляет
     именно этот код Честного Знака на привязанный к сканеру принтер.
   - Если отсканированный ШК НЕ связан ни с одним кодом ЧЗ в базе (или свободные коды закончились) ->
     блокирует печать и гарантированно выводит оператору ошибку («Неизвестный ШК: ...») через всплывающий Toast
     и диалоговое окно предупреждения QMessageBox.
"""

import sys
import time
import re
import json
import socket
import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ChestZn.Engine")

try:
    from PySide6.QtCore import QObject, QThread, Signal, QRunnable, QThreadPool, Slot, QTimer, QEvent, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox, QLineEdit, QWidget
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False
    QApplication = None
    QMessageBox = None
    QLineEdit = None
    QWidget = None
    QEvent = None
    Qt = None
    class QObject:
        def __init__(self, parent=None):
            self._parent = parent
        def parent(self):
            return getattr(self, "_parent", None)
    class Signal:
        def __init__(self, *types): self._callbacks = []
        def connect(self, cb): self._callbacks.append(cb)
        def emit(self, *args):
            for cb in self._callbacks:
                try: cb(*args)
                except Exception as e: logger.error("Signal callback error: %s", e)
    def Slot(*types):
        return lambda f: f
    class QThread(threading.Thread):
        def __init__(self, parent=None):
            super().__init__()
            self.daemon = True
    class QRunnable: pass
    class QThreadPool:
        @staticmethod
        def globalInstance(): return QThreadPool()
        def start(self, runnable):
            t = threading.Thread(target=runnable.run, daemon=True)
            t.start()
    class QTimer:
        @staticmethod
        def singleShot(ms, cb):
            cb()

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    SERIAL_AVAILABLE = False


# Глобальное состояние маршрутизатора и резолвера ШК -> ЧЗ
_LAST_ACTIVE_SCANNER_KEY: Optional[str] = None
_PRINT_ROUTER_INSTALLED: bool = False
_BARCODE_RESOLVER_INSTALLED: bool = False
_GLOBAL_EVENT_FILTER_INSTALLED: bool = False
_GLOBAL_EVENT_FILTER_OBJ: Optional[Any] = None
_ENGINE_INSTANCE: Optional["WorkstationEngine"] = None
_DB_CLAIM_LOCK = threading.Lock()

_LAST_SCAN_ERROR_INFO: Optional[str] = None
_LAST_SCAN_ERROR_TS: float = 0.0
_LAST_ERROR_DIALOG_TS: float = 0.0

# Состояние контроля гарантированной отправки на печать при валидном скане
_PENDING_VALID_SCAN: Dict[str, Any] = {
    "raw": "",
    "ts": 0.0,
    "printed": False,
    "handled": False,
    "last_result": None,
}

# Константа тестовой печати из AdminWindow (разрешена без списания из пула)
_ADMIN_TEST_PRINT_MARKER = "SN00000001\x1d91TEST\x1d92SAMPLE01"


# ---------------------------------------------------------------------------
# 1. ЯДРО СРАВНЕНИЯ И СВЯЗЫВАНИЯ ШК -> ЧЕСТНЫЙ ЗНАК
# ---------------------------------------------------------------------------

def clean_scanned_barcode(raw_barcode: str) -> str:
    """
    Очищает сырую строку от сканера от переводов строк, пробелов по краям
    и AIM-префиксов сканера (]d2, ]C1, ]E0, ]Q3), сохраняя внутренние разделители GS (\\x1d).
    """
    if not raw_barcode:
        return ""
    s = str(raw_barcode).strip("\r\n\t ")
    if len(s) > 3 and s.startswith("]") and s[1:3] in ("d2", "d1", "C1", "E0", "E4", "I1", "Q3"):
        s = s[3:]
    s = s.lstrip("\x1d")
    return s.strip()


def extract_gtin_candidates(raw_barcode: str) -> Dict[str, Any]:
    """
    Извлекает все возможные варианты GTIN и серийного номера (SN) из отсканированного ШК:
    - Линейные ШК товара: EAN-13 (13 цифр), GTIN-14 / ITF-14 (14 цифр), UPC-A (12 цифр), EAN-8 (8 цифр)
    - Скобочный GS1: (01)04607012345678(21)SN...
    - Бесскобочный GS1 / DataMatrix: 010460701234567821SN...
    - Произвольные внешние ШК / артикулы (для поиска в таблице gtin_map)
    """
    s = clean_scanned_barcode(raw_barcode)
    if not s:
        return {
            "clean": "",
            "candidates": [],
            "stripped_digits": [],
            "extracted_sn": None,
            "is_gs1": False,
        }

    candidates: List[str] = []
    extracted_sn: Optional[str] = None
    is_gs1 = False

    def _add_gtin_variants(g: str):
        g = g.strip()
        if not g:
            return
        if g not in candidates:
            candidates.append(g)
        if g.isdigit():
            g14 = g.zfill(14) if len(g) <= 14 else g
            if g14 not in candidates:
                candidates.append(g14)
            if len(g14) == 14 and g14.startswith("0"):
                g13 = g14[1:]
                if g13 not in candidates:
                    candidates.append(g13)
            g_no_zero = g.lstrip("0")
            if g_no_zero and g_no_zero not in candidates:
                candidates.append(g_no_zero)

    # 1. Скобочный формат GS1: (01)04607012345678(21)SN... или (01)4607012345678...
    m_bracket = re.search(r"\(01\)\s*(\d{8,14})(?:\s*\(21\)\s*([^\(\x1d\r\n]+))?", s)
    if m_bracket:
        is_gs1 = True
        _add_gtin_variants(m_bracket.group(1))
        if m_bracket.group(2):
            extracted_sn = m_bracket.group(2).strip()

    # 2. Бесскобочный формат GS1 DataMatrix: 01 + 14 цифр GTIN + 21 + SN
    if not is_gs1 and len(s) >= 18 and s.startswith("01") and s[2:16].isdigit() and s[16:18] == "21":
        is_gs1 = True
        _add_gtin_variants(s[2:16])
        rest = s[18:]
        sn_part = rest.split("\x1d")[0]
        if "\x1d" not in rest and len(sn_part) > 13:
            m_tail = re.match(r"^(.{6,20}?)(?:91[A-Za-z0-9]{4}|93[A-Za-z0-9]{4})", sn_part)
            if m_tail:
                sn_part = m_tail.group(1)
        extracted_sn = sn_part.strip()

    # 3. Бесскобочный GS1 только с AI 01 (16 символов: 01 + 14 цифр)
    if not is_gs1 and len(s) == 16 and s.startswith("01") and s[2:16].isdigit():
        _add_gtin_variants(s[2:16])

    # 4. Линейный цифровой ШК товара (EAN-13, GTIN-14, UPC-A, EAN-8)
    if s.isdigit() and 8 <= len(s) <= 14:
        _add_gtin_variants(s)

    # 5. Если в строке есть 13 или 14 цифр подряд
    if not candidates:
        m_digits = re.search(r"(\d{13,14})", s)
        if m_digits:
            _add_gtin_variants(m_digits.group(1))

    # 6. Всегда добавляем саму очищенную строку
    if s not in candidates:
        candidates.append(s)

    stripped_digits: List[str] = []
    for c in candidates:
        if c.isdigit():
            st = c.lstrip("0")
            if st and st not in stripped_digits:
                stripped_digits.append(st)

    return {
        "clean": s,
        "candidates": candidates,
        "stripped_digits": stripped_digits,
        "extracted_sn": extracted_sn,
        "is_gs1": is_gs1,
    }


def _get_table_columns(conn, table_name: str) -> List[str]:
    """Безопасное получение списка колонок таблицы SQLite через PRAGMA table_info."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [
            (r["name"] if isinstance(r, dict) or hasattr(r, "keys") else r[1])
            for r in rows
        ]
    except Exception:
        return []


def _lookup_mapped_gtins_in_conn(conn, candidates: List[str], stripped_digits: List[str]) -> List[str]:
    """
    Ищет сопоставленные российские GTIN ЧЗ в таблице gtin_map.
    """
    mapped: List[str] = []
    if not candidates:
        return mapped

    try:
        col_names = _get_table_columns(conn, "gtin_map")
        if len(col_names) < 2:
            return mapped

        src_col = None
        dst_col = None
        for c in col_names:
            cl = c.lower()
            if cl in ("foreign_gtin", "ext_gtin", "source_gtin", "barcode", "shk", "ean", "foreign_barcode", "in_gtin"):
                src_col = c
            elif cl in ("gtin", "ru_gtin", "target_gtin", "cz_gtin", "local_gtin", "rus_gtin", "out_gtin"):
                dst_col = c

        if not src_col or not dst_col:
            non_id_cols = [c for c in col_names if c.lower() not in ("id", "created_at", "updated_at", "note", "name", "product_name")]
            if len(non_id_cols) >= 2:
                src_col = src_col or non_id_cols[0]
                dst_col = dst_col or non_id_cols[1]
            else:
                return mapped

        placeholders = ",".join(["?"] * len(candidates))
        params: List[Any] = list(candidates)
        where_clauses = [f"TRIM([{src_col}]) IN ({placeholders})"]

        if stripped_digits:
            ph_strip = ",".join(["?"] * len(stripped_digits))
            where_clauses.append(f"LTRIM(TRIM([{src_col}]), '0') IN ({ph_strip})")
            params.extend(stripped_digits)

        sql = f"SELECT [{dst_col}] AS target_gtin FROM gtin_map WHERE " + " OR ".join(where_clauses)
        rows = conn.execute(sql, tuple(params)).fetchall()
        for r in rows:
            val = (r["target_gtin"] if isinstance(r, dict) or hasattr(r, "keys") else r[0])
            if val:
                val_str = str(val).strip()
                if val_str and val_str not in mapped:
                    mapped.append(val_str)
    except Exception as e:
        logger.debug("Ошибка проверки таблицы gtin_map: %s", e)

    return mapped


def _match_barcode_against_all_cz_in_python(
    conn,
    candidates: List[str],
    stripped_digits: List[str],
    sn_expr: str,
    pt_expr: str,
    point: Optional[str]
) -> Tuple[int, int, Optional[str], Optional[Dict[str, Any]]]:
    """
    Полное прямое сравнение отсканированного ШК со всеми записями ЧЗ в pool_codes:
    проверяет как колонку gtin, так и встроенный в сам код ЧЗ (pool_codes.code) GTIN.
    Возвращает: (total_matching, free_matching, matched_gtin, best_free_row_dict)
    """
    cand_set = {c.strip() for c in candidates if c.strip()}
    strip_set = {s.strip() for s in stripped_digits if s.strip()}

    rows = conn.execute(
        f"SELECT code, gtin, {sn_expr}, used, {pt_expr} FROM pool_codes"
    ).fetchall()

    total_matching = 0
    free_rows: List[Dict[str, Any]] = []
    matched_gtin: Optional[str] = None

    for r in rows:
        r_dict = dict(r) if hasattr(r, "keys") else {
            "code": r[0], "gtin": r[1], "sn": r[2], "used": r[3], "assigned_point": r[4]
        }
        code_val = str(r_dict.get("code") or "").strip()
        gtin_val = str(r_dict.get("gtin") or "").strip()
        gtin_strip = gtin_val.lstrip("0") if gtin_val.isdigit() else ""

        is_match = False
        # 1. Сравнение по колонке gtin
        if gtin_val and (gtin_val in cand_set or (gtin_strip and gtin_strip in strip_set)):
            is_match = True
        # 2. Сравнение по полному коду ЧЗ (извлекаем GTIN из самой строки кода ЧЗ 01<GTIN>21...)
        if not is_match and code_val:
            if code_val in cand_set:
                is_match = True
            else:
                code_parsed = extract_gtin_candidates(code_val)
                if cand_set.intersection(code_parsed["candidates"]) or (
                    strip_set and strip_set.intersection(code_parsed["stripped_digits"])
                ):
                    is_match = True

        if is_match:
            total_matching += 1
            if not matched_gtin:
                matched_gtin = gtin_val or code_val[:16]
            if int(r_dict.get("used") or 0) == 0:
                free_rows.append(r_dict)

    if not free_rows:
        return total_matching, 0, matched_gtin, None

    # Сортируем свободные коды с приоритетом текущей точки оператора
    def _sort_key(item: Dict[str, Any]) -> int:
        ap = str(item.get("assigned_point") or "").strip()
        if point and ap == point:
            return 0
        if not ap:
            return 1
        return 2

    free_rows.sort(key=_sort_key)
    return total_matching, len(free_rows), matched_gtin, free_rows[0]


def resolve_and_claim_cz_code(
    raw_barcode: str,
    store: Any = None,
    point: Optional[str] = None,
    claim: bool = True,
    allow_already_claimed: bool = False,
    align_point_for_operator: bool = True
) -> Dict[str, Any]:
    """
    Главный резолвер «Отсканированный ШК -> Код Честного Знака (DataMatrix)».
    Сравнивает отсканированный ШК со всеми кодами ЧЗ в базе данных (pool_codes и gtin_map).
    Если связать ШК и ЧЗ невозможно — возвращает ошибку UNKNOWN_BARCODE («Неизвестный ШК...»).
    """
    global _LAST_SCAN_ERROR_INFO, _LAST_SCAN_ERROR_TS

    parsed = extract_gtin_candidates(raw_barcode)
    clean_str = parsed["clean"]
    short_display = (clean_str[:32] + "…") if len(clean_str) > 32 else (clean_str or "пусто")

    if not clean_str:
        err_msg = "Неизвестный ШК: получен пустой скан"
        _LAST_SCAN_ERROR_INFO = err_msg
        _LAST_SCAN_ERROR_TS = time.time()
        return {
            "ok": False,
            "error_code": "UNKNOWN_BARCODE",
            "info": err_msg,
            "barcode": raw_barcode,
        }

    if not point and store is not None:
        point = getattr(store, "current_point", None)
    if not point and _ENGINE_INSTANCE is not None and _ENGINE_INSTANCE.store is not None:
        point = getattr(_ENGINE_INSTANCE.store, "current_point", None)

    try:
        import db
    except ImportError:
        return {
            "ok": False,
            "error_code": "DB_UNAVAILABLE",
            "info": f"База данных ЧЗ недоступна ({short_display})",
            "barcode": raw_barcode,
        }

    candidates = list(parsed["candidates"])
    stripped_digits = list(parsed["stripped_digits"])

    with _DB_CLAIM_LOCK:
        try:
            with db.get_conn() as conn:
                pool_cols = _get_table_columns(conn, "pool_codes")
                pool_cols_lower = {c.lower(): c for c in pool_cols}
                has_assigned_point = "assigned_point" in pool_cols_lower
                has_used_point = "used_point" in pool_cols_lower
                has_sn = "sn" in pool_cols_lower
                has_printed = "printed" in pool_cols_lower

                sn_expr = "sn" if has_sn else "'' AS sn"
                pt_expr = "assigned_point" if has_assigned_point else "'' AS assigned_point"
                pr_expr = "printed" if has_printed else "0 AS printed"

                # ШАГ 1: Проверяем, не передан ли уже точный полный код ЧЗ (pool_codes.code)
                exact_variants = [clean_str]
                if raw_barcode and str(raw_barcode).strip("\r\n") not in exact_variants:
                    exact_variants.append(str(raw_barcode).strip("\r\n"))

                ph_exact = ",".join(["?"] * len(exact_variants))
                exact_row = conn.execute(
                    f"SELECT code, gtin, {sn_expr}, used, {pr_expr}, {pt_expr} "
                    f"FROM pool_codes WHERE code IN ({ph_exact}) LIMIT 1",
                    tuple(exact_variants)
                ).fetchone()

                if exact_row is not None:
                    r_dict = dict(exact_row) if hasattr(exact_row, "keys") else {
                        "code": exact_row[0], "gtin": exact_row[1], "sn": exact_row[2],
                        "used": exact_row[3], "printed": exact_row[4], "assigned_point": exact_row[5]
                    }
                    if allow_already_claimed:
                        if r_dict.get("used", 0) == 0 and claim:
                            if has_used_point:
                                conn.execute(
                                    "UPDATE pool_codes SET used = 1, used_point = COALESCE(?, used_point) WHERE code = ?",
                                    (point, r_dict["code"])
                                )
                            else:
                                conn.execute("UPDATE pool_codes SET used = 1 WHERE code = ?", (r_dict["code"],))
                            conn.commit()
                        _LAST_SCAN_ERROR_INFO = None
                        return {
                            "ok": True,
                            "code": r_dict["code"],
                            "gtin": r_dict["gtin"],
                            "sn": r_dict.get("sn") or "",
                            "barcode": raw_barcode,
                            "already_in_pool": True,
                            "info": f"Код ЧЗ подтверждён (GTIN {r_dict['gtin']})",
                        }

                    if r_dict.get("used", 0) == 0:
                        if claim:
                            if has_used_point:
                                conn.execute(
                                    "UPDATE pool_codes SET used = 1, used_point = ? WHERE code = ?",
                                    (point or r_dict.get("assigned_point") or "T-01", r_dict["code"])
                                )
                            else:
                                conn.execute("UPDATE pool_codes SET used = 1 WHERE code = ?", (r_dict["code"],))
                            conn.commit()
                        elif align_point_for_operator and point and has_assigned_point and r_dict.get("assigned_point") != point:
                            conn.execute(
                                "UPDATE pool_codes SET assigned_point = ? WHERE code = ?",
                                (point, r_dict["code"])
                            )
                            conn.commit()
                        _LAST_SCAN_ERROR_INFO = None
                        return {
                            "ok": True,
                            "code": r_dict["code"],
                            "gtin": r_dict["gtin"],
                            "sn": r_dict.get("sn") or "",
                            "barcode": raw_barcode,
                            "info": f"Связан с ЧЗ (GTIN {r_dict['gtin']}, SN {r_dict.get('sn')})",
                        }
                    else:
                        g_from_row = str(r_dict.get("gtin") or "").strip()
                        if g_from_row and g_from_row not in candidates:
                            candidates.insert(0, g_from_row)
                            if g_from_row.isdigit() and g_from_row.lstrip("0") not in stripped_digits:
                                stripped_digits.insert(0, g_from_row.lstrip("0"))

                # ШАГ 2: Ищем маппинг внешнего ШК -> GTIN ЧЗ в таблице gtin_map
                mapped_gtins = _lookup_mapped_gtins_in_conn(conn, candidates, stripped_digits)
                for mg in reversed(mapped_gtins):
                    mg_parsed = extract_gtin_candidates(mg)
                    for c in reversed(mg_parsed["candidates"]):
                        if c not in candidates:
                            candidates.insert(0, c)
                    for sd in reversed(mg_parsed["stripped_digits"]):
                        if sd not in stripped_digits:
                            stripped_digits.insert(0, sd)

                if not candidates:
                    err_msg = f"Неизвестный ШК ({short_display}): не удалось связать ШК и Честный Знак"
                    _LAST_SCAN_ERROR_INFO = err_msg
                    _LAST_SCAN_ERROR_TS = time.time()
                    return {
                        "ok": False,
                        "error_code": "UNKNOWN_BARCODE",
                        "info": err_msg,
                        "barcode": raw_barcode,
                    }

                # ШАГ 3: Быстрый SQL-поиск по колонке gtin в pool_codes
                ph_cand = ",".join(["?"] * len(candidates))
                where_parts = [f"TRIM(gtin) IN ({ph_cand})"]
                sql_params: List[Any] = list(candidates)

                if stripped_digits:
                    ph_strip = ",".join(["?"] * len(stripped_digits))
                    where_parts.append(f"LTRIM(TRIM(gtin), '0') IN ({ph_strip})")
                    sql_params.extend(stripped_digits)

                where_expr = "(" + " OR ".join(where_parts) + ")"

                stat_row = conn.execute(
                    f"SELECT COUNT(*) AS total_cnt, "
                    f"       SUM(CASE WHEN used = 0 THEN 1 ELSE 0 END) AS free_cnt, "
                    f"       MIN(gtin) AS sample_gtin "
                    f"FROM pool_codes WHERE {where_expr}",
                    tuple(sql_params)
                ).fetchone()

                total_cnt = int((stat_row["total_cnt"] if hasattr(stat_row, "keys") else stat_row[0]) or 0) if stat_row else 0
                free_cnt = int((stat_row["free_cnt"] if hasattr(stat_row, "keys") else stat_row[1]) or 0) if stat_row else 0
                matched_gtin = (stat_row["sample_gtin"] if hasattr(stat_row, "keys") else stat_row[2]) if stat_row else None

                f_dict: Optional[Dict[str, Any]] = None

                if total_cnt > 0 and free_cnt > 0:
                    if has_assigned_point:
                        select_sql = (
                            f"SELECT code, gtin, {sn_expr}, assigned_point "
                            f"FROM pool_codes "
                            f"WHERE used = 0 AND {where_expr} "
                            f"ORDER BY "
                            f"  CASE "
                            f"    WHEN assigned_point = ? THEN 0 "
                            f"    WHEN assigned_point IS NULL OR assigned_point = '' THEN 1 "
                            f"    ELSE 2 "
                            f"  END, "
                            f"  rowid ASC "
                            f"LIMIT 1"
                        )
                        free_row = conn.execute(select_sql, tuple(sql_params) + (point or "",)).fetchone()
                    else:
                        select_sql = (
                            f"SELECT code, gtin, {sn_expr}, '' AS assigned_point "
                            f"FROM pool_codes "
                            f"WHERE used = 0 AND {where_expr} "
                            f"LIMIT 1"
                        )
                        free_row = conn.execute(select_sql, tuple(sql_params)).fetchone()

                    if free_row is not None:
                        f_dict = dict(free_row) if hasattr(free_row, "keys") else {
                            "code": free_row[0],
                            "gtin": free_row[1],
                            "sn": free_row[2],
                            "assigned_point": free_row[3],
                        }

                # ШАГ 4: Если по колонке gtin совпадений не нашлось — сравниваем ШК со ВСЕМИ кодами ЧЗ (pool_codes.code)
                if total_cnt == 0:
                    total_cnt, free_cnt, matched_gtin, f_dict = _match_barcode_against_all_cz_in_python(
                        conn, candidates, stripped_digits, sn_expr, pt_expr, point
                    )

                if total_cnt == 0:
                    err_msg = f"Неизвестный ШК ({short_display}): связанный код Честного Знака не найден в базе"
                    _LAST_SCAN_ERROR_INFO = err_msg
                    _LAST_SCAN_ERROR_TS = time.time()
                    logger.warning("[CZResolver] %s", err_msg)
                    return {
                        "ok": False,
                        "error_code": "UNKNOWN_BARCODE",
                        "info": err_msg,
                        "barcode": raw_barcode,
                    }

                if free_cnt <= 0 or f_dict is None:
                    err_msg = f"Нет свободных кодов ЧЗ для ШК {short_display} (GTIN {matched_gtin}: все {total_cnt} кодов использованы)"
                    _LAST_SCAN_ERROR_INFO = err_msg
                    _LAST_SCAN_ERROR_TS = time.time()
                    logger.warning("[CZResolver] %s", err_msg)
                    return {
                        "ok": False,
                        "error_code": "NO_FREE_CODES",
                        "info": err_msg,
                        "gtin": matched_gtin,
                        "barcode": raw_barcode,
                    }

                use_pt = point or f_dict.get("assigned_point") or "T-01"
                if claim:
                    if has_used_point:
                        conn.execute(
                            "UPDATE pool_codes SET used = 1, used_point = ? WHERE code = ?",
                            (use_pt, f_dict["code"])
                        )
                    else:
                        conn.execute(
                            "UPDATE pool_codes SET used = 1 WHERE code = ?",
                            (f_dict["code"],)
                        )
                    conn.commit()
                    logger.info(
                        "[CZResolver] ШК «%s» успешно связан с кодом ЧЗ (GTIN=%s, SN=%s)",
                        short_display, f_dict["gtin"], f_dict.get("sn")
                    )
                elif align_point_for_operator and point and has_assigned_point and f_dict.get("assigned_point") != point:
                    conn.execute(
                        "UPDATE pool_codes SET assigned_point = ? WHERE code = ?",
                        (point, f_dict["code"])
                    )
                    conn.commit()

                _LAST_SCAN_ERROR_INFO = None
                return {
                    "ok": True,
                    "code": f_dict["code"],
                    "gtin": f_dict["gtin"],
                    "sn": f_dict.get("sn") or "",
                    "point": use_pt,
                    "barcode": raw_barcode,
                    "info": f"Связан с ЧЗ (GTIN {f_dict['gtin']}, SN {f_dict.get('sn')})",
                }

        except Exception as e:
            logger.exception("Ошибка БД при связывании ШК %s с ЧЗ: %s", short_display, e)
            return {
                "ok": False,
                "error_code": "DB_ERROR",
                "info": f"Ошибка БД при проверке ШК ({short_display}): {e}",
                "barcode": raw_barcode,
            }


# ---------------------------------------------------------------------------
# 2. ГАРАНТИРОВАННЫЙ ПОКАЗ УВЕДОМЛЕНИЙ И ОШИБОК ОПЕРАТОРУ (Toast + QMessageBox)
# ---------------------------------------------------------------------------

def _find_operator_window():
    """
    Находит активный экземпляр OperatorWindow среди всех виджетов приложения
    (включая дочерние виджеты внутри QMainWindow / QStackedWidget).
    """
    if _ENGINE_INSTANCE is not None:
        op_win = (
            _ENGINE_INSTANCE.parent()
            if hasattr(_ENGINE_INSTANCE, "parent") and callable(_ENGINE_INSTANCE.parent)
            else getattr(_ENGINE_INSTANCE, "_parent_ref", None)
        )
        if op_win is not None:
            return op_win

    if PYSIDE_AVAILABLE and QApplication is not None and QApplication.instance() is not None:
        try:
            # 1. Ищем среди всех виджетов по имени класса OperatorWindow
            for w in QApplication.allWidgets():
                if type(w).__name__ == "OperatorWindow" and w.isVisible():
                    return w
            for w in QApplication.allWidgets():
                if type(w).__name__ == "OperatorWindow":
                    return w
            # 2. Ищем активное окно или любое видимое окно верхнего уровня
            active_w = QApplication.activeWindow()
            if active_w is not None:
                return active_w
            for w in QApplication.topLevelWidgets():
                if w.isVisible():
                    return w
        except Exception:
            pass
    return None


def _notify_operator_ui(message: str, level: str = "err", show_dialog: bool = False):
    """
    Потокобезопасный вывод уведомления (Toast + при ошибках QMessageBox) в окно оператора.
    """
    if _ENGINE_INSTANCE is not None and PYSIDE_AVAILABLE:
        try:
            _ENGINE_INSTANCE.ui_notification.emit(message, level, show_dialog)
            return
        except Exception:
            pass
    _show_operator_feedback_direct(message, level, show_dialog)


def _show_operator_feedback_direct(message: str, level: str = "err", show_dialog: bool = False):
    """
    Отображает уведомление во всех найденных Toast-виджетах и при ошибке открывает
    наглядное окно предупреждения QMessageBox, чтобы оператор гарантированно увидел ошибку.
    """
    global _LAST_ERROR_DIALOG_TS
    if not PYSIDE_AVAILABLE or QApplication is None or QApplication.instance() is None:
        return

    op_win = _find_operator_window()
    toast_shown = False

    # 1. Показываем сообщение во всех доступных Toast-виджетах видимых окон
    try:
        candidates = []
        if op_win is not None:
            candidates.append(op_win)
            if op_win.window() is not None and op_win.window() not in candidates:
                candidates.append(op_win.window())
        for w in QApplication.allWidgets():
            if hasattr(w, "toast") and w.isVisible() and w not in candidates:
                candidates.append(w)

        for target_w in candidates:
            toast = getattr(target_w, "toast", None)
            if toast is None:
                try:
                    from toast import Toast
                    toast = Toast(target_w)
                    setattr(target_w, "toast", toast)
                except Exception:
                    toast = None
            if toast is not None and hasattr(toast, "show_message"):
                fn = getattr(toast, "_orig_show_message", toast.show_message)
                fn(message, level)
                toast_shown = True
    except Exception as e:
        logger.debug("Ошибка показа Toast: %s", e)

    # 2. Если произошла ошибка связывания ШК и ЧЗ — выводим явное окно предупреждения QMessageBox
    if (show_dialog or not toast_shown) and level in ("err", "error", "warn", "warning") and QMessageBox is not None:
        now = time.time()
        if (now - _LAST_ERROR_DIALOG_TS) >= 1.2:
            _LAST_ERROR_DIALOG_TS = now

            def _open_msgbox():
                try:
                    parent_w = _find_operator_window()
                    title = "Неизвестный ШК" if "Неизвестный ШК" in message else "Ошибка сканирования ШК"
                    QMessageBox.warning(parent_w, title, message)
                except Exception as ex:
                    logger.debug("Ошибка показа QMessageBox: %s", ex)

            QTimer.singleShot(10, _open_msgbox)


# ---------------------------------------------------------------------------
# 3. ГАРАНТИРОВАННОЕ ВЫПОЛНЕНИЕ ПЕЧАТИ ИЛИ ВЫВОДА ОШИБКИ ПРИ СКАНИРОВАНИИ
# ---------------------------------------------------------------------------

def resolve_bound_printer_for_scan(
    scanner_hint: Optional[str] = None,
    config_path: str = "devices_config.json"
) -> Optional[str]:
    """
    Определяет точное имя принтера Windows, привязанного старшим смены к сканеру в devices_config.json.
    """
    p = Path(config_path)
    if not p.exists():
        return None

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("Ошибка чтения %s при выборе принтера: %s", config_path, e)
        return None

    bindings: Dict[str, str] = data.get("bindings") or {}
    if not bindings:
        return None

    for candidate in (scanner_hint, _LAST_ACTIVE_SCANNER_KEY):
        if candidate:
            if candidate in bindings and bindings[candidate]:
                return bindings[candidate]
            cand_up = str(candidate).strip().upper()
            for k, v in bindings.items():
                if k.strip().upper() == cand_up and v:
                    return v

    op_win = _find_operator_window()
    if op_win is not None:
        for attr in ("_current_scanner", "current_scanner", "active_scanner"):
            sc_obj = getattr(op_win, attr, None)
            if isinstance(sc_obj, dict):
                com_p = (sc_obj.get("com_port") or "").strip()
                sc_name = (sc_obj.get("name") or "").strip()
                for k_cand in (com_p, sc_name):
                    if k_cand and k_cand in bindings and bindings[k_cand]:
                        return bindings[k_cand]

    if "USB-HID" in bindings and bindings["USB-HID"]:
        return bindings["USB-HID"]

    for s_key, pr_name in bindings.items():
        if pr_name:
            return pr_name

    return None


def _ensure_valid_scan_printed(raw_barcode: str):
    """
    Страхующий коллбэк (срабатывает через 120 мс после сканирования):
    Если штатный код OperatorWindow по какой-либо причине (лимит, узел, точка или фильтр)
    проигнорировал скан и НЕ вызвал print_code и НЕ показал ошибку —
    мы самостоятельно сравниваем ШК с базой ЧЗ и либо печатаем связанный ЧЗ, либо выдаём ошибку!
    """
    global _PENDING_VALID_SCAN
    clean_s = clean_scanned_barcode(raw_barcode)
    if not clean_s:
        return

    # Если этот скан уже был напечатан или обработан штатным потоком — ничего не дублируем
    if _PENDING_VALID_SCAN.get("raw") == clean_s and _PENDING_VALID_SCAN.get("printed"):
        return

    store_ref = _ENGINE_INSTANCE.store if _ENGINE_INSTANCE is not None else None
    cz_res = resolve_and_claim_cz_code(clean_s, store=store_ref, claim=True)

    if not cz_res.get("ok"):
        err_msg = cz_res.get("info") or f"Неизвестный ШК ({clean_s}): связанный код Честного Знака не найден"
        if store_ref is not None:
            try:
                store_ref.increment_counter("errors")
                store_ref.log(
                    "Неизвестный ШК",
                    point=getattr(store_ref, "current_point", "T-01"),
                    operator="Оператор",
                    gtin=clean_s[:14],
                    sn="—",
                    result=err_msg
                )
            except Exception:
                pass
        _notify_operator_ui(err_msg, "err", show_dialog=True)
        return

    # Связанный код ЧЗ найден! Отправляем его напрямую на привязанный принтер
    dm_code = cz_res["code"]
    gtin = cz_res.get("gtin", "")
    sn = cz_res.get("sn", "")
    point = cz_res.get("point") or (getattr(store_ref, "current_point", "T-01") if store_ref else "T-01")

    _PENDING_VALID_SCAN["raw"] = clean_s
    _PENDING_VALID_SCAN["ts"] = time.time()
    _PENDING_VALID_SCAN["printed"] = True

    try:
        from printer import print_code, load_config, get_printer_for_point
        cfg = load_config() if callable(load_config) else {}
        p_cfg = get_printer_for_point(cfg, point) if callable(get_printer_for_point) else dict(cfg or {})
        p_cfg = dict(p_cfg or {})
        p_cfg["_verified_cz_code"] = True

        pr_res = print_code(dm_code, p_cfg)
        ok = pr_res.get("ok", False) if isinstance(pr_res, dict) else bool(pr_res)
        info = pr_res.get("info", "") if isinstance(pr_res, dict) else str(pr_res)

        if ok:
            if store_ref is not None:
                try:
                    store_ref.mark_printed(dm_code, point=point)
                    store_ref.increment_counter("scans")
                    store_ref.increment_counter("prints")
                    store_ref.log(
                        "Печать этикетки ЧЗ",
                        point=point,
                        operator="Оператор",
                        gtin=gtin,
                        sn=sn,
                        result=f"успех ({info})"
                    )
                except Exception:
                    pass
            _notify_operator_ui(f"✓ Напечатан ЧЗ (GTIN {gtin}, SN {sn})", "ok", show_dialog=False)
        else:
            if store_ref is not None:
                try:
                    store_ref.increment_counter("errors")
                except Exception:
                    pass
            _notify_operator_ui(f"Ошибка печати ЧЗ: {info}", "err", show_dialog=True)
    except Exception as e:
        logger.exception("Ошибка прямой отправки ЧЗ на печать: %s", e)
        _notify_operator_ui(f"Ошибка отправки на печать: {e}", "err", show_dialog=True)


class _GlobalScanEventFilter(QObject):
    """
    Глобальный фильтр событий клавиатуры для OperatorWindow:
    Перехватывает нажатие Enter (завершение сканирования USB-HID сканером) в поле ввода
    окна оператора ДО того, как поле очистится, и гарантирует, что по отсканированному ШК
    будет выполнена проверка ЧЗ -> печать или вывод ошибки «Неизвестный ШК».
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._unfocused_buf = ""
        self._last_key_ts = 0.0

    def eventFilter(self, obj, event):
        if not PYSIDE_AVAILABLE or QEvent is None:
            return False
        try:
            if event.type() == QEvent.KeyPress:
                key = event.key()
                # Проверяем, что активно именно рабочее окно оператора (а не диалог или админка)
                active_win = QApplication.activeWindow() if QApplication else None
                if active_win is not None and type(active_win).__name__ in ("AdminWindow", "SQLServerConfigDialog", "AssignPrinterDialog"):
                    return False

                if isinstance(obj, QLineEdit):
                    # Игнорируем поля ввода пароля или поиска в админке
                    obj_win = obj.window()
                    if obj_win is not None and type(obj_win).__name__ == "AdminWindow":
                        return False
                    if obj.echoMode() == QLineEdit.Password:
                        return False

                    if key in (Qt.Key_Return, Qt.Key_Enter):
                        scanned_text = obj.text().strip()
                        if scanned_text:
                            logger.info("[GlobalScanFilter] Перехвачен ввод ШК в поле ввода: %s", scanned_text)
                            QTimer.singleShot(120, lambda t=scanned_text: _ensure_valid_scan_printed(t))
        except Exception as e:
            logger.debug("Ошибка в _GlobalScanEventFilter: %s", e)
        return False


def install_global_scan_event_filter():
    """Устанавливает глобальный фильтр перехвата сканирований USB-HID на QApplication."""
    global _GLOBAL_EVENT_FILTER_INSTALLED, _GLOBAL_EVENT_FILTER_OBJ
    if _GLOBAL_EVENT_FILTER_INSTALLED or not PYSIDE_AVAILABLE or QApplication is None:
        return
    app = QApplication.instance()
    if app is not None:
        _GLOBAL_EVENT_FILTER_OBJ = _GlobalScanEventFilter(app)
        app.installEventFilter(_GLOBAL_EVENT_FILTER_OBJ)
        _GLOBAL_EVENT_FILTER_INSTALLED = True
        logger.info("Глобальный перехватчик USB-HID сканирований установлен на QApplication")


# ---------------------------------------------------------------------------
# 4. ПЕРЕХВАТЧИК ПАРСЕРА ШК И МАРШРУТИЗАТОР ПЕЧАТИ (BarcodeResolver & PrintRouter)
# ---------------------------------------------------------------------------

def install_barcode_resolver_hooks():
    """
    Устанавливает перехватчик на функцию parse_foreign_barcode в модуле barcode
    и во всех модулях, которые её импортировали (operator_window, store, __main__).
    """
    global _BARCODE_RESOLVER_INSTALLED
    if _BARCODE_RESOLVER_INSTALLED:
        return

    try:
        import barcode as barcode_mod
    except ImportError:
        return

    orig_parse = getattr(barcode_mod, "_orig_parse_foreign_barcode", getattr(barcode_mod, "parse_foreign_barcode", None))
    if orig_parse is None:
        return

    def patched_parse_foreign_barcode(raw: str, *args, **kwargs):
        global _LAST_SCAN_ERROR_INFO, _LAST_SCAN_ERROR_TS, _PENDING_VALID_SCAN
        clean_s = clean_scanned_barcode(raw)
        short_s = (clean_s[:32] + "…") if len(clean_s) > 32 else (clean_s or "пусто")

        if not clean_s:
            return orig_parse(raw, *args, **kwargs)

        store_ref = _ENGINE_INSTANCE.store if _ENGINE_INSTANCE is not None else None
        check = resolve_and_claim_cz_code(
            clean_s,
            store=store_ref,
            claim=False,
            align_point_for_operator=True
        )

        # Если произошла непредвиденная ошибка БД — не блокируем штатный парсер
        if not check.get("ok") and check.get("error_code") in ("DB_ERROR", "DB_UNAVAILABLE"):
            return orig_parse(raw, *args, **kwargs)

        # ЕСЛИ ОТСКАНИРОВАННЫЙ ШК НЕ СВЯЗАН НИ С ОДНИМ КОДОМ ЧЗ (ИЛИ НЕТ СВОБОДНЫХ КОДОВ):
        # Немедленно выводим ошибку оператору (Toast + QMessageBox) и блокируем печать!
        if not check.get("ok"):
            err_text = check.get("info") or f"Неизвестный ШК ({short_s}): не удалось связать ШК и Честный Знак"
            _LAST_SCAN_ERROR_INFO = err_text
            _LAST_SCAN_ERROR_TS = time.time()
            _PENDING_VALID_SCAN["raw"] = clean_s
            _PENDING_VALID_SCAN["ts"] = time.time()
            _PENDING_VALID_SCAN["printed"] = True  # Помечаем обработанным, чтобы таймер не открыл второе окно

            # ГАРАНТИРОВАННО показываем ошибку на экране!
            _notify_operator_ui(err_text, "err", show_dialog=True)

            try:
                empty_res = orig_parse("", *args, **kwargs)
            except Exception:
                empty_res = None

            if isinstance(empty_res, dict):
                res_err = dict(empty_res)
                res_err["ok"] = False
                for k in ("info", "error", "message", "reason"):
                    if k in res_err or k in ("info", "error"):
                        res_err[k] = err_text
                return res_err
            elif hasattr(empty_res, "_replace") and hasattr(empty_res, "_fields"):
                updates = {}
                if "ok" in empty_res._fields:
                    updates["ok"] = False
                for k in ("info", "error", "message"):
                    if k in empty_res._fields:
                        updates[k] = err_text
                if "gtin" in empty_res._fields:
                    updates["gtin"] = ""
                return empty_res._replace(**updates) if updates else empty_res

            return empty_res

        # СВЯЗАННЫЙ СВОБОДНЫЙ КОД ЧЗ НАЙДЕН В БАЗЕ!
        _LAST_SCAN_ERROR_INFO = None
        db_gtin = str(check["gtin"]).strip()
        db_sn = str(check.get("sn") or "SN000001").strip()
        parsed_cand = extract_gtin_candidates(clean_s)
        raw_sn = parsed_cand.get("extracted_sn") or db_sn or f"SN{int(time.time() * 1000) % 1000000:06d}"
        clean_sn = re.sub(r"[^A-Za-z0-9\-_]", "", raw_sn)[:20] or "SN000001"

        # Регистрируем ожидание печати: если штатный код OperatorWindow сам не вызовет print_code за 120 мс,
        # _ensure_valid_scan_printed гарантированно отправит найденный код ЧЗ на принтер!
        _PENDING_VALID_SCAN["raw"] = clean_s
        _PENDING_VALID_SCAN["ts"] = time.time()
        _PENDING_VALID_SCAN["printed"] = False
        if PYSIDE_AVAILABLE and QApplication is not None and QApplication.instance() is not None:
            QTimer.singleShot(120, lambda r=clean_s: _ensure_valid_scan_printed(r))

        res = None
        for gs1_candidate in (
            f"01{db_gtin.zfill(14)}21{clean_sn}",
            f"01{db_gtin.zfill(14)}21{clean_sn}\x1d91EE06\x1d92SAMPLE==",
            raw,
        ):
            try:
                cand_res = orig_parse(gs1_candidate, *args, **kwargs)
                if cand_res is not None:
                    if isinstance(cand_res, dict) and not cand_res.get("ok", True) and not cand_res.get("gtin"):
                        continue
                    res = cand_res
                    break
            except Exception:
                continue

        if isinstance(res, dict):
            res_copy = dict(res)
            if "ok" in res_copy:
                res_copy["ok"] = True
            res_copy["gtin"] = db_gtin
            if "sn" in res_copy and not res_copy.get("sn"):
                res_copy["sn"] = clean_sn
            return res_copy
        elif hasattr(res, "_replace") and hasattr(res, "_fields"):
            updates = {}
            if "gtin" in res._fields:
                updates["gtin"] = db_gtin
            if "ok" in res._fields:
                updates["ok"] = True
            return res._replace(**updates) if updates else res
        elif isinstance(res, tuple):
            if len(res) >= 2:
                return (db_gtin, clean_sn) + res[2:]
            return (db_gtin,)
        elif res is not None and hasattr(res, "gtin"):
            try:
                setattr(res, "gtin", db_gtin)
            except Exception:
                pass
            return res

        return {"ok": True, "gtin": db_gtin, "sn": clean_sn, "raw": clean_s}

    barcode_mod._orig_parse_foreign_barcode = orig_parse
    barcode_mod.parse_foreign_barcode = patched_parse_foreign_barcode

    for mod in list(sys.modules.values()):
        if mod is not None and hasattr(mod, "parse_foreign_barcode"):
            try:
                setattr(mod, "parse_foreign_barcode", patched_parse_foreign_barcode)
            except Exception:
                pass

    _BARCODE_RESOLVER_INSTALLED = True
    logger.info("Глобальный резолвер ШК -> ЧЗ (parse_foreign_barcode) успешно установлен")


def install_global_print_router(config_path: str = "devices_config.json"):
    """
    Устанавливает перехватчик на функции print_code, get_printer_for_point и parse_foreign_barcode
    во всех модулях проекта.
    """
    global _PRINT_ROUTER_INSTALLED
    install_barcode_resolver_hooks()
    install_global_scan_event_filter()

    if _PRINT_ROUTER_INSTALLED:
        return

    try:
        import printer as printer_mod
    except ImportError:
        return

    orig_print_code = getattr(printer_mod, "_orig_print_code", printer_mod.print_code)
    orig_get_printer_for_point = getattr(
        printer_mod, "_orig_get_printer_for_point",
        getattr(printer_mod, "get_printer_for_point", None)
    )
    detect_lang_fn = getattr(printer_mod, "detect_language", lambda name: "auto")

    def routed_print_code(code: str, cfg: Optional[Dict[str, Any]] = None, *args, **kwargs):
        global _PENDING_VALID_SCAN
        cfg_copy = dict(cfg or {})
        code_str = str(code or "").strip()

        # Помечаем, что печать для текущего скана была вызвана
        _PENDING_VALID_SCAN["printed"] = True

        is_admin_test = (_ADMIN_TEST_PRINT_MARKER in code_str) or bool(cfg_copy.get("_is_test_print"))
        already_verified_cz = bool(cfg_copy.pop("_verified_cz_code", False))

        claimed_here = False
        resolved_info: Optional[Dict[str, Any]] = None
        store_ref = _ENGINE_INSTANCE.store if _ENGINE_INSTANCE is not None else None

        if not is_admin_test and not already_verified_cz:
            resolved_info = resolve_and_claim_cz_code(
                code_str,
                store=store_ref,
                claim=True,
                allow_already_claimed=True
            )
            err_code = resolved_info.get("error_code")
            if not resolved_info.get("ok") and err_code in ("UNKNOWN_BARCODE", "NO_FREE_CODES"):
                err_msg = resolved_info.get("info") or f"Неизвестный ШК ({code_str}): не удалось связать с ЧЗ"
                logger.warning("[PrintRouter] Печать заблокирована: %s", err_msg)
                _notify_operator_ui(err_msg, "err", show_dialog=True)
                return {
                    "ok": False,
                    "info": err_msg,
                    "error_code": err_code,
                }

            if resolved_info.get("ok") and resolved_info.get("code") and resolved_info["code"] != code_str:
                logger.info(
                    "[PrintRouter] ШК «%s» заменён на связанный код ЧЗ (GTIN %s, SN %s)",
                    code_str[:30], resolved_info.get("gtin"), resolved_info.get("sn")
                )
                code_str = resolved_info["code"]
                claimed_here = not resolved_info.get("already_in_pool", False)

        scanner_hint = cfg_copy.get("_scanner_key") or cfg_copy.get("com_port")
        bound_printer = resolve_bound_printer_for_scan(scanner_hint=scanner_hint, config_path=config_path)

        if bound_printer:
            lang = detect_lang_fn(bound_printer)
            cfg_copy["printer_name"] = bound_printer
            cfg_copy["name"] = bound_printer
            cfg_copy["printer_ip"] = ""
            cfg_copy["ip"] = ""
            if not cfg_copy.get("printer_lang") or cfg_copy.get("printer_lang") in ("zpl", "tspl", "ezpl"):
                cfg_copy["printer_lang"] = lang
                cfg_copy["lang"] = lang

            logger.info(
                "[PrintRouter] Печать кода ЧЗ направлена строго на назначенный принтер: «%s» (язык: %s)",
                bound_printer, cfg_copy.get("printer_lang")
            )

        res = orig_print_code(code_str, cfg_copy, *args, **kwargs)
        ok = res.get("ok", False) if isinstance(res, dict) else bool(res)

        if ok and claimed_here and resolved_info:
            try:
                if store_ref is not None and hasattr(store_ref, "mark_printed"):
                    store_ref.mark_printed(code_str, point=resolved_info.get("point"))
                else:
                    import db
                    with db.get_conn() as conn:
                        conn.execute("UPDATE pool_codes SET printed = 1 WHERE code = ?", (code_str,))
                        conn.commit()
            except Exception as e:
                logger.debug("Ошибка отметки printed=1 для кода ЧЗ: %s", e)

        return res

    def routed_get_printer_for_point(cfg: Dict[str, Any], point: str, *args, **kwargs):
        res = orig_get_printer_for_point(cfg, point, *args, **kwargs) if orig_get_printer_for_point else dict(cfg or {})
        res_copy = dict(res or {})
        bound_printer = resolve_bound_printer_for_scan(config_path=config_path)
        if bound_printer:
            lang = detect_lang_fn(bound_printer)
            res_copy["printer_name"] = bound_printer
            res_copy["name"] = bound_printer
            res_copy["printer_ip"] = ""
            res_copy["ip"] = ""
            res_copy["printer_lang"] = lang
            res_copy["lang"] = lang
        return res_copy

    printer_mod._orig_print_code = orig_print_code
    printer_mod.print_code = routed_print_code
    if orig_get_printer_for_point:
        printer_mod._orig_get_printer_for_point = orig_get_printer_for_point
        printer_mod.get_printer_for_point = routed_get_printer_for_point

    for mod in list(sys.modules.values()):
        if mod is not None:
            if hasattr(mod, "print_code"):
                try:
                    setattr(mod, "print_code", routed_print_code)
                except Exception:
                    pass
            if hasattr(mod, "get_printer_for_point") and orig_get_printer_for_point:
                try:
                    setattr(mod, "get_printer_for_point", routed_get_printer_for_point)
                except Exception:
                    pass

    _PRINT_ROUTER_INSTALLED = True
    logger.info("Глобальный маршрутизатор печати (PrintRouter) успешно установлен")


# ---------------------------------------------------------------------------
# 5. МОДЕЛЬ ДАННЫХ ПАРЫ ОБОРУДОВАНИЯ
# ---------------------------------------------------------------------------

class DevicePairConfig:
    """Конфигурация одной независимой рабочей пары: Сканер + Принтер"""
    def __init__(
        self,
        pair_id: int,
        name: str,
        scanner_type: str,     # 'usb-com', 'bt-spp', 'tcp', 'usb-hid'
        scanner_port: str,     # 'COM3', 'USB-HID', '192.168.1.101', etc.
        printer_name: str,     # Windows printer name
        printer_ip: str = "",  # RAW TCP IP (if network printer)
        printer_port: int = 9100,
        printer_lang: str = "auto",
        label_w: float = 58.0,
        label_h: float = 40.0,
        dpi: int = 203,
        enabled: bool = True
    ):
        self.pair_id = pair_id
        self.name = name
        self.scanner_type = scanner_type
        self.scanner_port = scanner_port
        self.printer_name = printer_name
        self.printer_ip = printer_ip
        self.printer_port = printer_port
        self.printer_lang = printer_lang
        self.label_w = label_w
        self.label_h = label_h
        self.dpi = dpi
        self.enabled = enabled

        self.scans_count = 0
        self.prints_count = 0
        self.errors_count = 0
        self.last_barcode = ""
        self.last_status = "Ожидание сканирования"
        self.last_active_time = 0.0


# ---------------------------------------------------------------------------
# 6. ПОТОК ЧТЕНИЯ СКАНЕРА (_ScannerWorkerThread)
# ---------------------------------------------------------------------------

class _ScannerWorkerThread(QThread):
    """
    Выделенный фоновый поток непрерывного чтения конкретного сканера.
    Поддерживает сканеры как с суффиксом CR/LF, так и без суффикса (по таймауту 80 мс).
    """
    barcode_received = Signal(int, str)      # pair_id, barcode_text
    status_changed = Signal(int, str, bool)  # pair_id, status_text, is_alive

    def __init__(self, pair_cfg: DevicePairConfig, parent=None):
        super().__init__(parent)
        self.pair_cfg = pair_cfg
        self.pair_id = pair_cfg.pair_id
        self._running = True
        self._last_barcode = ""
        self._last_scan_time = 0.0
        self.debounce_sec = 1.2

    def stop(self):
        self._running = False
        if hasattr(self, "wait"):
            self.wait(1000)

    def run(self):
        stype = self.pair_cfg.scanner_type.lower()
        if stype in ("usb-com", "bt-spp", "com") and self.pair_cfg.scanner_port.upper().startswith("COM"):
            self._run_serial_loop()
        elif stype == "tcp":
            self._run_tcp_loop()
        else:
            self.status_changed.emit(self.pair_id, "USB-HID (обработка через PrintRouter)", True)

    def _run_serial_loop(self):
        if not SERIAL_AVAILABLE:
            self.status_changed.emit(self.pair_id, "Ошибка: pyserial не установлен", False)
            return

        port_name = self.pair_cfg.scanner_port
        buffer = bytearray()
        last_rx_time = 0.0

        while self._running:
            ser = None
            try:
                self.status_changed.emit(self.pair_id, f"Подключение к {port_name}...", False)
                ser = serial.Serial(
                    port=port_name,
                    baudrate=9600,
                    timeout=0.05,
                    write_timeout=0.5
                )
                self.status_changed.emit(self.pair_id, f"В сети ({port_name})", True)

                while self._running:
                    chunk = ser.read(128)
                    now = time.time()
                    if chunk:
                        buffer.extend(chunk)
                        last_rx_time = now
                        while b'\r' in buffer or b'\n' in buffer:
                            pos_cr = buffer.find(b'\r')
                            pos_lf = buffer.find(b'\n')

                            pos = pos_cr if pos_cr != -1 else pos_lf
                            if pos_cr != -1 and pos_lf != -1:
                                pos = min(pos_cr, pos_lf)

                            line_raw = buffer[:pos]
                            if pos + 1 < len(buffer) and buffer[pos:pos+2] in (b'\r\n', b'\n\r'):
                                buffer = buffer[pos+2:]
                            else:
                                buffer = buffer[pos+1:]

                            code_str = line_raw.decode("utf-8", errors="replace").strip()
                            if code_str:
                                self._dispatch_barcode(code_str)
                    elif buffer and (now - last_rx_time) >= 0.08:
                        # Если сканер не присылает CR/LF в конце посылки — сбрасываем буфер через 80 мс тишины
                        code_str = bytes(buffer).decode("utf-8", errors="replace").strip()
                        buffer.clear()
                        if code_str:
                            self._dispatch_barcode(code_str)

                    time.sleep(0.01)

            except Exception as e:
                err_s = str(e).lower()
                if "access is denied" in err_s or "permission" in err_s or "отказано в доступе" in err_s:
                    self.status_changed.emit(self.pair_id, f"Порт {port_name} управляется OperatorWindow", True)
                    return
                if self._running:
                    self.status_changed.emit(self.pair_id, f"Сбой порта {port_name}: {e}", False)
                    time.sleep(2.0)
            finally:
                if ser and getattr(ser, "is_open", False):
                    try:
                        ser.close()
                    except Exception:
                        pass

    def _run_tcp_loop(self):
        host = self.pair_cfg.scanner_port
        port = 9100
        if ":" in host:
            parts = host.split(":")
            host = parts[0]
            port = int(parts[1])

        while self._running:
            sock = None
            try:
                self.status_changed.emit(self.pair_id, f"Подключение к {host}:{port}...", False)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3.0)
                sock.connect((host, port))
                sock.settimeout(1.0)
                self.status_changed.emit(self.pair_id, f"В сети ({host})", True)

                buf = ""
                while self._running:
                    try:
                        data = sock.recv(1024)
                        if not data:
                            break
                        buf += data.decode("utf-8", errors="replace")
                        while "\n" in buf or "\r" in buf:
                            lines = re.split(r'[\r\n]+', buf)
                            if len(lines) > 1:
                                code = lines[0].strip()
                                buf = "\n".join(lines[1:])
                                if code:
                                    self._dispatch_barcode(code)
                    except socket.timeout:
                        if buf.strip():
                            code = buf.strip()
                            buf = ""
                            self._dispatch_barcode(code)
                        continue
            except Exception as e:
                if self._running:
                    self.status_changed.emit(self.pair_id, f"Ошибка TCP {host}: {e}", False)
                    time.sleep(3.0)
            finally:
                if sock:
                    sock.close()

    def _dispatch_barcode(self, raw_code: str):
        global _LAST_ACTIVE_SCANNER_KEY
        now = time.time()
        if raw_code == self._last_barcode and (now - self._last_scan_time) < self.debounce_sec:
            logger.debug("Дублирующий скан от пары #%d отброшен: %s", self.pair_id, raw_code)
            return

        self._last_barcode = raw_code
        self._last_scan_time = now
        _LAST_ACTIVE_SCANNER_KEY = self.pair_cfg.scanner_port
        logger.info("Пара #%d [Сканер %s] принял ШК: %s", self.pair_id, self.pair_cfg.scanner_port, raw_code)
        self.barcode_received.emit(self.pair_id, raw_code)


# ---------------------------------------------------------------------------
# 7. АСИНХРОННЫЙ ВОРКЕР ПЕЧАТИ (_AsyncPrintTask)
# ---------------------------------------------------------------------------

class _AsyncPrintTask(QRunnable):
    """
    Фоновая неблокирующая отправка связанного кода ЧЗ на физический термопринтер конкретной пары
    """
    def __init__(
        self,
        pair_cfg: DevicePairConfig,
        datamatrix_code: str,
        cz_meta: Dict[str, Any],
        on_finished_callback
    ):
        super().__init__()
        self.pair_cfg = pair_cfg
        self.datamatrix_code = datamatrix_code
        self.cz_meta = cz_meta
        self.callback = on_finished_callback

    def run(self):
        pair_id = self.pair_cfg.pair_id
        try:
            from printer import print_code

            p_config = {
                "_scanner_key": self.pair_cfg.scanner_port,
                "_verified_cz_code": True,
                "printer_name": self.pair_cfg.printer_name,
                "name": self.pair_cfg.printer_name,
                "printer_ip": self.pair_cfg.printer_ip,
                "ip": self.pair_cfg.printer_ip,
                "printer_port": self.pair_cfg.printer_port,
                "port": self.pair_cfg.printer_port,
                "printer_lang": self.pair_cfg.printer_lang,
                "lang": self.pair_cfg.printer_lang,
                "label_width_mm": self.pair_cfg.label_w,
                "label_height_mm": self.pair_cfg.label_h,
                "dpi": self.pair_cfg.dpi,
                "copies": 1
            }

            res = print_code(self.datamatrix_code, p_config)
            success = res.get("ok", False) if isinstance(res, dict) else bool(res)
            msg = res.get("info", "Успешно") if isinstance(res, dict) else str(res)

            if self.callback:
                self.callback(pair_id, success, msg, self.cz_meta)

        except Exception as e:
            logger.exception("Ошибка отправки задания на принтер пары #%d: %s", pair_id, e)
            if self.callback:
                self.callback(pair_id, False, str(e), self.cz_meta)


# ---------------------------------------------------------------------------
# 8. ЦЕНТРАЛЬНЫЙ ДИСПЕТЧЕР (WorkstationEngine)
# ---------------------------------------------------------------------------

class WorkstationEngine(QObject):
    """
    Ядро управления N параллельными парами оборудования на одном компьютере.
    """
    pair_updated = Signal(int, dict)                    # pair_id, stats_dict
    code_processed = Signal(int, str, str)              # pair_id, barcode, status
    ui_notification = Signal(str, str, bool)            # message, level, show_dialog
    _print_done_internal = Signal(int, bool, str, dict) # pair_id, success, msg, cz_meta

    def __init__(self, store_instance=None, parent=None):
        super().__init__(parent)
        global _ENGINE_INSTANCE
        _ENGINE_INSTANCE = self
        self._parent_ref = parent
        self.store = store_instance
        self.pairs: Dict[int, DevicePairConfig] = {}
        self.threads: Dict[int, _ScannerWorkerThread] = {}
        self.thread_pool = QThreadPool.globalInstance()

        self.ui_notification.connect(self._on_ui_notification)
        self._print_done_internal.connect(self._on_print_finished_main_thread)

        self._attach_store_and_operator_hooks()
        install_global_print_router()

    def _attach_store_and_operator_hooks(self):
        """
        Интегрирует методы связывания ШК->ЧЗ в Store, перехватывает Toast в OperatorWindow
        и устанавливает фильтр клавиатурного сканирования USB-HID.
        """
        install_global_scan_event_filter()

        if self.store is not None:
            def _store_get_free_code(barcode: str, scanner_id: Optional[int] = None, point: Optional[str] = None) -> Optional[str]:
                res = resolve_and_claim_cz_code(barcode, store=self.store, point=point, claim=True)
                return res.get("code") if res.get("ok") else None

            self.store.get_free_code_for_barcode = _store_get_free_code
            self.store.resolve_and_claim_cz_code = lambda barcode, point=None, claim=True: resolve_and_claim_cz_code(
                barcode, store=self.store, point=point, claim=claim
            )

        op_win = _find_operator_window()
        if op_win is not None:
            toast = getattr(op_win, "toast", None)
            if toast is not None and hasattr(toast, "show_message") and not hasattr(toast, "_orig_show_message"):
                orig_show = toast.show_message

                def _hooked_show_message(msg: str, kind: str = "ok", *args, **kwargs):
                    global _LAST_SCAN_ERROR_INFO
                    if kind in ("err", "warn", "error", "warning") and _LAST_SCAN_ERROR_INFO:
                        if (time.time() - _LAST_SCAN_ERROR_TS) < 1.5:
                            msg = _LAST_SCAN_ERROR_INFO
                            kind = "err"
                            _LAST_SCAN_ERROR_INFO = None
                    return orig_show(msg, kind, *args, **kwargs)

                toast._orig_show_message = orig_show
                toast.show_message = _hooked_show_message

    @Slot(str, str, bool)
    def _on_ui_notification(self, message: str, level: str, show_dialog: bool):
        _show_operator_feedback_direct(message, level, show_dialog)

    def register_pair(self, pair_cfg: DevicePairConfig):
        """Регистрация независимой пары 'Сканер -> Принтер'"""
        self.pairs[pair_cfg.pair_id] = pair_cfg
        logger.info("Зарегистрирована рабочая пара #%d: [%s -> %s]",
                    pair_cfg.pair_id, pair_cfg.scanner_port, pair_cfg.printer_name)

    def load_pairs_from_config(self, config_path: str = "devices_config.json") -> int:
        """
        Автоматическая загрузка привязок 'Сканер -> Принтер', выбранных старшим смены
        в админ-панели (из файла devices_config.json), и синхронизация со Store и printer.py.
        """
        self._attach_store_and_operator_hooks()
        install_global_print_router(config_path=config_path)

        p = Path(config_path)
        if not p.exists():
            logger.warning("Файл конфигурации %s не найден", config_path)
            return 0

        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)

            bindings = data.get("bindings") or {}
            if bindings:
                try:
                    from master_devices_window import sync_bindings_to_system
                    sync_bindings_to_system(bindings=bindings, store=self.store)
                except Exception as sync_err:
                    logger.debug("Ошибка вызова sync_bindings_to_system: %s", sync_err)

            self.pairs.clear()
            for item in data.get("pairs", []):
                cfg = DevicePairConfig(
                    pair_id=int(item.get("pair_id", len(self.pairs) + 1)),
                    name=item.get("name", f"Пост #{len(self.pairs) + 1}"),
                    scanner_type=item.get("scanner_type", "usb-com"),
                    scanner_port=item.get("scanner_port", ""),
                    printer_name=item.get("printer_name", ""),
                    printer_ip=item.get("printer_ip", ""),
                    printer_port=int(item.get("printer_port", 9100)),
                    printer_lang=item.get("printer_lang", "auto"),
                    label_w=float(item.get("label_w", 58.0)),
                    label_h=float(item.get("label_h", 40.0)),
                    dpi=int(item.get("dpi", 203)),
                    enabled=bool(item.get("enabled", True))
                )
                self.register_pair(cfg)

            logger.info("Загружено %d рабочих пар из %s", len(self.pairs), config_path)
            return len(self.pairs)
        except Exception as e:
            logger.error("Ошибка загрузки пар из %s: %s", config_path, e)
            return 0

    def start_all(self):
        """Запуск изолированных потоков для всех активных сканеров."""
        self._attach_store_and_operator_hooks()
        install_global_print_router()
        self.stop_all()

        for pair_id, cfg in self.pairs.items():
            if not cfg.enabled:
                continue

            th = _ScannerWorkerThread(cfg, self)
            th.barcode_received.connect(self.handle_incoming_scan)
            th.status_changed.connect(self._on_scanner_status_changed)
            self.threads[pair_id] = th
            th.start()
            logger.info("Запущен поток сканера для пары #%d (%s -> %s)", pair_id, cfg.scanner_port, cfg.printer_name)

    def stop_all(self):
        """Корректная остановка всех потоков оборудования"""
        for pair_id, th in self.threads.items():
            th.stop()
        self.threads.clear()
        logger.info("Все потоки оборудования остановлены")

    @Slot(int, str)
    def handle_incoming_scan(self, pair_id: int, scanned_barcode: str):
        """
        Обработка скана с пары #i:
        1. Сравнение отсканированного ШК со всеми кодами Честного Знака в базе (pool_codes / gtin_map).
        2. Если ШК не связан ни с одним ЧЗ -> блокировка печати и показ ошибки (Toast + QMessageBox).
        3. Если код ЧЗ найден -> неблокирующая отправка связанного кода ЧЗ на Принтер_i.
        """
        global _LAST_ACTIVE_SCANNER_KEY, _PENDING_VALID_SCAN
        pair = self.pairs.get(pair_id)
        if not pair:
            return

        _LAST_ACTIVE_SCANNER_KEY = pair.scanner_port
        pair.scans_count += 1
        pair.last_barcode = scanned_barcode
        pair.last_active_time = time.time()

        _PENDING_VALID_SCAN["raw"] = clean_scanned_barcode(scanned_barcode)
        _PENDING_VALID_SCAN["ts"] = time.time()
        _PENDING_VALID_SCAN["printed"] = True

        cz_res = self._resolve_datamatrix_code_full(pair_id, scanned_barcode)

        if not cz_res.get("ok"):
            err_msg = cz_res.get("info") or f"Неизвестный ШК ({scanned_barcode}): не удалось связать с ЧЗ"
            err_code = cz_res.get("error_code", "UNKNOWN_BARCODE")

            pair.errors_count += 1
            pair.last_status = err_msg
            if self.store is not None:
                try:
                    self.store.increment_counter("errors")
                    self.store.log(
                        "Неизвестный ШК" if err_code == "UNKNOWN_BARCODE" else "Нет свободных кодов ЧЗ",
                        point=getattr(self.store, "current_point", "T-01"),
                        operator=pair.name,
                        gtin=cz_res.get("gtin") or clean_scanned_barcode(scanned_barcode)[:14],
                        sn="—",
                        result=err_msg
                    )
                except Exception as log_err:
                    logger.debug("Ошибка записи в журнал Store: %s", log_err)

            self._notify_pair_update(pair_id)
            self.code_processed.emit(pair_id, scanned_barcode, err_code)
            _notify_operator_ui(err_msg, "err", show_dialog=True)
            return

        dm_code = cz_res["code"]
        if self.store is not None:
            try:
                self.store.increment_counter("scans")
            except Exception:
                pass

        pair.last_status = f"Печать ЧЗ (GTIN {cz_res.get('gtin')}) на {pair.printer_name}..."
        self._notify_pair_update(pair_id)

        task = _AsyncPrintTask(
            pair_cfg=pair,
            datamatrix_code=dm_code,
            cz_meta=cz_res,
            on_finished_callback=self._on_print_finished_worker
        )
        self.thread_pool.start(task)

    def _resolve_datamatrix_code_full(self, pair_id: int, barcode: str) -> Dict[str, Any]:
        """Полное связывание отсканированного ШК с кодом Честного Знака из БД."""
        point = getattr(self.store, "current_point", None) if self.store is not None else None
        return resolve_and_claim_cz_code(barcode, store=self.store, point=point, claim=True)

    def _resolve_datamatrix_code(self, pair_id: int, barcode: str) -> Optional[str]:
        """Совместимая обёртка: возвращает строку кода ЧЗ или None, если ШК неизвестен."""
        res = self._resolve_datamatrix_code_full(pair_id, barcode)
        return res.get("code") if res.get("ok") else None

    def _on_print_finished_worker(self, pair_id: int, success: bool, message: str, cz_meta: Dict[str, Any]):
        """Промежуточный коллбэк из фонового потока печати -> пересылка в главный поток Qt."""
        self._print_done_internal.emit(pair_id, success, message, cz_meta or {})

    @Slot(int, bool, str, dict)
    def _on_print_finished_main_thread(self, pair_id: int, success: bool, message: str, cz_meta: Dict[str, Any]):
        """Коллбэк завершения печати в главном потоке приложения."""
        pair = self.pairs.get(pair_id)
        if not pair:
            return

        dm_code = cz_meta.get("code", "")
        gtin = cz_meta.get("gtin", "")
        sn = cz_meta.get("sn", "")
        point = cz_meta.get("point") or (getattr(self.store, "current_point", "T-01") if self.store else "T-01")

        if success:
            pair.prints_count += 1
            pair.last_status = f"Напечатан ЧЗ (GTIN {gtin}, SN {sn}) на {pair.printer_name}"
            status_tag = "УСПЕХ"

            if self.store is not None and dm_code:
                try:
                    self.store.mark_printed(dm_code, point=point)
                    self.store.increment_counter("prints")
                    self.store.log(
                        "Печать этикетки ЧЗ",
                        point=point,
                        operator=pair.name,
                        gtin=gtin,
                        sn=sn,
                        result=f"успех ({pair.printer_name})"
                    )
                except Exception as e:
                    logger.debug("Ошибка обновления статуса напечатанного кода в Store: %s", e)

            _notify_operator_ui(f"✓ Напечатан ЧЗ (GTIN {gtin}, SN {sn}) → {pair.printer_name}", "ok", show_dialog=False)
        else:
            pair.errors_count += 1
            pair.last_status = f"Ошибка принтера ({pair.printer_name}): {message}"
            status_tag = "ОШИБКА"

            if self.store is not None:
                try:
                    self.store.increment_counter("errors")
                    self.store.log(
                        "Ошибка печати ЧЗ",
                        point=point,
                        operator=pair.name,
                        gtin=gtin,
                        sn=sn,
                        result=f"ошибка ({pair.printer_name}): {message}"
                    )
                except Exception:
                    pass

            _notify_operator_ui(f"Ошибка печати ({pair.printer_name}): {message}", "err", show_dialog=True)

        self._notify_pair_update(pair_id)
        self.code_processed.emit(pair_id, pair.last_barcode, status_tag)

    def _on_scanner_status_changed(self, pair_id: int, text: str, is_alive: bool):
        pair = self.pairs.get(pair_id)
        if pair:
            pair.last_status = text
            self._notify_pair_update(pair_id)

    def _notify_pair_update(self, pair_id: int):
        pair = self.pairs.get(pair_id)
        if not pair:
            return
        data = {
            "pair_id": pair.pair_id,
            "name": pair.name,
            "scanner": pair.scanner_port,
            "printer": pair.printer_name,
            "scans": pair.scans_count,
            "prints": pair.prints_count,
            "errors": pair.errors_count,
            "status": pair.last_status,
            "last_barcode": pair.last_barcode
        }
        self.pair_updated.emit(pair_id, data)