"""
Проверка прав администратора перед опасными действиями.
Открывает LoginWindow и возвращает True/False.
"""
from PySide6.QtWidgets import QApplication

from store import Store
from login_window import LoginWindow


ADMIN_LOGIN = "admin"


def require_admin(store: Store, parent=None, reason: str = "") -> bool:
    """
    Запросить пароль администратора.
    Возвращает True, если админ вошёл, иначе False.

    store — текущий Store (используется для check_login).
    parent — родительское окно (для модальности).
    reason — причина запроса (для логов).
    """
    login_window = LoginWindow(store, parent)
    result = login_window.exec()

    if result == LoginWindow.Accepted and login_window.user:
        if reason:
            store.log(f"Доступ админа: {reason}",
                      operator=login_window.user.get("fio"),
                      result="успех")
        print(f"[admin_guard] доступ разрешён: {reason or 'действие'}")
        return True

    if reason:
        store.log(f"Отказ доступа: {reason}",
                  operator="—",
                  result="отказ")
    print(f"[admin_guard] доступ запрещён: {reason or 'действие'}")
    return False