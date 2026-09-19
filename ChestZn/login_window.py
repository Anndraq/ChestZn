"""
Окно входа администратора ChestZn.
Оператор работает без пароля — окно нужно только для доступа в админку.

Логин: admin (подставляется автоматически).
Пароль: проверяется через store.check_login.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence


ADMIN_LOGIN = "admin"     # логин подставляется автоматически


class LoginWindow(QDialog):
    """
    Модальное окно входа администратора.
    accept() — если пароль верный.
    reject() — если отменили (Escape, «Отмена», крестик).
    """

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.user = None          # сюда сохранится админ после успешного входа

        self.setWindowTitle("ChestZn · Вход администратора")
        self.setModal(True)
        self.setFixedSize(380, 260)
        self.setObjectName("loginWindow")

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(14)

        # ---------- Заголовок ----------
        title = QLabel("ВХОД ДЛЯ АДМИНИСТРАТОРА")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            color: #e6edf3;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 2px;
        """)
        root.addWidget(title)

        subtitle = QLabel("Введите пароль администратора")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #8b97a6; font-size: 12px;")
        root.addWidget(subtitle)

        root.addSpacing(6)

        # ---------- Поле пароля ----------
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Пароль")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumHeight(38)
        self.password_input.setObjectName("loginInput")
        root.addWidget(self.password_input)

        # ---------- Ошибка ----------
        self.error_label = QLabel("")
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setStyleSheet("color: #ef4444; font-size: 12px;")
        self.error_label.setMinimumHeight(18)
        root.addWidget(self.error_label)

        # ---------- Кнопки ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(10)

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setObjectName("cancelButton")
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.setAutoDefault(False)       # ← чтобы Enter не срабатывал на «Отмена»
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_login = QPushButton("Войти")
        self.btn_login.setObjectName("loginButton")
        self.btn_login.setMinimumHeight(38)
        self.btn_login.setDefault(True)             # ← Enter = «Войти»
        self.btn_login.setAutoDefault(True)
        self.btn_login.clicked.connect(self.try_login)

        buttons.addWidget(self.btn_cancel, stretch=1)
        buttons.addWidget(self.btn_login, stretch=2)

        root.addLayout(buttons)
        root.addStretch(1)

        # ---------- Горячие клавиши ----------
        self.password_input.returnPressed.connect(self.try_login)
        QShortcut(QKeySequence("Escape"), self, self.reject)

        # ---------- Стили ----------
        self._apply_style()

        # ---------- Фокус на поле пароля ----------
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.password_input.setFocus)

    # ------------------------------------------------------------------
    def _apply_style(self):
        self.setStyleSheet("""
            QDialog#loginWindow {
                background-color: #0f1419;
            }
            QLineEdit#loginInput {
                background-color: #1c2530;
                border: 1px solid #2a3542;
                border-radius: 8px;
                padding: 8px 14px;
                color: #e6edf3;
                font-size: 14px;
            }
            QLineEdit#loginInput:focus {
                border: 2px solid #3b82f6;
                padding: 7px 13px;
            }
            QPushButton#loginButton {
                background-color: #3b82f6;
                border: none;
                border-radius: 8px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#loginButton:hover {
                background-color: #2563eb;
            }
            QPushButton#loginButton:pressed {
                background-color: #1d4ed8;
            }
            QPushButton#cancelButton {
                background-color: transparent;
                border: 1px solid #2a3542;
                border-radius: 8px;
                color: #8b97a6;
                font-size: 13px;
            }
            QPushButton#cancelButton:hover {
                background-color: #1c2530;
                color: #e6edf3;
            }
        """)

    # ------------------------------------------------------------------
    def try_login(self):
        password = self.password_input.text()

        if not password:
            self.error_label.setText("Введите пароль")
            self.password_input.setFocus()
            return

        user = self.store.check_login(ADMIN_LOGIN, password)

        if user is None:
            self.error_label.setText("Неверный пароль")
            self.password_input.clear()
            self.password_input.setFocus()
            self.store.log("Неудачный вход в админку",
                           operator=ADMIN_LOGIN, result="отказ")
            return

        # Проверяем, что это действительно админ
        if user.get("role") != "Администратор":
            self.error_label.setText("Нет прав администратора")
            self.password_input.clear()
            self.password_input.setFocus()
            return

        # Успех
        self.user = user
        self.store.current_user = user
        self.store.log("Вход в админку",
                       operator=user.get("fio"), result="успех")
        self.accept()