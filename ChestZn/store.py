"""
Хранилище данных ChestZn.
Все изменяемые данные живут в SQLite (см. db.py).
Справочники (точки, шаблоны, интеграции) остаются статичными.
"""
from datetime import datetime
import random
import string
import hashlib
import os
import re
from pathlib import Path

import db


class Store:
    def __init__(self):
        db.init_db()

        self.points = [
            {"id": "T-01", "name": "Склад-Восток", "address": "Корпус А, 1 этаж",
             "ip": "10.0.1.15", "online": True, "operator": "Иванов И.И.",
             "printer": "Zebra ZD421", "template": "electronics"},
            {"id": "T-02", "name": "Склад-Запад", "address": "Корпус Б, 2 этаж",
             "ip": "10.0.2.15", "online": True, "operator": "Петрова А.С.",
             "printer": "TSC TE200", "template": "shoes"},
            {"id": "T-03", "name": "Пост-Север", "address": "КПП-3",
             "ip": "10.0.3.15", "online": False, "operator": "Сидоров В.П.",
             "printer": "Godex G500", "template": "electronics"},
        ]

        self.current_point = "T-01"
        self.current_operator = "Иванов И.И."
        self.current_user = None

        self.integrations = {
            "gis_mt": {"key": "gis_mt", "name": "ГИС МТ / Честный знак",
                       "status": "ok", "last": "08:14:23", "calls_today": 1284,
                       "endpoint": "https://ismp.crpt.ru/api/v3/",
                       "contour": "боевой (prod)"},
            "onec": {"key": "onec", "name": "1С / WMS",
                     "status": "error", "last": "08:12:01", "calls_today": 0,
                     "endpoint": "http://1c.local/hs/wms/",
                     "contour": "prod · timeout"},
        }

        self._ensure_seed_data()
        self._ensure_users_seeded()
        self._ensure_templates_seeded()

        try:
            self.migrate_audit_hmac()
        except Exception as e:
            from logger import get_logger
            get_logger("store").error("Ошибка миграции HMAC: %s", e)

        self.counter = self._load_counter()
        self.queue   = self._load_queue()
        self.audit   = self._load_audit(limit=200)
        self.gtin_map = self._load_gtin_map()
        self.users = self.list_users()

    # ==========================================================
    # Seed / загрузка
    # ==========================================================
    def _ensure_seed_data(self):
        """Наполнить пул кодов и справочник GTIN, если БД пустая."""
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM pool_codes").fetchone()["c"]
            if n == 0:
                for gtin in ["04600123456782", "04600123456775"]:
                    for i in range(30):
                        code_data = self._make_cz(gtin, i)
                        conn.execute(
                            "INSERT INTO pool_codes "
                            "(gtin, code, sn, assigned_point, import_batch) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (code_data["gtin"], code_data["code"],
                             code_data["sn"], "T-01", "DEMO-T-01")
                        )
                for i in range(30):
                    code_data = self._make_cz("04600123456768", i)
                    conn.execute(
                        "INSERT INTO pool_codes "
                        "(gtin, code, sn, assigned_point, import_batch) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (code_data["gtin"], code_data["code"],
                         code_data["sn"], "T-02", "DEMO-T-02")
                    )

            n = conn.execute("SELECT COUNT(*) AS c FROM gtin_map").fetchone()["c"]
            if n == 0:
                for fg, cz in [
                    ("04600123456782", "04600123456782"),
                    ("04600123456775", "04600123456775"),
                    ("04600123456768", "04600123456768"),
                ]:
                    conn.execute(
                        "INSERT OR IGNORE INTO gtin_map (foreign_gtin, cz_gtin) "
                        "VALUES (?, ?)",
                        (fg, cz)
                    )

    def _make_cz(self, gtin: str, idx: int) -> dict:
        sn = f"SN{idx:07d}"
        verify = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        crypto = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return {
            "gtin": gtin,
            "sn": sn,
            "code": f"01{gtin}21{sn}\x1d91{verify}\x1d92{crypto}",
        }

    def _load_counter(self) -> dict:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT scans, prints, errors, limit_value FROM counters WHERE id = 1"
            ).fetchone()
        return {
            "scans":  row["scans"],
            "prints": row["prints"],
            "errors": row["errors"],
            "limit":  row["limit_value"],
        }

    def _load_queue(self) -> list:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, time, gtin, sn, point, operator, status "
                "FROM queue WHERE status = 'pending' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def _load_audit(self, limit: int = 200) -> list:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT time, action, point, operator, gtin, sn, result "
                "FROM audit ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _load_gtin_map(self) -> dict:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT foreign_gtin, cz_gtin FROM gtin_map").fetchall()
        return {r["foreign_gtin"]: r["cz_gtin"] for r in rows}

    # ==========================================================
    # Пул кодов
    # ==========================================================
    def take_code(self, gtin: str, point: str = None):
        if point is None:
            point = self.current_point

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, gtin, code, sn FROM pool_codes "
                "WHERE gtin = ? AND assigned_point = ? AND used = 0 "
                "ORDER BY id LIMIT 1",
                (gtin, point)
            ).fetchone()

            if not row:
                return None

            conn.execute(
                "UPDATE pool_codes SET used = 1, used_point = ? WHERE id = ?",
                (point, row["id"])
            )
            return {
                "id": row["id"], "gtin": row["gtin"],
                "code": row["code"], "sn": row["sn"],
                "used": True, "point": point,
            }

    def mark_printed(self, code: str, point: str = None):
        if point is None:
            point = self.current_point
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE pool_codes SET printed = 1, "
                "printed_at = datetime('now','localtime') "
                "WHERE code = ?",
                (code,)
            )

    def pool_stats(self, point: str = None) -> list:
        with db.get_conn() as conn:
            if point:
                rows = conn.execute(
                    "SELECT gtin, "
                    "       COUNT(*) AS total, "
                    "       SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free, "
                    "       SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used, "
                    "       SUM(CASE WHEN printed=1 THEN 1 ELSE 0 END) AS printed "
                    "FROM pool_codes WHERE assigned_point = ? "
                    "GROUP BY gtin ORDER BY gtin",
                    (point,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT gtin, "
                    "       COUNT(*) AS total, "
                    "       SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free, "
                    "       SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used, "
                    "       SUM(CASE WHEN printed=1 THEN 1 ELSE 0 END) AS printed "
                    "FROM pool_codes GROUP BY gtin ORDER BY gtin"
                ).fetchall()
        return [dict(r) for r in rows]

    def count_pool(self, point: str = None) -> int:
        with db.get_conn() as conn:
            if point:
                return conn.execute(
                    "SELECT COUNT(*) AS c FROM pool_codes WHERE assigned_point = ?",
                    (point,)
                ).fetchone()["c"]
            return conn.execute("SELECT COUNT(*) AS c FROM pool_codes").fetchone()["c"]

    def pool_remaining_for_gtin(self, gtin: str, point: str = None) -> int:
        if point is None:
            point = self.current_point
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM pool_codes "
                "WHERE gtin = ? AND assigned_point = ? AND used = 0",
                (gtin, point)
            ).fetchone()["c"]

    # ==========================================================
    # Импорт кодов ЧЗ (xlsx)
    # ==========================================================
    def import_codes_from_file(self, path: str, point: str,
                               user: str = None) -> dict:
        try:
            from cz_import import parse_xlsx
        except ImportError as e:
            return {"ok": False, "info": f"Модуль cz_import не найден: {e}"}

        try:
            rows = parse_xlsx(path)
        except Exception as e:
            return {"ok": False, "info": f"Ошибка чтения файла: {e}"}

        if not rows:
            return {"ok": False, "info": "Файл пуст или не распознан"}

        order_name = rows[0].get("order") or Path(path).stem

        added = 0
        skipped = 0
        errors = []
        gtin_counts = {}

        with db.get_conn() as conn:
            for i, row in enumerate(rows, start=2):
                try:
                    gtin = str(row.get("gtin", "")).strip()
                    code = str(row.get("code", "")).strip()
                    sn = str(row.get("sn", "")).strip() or "—"

                    if not gtin or len(gtin) != 14 or not gtin.isdigit():
                        errors.append(f"Строка {i}: неверный GTIN «{gtin}»")
                        continue
                    if not code:
                        errors.append(f"Строка {i}: пустой код ЧЗ")
                        continue

                    cur = conn.execute(
                        "INSERT OR IGNORE INTO pool_codes "
                        "(gtin, code, sn, assigned_point, import_batch) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (gtin, code, sn, point, order_name)
                    )
                    if cur.rowcount > 0:
                        added += 1
                        gtin_counts[gtin] = gtin_counts.get(gtin, 0) + 1
                    else:
                        skipped += 1

                except Exception as e:
                    errors.append(f"Строка {i}: {e}")

            conn.execute(
                "INSERT INTO import_batches "
                "(name, file_name, point, total_codes, imported_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (order_name, Path(path).name, point, added, user)
            )

        summary = (
            f"Заказ: {order_name}\n"
            f"Точка: {point}\n"
            f"Добавлено: {added}, пропущено (дубли): {skipped}, "
            f"ошибок: {len(errors)}"
        )
        if gtin_counts:
            summary += "\n\nПо GTIN:\n" + "\n".join(
                f"  {g}: {c} шт." for g, c in sorted(gtin_counts.items())
            )

        from logger import get_logger
        get_logger("store").info("Импорт ЧЗ: %s", summary)

        return {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "errors": errors,
            "order": order_name,
            "info": summary,
        }

    def list_import_batches(self, limit: int = 100) -> list:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, file_name, point, total_codes, "
                "       imported_by, imported_at "
                "FROM import_batches ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ==========================================================
    # Сканы: привязка ШК → ЧЗ + счётчики по GTIN
    # ==========================================================
    def find_scanned_shk(self, foreign_code: str):
        """Найти последний скан по иностранному ШК (для информации)."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM scanned_shk WHERE foreign_code = ? "
                "ORDER BY id DESC LIMIT 1",
                (foreign_code,)
            ).fetchone()
        return dict(row) if row else None

    def count_scanned_shk(self, foreign_code: str) -> int:
        """
        Сколько раз этот иностранный ШК уже сканировали.
        Нужно для «умного варианта» — понять, повтор это или нет.
        """
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM scanned_shk WHERE foreign_code = ?",
                (foreign_code,)
            ).fetchone()["c"]

    def bind_shk_to_cz(self, foreign_code, foreign_gtin, foreign_sn,
                       cz_code, cz_gtin, cz_sn, point, operator):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO scanned_shk "
                "(foreign_code, foreign_gtin, foreign_sn, "
                " cz_code, cz_gtin, cz_sn, point, operator) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (foreign_code, foreign_gtin, foreign_sn,
                 cz_code, cz_gtin, cz_sn, point, operator)
            )

    # ---------------- Счётчик по GTIN ----------------

    def _increment_gtin_counter(self, gtin: str, point: str):
        """
        Увеличить счётчик сканов для GTIN+точка на 1.
        Создаёт запись, если её ещё нет.
        """
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO gtin_counters (gtin, point, scans, last_scan_at) "
                "VALUES (?, ?, 1, datetime('now','localtime')) "
                "ON CONFLICT(gtin, point) DO UPDATE SET "
                "  scans = scans + 1, "
                "  last_scan_at = datetime('now','localtime')",
                (gtin, point)
            )

    def _get_gtin_scans(self, gtin: str, point: str) -> int:
        """Сколько раз пикнули этот GTIN на точке."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT scans FROM gtin_counters WHERE gtin = ? AND point = ?",
                (gtin, point)
            ).fetchone()
        return row["scans"] if row else 0

    def _get_gtin_total(self, gtin: str, point: str) -> int:
        """Сколько ВСЕГО кодов в пуле для GTIN+точка (и использованных, и свободных)."""
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM pool_codes "
                "WHERE gtin = ? AND assigned_point = ?",
                (gtin, point)
            ).fetchone()["c"]

    # ---------------- Основной метод скана ----------------

    def process_scan(self, foreign_code: str, point: str = None,
                     operator: str = None) -> dict:
        """
        Обработать скан иностранного ШК.

        Логика:
        1. Считаем, сколько раз этот ШК уже был (не блокируем).
        2. Ищем GTIN ЧЗ (через gtin_map или как есть).
        3. Берём свободный код из пула СВОЕЙ точки.
        4. Инкрементируем счётчик сканов по GTIN.
        5. Привязываем.
        6. Если scans > total — пишем «Перебор GTIN» в журнал.
        """
        if point is None:
            point = self.current_point
        if operator is None:
            operator = self.current_operator

        foreign = parse_foreign_barcode(foreign_code)

        # 1. Сколько раз этот ШК уже сканировали? (не блокирует)
        prev_count = self.count_scanned_shk(foreign_code)

        # 2. GTIN ЧЗ
        gtin_cz = self.gtin_map.get(foreign["gtin"], foreign["gtin"])

        # 3. Берём свободный код
        cz = self.take_code(gtin_cz, point=point)
        if not cz:
            from logger import get_logger
            get_logger("store").warning(
                "Нет свободных кодов для GTIN=%s на точке %s",
                gtin_cz, point
            )
            return {
                "status": "error",
                "code": "NO_FREE_CODES",
                "message": "Нет свободных кодов для этого товара",
                "foreign": foreign,
                "gtin": gtin_cz,
            }

        # 4. Счётчик по GTIN
        self._increment_gtin_counter(gtin_cz, point)

        # 5. Привязываем
        self.bind_shk_to_cz(
            foreign_code=foreign_code,
            foreign_gtin=foreign["gtin"],
            foreign_sn=foreign.get("serial"),
            cz_code=cz["code"],
            cz_gtin=cz["gtin"],
            cz_sn=cz["sn"],
            point=point,
            operator=operator,
        )

        # 6. Проверка перебора
        scans = self._get_gtin_scans(gtin_cz, point)
        total = self._get_gtin_total(gtin_cz, point)
        overflow = scans > total

        if overflow:
            from logger import get_logger
            get_logger("store").warning(
                "Перебор GTIN %s на точке %s: сканов %s > кодов %s",
                gtin_cz, point, scans, total
            )
            try:
                self.log(
                    "Перебор GTIN",
                    point=point,
                    operator=operator,
                    gtin=gtin_cz,
                    result=f"сканов {scans} > кодов {total}",
                )
            except Exception as e:
                from logger import get_logger
                get_logger("store").error("Не удалось записать перебор: %s", e)

        return {
            "status": "ok",
            "foreign": foreign,
            "cz": cz,
            "prev_count": prev_count,   # сколько раз ШК был до этого
            "gtin_scans": scans,        # сколько всего раз пикнули GTIN
            "gtin_total": total,        # сколько всего кодов у GTIN
            "overflow": overflow,       # True, если сканов > кодов
        }

    def count_unprinted_for_point(self, point: str) -> int:
        """
        Сколько кодов взято, но не напечатано на точке.
        Считаем коды, которые были использованы (used=1),
        но печать не подтверждена (printed=0).
        """
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM pool_codes "
                "WHERE used_point = ? AND used = 1 AND printed = 0",
                (point,)
            ).fetchone()["c"]


    def list_unprinted_for_point(self, point: str, limit: int = 100) -> list:
        """Список кодов, взятых, но не напечатанных."""
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, gtin, code, sn, used_point, printed_at, created_at "
                "FROM pool_codes "
                "WHERE used_point = ? AND used = 1 AND printed = 0 "
                "ORDER BY id DESC LIMIT ?",
                (point, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_scanned_shk(self, point: str = None, limit: int = 500) -> list:
        with db.get_conn() as conn:
            if point:
                rows = conn.execute(
                    "SELECT * FROM scanned_shk WHERE point = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (point, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scanned_shk ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    # ==========================================================
    # Расхождения (для админки)
    # ==========================================================
    def get_discrepancies(self, point: str = None) -> list:
        """
        Сводка: пул vs счётчик сканов.
        Возвращает список строк:
          {gtin, point, total, scans, used, free, overflow}
        где overflow = scans > total.
        """
        with db.get_conn() as conn:
            if point:
                pool_rows = conn.execute(
                    "SELECT gtin, assigned_point AS point, "
                    "       COUNT(*) AS total, "
                    "       SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free, "
                    "       SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used "
                    "FROM pool_codes WHERE assigned_point = ? "
                    "GROUP BY gtin, assigned_point ORDER BY gtin",
                    (point,)
                ).fetchall()

                counter_rows = conn.execute(
                    "SELECT gtin, point, scans FROM gtin_counters "
                    "WHERE point = ?",
                    (point,)
                ).fetchall()
            else:
                pool_rows = conn.execute(
                    "SELECT gtin, assigned_point AS point, "
                    "       COUNT(*) AS total, "
                    "       SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free, "
                    "       SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used "
                    "FROM pool_codes "
                    "GROUP BY gtin, assigned_point ORDER BY assigned_point, gtin"
                ).fetchall()

                counter_rows = conn.execute(
                    "SELECT gtin, point, scans FROM gtin_counters"
                ).fetchall()

        counters = {(r["gtin"], r["point"]): r["scans"] for r in counter_rows}

        result = []
        for p in pool_rows:
            gtin = p["gtin"]
            pt = p["point"] or "—"
            scans = counters.get((gtin, pt), 0)
            total = p["total"]
            result.append({
                "gtin": gtin,
                "point": pt,
                "total": total,
                "scans": scans,
                "used": p["used"],
                "free": p["free"],
                "overflow": scans > total,
            })
        return result

    # ==========================================================
    # Шаблоны этикеток
    # ==========================================================
    _DEFAULT_TEMPLATES = [
        {
            "code": "electronics",
            "name": "Электроника",
            "is_default": 1,
            "width_mm": 58, "height_mm": 40, "dpi": 203,
            "fields": [
                {"name": "dataMatrix", "label": "", "required": True, "order": 1, "font_size": 0},
                {"name": "name_ru", "label": "Наименование", "required": True, "order": 2, "font_size": 13},
                {"name": "model", "label": "Модель", "required": False, "order": 3, "font_size": 11},
                {"name": "importer", "label": "Импортёр", "required": True, "order": 4, "font_size": 10},
                {"name": "exp_date", "label": "Срок годности", "required": False, "order": 5, "font_size": 10},
                {"name": "qty", "label": "Кол-во", "required": False, "order": 6, "font_size": 10},
            ],
        },
        {
            "code": "shoes",
            "name": "Обувь (ТР ТС 017)",
            "is_default": 0,
            "width_mm": 58, "height_mm": 40, "dpi": 203,
            "fields": [
                {"name": "dataMatrix", "label": "", "required": True, "order": 1, "font_size": 0},
                {"name": "name_ru", "label": "Наименование", "required": True, "order": 2, "font_size": 13},
                {"name": "material", "label": "Материал", "required": True, "order": 3, "font_size": 11},
                {"name": "size_eu", "label": "Размер EU", "required": True, "order": 4, "font_size": 11},
                {"name": "importer", "label": "Импортёр", "required": True, "order": 5, "font_size": 10},
            ],
        },
        {
            "code": "clothing",
            "name": "Одежда (ТР ТС 017)",
            "is_default": 0,
            "width_mm": 58, "height_mm": 40, "dpi": 203,
            "fields": [
                {"name": "dataMatrix", "label": "", "required": True, "order": 1, "font_size": 0},
                {"name": "name_ru", "label": "Наименование", "required": True, "order": 2, "font_size": 13},
                {"name": "composition", "label": "Состав", "required": True, "order": 3, "font_size": 11},
                {"name": "importer", "label": "Импортёр", "required": True, "order": 4, "font_size": 10},
            ],
        },
    ]

    def _ensure_templates_seeded(self):
        import json
        with db.get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM label_templates"
            ).fetchone()["c"]
            if n > 0:
                return

            for i, tpl in enumerate(self._DEFAULT_TEMPLATES):
                conn.execute(
                    "INSERT INTO label_templates "
                    "(code, name, is_default, width_mm, height_mm, dpi, "
                    " fields_json, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (tpl["code"], tpl["name"], tpl["is_default"],
                     tpl["width_mm"], tpl["height_mm"], tpl["dpi"],
                     json.dumps(tpl["fields"], ensure_ascii=False), i * 10)
                )

    def list_templates(self) -> list:
        import json
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, code, name, is_default, version, "
                "       width_mm, height_mm, dpi, fields_json, sort_order "
                "FROM label_templates ORDER BY sort_order, id"
            ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            try:
                d["fields"] = json.loads(d["fields_json"])
            except Exception:
                d["fields"] = []
            d.pop("fields_json", None)
            result.append(d)
        return result

    def get_template(self, code: str):
        import json
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT id, code, name, is_default, version, "
                "       width_mm, height_mm, dpi, fields_json, sort_order "
                "FROM label_templates WHERE code = ?",
                (code,)
            ).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["fields"] = json.loads(d["fields_json"])
        except Exception:
            d["fields"] = []
        d.pop("fields_json", None)
        return d

    def get_default_template(self):
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT code FROM label_templates "
                "WHERE is_default = 1 LIMIT 1"
            ).fetchone()
            if not r:
                r = conn.execute(
                    "SELECT code FROM label_templates "
                    "ORDER BY sort_order, id LIMIT 1"
                ).fetchone()
        return self.get_template(r["code"]) if r else None

    def create_template(self, code, name, fields=None,
                        width_mm=58, height_mm=40, dpi=203,
                        is_default=False) -> dict:
        import json
        fields = fields or []
        try:
            with db.get_conn() as conn:
                if is_default:
                    conn.execute("UPDATE label_templates SET is_default = 0")
                conn.execute(
                    "INSERT INTO label_templates "
                    "(code, name, is_default, width_mm, height_mm, dpi, "
                    " fields_json, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (code, name, 1 if is_default else 0,
                     width_mm, height_mm, dpi,
                     json.dumps(fields, ensure_ascii=False), 100)
                )
            return {"ok": True, "info": f"Шаблон «{name}» создан"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def update_template(self, code, name=None, fields=None,
                        width_mm=None, height_mm=None, dpi=None,
                        is_default=None) -> dict:
        import json
        tpl = self.get_template(code)
        if not tpl:
            return {"ok": False, "info": "Шаблон не найден"}

        new_name = name if name is not None else tpl["name"]
        new_fields = fields if fields is not None else tpl["fields"]
        new_w = width_mm if width_mm is not None else tpl["width_mm"]
        new_h = height_mm if height_mm is not None else tpl["height_mm"]
        new_dpi = dpi if dpi is not None else tpl["dpi"]
        new_default = tpl["is_default"] if is_default is None else (1 if is_default else 0)

        try:
            with db.get_conn() as conn:
                if new_default:
                    conn.execute(
                        "UPDATE label_templates SET is_default = 0 "
                        "WHERE code != ?", (code,)
                    )
                conn.execute(
                    "UPDATE label_templates SET "
                    "  name = ?, fields_json = ?, width_mm = ?, "
                    "  height_mm = ?, dpi = ?, is_default = ?, "
                    "  updated_at = datetime('now','localtime') "
                    "WHERE code = ?",
                    (new_name, json.dumps(new_fields, ensure_ascii=False),
                     new_w, new_h, new_dpi, new_default, code)
                )
            return {"ok": True, "info": f"Шаблон «{new_name}» сохранён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def delete_template(self, code: str) -> dict:
        tpl = self.get_template(code)
        if not tpl:
            return {"ok": False, "info": "Шаблон не найден"}
        if tpl["is_default"]:
            return {"ok": False, "info": "Нельзя удалить шаблон по умолчанию"}

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "DELETE FROM template_bindings WHERE template_code = ?",
                    (code,)
                )
                conn.execute("DELETE FROM label_templates WHERE code = ?", (code,))
            return {"ok": True, "info": f"Шаблон «{tpl['name']}» удалён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def list_bindings(self) -> list:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, template_code, gtin_prefix, priority, created_at "
                "FROM template_bindings ORDER BY priority, gtin_prefix"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_binding(self, template_code, gtin_prefix, priority=100) -> dict:
        gtin_prefix = (gtin_prefix or "").strip()
        if not gtin_prefix:
            return {"ok": False, "info": "Пустой GTIN-префикс"}
        if not gtin_prefix.isdigit():
            return {"ok": False, "info": "GTIN-префикс должен содержать только цифры"}
        if len(gtin_prefix) < 4:
            return {"ok": False, "info": "GTIN-префикс слишком короткий"}

        if not self.get_template(template_code):
            return {"ok": False, "info": "Шаблон не найден"}

        try:
            with db.get_conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM template_bindings WHERE gtin_prefix = ?",
                    (gtin_prefix,)
                ).fetchone()

                if existing:
                    conn.execute(
                        "UPDATE template_bindings "
                        "SET template_code = ?, priority = ? WHERE id = ?",
                        (template_code, priority, existing["id"])
                    )
                    return {"ok": True, "info": f"Привязка для {gtin_prefix} обновлена"}

                conn.execute(
                    "INSERT INTO template_bindings "
                    "(template_code, gtin_prefix, priority) VALUES (?, ?, ?)",
                    (template_code, gtin_prefix, priority)
                )
            return {"ok": True, "info": f"Привязка для {gtin_prefix} создана"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def delete_binding(self, binding_id: int) -> dict:
        try:
            with db.get_conn() as conn:
                conn.execute(
                    "DELETE FROM template_bindings WHERE id = ?",
                    (binding_id,)
                )
            return {"ok": True, "info": "Привязка удалена"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def get_template_for_gtin(self, gtin: str):
        gtin = (gtin or "").strip()
        if gtin:
            with db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT template_code, gtin_prefix FROM template_bindings "
                    "ORDER BY priority, LENGTH(gtin_prefix) DESC"
                ).fetchall()

            best = None
            best_len = 0
            for r in rows:
                p = r["gtin_prefix"]
                if gtin.startswith(p) and len(p) > best_len:
                    best = r["template_code"]
                    best_len = len(p)

            if best:
                tpl = self.get_template(best)
                if tpl:
                    return tpl

        return self.get_default_template()

    # ==========================================================
    # Счётчики
    # ==========================================================
    _COUNTER_FIELDS = {"scans": "scans", "prints": "prints", "errors": "errors"}

    def increment_counter(self, key: str, delta: int = 1):
        if key not in self._COUNTER_FIELDS:
            return
        col = self._COUNTER_FIELDS[key]
        with db.get_conn() as conn:
            conn.execute(
                f"UPDATE counters SET {col} = {col} + ? WHERE id = 1",
                (delta,)
            )
        self.counter[key] = self.counter.get(key, 0) + delta

    def reset_counters(self):
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE counters SET scans=0, prints=0, errors=0 WHERE id = 1"
            )
        self.counter["scans"] = 0
        self.counter["prints"] = 0
        self.counter["errors"] = 0

    # ==========================================================
    # Очередь
    # ==========================================================
    def add_queue_entry(self, gtin, sn, point=None, operator=None):
        if point is None:
            point = self.current_point
        if operator is None:
            operator = self.current_operator
        now = datetime.now().strftime("%H:%M:%S")
        with db.get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO queue (time, gtin, sn, point, operator, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (now, gtin, sn, point, operator)
            )
            entry_id = cur.lastrowid
        self.queue.append({
            "id": entry_id, "time": now, "gtin": gtin, "sn": sn,
            "point": point, "operator": operator, "status": "pending",
        })

    def clear_queue(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM queue WHERE status = 'pending'")
        self.queue.clear()

    # ==========================================================
    # Журнал + HMAC
    # ==========================================================
    def _compute_audit_hmac(self, prev_hmac: str, entry: dict) -> str:
        import hmac as hmac_mod
        import hashlib
        from crypto import get_hmac_key

        key = get_hmac_key()
        parts = [
            prev_hmac or "",
            entry.get("time") or "",
            entry.get("action") or "",
            entry.get("point") or "",
            entry.get("operator") or "",
            entry.get("gtin") or "",
            entry.get("sn") or "",
            entry.get("result") or "",
        ]
        data = "|".join(parts).encode("utf-8")
        return hmac_mod.new(key, data, hashlib.sha256).hexdigest()

    def log(self, action: str, **kwargs):
        now = datetime.now().strftime("%H:%M:%S")
        entry = {
            "time": now,
            "action": action,
            "point": kwargs.get("point"),
            "operator": kwargs.get("operator"),
            "gtin": kwargs.get("gtin"),
            "sn": kwargs.get("sn"),
            "result": kwargs.get("result"),
        }

        with db.get_conn() as conn:
            last = conn.execute(
                "SELECT hmac FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hmac = (last["hmac"] if last and last["hmac"] else "") or ""

            cur_hmac = self._compute_audit_hmac(prev_hmac, entry)

            conn.execute(
                "INSERT INTO audit "
                "(time, action, point, operator, gtin, sn, result, hmac, prev_hmac) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry["time"], entry["action"], entry["point"],
                 entry["operator"], entry["gtin"], entry["sn"], entry["result"],
                 cur_hmac, prev_hmac)
            )

        entry["hmac"] = cur_hmac
        entry["prev_hmac"] = prev_hmac
        self.audit.insert(0, entry)
        if len(self.audit) > 200:
            self.audit = self.audit[:200]

    def query_audit(self, filters: dict = None, limit: int = 100, offset: int = 0):
        filters = filters or {}
        where = []
        params = []

        df = filters.get("date_from")
        dt = filters.get("date_to")
        if df:
            where.append("created_at >= ?")
            params.append(df + " 00:00:00")
        if dt:
            where.append("created_at <= ?")
            params.append(dt + " 23:59:59")

        if filters.get("point"):
            where.append("point = ?")
            params.append(filters["point"])
        if filters.get("operator"):
            where.append("operator = ?")
            params.append(filters["operator"])
        if filters.get("result"):
            where.append("result = ?")
            params.append(filters["result"])
        if filters.get("search"):
            s = f"%{filters['search']}%"
            where.append("(gtin LIKE ? OR sn LIKE ?)")
            params.extend([s, s])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with db.get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM audit {where_sql}", params
            ).fetchone()["c"]
            rows = conn.execute(
                f"SELECT time, action, point, operator, gtin, sn, result, created_at "
                f"FROM audit {where_sql} "
                f"ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()

        return [dict(r) for r in rows], total

    def get_audit_filter_values(self) -> dict:
        with db.get_conn() as conn:
            points = [r["point"] for r in conn.execute(
                "SELECT DISTINCT point FROM audit "
                "WHERE point IS NOT NULL AND point != '' ORDER BY point"
            ).fetchall()]
            operators = [r["operator"] for r in conn.execute(
                "SELECT DISTINCT operator FROM audit "
                "WHERE operator IS NOT NULL AND operator != '' ORDER BY operator"
            ).fetchall()]
            results = [r["result"] for r in conn.execute(
                "SELECT DISTINCT result FROM audit "
                "WHERE result IS NOT NULL AND result != '' ORDER BY result"
            ).fetchall()]
        return {"points": points, "operators": operators, "results": results}

    def migrate_audit_hmac(self):
        with db.get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM audit"
            ).fetchone()["c"]
            if total == 0:
                return

            signed = conn.execute(
                "SELECT COUNT(*) AS c FROM audit "
                "WHERE hmac IS NOT NULL AND hmac != ''"
            ).fetchone()["c"]
            if signed == total:
                return

            from logger import get_logger
            get_logger("store").info(
                "Начата подпись журнала: %s/%s уже подписано", signed, total
            )

            rows = conn.execute(
                "SELECT id, time, action, point, operator, gtin, sn, result "
                "FROM audit ORDER BY id ASC"
            ).fetchall()

            prev_hmac = ""
            updated = 0
            for r in rows:
                entry = {
                    "time": r["time"], "action": r["action"],
                    "point": r["point"], "operator": r["operator"],
                    "gtin": r["gtin"], "sn": r["sn"], "result": r["result"],
                }
                new_hmac = self._compute_audit_hmac(prev_hmac, entry)
                conn.execute(
                    "UPDATE audit SET hmac = ?, prev_hmac = ? WHERE id = ?",
                    (new_hmac, prev_hmac, r["id"])
                )
                prev_hmac = new_hmac
                updated += 1

            from logger import get_logger
            get_logger("store").info(
                "Миграция audit HMAC: подписано %s записей", updated
            )

    def verify_audit_chain(self) -> dict:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, time, action, point, operator, gtin, sn, result, "
                "       hmac, prev_hmac "
                "FROM audit ORDER BY id ASC"
            ).fetchall()

        if not rows:
            return {"ok": True, "total": 0, "broken": [], "info": "Журнал пуст"}

        broken = []
        expected_prev = ""

        for r in rows:
            actual_prev = (r["prev_hmac"] or "")
            actual_hmac = (r["hmac"] or "")

            if actual_prev != expected_prev:
                broken.append({
                    "id": r["id"], "time": r["time"], "action": r["action"],
                    "reason": "Разрыв связи с предыдущей записью",
                })

            entry = {
                "time": r["time"], "action": r["action"],
                "point": r["point"], "operator": r["operator"],
                "gtin": r["gtin"], "sn": r["sn"], "result": r["result"],
            }
            computed = self._compute_audit_hmac(actual_prev, entry)
            if computed != actual_hmac:
                reason = ("HMAC отсутствует"
                          if not actual_hmac
                          else "Данные записи изменены (HMAC не совпадает)")
                broken.append({
                    "id": r["id"], "time": r["time"], "action": r["action"],
                    "reason": reason,
                })

            expected_prev = actual_hmac

        total = len(rows)
        ok = len(broken) == 0

        if ok:
            return {"ok": True, "total": total, "broken": [],
                    "info": f"✅ Журнал цел. Записей: {total}"}
        return {"ok": False, "total": total, "broken": broken,
                "info": f"⚠ Обнаружено {len(broken)} повреждений из {total}"}

    # ==========================================================
    # Пользователи
    # ==========================================================
    @staticmethod
    def _hash_password(password: str, salt: bytes = None):
        if salt is None:
            salt = os.urandom(16)
        h = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, 200_000
        )
        return h.hex(), salt.hex()

    def _ensure_users_seeded(self):
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            if n > 0:
                return

            default_users = [
                ("admin",      "Администратор системы",     "Администратор",  "Все",   "admin123"),
                ("i.ivanov",   "Иванов Иван Иванович",      "Оператор",       "T-01",  "1234"),
                ("a.petrova",  "Петрова Анна Сергеевна",    "Оператор",       "T-02",  "1234"),
                ("v.sidorov",  "Сидоров Виктор Петрович",   "Оператор",       "T-03",  "1234"),
                ("d.kuznetsov","Кузнецов Дмитрий Олегович", "Старший смены",  "Все",   "1234"),
                ("e.morozova", "Морозова Елена Владимировна","Администратор","Все",   "1234"),
            ]

            for login, fio, role, point, pwd in default_users:
                h, s = self._hash_password(pwd)
                conn.execute(
                    "INSERT OR IGNORE INTO users "
                    "(login, fio, role, point, password_hash, password_salt) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (login, fio, role, point, h, s)
                )

    def check_login(self, login: str, password: str):
        if not login or not password:
            return None

        login = login.strip()
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, login, fio, role, point, active, "
                "       password_hash, password_salt "
                "FROM users WHERE login = ?",
                (login,)
            ).fetchone()

        if not row:
            return None
        if not row["active"]:
            return None

        salt = bytes.fromhex(row["password_salt"])
        h, _ = self._hash_password(password, salt)

        if h != row["password_hash"]:
            return None

        with db.get_conn() as conn:
            conn.execute(
                "UPDATE users SET last_login = datetime('now','localtime') "
                "WHERE login = ?",
                (login,)
            )

        return {
            "id":     row["id"],
            "login":  row["login"],
            "fio":    row["fio"],
            "role":   row["role"],
            "point":  row["point"],
            "active": bool(row["active"]),
        }

    def has_security_question(self, login: str) -> bool:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM security_questions WHERE login = ?",
                (login,)
            ).fetchone()
        return row is not None

    def get_security_question(self, login: str):
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT question FROM security_questions WHERE login = ?",
                (login,)
            ).fetchone()
        return row["question"] if row else None

    def set_security_question(self, login: str, question: str, answer: str) -> bool:
        if not login or not question or not answer:
            return False

        answer_norm = answer.strip().lower()
        h, s = self._hash_password(answer_norm)

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO security_questions "
                    "(login, question, answer_hash, answer_salt, updated_at) "
                    "VALUES (?, ?, ?, ?, datetime('now','localtime')) "
                    "ON CONFLICT(login) DO UPDATE SET "
                    "  question    = excluded.question, "
                    "  answer_hash = excluded.answer_hash, "
                    "  answer_salt = excluded.answer_salt, "
                    "  updated_at  = excluded.updated_at",
                    (login, question, h, s)
                )
            return True
        except Exception as e:
            from logger import get_logger
            get_logger("store").error(
                "Ошибка сохранения секретного вопроса: %s", e
            )
            return False

    def verify_security_answer(self, login: str, answer: str) -> bool:
        if not login or not answer:
            return False

        answer_norm = answer.strip().lower()

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT answer_hash, answer_salt FROM security_questions "
                "WHERE login = ?",
                (login,)
            ).fetchone()

        if not row:
            return False

        salt = bytes.fromhex(row["answer_salt"])
        h, _ = self._hash_password(answer_norm, salt)
        return h == row["answer_hash"]

    def change_password(self, login, old_password, new_password):
        if not login or not old_password or not new_password:
            return False, "Заполните все поля"
        if len(new_password) < 6:
            return False, "Новый пароль должен быть не короче 6 символов"

        user = self.check_login(login, old_password)
        if not user:
            return False, "Неверный текущий пароль"

        h, s = self._hash_password(new_password)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, password_salt = ? "
                "WHERE login = ?",
                (h, s, login)
            )
        return True, "Пароль изменён"

    def list_users(self) -> list:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, login, fio, role, point, active, last_login "
                "FROM users ORDER BY role, fio"
            ).fetchall()
        return [dict(r) for r in rows]

    # ==========================================================
    # Массовый импорт CSV
    # ==========================================================
    def bulk_import_pool_codes(self, rows: list, point: str = None,
                               batch: str = None) -> dict:
        if point is None:
            point = self.current_point

        added = 0
        skipped = 0
        errors = []
        gtin_counts = {}

        with db.get_conn() as conn:
            for i, row in enumerate(rows, start=2):
                try:
                    gtin = str(row.get("gtin", "")).strip()
                    code = str(row.get("code", "")).strip()
                    sn = str(row.get("sn", "")).strip()

                    if not gtin:
                        errors.append(f"Строка {i}: пустой GTIN")
                        continue
                    if len(gtin) != 14 or not gtin.isdigit():
                        errors.append(f"Строка {i}: GTIN должен быть 14 цифр — «{gtin}»")
                        continue
                    if not code:
                        errors.append(f"Строка {i}: пустой код ЧЗ")
                        continue

                    if not sn:
                        m = re.search(r"21([^\x1d]+)", code)
                        if m:
                            sn = m.group(1)
                        else:
                            sn = "—"

                    cur = conn.execute(
                        "INSERT OR IGNORE INTO pool_codes "
                        "(gtin, code, sn, assigned_point, import_batch) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (gtin, code, sn, point, batch)
                    )
                    if cur.rowcount > 0:
                        added += 1
                        gtin_counts[gtin] = gtin_counts.get(gtin, 0) + 1
                    else:
                        skipped += 1

                except Exception as e:
                    errors.append(f"Строка {i}: {e}")

        summary = (
            f"Добавлено: {added}, пропущено (дубли): {skipped}, "
            f"ошибок: {len(errors)}"
        )
        if gtin_counts:
            summary += "\n\nПо GTIN:\n" + "\n".join(
                f"  {g}: {c} шт." for g, c in sorted(gtin_counts.items())
            )

        from logger import get_logger
        get_logger("store").info("Импорт пула ЧЗ: %s", summary)

        return {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "errors": errors,
            "info": summary,
        }

    def bulk_import_gtin_map(self, rows: list) -> dict:
        added = 0
        updated = 0
        errors = []

        with db.get_conn() as conn:
            for i, row in enumerate(rows, start=2):
                try:
                    fg = str(row.get("foreign_gtin", "")).strip()
                    cz = str(row.get("cz_gtin", "")).strip()

                    if not fg or not cz:
                        errors.append(f"Строка {i}: пустой GTIN")
                        continue
                    if len(fg) != 14 or not fg.isdigit():
                        errors.append(f"Строка {i}: иностранный GTIN должен быть 14 цифр")
                        continue
                    if len(cz) != 14 or not cz.isdigit():
                        errors.append(f"Строка {i}: GTIN ЧЗ должен быть 14 цифр")
                        continue

                    existing = conn.execute(
                        "SELECT cz_gtin FROM gtin_map WHERE foreign_gtin = ?",
                        (fg,)
                    ).fetchone()

                    if existing is None:
                        conn.execute(
                            "INSERT INTO gtin_map (foreign_gtin, cz_gtin) "
                            "VALUES (?, ?)",
                            (fg, cz)
                        )
                        added += 1
                    elif existing["cz_gtin"] != cz:
                        conn.execute(
                            "UPDATE gtin_map SET cz_gtin = ? WHERE foreign_gtin = ?",
                            (cz, fg)
                        )
                        updated += 1

                except Exception as e:
                    errors.append(f"Строка {i}: {e}")

        self.gtin_map = self._load_gtin_map()

        summary = (
            f"Добавлено: {added}, обновлено: {updated}, "
            f"ошибок: {len(errors)}"
        )

        from logger import get_logger
        get_logger("store").info("Импорт маппинга GTIN: %s", summary)

        return {
            "ok": True,
            "added": added,
            "updated": updated,
            "errors": errors,
            "info": summary,
        }

    def count_gtin_map(self) -> int:
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM gtin_map").fetchone()["c"]

    # ==========================================================
    # Настройки (таблица meta)
    # ==========================================================
    def get_setting(self, key: str, default=None):
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value))
            )

    # ==========================================================
    # Очистка старых данных
    # ==========================================================
    def get_audit_retention_days(self) -> int:
        v = self.get_setting("audit_retention_days", "365")
        try:
            return int(v)
        except (ValueError, TypeError):
            return 365

    def set_audit_retention_days(self, days: int):
        days = max(7, int(days))
        self.set_setting("audit_retention_days", str(days))

    def cleanup_old_audit(self) -> dict:
        days = self.get_audit_retention_days()

        try:
            with db.get_conn() as conn:
                threshold_mod = f"-{days} days"
                count_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM audit "
                    "WHERE created_at < datetime('now', 'localtime', ?)",
                    (threshold_mod,)
                ).fetchone()
                to_delete = count_row["c"]

                if to_delete == 0:
                    return {"ok": True, "deleted": 0,
                            "info": f"Нет записей старше {days} дней"}

                conn.execute(
                    "DELETE FROM audit "
                    "WHERE created_at < datetime('now', 'localtime', ?)",
                    (threshold_mod,)
                )

            self.audit = self._load_audit(limit=200)

            from logger import get_logger
            get_logger("store").info(
                "Очистка audit: удалено %s записей старше %s дней",
                to_delete, days
            )

            return {"ok": True, "deleted": to_delete,
                    "info": f"Удалено {to_delete} записей старше {days} дней"}
        except Exception as e:
            from logger import get_logger
            get_logger("store").error("Ошибка очистки audit: %s", e)
            return {"ok": False, "deleted": 0, "info": str(e)}

    def auto_cleanup_if_needed(self):
        from datetime import datetime
        last = self.get_setting("last_cleanup")

        need = False
        if not last:
            need = True
        else:
            try:
                last_dt = datetime.fromisoformat(last)
                hours = (datetime.now() - last_dt).total_seconds() / 3600
                if hours >= 24:
                    need = True
            except Exception:
                need = True

        if not need:
            return None

        result = self.cleanup_old_audit()
        self.set_setting("last_cleanup", datetime.now().isoformat(timespec="seconds"))
        return result


# ==========================================================
# Парсер иностранного ШК (вне класса!)
# ==========================================================
def parse_foreign_barcode(raw: str) -> dict:
    raw = (raw or "").strip()

    gtin = "04600123456782"
    sn = "SN" + ''.join(random.choices(string.digits, k=7))
    lot = "LOT-2025-04"
    exp = "12.2026"

    if raw:
        s = raw.replace("_x001D_", "\x1d")

        m01 = re.search(r"01(\d{14})", s)
        if m01:
            gtin = m01.group(1)

        m21 = re.search(r"21([^\x1d]+)", s)
        if m21:
            sn = m21.group(1).strip()

        m10 = re.search(r"10([^\x1d]+)", s)
        if m10:
            lot = m10.group(1).strip()

        m17 = re.search(r"17(\d{6})", s)
        if m17:
            d = m17.group(1)
            yy, mm = d[:2], d[2:4]
            exp = f"{mm}.20{yy}"

        if not m01:
            digits = ''.join(ch for ch in raw if ch.isdigit())
            if len(digits) >= 14:
                gtin = digits[:14]

    return {
        "type": "GS1-128" if "01" in raw else "EAN-13",
        "gtin": gtin,
        "serial": sn,
        "lot": lot,
        "exp": exp,
        "country": "CN",
        "name": "Наушники беспроводные BT-500",
        "model": "BT-500",
        "importer": "ООО «Ромашка», г. Москва",
    }