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

        # ---------- Точки (из БД) ----------
        self._ensure_points_seeded()
        self.points = self._load_points()

        self.current_point = "T-01"
        self.current_operator = "Оператор"
        self.current_user = None

        self.integrations = {
            "gis_mt": {"key": "gis_mt", "name": "ГИС МТ / Честный знак",
                       "status": "ok", "last": "08:14:23", "calls_today": 0,
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
    # Точки и компьютеры
    # ==========================================================
    _DEFAULT_POINTS = [
        {
            "id": "T-01", "name": "Склад-Восток",
            "address": "Корпус А, 1 этаж",
            "ip": "", "printer": "",
            "operator": "Оператор", "online": 1,
            "template": "electronics", "sort_order": 10,
            "computers": [],
        },
        {
            "id": "T-02", "name": "Склад-Запад",
            "address": "Корпус Б, 2 этаж",
            "ip": "", "printer": "",
            "operator": "Оператор", "online": 1,
            "template": "shoes", "sort_order": 20,
            "computers": [],
        },
        {
            "id": "T-03", "name": "Пост-Север",
            "address": "КПП-3",
            "ip": "", "printer": "",
            "operator": "Оператор", "online": 0,
            "template": "electronics", "sort_order": 30,
            "computers": [],
        },
    ]

    def _ensure_points_seeded(self):
        """Наполнить таблицу points, если она пустая."""
        import json
        with db.get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM points"
            ).fetchone()["c"]
            if n > 0:
                return

            for p in self._DEFAULT_POINTS:
                conn.execute(
                    "INSERT INTO points "
                    "(id, name, address, ip, printer, operator, online, "
                    " template, computers_json, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (p["id"], p["name"], p["address"], p["ip"],
                     p["printer"], p["operator"], p["online"],
                     p["template"],
                     json.dumps(p["computers"], ensure_ascii=False),
                     p["sort_order"])
                )

    def _load_points(self) -> list:
        """Загрузить точки из БД."""
        import json
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, address, ip, printer, operator, "
                "       online, template, computers_json, sort_order "
                "FROM points ORDER BY sort_order, id"
            ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            d["online"] = bool(d["online"])
            try:
                d["computers"] = json.loads(d.get("computers_json") or "[]")
            except Exception:
                d["computers"] = []
            d.pop("computers_json", None)
            result.append(d)
        return result

    def get_point(self, point_id: str):
        """Одна точка."""
        for p in self.points:
            if p["id"] == point_id:
                return p
        return None

    def update_point(self, point_id: str, **kwargs) -> dict:
        """Обновить точку. Принимает name, address, ip, printer,
        operator, online, template, computers."""
        import json
        p = self.get_point(point_id)
        if not p:
            return {"ok": False, "info": "Точка не найдена"}

        new = dict(p)
        for k in ("name", "address", "ip", "printer", "operator",
                  "template"):
            if k in kwargs:
                new[k] = kwargs[k]
        if "online" in kwargs:
            new["online"] = bool(kwargs["online"])
        if "computers" in kwargs:
            new["computers"] = kwargs["computers"]

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE points SET "
                    "  name = ?, address = ?, ip = ?, printer = ?, "
                    "  operator = ?, online = ?, template = ?, "
                    "  computers_json = ? "
                    "WHERE id = ?",
                    (new["name"], new["address"], new["ip"],
                     new["printer"], new["operator"],
                     1 if new["online"] else 0, new["template"],
                     json.dumps(new["computers"], ensure_ascii=False),
                     point_id)
                )

            for i, pp in enumerate(self.points):
                if pp["id"] == point_id:
                    self.points[i] = new
                    break

            return {"ok": True, "info": f"Точка «{new['name']}» сохранена"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    # ==========================================================
    # Компьютеры / Принтеры / Сканеры
    # ==========================================================
    def list_computers(self, point_id: str = None) -> list:
        """Список компьютеров (опционально — только для точки)."""
        with db.get_conn() as conn:
            if point_id:
                rows = conn.execute(
                    "SELECT id, node_id, point_id, name, ip, role, "
                    "       sync_node, sync_enabled, online, last_seen_at, "
                    "       sort_order "
                    "FROM computers WHERE point_id = ? AND deleted = 0 "
                    "ORDER BY sort_order, name",
                    (point_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, node_id, point_id, name, ip, role, "
                    "       sync_node, sync_enabled, online, last_seen_at, "
                    "       sort_order "
                    "FROM computers WHERE deleted = 0 "
                    "ORDER BY point_id, sort_order, name"
                ).fetchall()
        return [dict(r) for r in rows]

    def get_computer(self, computer_id: str):
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT id, node_id, point_id, name, ip, role, "
                "       sync_node, sync_enabled, online, last_seen_at, "
                "       sort_order "
                "FROM computers WHERE id = ?",
                (computer_id,)
            ).fetchone()
        return dict(r) if r else None

    def create_computer(self, point_id: str, name: str, ip: str = "",
                        role: str = "", sync_node: str = "",
                        sync_enabled: bool = True,
                        online: bool = True) -> dict:
        import uuid
        cid = uuid.uuid4().hex
        node_id = db.get_node_id()
        sync_node = sync_node or name

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO computers "
                    "(id, node_id, point_id, name, ip, role, "
                    " sync_node, sync_enabled, online, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 100)",
                    (cid, node_id, point_id, name, ip, role,
                     sync_node, 1 if sync_enabled else 0,
                     1 if online else 0)
                )
                db.sync_computers_json(conn, point_id)
            return {"ok": True, "info": f"ПК «{name}» создан", "id": cid}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def update_computer(self, computer_id: str, **kwargs) -> dict:
        c = self.get_computer(computer_id)
        if not c:
            return {"ok": False, "info": "ПК не найден"}

        new = dict(c)
        for k in ("name", "ip", "role", "sync_node"):
            if k in kwargs:
                new[k] = kwargs[k]
        for k in ("sync_enabled", "online"):
            if k in kwargs:
                new[k] = bool(kwargs[k])

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE computers SET "
                    "  name = ?, ip = ?, role = ?, sync_node = ?, "
                    "  sync_enabled = ?, online = ?, "
                    "  updated_at = datetime('now','localtime') "
                    "WHERE id = ?",
                    (new["name"], new["ip"], new["role"], new["sync_node"],
                     1 if new["sync_enabled"] else 0,
                     1 if new["online"] else 0, computer_id)
                )
                db.sync_computers_json(conn, c["point_id"])
            return {"ok": True, "info": f"ПК «{new['name']}» сохранён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def delete_computer(self, computer_id: str) -> dict:
        c = self.get_computer(computer_id)
        if not c:
            return {"ok": False, "info": "ПК не найден"}

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE computers SET deleted = 1, "
                    "updated_at = datetime('now','localtime') WHERE id = ?",
                    (computer_id,)
                )
                # Удалить дочерние принтеры и сканеры
                conn.execute(
                    "UPDATE printers SET deleted = 1 WHERE computer_id = ?",
                    (computer_id,)
                )
                conn.execute(
                    "UPDATE scanners SET deleted = 1 WHERE computer_id = ?",
                    (computer_id,)
                )
                db.sync_computers_json(conn, c["point_id"])
            return {"ok": True, "info": f"ПК «{c['name']}» удалён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    # ---------- Принтеры ----------
    def list_printers_for_computer(self, computer_id: str) -> list:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, node_id, computer_id, name, ip, port, lang, "
                "       width_mm, height_mm, dpi, copies, online, sort_order "
                "FROM printers WHERE computer_id = ? AND deleted = 0 "
                "ORDER BY sort_order, name",
                (computer_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_printer(self, printer_id: str):
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT id, node_id, computer_id, name, ip, port, lang, "
                "       width_mm, height_mm, dpi, copies, online, sort_order "
                "FROM printers WHERE id = ?",
                (printer_id,)
            ).fetchone()
        return dict(r) if r else None

    def create_printer(self, computer_id: str, name: str,
                       ip: str = "", port: int = 9100,
                       lang: str = "auto",
                       width_mm: float = 58, height_mm: float = 40,
                       dpi: int = 203, copies: int = 1,
                       online: bool = True) -> dict:
        import uuid
        pid = uuid.uuid4().hex
        node_id = db.get_node_id()

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO printers "
                    "(id, node_id, computer_id, name, ip, port, lang, "
                    " width_mm, height_mm, dpi, copies, online, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 100)",
                    (pid, node_id, computer_id, name, ip, port, lang,
                     width_mm, height_mm, dpi, copies,
                     1 if online else 0)
                )
            return {"ok": True, "info": f"Принтер «{name}» создан", "id": pid}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def update_printer(self, printer_id: str, **kwargs) -> dict:
        p = self.get_printer(printer_id)
        if not p:
            return {"ok": False, "info": "Принтер не найден"}

        new = dict(p)
        for k in ("name", "ip", "lang"):
            if k in kwargs:
                new[k] = kwargs[k]
        for k in ("port", "dpi", "copies"):
            if k in kwargs:
                try:
                    new[k] = int(kwargs[k])
                except (ValueError, TypeError):
                    pass
        for k in ("width_mm", "height_mm"):
            if k in kwargs:
                try:
                    new[k] = float(kwargs[k])
                except (ValueError, TypeError):
                    pass
        if "online" in kwargs:
            new["online"] = bool(kwargs["online"])

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE printers SET "
                    "  name = ?, ip = ?, port = ?, lang = ?, "
                    "  width_mm = ?, height_mm = ?, dpi = ?, copies = ?, "
                    "  online = ?, updated_at = datetime('now','localtime') "
                    "WHERE id = ?",
                    (new["name"], new["ip"], new["port"], new["lang"],
                     new["width_mm"], new["height_mm"], new["dpi"],
                     new["copies"], 1 if new["online"] else 0, printer_id)
                )
            return {"ok": True, "info": f"Принтер «{new['name']}» сохранён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def delete_printer(self, printer_id: str) -> dict:
        p = self.get_printer(printer_id)
        if not p:
            return {"ok": False, "info": "Принтер не найден"}

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE printers SET deleted = 1, "
                    "updated_at = datetime('now','localtime') WHERE id = ?",
                    (printer_id,)
                )
                # Отвязать сканеры
                conn.execute(
                    "UPDATE scanners SET printer_id = NULL WHERE printer_id = ?",
                    (printer_id,)
                )
            return {"ok": True, "info": f"Принтер «{p['name']}» удалён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    # ---------- Сканеры ----------
    def list_scanners_for_computer(self, computer_id: str) -> list:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, node_id, computer_id, printer_id, name, operator, "
                "       connect_type, com_port, bt_address, ip, port, "
                "       enabled, sort_order "
                "FROM scanners WHERE computer_id = ? AND deleted = 0 "
                "ORDER BY sort_order, name",
                (computer_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_scanner(self, scanner_id: str):
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT id, node_id, computer_id, printer_id, name, operator, "
                "       connect_type, com_port, bt_address, ip, port, "
                "       enabled, sort_order "
                "FROM scanners WHERE id = ?",
                (scanner_id,)
            ).fetchone()
        return dict(r) if r else None

    def create_scanner(self, computer_id: str, name: str,
                       operator: str = "",
                       printer_id: str = None,
                       connect_type: str = "usb",
                       com_port: str = "",
                       bt_address: str = "",
                       ip: str = "",
                       port: int = 9100,
                       enabled: bool = True) -> dict:
        import uuid
        sid = uuid.uuid4().hex
        node_id = db.get_node_id()

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO scanners "
                    "(id, node_id, computer_id, printer_id, name, operator, "
                    " connect_type, com_port, bt_address, ip, port, "
                    " enabled, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 100)",
                    (sid, node_id, computer_id, printer_id, name, operator,
                     connect_type, com_port, bt_address, ip, port,
                     1 if enabled else 0)
                )
            return {"ok": True, "info": f"Сканер «{name}» создан", "id": sid}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def update_scanner(self, scanner_id: str, **kwargs) -> dict:
        s = self.get_scanner(scanner_id)
        if not s:
            return {"ok": False, "info": "Сканер не найден"}

        new = dict(s)
        for k in ("name", "operator", "connect_type",
                  "com_port", "bt_address", "ip"):
            if k in kwargs:
                new[k] = kwargs[k]
        if "printer_id" in kwargs:
            new["printer_id"] = kwargs["printer_id"] or None
        if "port" in kwargs:
            try:
                new["port"] = int(kwargs["port"])
            except (ValueError, TypeError):
                pass
        if "enabled" in kwargs:
            new["enabled"] = bool(kwargs["enabled"])

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE scanners SET "
                    "  name = ?, operator = ?, printer_id = ?, "
                    "  connect_type = ?, com_port = ?, bt_address = ?, "
                    "  ip = ?, port = ?, enabled = ?, "
                    "  updated_at = datetime('now','localtime') "
                    "WHERE id = ?",
                    (new["name"], new["operator"], new["printer_id"],
                     new["connect_type"], new["com_port"], new["bt_address"],
                     new["ip"], new["port"], 1 if new["enabled"] else 0,
                     scanner_id)
                )
            return {"ok": True, "info": f"Сканер «{new['name']}» сохранён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def delete_scanner(self, scanner_id: str) -> dict:
        s = self.get_scanner(scanner_id)
        if not s:
            return {"ok": False, "info": "Сканер не найден"}

        try:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE scanners SET deleted = 1, "
                    "updated_at = datetime('now','localtime') WHERE id = ?",
                    (scanner_id,)
                )
            return {"ok": True, "info": f"Сканер «{s['name']}» удалён"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    # ==========================================================
    # Seed / загрузка
    # ==========================================================
    def _ensure_seed_data(self):
        """Демо-заполнение отключено: база работает только с реальными кодами ЧЗ и привязками."""
        return
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

    def _increment_gtin_counter(self, gtin: str, point: str):
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
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT scans FROM gtin_counters WHERE gtin = ? AND point = ?",
                (gtin, point)
            ).fetchone()
        return row["scans"] if row else 0

    def _get_gtin_total(self, gtin: str, point: str) -> int:
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM pool_codes "
                "WHERE gtin = ? AND assigned_point = ?",
                (gtin, point)
            ).fetchone()["c"]

    def process_scan(self, foreign_code: str, point: str = None,
                     operator: str = None) -> dict:
        if point is None:
            point = self.current_point
        if operator is None:
            operator = self.current_operator

        foreign = parse_foreign_barcode(foreign_code)
        prev_count = self.count_scanned_shk(foreign_code)
        gtin_cz = self.gtin_map.get(foreign["gtin"], foreign["gtin"])

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

        self._increment_gtin_counter(gtin_cz, point)

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
            "prev_count": prev_count,
            "gtin_scans": scans,
            "gtin_total": total,
            "overflow": overflow,
        }

    def count_unprinted_for_point(self, point: str) -> int:
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM pool_codes "
                "WHERE used_point = ? AND used = 1 AND printed = 0",
                (point,)
            ).fetchone()["c"]

    def list_unprinted_for_point(self, point: str, limit: int = 100) -> list:
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
            "config": {
                "print_mode": "combined",
                "combined": {
                    "w": 58, "h": 40, "dpi": 203,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "x": 5, "y": 5},
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 13, "x": 100, "y": 5},
                        {"name": "model", "label": "Модель", "required": False,
                         "font_size": 11, "x": 100, "y": 25},
                        {"name": "importer", "label": "Импортёр", "required": True,
                         "font_size": 10, "x": 100, "y": 40},
                        {"name": "exp_date", "label": "Срок годности", "required": False,
                         "font_size": 10, "x": 100, "y": 55},
                    ],
                },
                "cz": {
                    "w": 30, "h": 30, "dpi": 203,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "x": 5, "y": 5},
                    ],
                },
                "label": {
                    "w": 58, "h": 40, "dpi": 203,
                    "fields": [
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 13, "x": 5, "y": 5},
                        {"name": "model", "label": "Модель", "required": False,
                         "font_size": 11, "x": 5, "y": 25},
                        {"name": "importer", "label": "Импортёр", "required": True,
                         "font_size": 10, "x": 5, "y": 40},
                        {"name": "exp_date", "label": "Срок годности", "required": False,
                         "font_size": 10, "x": 5, "y": 55},
                    ],
                },
            },
        },
        {
            "code": "shoes",
            "name": "Обувь (ТР ТС 017)",
            "is_default": 0,
            "config": {
                "print_mode": "separate",
                "combined": {
                    "w": 58, "h": 40, "dpi": 203,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "x": 5, "y": 5},
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 13, "x": 100, "y": 5},
                    ],
                },
                "cz": {
                    "w": 30, "h": 30, "dpi": 203,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "x": 5, "y": 5},
                    ],
                },
                "label": {
                    "w": 58, "h": 40, "dpi": 203,
                    "fields": [
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 13, "x": 5, "y": 5},
                        {"name": "material", "label": "Материал", "required": True,
                         "font_size": 11, "x": 5, "y": 25},
                        {"name": "size_eu", "label": "Размер EU", "required": True,
                         "font_size": 11, "x": 5, "y": 40},
                        {"name": "importer", "label": "Импортёр", "required": True,
                         "font_size": 10, "x": 5, "y": 55},
                    ],
                },
            },
        },
        {
            "code": "clothing",
            "name": "Одежда (ТР ТС 017)",
            "is_default": 0,
            "config": {
                "print_mode": "separate",
                "combined": {
                    "w": 58, "h": 40, "dpi": 203,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "x": 5, "y": 5},
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 13, "x": 100, "y": 5},
                    ],
                },
                "cz": {
                    "w": 30, "h": 30, "dpi": 203,
                    "fields": [
                        {"name": "dataMatrix", "label": "", "required": True,
                         "font_size": 0, "x": 5, "y": 5},
                    ],
                },
                "label": {
                    "w": 58, "h": 40, "dpi": 203,
                    "fields": [
                        {"name": "name_ru", "label": "", "required": True,
                         "font_size": 13, "x": 5, "y": 5},
                        {"name": "composition", "label": "Состав", "required": True,
                         "font_size": 11, "x": 5, "y": 25},
                        {"name": "importer", "label": "Импортёр", "required": True,
                         "font_size": 10, "x": 5, "y": 40},
                    ],
                },
            },
        },
    ]

    def _ensure_templates_seeded(self):
        """Наполнить таблицу шаблонов, если она пустая."""
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
                    " fields_json, sort_order, config_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tpl["code"], tpl["name"], tpl["is_default"],
                     58, 40, 203,
                     json.dumps(tpl["config"]["combined"]["fields"],
                                ensure_ascii=False),
                     i * 10,
                     json.dumps(tpl["config"], ensure_ascii=False))
                )

    def list_templates(self) -> list:
        """Список шаблонов с конфигурацией."""
        import json
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, code, name, is_default, version, "
                "       width_mm, height_mm, dpi, fields_json, "
                "       sort_order, config_json "
                "FROM label_templates ORDER BY sort_order, id"
            ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            try:
                config = json.loads(d.get("config_json") or "{}")
            except Exception:
                config = {}

            if not config:
                old_fields = []
                try:
                    old_fields = json.loads(d.get("fields_json") or "[]")
                except Exception:
                    pass
                config = {
                    "print_mode": "combined",
                    "combined": {
                        "w": d["width_mm"], "h": d["height_mm"],
                        "dpi": d["dpi"], "fields": old_fields,
                    },
                    "cz": {"w": 30, "h": 30, "dpi": 203,
                           "fields": [{"name": "dataMatrix", "label": "",
                                       "required": True, "font_size": 0,
                                       "x": 5, "y": 5}]},
                    "label": {"w": d["width_mm"], "h": d["height_mm"],
                              "dpi": d["dpi"], "fields": old_fields},
                }

            d["config"] = config
            d["print_mode"] = config.get("print_mode", "combined")
            d["width_mm"] = config.get("combined", {}).get("w", d["width_mm"])
            d["height_mm"] = config.get("combined", {}).get("h", d["height_mm"])
            d["dpi"] = config.get("combined", {}).get("dpi", d["dpi"])
            d["fields"] = config.get("combined", {}).get("fields", [])

            d.pop("fields_json", None)
            d.pop("config_json", None)
            result.append(d)
        return result

    def get_template(self, code: str):
        """Один шаблон по коду."""
        import json
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT id, code, name, is_default, version, "
                "       width_mm, height_mm, dpi, fields_json, "
                "       sort_order, config_json "
                "FROM label_templates WHERE code = ?",
                (code,)
            ).fetchone()
        if not r:
            return None

        d = dict(r)
        try:
            config = json.loads(d.get("config_json") or "{}")
        except Exception:
            config = {}

        if not config:
            old_fields = []
            try:
                old_fields = json.loads(d.get("fields_json") or "[]")
            except Exception:
                pass
            config = {
                "print_mode": "combined",
                "combined": {"w": d["width_mm"], "h": d["height_mm"],
                             "dpi": d["dpi"], "fields": old_fields},
                "cz": {"w": 30, "h": 30, "dpi": 203,
                       "fields": [{"name": "dataMatrix", "label": "",
                                   "required": True, "font_size": 0,
                                   "x": 5, "y": 5}]},
                "label": {"w": d["width_mm"], "h": d["height_mm"],
                          "dpi": d["dpi"], "fields": old_fields},
            }

        d["config"] = config
        d["print_mode"] = config.get("print_mode", "combined")
        d.pop("fields_json", None)
        d.pop("config_json", None)
        return d

    def get_default_template(self):
        """Шаблон по умолчанию."""
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

    def create_template(self, code: str, name: str,
                        config: dict = None,
                        is_default: bool = False) -> dict:
        """Создать шаблон с конфигурацией трёх вкладок."""
        import json

        if config is None:
            config = {
                "print_mode": "combined",
                "combined": {"w": 58, "h": 40, "dpi": 203,
                             "fields": [
                                 {"name": "dataMatrix", "label": "",
                                  "required": True, "font_size": 0,
                                  "x": 5, "y": 5},
                                 {"name": "name_ru", "label": "",
                                  "required": True, "font_size": 13,
                                  "x": 100, "y": 5},
                             ]},
                "cz": {"w": 30, "h": 30, "dpi": 203,
                       "fields": [{"name": "dataMatrix", "label": "",
                                   "required": True, "font_size": 0,
                                   "x": 5, "y": 5}]},
                "label": {"w": 58, "h": 40, "dpi": 203,
                          "fields": [
                              {"name": "name_ru", "label": "",
                               "required": True, "font_size": 13,
                               "x": 5, "y": 5},
                          ]},
            }

        try:
            with db.get_conn() as conn:
                if is_default:
                    conn.execute(
                        "UPDATE label_templates SET is_default = 0"
                    )
                conn.execute(
                    "INSERT INTO label_templates "
                    "(code, name, is_default, width_mm, height_mm, dpi, "
                    " fields_json, sort_order, config_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (code, name, 1 if is_default else 0,
                     config["combined"]["w"],
                     config["combined"]["h"],
                     config["combined"]["dpi"],
                     json.dumps(config["combined"]["fields"], ensure_ascii=False),
                     100,
                     json.dumps(config, ensure_ascii=False))
                )
            return {"ok": True, "info": f"Шаблон «{name}» создан"}
        except Exception as e:
            return {"ok": False, "info": str(e)}

    def update_template(self, code: str, name: str = None,
                        config: dict = None,
                        is_default: bool = None) -> dict:
        """Обновить шаблон."""
        import json
        tpl = self.get_template(code)
        if not tpl:
            return {"ok": False, "info": "Шаблон не найден"}

        new_name = name if name is not None else tpl["name"]
        new_config = config if config is not None else tpl["config"]
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
                    "  name = ?, "
                    "  width_mm = ?, height_mm = ?, dpi = ?, "
                    "  fields_json = ?, "
                    "  config_json = ?, "
                    "  is_default = ?, "
                    "  updated_at = datetime('now','localtime') "
                    "WHERE code = ?",
                    (new_name,
                     new_config["combined"]["w"],
                     new_config["combined"]["h"],
                     new_config["combined"]["dpi"],
                     json.dumps(new_config["combined"]["fields"],
                                ensure_ascii=False),
                     json.dumps(new_config, ensure_ascii=False),
                     new_default, code)
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

    def get_recent_audit(self, limit: int = 5,
                         actions: list = None) -> list:
        """
        Последние N записей журнала для мини-журнала оператора.
        По умолчанию возвращает только сканы и печати.
        """
        if actions is None:
            actions = ["Скан", "Автопечать", "Повтор печати"]

        placeholders = ",".join("?" * len(actions))
        with db.get_conn() as conn:
            rows = conn.execute(
                f"SELECT time, action, point, operator, gtin, sn, result "
                f"FROM audit WHERE action IN ({placeholders}) "
                f"ORDER BY id DESC LIMIT ?",
                actions + [limit]
            ).fetchall()
        return [dict(r) for r in rows]

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
    # СИНХРОНИЗАЦИЯ МАСТЕР ↔ ТОЧКИ
    # ==========================================================
    def get_codes_for_export(self, point_id: str,
                             since: str = None) -> list:
        """Коды точки для выгрузки мастером в outbox/to_{point}.json."""
        with db.get_conn() as conn:
            if since:
                rows = conn.execute(
                    "SELECT gtin, code, sn, assigned_point, import_batch, "
                    "       created_at "
                    "FROM pool_codes "
                    "WHERE assigned_point = ? AND created_at > ? "
                    "ORDER BY created_at",
                    (point_id, since)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT gtin, code, sn, assigned_point, import_batch, "
                    "       created_at "
                    "FROM pool_codes "
                    "WHERE assigned_point = ? "
                    "ORDER BY created_at",
                    (point_id,)
                ).fetchall()
        return [dict(r) for r in rows]

    def apply_incoming_codes(self, point_id: str, codes: list) -> int:
        """Точка принимает коды от мастера. Идемпотентно по `code`."""
        if not codes:
            return 0
        node_id = db.get_node_id()
        added = 0
        with db.get_conn() as conn:
            for c in codes:
                code = (c.get("code") or "").strip()
                if not code:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM pool_codes WHERE code = ? LIMIT 1",
                    (code,)
                ).fetchone()
                if exists:
                    continue

                conn.execute(
                    "INSERT INTO pool_codes "
                    "(gtin, code, sn, assigned_point, assigned_node_id, "
                    " import_batch, used, printed, synced_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0, 0, "
                    "        datetime('now','localtime'), "
                    "        datetime('now','localtime'))",
                    (c.get("gtin"), code, c.get("sn"),
                     point_id, node_id,
                     c.get("import_batch") or "")
                )
                added += 1
        return added

    def get_printed_for_master(self, point_id: str,
                               since: str = None) -> list:
        """Напечатанные на точке коды — для отчёта мастеру."""
        with db.get_conn() as conn:
            if since:
                rows = conn.execute(
                    "SELECT gtin, code, sn, used_point, printed_at "
                    "FROM pool_codes "
                    "WHERE used_point = ? AND printed = 1 "
                    "  AND printed_at IS NOT NULL AND printed_at > ? "
                    "ORDER BY printed_at",
                    (point_id, since)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT gtin, code, sn, used_point, printed_at "
                    "FROM pool_codes "
                    "WHERE used_point = ? AND printed = 1 "
                    "  AND printed_at IS NOT NULL "
                    "ORDER BY printed_at",
                    (point_id,)
                ).fetchall()
        return [dict(r) for r in rows]

    def apply_incoming_printed(self, point_id: str,
                               records: list) -> int:
        """Мастер принимает отчёт от точки. Идемпотентно по `code`."""
        if not records:
            return 0
        updated = 0
        with db.get_conn() as conn:
            for r in records:
                code = (r.get("code") or "").strip()
                if not code:
                    continue
                cur = conn.execute(
                    "UPDATE pool_codes SET printed = 1, "
                    "  printed_at = COALESCE(?, datetime('now','localtime')) "
                    "WHERE code = ? AND (printed = 0 OR printed IS NULL)",
                    (r.get("printed_at"), code)
                )
                if cur.rowcount > 0:
                    updated += 1
        return updated

    def sync_status(self) -> list:
        """Сводка: для каждой точки — когда выгружали/принимали."""
        rows = []
        for p in self.points:
            pid = p["id"]
            rows.append({
                "point": pid,
                "name": p.get("name", ""),
                "last_export": self.get_setting(f"last_export_{pid}") or "—",
                "last_report": self.get_setting(f"last_report_{pid}") or "—",
                "last_import": self.get_setting(f"last_import_{pid}") or "—",
            })
        return rows

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
    """
    Боевой разбор отсканированного штрихкода товара (EAN-13, GTIN-14, GS1-128, DataMatrix)
    без демонстрационных заглушек (без 04600123456782, «Наушников BT-500» и «ООО Ромашка»).
    """
    raw_s = (raw or "").strip("\r\n\t ")
    if len(raw_s) > 3 and raw_s.startswith("]") and raw_s[1:3] in ("d2", "d1", "C1", "E0", "E4", "I1", "Q3"):
        raw_s = raw_s[3:].strip()

    s = raw_s.replace("_x001D_", "\x1d").lstrip("\x1d")

    gtin = ""
    sn = "—"
    lot = "—"
    exp = "—"
    btype = "EAN-13"

    if s:
        # 1. Скобочный формат GS1: (01)04607012345678(21)SN...
        m_br = re.search(r"\(01\)\s*(\d{8,14})(?:\s*\(21\)\s*([^\(\x1d\r\n]+))?", s)
        if m_br:
            btype = "GS1-128"
            g_raw = m_br.group(1)
            gtin = g_raw.zfill(14) if len(g_raw) == 13 else g_raw
            if m_br.group(2):
                sn = m_br.group(2).strip()

        # 2. Бесскобочный GS1 DataMatrix / GS1-128: 01 + 14 цифр + 21 + SN
        if not gtin:
            m01 = re.search(r"(?:^|\x1d)01(\d{14})", s)
            if m01:
                btype = "GS1-128"
                gtin = m01.group(1)
                after_01 = s[m01.end():]
                m21 = re.match(r"^21([^\x1d\r\n]{1,30})", after_01)
                if m21:
                    sn = m21.group(1).strip()

        m10 = re.search(r"(?:\(10\)|\x1d10)([^\(\x1d\r\n]{1,20})", s)
        if m10:
            lot = m10.group(1).strip()

        m17 = re.search(r"(?:\(17\)|\x1d17)(\d{6})", s)
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
