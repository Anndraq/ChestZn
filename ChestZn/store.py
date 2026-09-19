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
        self._ensure_users_seeded()

        # ---------- Кэши в памяти ----------
        self.counter = self._load_counter()
        self.queue   = self._load_queue()
        self.audit   = self._load_audit(limit=200)

        # ---------- Маппинг GTIN (кэш из БД) ----------
        self.gtin_map = self._load_gtin_map()

        # ---------- Список пользователей для отображения ----------
        self.users = self.list_users()

    # ==========================================================
    # Seed / загрузка
    # ==========================================================
    def _ensure_seed_data(self):
        """Наполнить пул кодов и справочник GTIN, если БД пустая."""
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM pool_codes").fetchone()["c"]
            if n == 0:
                for gtin in ["04600123456782", "04600123456775", "04600123456768"]:
                    for i in range(30):
                        code_data = self._make_cz(gtin, i)
                        conn.execute(
                            "INSERT INTO pool_codes (gtin, code, sn) VALUES (?, ?, ?)",
                            (code_data["gtin"], code_data["code"], code_data["sn"])
                        )

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
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, gtin, code, sn FROM pool_codes "
                "WHERE gtin = ? AND used = 0 LIMIT 1",
                (gtin,)
            ).fetchone()

            if not row:
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
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE pool_codes SET printed = 1, printed_at = datetime('now','localtime') "
                "WHERE code = ?",
                (code,)
            )

    def pool_stats(self) -> list:
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
        return {
            "points": points,
            "operators": operators,
            "results": results,
        }

    # ==========================================================
    # Пользователи и аутентификация
    # ==========================================================
    @staticmethod
    def _hash_password(password: str, salt: bytes = None):
        """Хэш пароля через pbkdf2_hmac. Возвращает (hash_hex, salt_hex)."""
        if salt is None:
            salt = os.urandom(16)
        h = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, 200_000
        )
        return h.hex(), salt.hex()

    def _ensure_users_seeded(self):
        """Наполнить таблицу users, если она пустая."""
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
        """Проверить логин и пароль. Возвращает dict или None."""
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

    def change_password(self, login: str, old_password: str,
                        new_password: str) -> tuple:
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
    # Массовый импорт
    # ==========================================================
    def bulk_import_pool_codes(self, rows: list) -> dict:
        """
        Массовая вставка кодов ЧЗ в пул.
        rows = [
            {"gtin": "04600123456782", "code": "(01)...", "sn": "SN0001"},
            ...
        ]
        Возвращает {ok, added, skipped, errors, messages}.
        """
        added = 0
        skipped = 0
        errors = []
        gtin_counts = {}

        with db.get_conn() as conn:
            for i, row in enumerate(rows, start=2):  # 2 = первая строка данных (после заголовка)
                try:
                    gtin = str(row.get("gtin", "")).strip()
                    code = str(row.get("code", "")).strip()
                    sn = str(row.get("sn", "")).strip()

                    # Валидация
                    if not gtin:
                        errors.append(f"Строка {i}: пустой GTIN")
                        continue
                    if len(gtin) != 14 or not gtin.isdigit():
                        errors.append(f"Строка {i}: GTIN должен быть 14 цифр — «{gtin}»")
                        continue
                    if not code:
                        errors.append(f"Строка {i}: пустой код ЧЗ")
                        continue

                    # Если SN пустой — попробуем извлечь из кода
                    if not sn:
                        import re
                        m = re.search(r"\(21\)([^\(]+)", code)
                        if m:
                            sn = m.group(1)
                        else:
                            sn = "—"

                    cur = conn.execute(
                        "INSERT OR IGNORE INTO pool_codes (gtin, code, sn) "
                        "VALUES (?, ?, ?)",
                        (gtin, code, sn)
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
        """
        Массовая вставка/обновление маппинга GTIN.
        rows = [{"foreign_gtin": "...", "cz_gtin": "..."}, ...]
        Возвращает {ok, added, updated, errors, messages}.
        """
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
                            "INSERT INTO gtin_map (foreign_gtin, cz_gtin) VALUES (?, ?)",
                            (fg, cz)
                        )
                        added += 1
                    elif existing["cz_gtin"] != cz:
                        conn.execute(
                            "UPDATE gtin_map SET cz_gtin = ? WHERE foreign_gtin = ?",
                            (cz, fg)
                        )
                        updated += 1
                    # если совпадает — ничего не делаем

                except Exception as e:
                    errors.append(f"Строка {i}: {e}")

        # Обновить кэш в памяти
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

    def count_pool(self) -> int:
        """Всего кодов в пуле."""
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM pool_codes").fetchone()["c"]

    def count_gtin_map(self) -> int:
        """Всего записей в маппинге GTIN."""
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM gtin_map").fetchone()["c"]

    
    # ==========================================================
    # Настройки (таблица meta)
    # ==========================================================
    def get_setting(self, key: str, default=None):
        """Прочитать настройку из таблицы meta."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value):
        """Сохранить настройку в таблицу meta."""
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
        """Сколько дней хранить журнал. По умолчанию 365."""
        v = self.get_setting("audit_retention_days", "365")
        try:
            return int(v)
        except (ValueError, TypeError):
            return 365

    def set_audit_retention_days(self, days: int):
        """Сохранить срок хранения журнала."""
        days = max(7, int(days))  # минимум 7 дней
        self.set_setting("audit_retention_days", str(days))

    def cleanup_old_audit(self) -> dict:
        """
        Удалить записи audit старше N дней.
        Возвращает {ok, deleted, info}.
        """
        days = self.get_audit_retention_days()

        try:
            with db.get_conn() as conn:
                # Сколько записей под удаление
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

                # Удаляем
                conn.execute(
                    "DELETE FROM audit "
                    "WHERE created_at < datetime('now', 'localtime', ?)",
                    (threshold_mod,)
                )

            # Перезагружаем кэш в памяти
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
        """
        Автоочистка раз в сутки.
        Запускается при старте приложения.
        """
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