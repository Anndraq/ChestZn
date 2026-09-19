"""Просмотр содержимого БД ChestZn."""
import sys
import sqlite3
import db


def open_db():
    conn = sqlite3.connect(str(db.DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn


def show(title: str, rows, limit=None):
    print(f"\n=== {title} ===")
    rows = list(rows)
    if limit:
        rows = rows[:limit]
    if not rows:
        print("(пусто)")
        return
    for r in rows:
        print(dict(r))


def main():
    table = sys.argv[1] if len(sys.argv) > 1 else "all"
    conn = open_db()

    if table in ("all", "tables"):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print("Таблицы:", [r[0] for r in rows])

    if table in ("all", "counters"):
        show("COUNTERS", conn.execute("SELECT * FROM counters"))

    if table in ("all", "pool"):
        show("POOL (по GTIN)", conn.execute(
            "SELECT gtin, COUNT(*) AS total, "
            "SUM(used) AS used, SUM(printed) AS printed "
            "FROM pool_codes GROUP BY gtin"
        ))

    if table in ("all", "queue"):
        show("QUEUE", conn.execute("SELECT * FROM queue"))

    if table in ("all", "audit"):
        show("AUDIT (последние 20)", conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT 20"
        ))

    if table in ("all", "gtin"):
        show("GTIN_MAP", conn.execute("SELECT * FROM gtin_map"))

    conn.close()


if __name__ == "__main__":
    main()