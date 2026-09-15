"""
Хранилище данных в памяти. Никакого Flask — просто Python-класс.
"""
from datetime import datetime
import random
import string
from collections import defaultdict


def gtin14_check_digit(gtin13: str) -> str:
    """
    Рассчитать контрольную цифру GTIN-14 по первым 13 цифрам.
    Алгоритм GS1: справа налево, нечётные позиции ×3, чётные ×1.
    """
    total = 0
    for i, ch in enumerate(reversed(gtin13)):
        n = int(ch)
        total += n * (3 if i % 2 == 0 else 1)
    return str((10 - total % 10) % 10)


def make_gtin14(prefix13: str) -> str:
    """Собрать валидный GTIN-14 из 13-значного префикса."""
    assert len(prefix13) == 13, "нужно ровно 13 цифр"
    return prefix13 + gtin14_check_digit(prefix13)


class Store:
    def __init__(self):
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

        # ---------- Пользователи ----------
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

        # ---------- Пул кодов ----------
        self.pool = defaultdict(list)
        self._seed_pool()

        # ---------- Очередь / журнал / счётчики ----------
        self.queue = []
        self.audit = []
        self.counter = {"scans": 0, "prints": 0, "errors": 0, "limit": 500}

        # ---------- Маппинг GTIN ----------
        g1 = make_gtin14("0460012345678")
        g2 = make_gtin14("0460012345679")
        g3 = make_gtin14("0460012345680")
        self.gtin_map = {
            g1: g1,
            g2: g2,
            g3: g3,
        }

    def _seed_pool(self):
        """Наполнить пул моковыми кодами ЧЗ."""
        gtins = [
            make_gtin14("0460012345678"),
            make_gtin14("0460012345679"),
            make_gtin14("0460012345680"),
        ]
        for gtin in gtins:
            for i in range(30):
                self.pool[gtin].append(self._make_cz(gtin, i))

    def _make_cz(self, gtin, idx):
        """Сгенерировать строку DataMatrix в формате GS1."""
        sn = f"SN{idx:07d}"
        verify = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        crypto = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return {
            "code": f"(01){gtin}(21){sn}(91){verify}(92){crypto}",
            "gtin": gtin,
            "sn": sn,
            "used": False,
            "printed": False,
            "point": None,
            "printed_at": None,
        }

    def take_code(self, gtin):
        """Взять свободный код из пула для указанного GTIN."""
        codes = self.pool.get(gtin, [])
        for c in codes:
            if not c["used"]:
                return c
        # Пул пуст — досыпаем
        for i in range(10):
            self.pool[gtin].append(self._make_cz(gtin, len(codes) + i))
        return self.pool[gtin][len(codes)]

    def pool_stats(self):
        """Статистика по пулу."""
        result = []
        for gtin, codes in self.pool.items():
            total = len(codes)
            free = sum(1 for c in codes if not c["used"])
            used = total - free
            printed = sum(1 for c in codes if c["printed"])
            result.append({
                "gtin": gtin, "total": total,
                "free": free, "used": used, "printed": printed,
            })
        return result

    def log(self, action, **kwargs):
        self.audit.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            **kwargs,
        })
        if len(self.audit) > 1000:
            self.audit = self.audit[:1000]


def parse_foreign_barcode(raw):
    """Парсер иностранного ШК (заглушка)."""
    raw = (raw or "").strip()
    gtin = make_gtin14("0460012345678")
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