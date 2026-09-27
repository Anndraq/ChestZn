"""Сброс таблиц шаблонов и привязок."""
import db

with db.get_conn() as conn:
    conn.execute("DELETE FROM template_bindings")
    conn.execute("DELETE FROM label_templates")
    print("OK: таблицы label_templates и template_bindings очищены")