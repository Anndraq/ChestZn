"""
Хранилище данных ChestZn.
Все изменяемые данные живут в SQLite (см. db.py).
Справочники (точки, пользователи, шаблоны, интеграции) остаются статичными.
"""
from datetime import datetime
import random
import string

import db


class Store:
    def __init__(self):
        db.init_db()

        # ---------- Статичные справочники ----------
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

        self.users = [
            {"id": 1, "fio": "Иванов Иван Иванович", "login": "i.ivanov",
             "role": "Оператор", "point": "T-01", "last_login": "сегодня 08:00", "active": True},
            {"id": 2, "fio": "Петрова Анна Сергеевна", "login": "a.petrova",
             "role": "Оператор", "point": "T-02", "last_login": "сегодня 08:00", "active": True},
            {"id": 3, "fio": "Сидоров Виктор Петрович", "login": "v.sidorov",
             "role": "Оператор", "point": "T-03", "last_login": "сегодня 08:00", "active": True},
            {"id": 4, "fio": "Кузнецов Дмитрий Олегович", "login": "d.kuznetsov",
             "role": "Старший смены", "point": "Все", "last_login": "сегодня 07:45", "active": True},
            {"id": 5, "fio": "Морозова Елена Владимировна", "login": "e.morozova",
             "role": "Администратор", "point": "Все", "last_login": "вчера 18:12", "active": True},
        ]

        self.templates = [
            {"id": "electronics", "name": "Электроника", "default": True,
             "version": "1.4", "size": "58×40 мм",
             "fields": [
                 {"name": "dataMatrix", "required": True},
                 {"name": "name_ru", "required": True},
                 {"name": "model", "required": False},
                 {"name": "importer", "required": True},
                 {"name": "exp_date", "required": False},
                 {"name": "qty", "required": False},
             ]},
            {"id": "shoes", "name": "Обувь (ТР ТС 017)", "default": False,
             "version": "1.2", "size": "58×40 мм",
             "fields": [
                 {"name": "dataMatrix", "required": True},
                 {"name": "name_ru", "required": True},
                 {"name": "material", "required": True},
                 {"name": "size_eu", "required": True},
                 {"name": "importer", "required": True},
             ]},
            {"id": "clothing", "name": "Одежда (ТР ТС 017)", "default": False,
             "version": "1.1", "size": "58×40 мм",
             "fields": [
                 {"name": "dataMatrix", "required": True},
                 {"name": "name_ru", "required": True},
                 {"name": "composition", "required": True},
                 {"name": "importer", "required": True},
             ]},
        ]

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

        # ---------- Инициализация БД ----------
        self._ensure_seed_data()

        # ---------- Кэши в памяти ----------
        self.counter = self._load_counter()
        self.queue   = self._load_queue()
        self.audit   = self._load_audit(limit=200)

        # ---------- Маппинг GTIN (кэш из БД) ----------
        self.gtin_map = self._load_gtin_map()

    # ==========================================================
    # Seed / загрузка
    # ==========================================================
    def _ensure_seed_data(self):
        """Наполнить пул кодов и справочник GTIN, если БД пустая."""
        with db.get_conn() as conn:
            # --- пул ---
            n = conn.execute("SELECT COUNT(*) AS c FROM pool_codes").fetchone()["c"]
            if n == 0:
                for gtin in ["04600123456782", "04600123456775", "04600123456768"]:
                    for i in range(30):
                        code_data = self._make_cz(gtin, i)
                        conn.execute(
                            "INSERT INTO pool_codes (gtin, code, sn) VALUES (?, ?, ?)",
                            (code_data["gtin"], code_data["code"], code_data["sn"])
                        )

            # --- маппинг GTIN ---
            n = conn.execute("SELECT COUNT(*) AS c FROM gtin_map").fetchone()["c"]
            if n == 0:
                for fg, cz in [
                    ("04600123456782", "04600123456782"),
                    ("04600123456775", "04600123456775"),
                    ("04600123456768", "04600123456768"),
                ]:
                    conn.execute(
                        "INSERT OR IGNORE INTO gtin_map (foreign_gtin, cz_gtin) VALUES (?, ?)",
                        (fg, cz)
                    )

    def _make_cz(self, gtin: str, idx: int) -> dict:
        sn = f"SN{idx:07d}"
        verify = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        crypto = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return {
            "gtin": gtin,
            "sn": sn,
            "code": f"(01){gtin}(21){sn}(91){verify}(92){crypto}",
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
    def take_code(self, gtin: str, point: str = "T-01"):
        """Взять свободный код из пула, пометить used."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, gtin, code, sn FROM pool_codes "
                "WHERE gtin = ? AND used = 0 LIMIT 1",
                (gtin,)
            ).fetchone()

            if not row:
                # досыпаем
                for i in range(10):
                    d = self._make_cz(gtin, random.randint(1000, 9999))
                    conn.execute(
                        "INSERT OR IGNORE INTO pool_codes (gtin, code, sn) VALUES (?, ?, ?)",
                        (d["gtin"], d["code"], d["sn"])
                    )
                row = conn.execute(
                    "SELECT id, gtin, code, sn FROM pool_codes "
                    "WHERE gtin = ? AND used = 0 LIMIT 1",
                    (gtin,)
                ).fetchone()

            if not row:
                return None

            conn.execute(
                "UPDATE pool_codes SET used = 1, point = ? WHERE id = ?",
                (point, row["id"])
            )
            return {
                "id": row["id"], "gtin": row["gtin"],
                "code": row["code"], "sn": row["sn"],
                "used": True, "point": point,
            }

    def mark_printed(self, code: str, point: str = "T-01"):
        """Пометить код как напечатанный."""
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE pool_codes SET printed = 1, printed_at = datetime('now','localtime') "
                "WHERE code = ?",
                (code,)
            )

    def pool_stats(self) -> list:
        """Статистика по пулу — по каждому GTIN."""
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT gtin, "
                "       COUNT(*) AS total, "
                "       SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS free, "
                "       SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used, "
                "       SUM(CASE WHEN printed=1 THEN 1 ELSE 0 END) AS printed "
                "FROM pool_codes GROUP BY gtin ORDER BY gtin"
            ).fetchall()
        return [dict(r) for r in rows]

    # ==========================================================
    # Счётчики
    # ==========================================================
    _COUNTER_FIELDS = {"scans": "scans", "prints": "prints", "errors": "errors"}

    def increment_counter(self, key: str, delta: int = 1):
        """Увеличить счётчик и обновить кэш."""
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
        """Сбросить счётчики."""
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
    def add_queue_entry(self, gtin: str, sn: str,
                        point: str = "T-01", operator: str = "Иванов И.И."):
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
    # Журнал
    # ==========================================================
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
            conn.execute(
                "INSERT INTO audit (time, action, point, operator, gtin, sn, result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry["time"], entry["action"], entry["point"],
                 entry["operator"], entry["gtin"], entry["sn"], entry["result"])
            )
        self.audit.insert(0, entry)
        if len(self.audit) > 200:
            self.audit = self.audit[:200]


# ==========================================================
# Парсер иностранного ШК (заглушка)
# ==========================================================
def parse_foreign_barcode(raw: str) -> dict:
    raw = (raw or "").strip()
    gtin = "04600123456782"
    sn = "SN" + ''.join(random.choices(string.digits, k=7))
    lot = "LOT-2025-04"

    if raw:
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 14:
            gtin = digits[:14]

    return {
        "type": "GS1-128",
        "gtin": gtin,
        "serial": sn,
        "lot": lot,
        "exp": "12.2026",
        "country": "CN",
        "name": "Наушники беспроводные BT-500",
        "model": "BT-500",
        "importer": "ООО «Ромашка», г. Москва",
    }