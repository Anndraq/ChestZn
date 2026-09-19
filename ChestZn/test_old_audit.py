"""Тестовая вставка старой записи в audit."""
import db


def insert_old():
    """Добавить запись от 2024-01-01 для проверки очистки."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO audit "
            "(time, action, point, operator, gtin, sn, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("10:00:00", "Тест старая", "T-01", "Тестов",
             "04600123456782", "SNTEST", "успех", "2024-01-01 10:00:00")
        )
    print("OK: добавлена старая запись от 2024-01-01")


def show_stats():
    """Показать статистику по audit."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(created_at) AS first, "
            "       MAX(created_at) AS last, "
            "       COUNT(*) AS c FROM audit"
        ).fetchone()
        print(f"Всего записей: {row['c']}")
        print(f"Самая старая: {row['first']}")
        print(f"Самая новая:  {row['last']}")

        old_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit "
            "WHERE created_at < datetime('now', 'localtime', '-365 days')"
        ).fetchone()["c"]
        print(f"Старше 365 дней: {old_count}")


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "show"
    if action == "insert":
        insert_old()
    show_stats()