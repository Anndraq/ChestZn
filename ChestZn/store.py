"""
Хранилище данных. Работает через db.py (get_conn, init_db).
Пользователи и пароли — в AppData/ChestZn/users.json.
Пул кодов, журнал, очередь, счётчики — в SQLite (chestzn.db).
"""
from datetime import datetime
import json
import os
import random
import string
from pathlib import Path

import db
from auth import hash_password, verify_password


USERS_FILE = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "users.json"


# ---------- Дефолтные пользователи ----------
DEFAULT_USERS = [
    {"id": 1, "fio": "Иванов Иван Иванович",      "login": "i.ivanov",
     "password": "ivanov123",     "role": "Оператор",      "point": "T-01",
     "last_login": "сегодня 08:00", "active": True},
    {"id": 2, "fio": "Петрова Анна Сергеевна",    "login": "a.petrova",
     "password": "petrova123",    "role": "Оператор",      "point": "T-02",
     "last_login": "сегодня 08:00", "active": True},
    {"id": 3, "fio": "Сидоров Виктор Петрович",   "login": "v.sidorov",
     "password": "sidorov123",    "role": "Оператор",      "point": "T-03",
     "last_login": "сегодня 08:00", "active": True},
    {"id": 4, "fio": "Кузнецов Дмитрий Олегович", "login": "d.kuznetsov",
     "password": "kuznetsov123",  "role": "Старший смены", "point": "Все",
     "last_login": "сегодня 07:45", "active": True},
    {"id": 5, "fio": "Морозова Елена Владимировна","login": "e.morozova",
     "password": "morozova123",   "role": "Администратор", "point": "Все",
     "last_login": "вчера 18:12",   "active": True},
    {"id": 6, "fio": "Администратор",              "login": "admin",
     "password": "123",          "role": "Администратор", "point": "Все",
     "last_login": "—",           "active": True},
]


def gtin14_check_digit(gtin13: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(gtin13)):
        n = int(ch)
        total += n * (3 if i % 2 == 0 else 1)
    return str((10 - total % 10) % 10)


def make_gtin14(prefix13: str) -> str:
    assert len(prefix13) == 13, "нужно ровно 13 цифр"
    return prefix13 + gtin14_check_digit(prefix13)


class Store:
    def __init__(self):
        # ---------- Убедимся, что БД создана ----------
        db.init_db()

        # ---------- Точки ----------
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

        # ---------- Шаблоны ----------
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

        # ---------- Интеграции ----------
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

        # ---------- Соответствие GTIN (иностранный → ЧЗ) ----------
        self.gtin_map = {}
        with db.get_conn() as conn:
            rows = conn.execute("SELECT foreign_gtin, cz_gtin FROM gtin_map").fetchall()
            for r in rows:
                self.gtin_map[r["foreign_gtin"]] = r["cz_gtin"]

        # Если таблица пуста — заполняем дефолтными и сохраняем в БД
        if not self.gtin_map:
            pairs = [
                ("04600123456782", "04600123456782"),
                ("04600123456799", "04600123456799"),
                ("04600123456805", "04600123456805"),
            ]
            with db.get_conn() as conn:
                for foreign, cz in pairs:
                    conn.execute(
                        "INSERT OR IGNORE INTO gtin_map(foreign_gtin, cz_gtin) "
                        "VALUES (?, ?)", (foreign, cz)
                    )
            self.gtin_map = dict(pairs)

        # ---------- Пользователи ----------
        self.users = self._load_users()
        self.current_user = None

        # ---------- Пул кодов ----------
        if self._pool_count() == 0:
            self._seed_pool()

    # ==================================================================
    # ПОЛЬЗОВАТЕЛИ
    # ==================================================================
    def _load_users(self):
        if USERS_FILE.exists():
            try:
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                by_login = {u["login"]: u for u in data}
                result = []
                for default in DEFAULT_USERS:
                    user = by_login.get(default["login"], default).copy()
                    if "password_hash" not in user:
                        user["password_hash"] = hash_password(
                            user.get("password", default["password"])
                        )
                        user.pop("password", None)
                    result.append(user)
                self._save_users(result)
                return result
            except Exception as e:
                print(f"[store] ошибка чтения users.json: {e} — пересоздаю")

        users = []
        for u in DEFAULT_USERS:
            u = u.copy()
            u["password_hash"] = hash_password(u.pop("password"))
            users.append(u)
        self._save_users(users)
        return users

    def _save_users(self, users):
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    def check_login(self, login: str, password: str):
        for u in self.users:
            if u["login"] == login and u.get("active", True):
                if verify_password(password, u["password_hash"]):
                    return u
        return None

    def set_password(self, login: str, new_password: str):
        """Сменить пароль пользователя."""
        for u in self.users:
            if u["login"] == login:
                u["password_hash"] = hash_password(new_password)
                break
        self._save_users(self.users)

    # ==================================================================
    # СЕКРЕТНЫЙ ВОПРОС (восстановление доступа)
    # ==================================================================
    def set_security_question(self, login: str, question: str, answer: str):
        """
        Сохранить секретный вопрос и ответ (хэш).
        Используется при первом входе админа.
        Регистр учитывается, пробелы по краям убираются.
        """
        answer = answer.strip()
        for u in self.users:
            if u["login"] == login:
                u["security_question"] = question
                u["security_answer_hash"] = hash_password(answer)
                break
        self._save_users(self.users)

    def has_security_question(self, login: str) -> bool:
        """Проверить, настроен ли секретный вопрос у пользователя."""
        for u in self.users:
            if u["login"] == login:
                return bool(u.get("security_question") and u.get("security_answer_hash"))
        return False

    def get_security_question(self, login: str) -> str:
        """Получить текст секретного вопроса."""
        for u in self.users:
            if u["login"] == login:
                return u.get("security_question", "")
        return ""

    def check_security_answer(self, login: str, answer: str) -> bool:
        """
        Проверить ответ на секретный вопрос.
        Регистр учитывается, пробелы по краям убираются.
        """
        for u in self.users:
            if u["login"] == login:
                stored = u.get("security_answer_hash")
                if not stored:
                    return False
                answer = answer.strip()
                return verify_password(answer, stored)
        return False

    def reset_password(self, login: str, new_password: str):
        """Сбросить пароль (используется после верного ответа)."""
        self.set_password(login, new_password)

    # ==================================================================
    # ПУЛ КОДОВ ЧЗ
    # ==================================================================
    def _pool_count(self) -> int:
        with db.get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM pool_codes").fetchone()
            return row["n"]

    def _seed_pool(self):
        gtins = [
            "04600123456782",
            "04600123456799",
            "04600123456805",
        ]
        with db.get_conn() as conn:
            for gtin in gtins:
                for i in range(30):
                    sn = f"SN{i:07d}"
                    verify = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
                    crypto = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                    code = f"(01){gtin}(21){sn}(91){verify}(92){crypto}"
                    conn.execute(
                        "INSERT OR IGNORE INTO pool_codes(gtin, code, sn) "
                        "VALUES (?, ?, ?)", (gtin, code, sn)
                    )

    def take_code(self, gtin, point=None):
        """Взять свободный код из пула. Помечает used=1."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, gtin, code, sn FROM pool_codes "
                "WHERE gtin = ? AND used = 0 ORDER BY id LIMIT 1",
                (gtin,),
            ).fetchone()

            if row is None:
                for i in range(10):
                    sn = "SN" + ''.join(random.choices(string.digits, k=7))
                    verify = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
                    crypto = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                    code = f"(01){gtin}(21){sn}(91){verify}(92){crypto}"
                    conn.execute(
                        "INSERT OR IGNORE INTO pool_codes(gtin, code, sn) "
                        "VALUES (?, ?, ?)", (gtin, code, sn)
                    )
                row = conn.execute(
                    "SELECT id, gtin, code, sn FROM pool_codes "
                    "WHERE gtin = ? AND used = 0 ORDER BY id LIMIT 1",
                    (gtin,),
                ).fetchone()

            if row is None:
                return None

            conn.execute(
                "UPDATE pool_codes SET used = 1, point = ? WHERE id = ?",
                (point, row["id"]),
            )
            return {"gtin": row["gtin"], "code": row["code"], "sn": row["sn"]}

    def mark_printed(self, code, point=None):
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE pool_codes SET printed = 1, printed_at = ?, point = ? "
                "WHERE code = ?",
                (datetime.now().strftime("%H:%M:%S"), point, code),
            )

    def pool_stats(self):
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT gtin,
                       COUNT(*) AS total,
                       SUM(CASE WHEN used = 0 THEN 1 ELSE 0 END) AS free,
                       SUM(used) AS used,
                       SUM(printed) AS printed
                FROM pool_codes GROUP BY gtin
            """).fetchall()
            return [dict(r) for r in rows]

    # ==================================================================
    # ЖУРНАЛ
    # ==================================================================
    def log(self, action, **kwargs):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO audit(time, action, point, operator, gtin, sn, result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().strftime("%H:%M:%S"),
                    action,
                    kwargs.get("point"),
                    kwargs.get("operator"),
                    kwargs.get("gtin"),
                    kwargs.get("sn"),
                    kwargs.get("result"),
                ),
            )

    @property
    def audit(self):
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT time, action, point, operator, gtin, sn, result "
                "FROM audit ORDER BY id DESC LIMIT 200"
            ).fetchall()
            return [dict(r) for r in rows]

    # ==================================================================
    # ОЧЕРЕДЬ
    # ==================================================================
    def add_queue_entry(self, time, gtin, sn, point=None, operator=None):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO queue(time, gtin, sn, point, operator) "
                "VALUES (?, ?, ?, ?, ?)",
                (time, gtin, sn, point, operator),
            )

    @property
    def queue(self):
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT time, gtin, sn, point, operator, status "
                "FROM queue ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def clear_queue(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM queue")

    # ==================================================================
    # СЧЁТЧИКИ
    # ==================================================================
    @property
    def counter(self):
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM counters WHERE id = 1").fetchone()
            if row is None:
                return {"scans": 0, "prints": 0, "errors": 0, "limit": 500}
            return {
                "scans":  row["scans"],
                "prints": row["prints"],
                "errors": row["errors"],
                "limit":  row["limit_value"],
            }

    def increment_counter(self, key, delta=1):
        """Увеличить счётчик. key ∈ {scans, prints, errors}."""
        col = {
            "scans":  "scans",
            "prints": "prints",
            "errors": "errors",
        }.get(key)
        if col is None:
            return 0

        with db.get_conn() as conn:
            conn.execute(
                f"UPDATE counters SET {col} = {col} + ? WHERE id = 1",
                (delta,),
            )
            row = conn.execute(f"SELECT {col} AS v FROM counters WHERE id = 1").fetchone()
            return row["v"] if row else 0


# ---------- Парсер иностранного ШК ----------
def parse_foreign_barcode(raw):
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