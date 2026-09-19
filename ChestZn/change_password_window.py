"""
Окно смены пароля администратора.
Открывается из админки, если админ уже вошёл.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence


class ChangePasswordWindow(QDialog):
    """
    Окно смены пароля.
    accept() — если пароль успешно изменён.
    reject() — если отменили.
    """

    def __init__(self, store, login: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.login = login
        self.changed = False

        self.setWindowTitle("ChestZn · Смена пароля")
        self.setModal(True)
        self.setFixedSize(440, 400)
        self.setObjectName("changePasswordWindow")

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(12)

        # ---------- Заголовок ----------
        title = QLabel("СМЕНА ПАРОЛЯ")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            color: #e6edf3;
            font-size: 14px;
            font-weight: 600;
            letter-spacing: 2px;
        """)
        root.addWidget(title)

        subtitle = QLabel(f"Пользователь: {login}")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #8b97a6; font-size: 12px;")
        root.addWidget(subtitle)

        root.addSpacing(8)

        # ---------- Текущий пароль ----------
        label_cur = QLabel("Текущий пароль:")
        label_cur.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_cur)

        self.current_input = QLineEdit()
        self.current_input.setObjectName("pwdInput")
        self.current_input.setEchoMode(QLineEdit.Password)
        self.current_input.setPlaceholderText("Введите текущий пароль")
        self.current_input.setMinimumHeight(38)
        root.addWidget(self.current_input)

        # ---------- Новый пароль ----------
        label_new = QLabel("Новый пароль:")
        label_new.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_new)

        self.new_input = QLineEdit()
        self.new_input.setObjectName("pwdInput")
        self.new_input.setEchoMode(QLineEdit.Password)
        self.new_input.setPlaceholderText("Минимум 3 символа")
        self.new_input.setMinimumHeight(38)
        root.addWidget(self.new_input)

        # ---------- Повтор пароля ----------
        label_rep = QLabel("Повторите пароль:")
        label_rep.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_rep)

        self.repeat_input = QLineEdit()
        self.repeat_input.setObjectName("pwdInput")
        self.repeat_input.setEchoMode(QLineEdit.Password)
        self.repeat_input.setPlaceholderText("Повторите новый пароль")
        self.repeat_input.setMinimumHeight(38)
        root.addWidget(self.repeat_input)

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
        self.btn_cancel.setMinimumHeight(40)
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("Сменить пароль")
        self.btn_save.setObjectName("saveButton")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.try_change)

        buttons.addWidget(self.btn_cancel, stretch=1)
        buttons.addWidget(self.btn_save, stretch=2)
        root.addLayout(buttons)

        root.addStretch(1)

        # ---------- Горячие клавиши ----------
        self.current_input.returnPressed.connect(self._focus_new)
        self.new_input.returnPressed.connect(self._focus_repeat)
        self.repeat_input.returnPressed.connect(self.try_change)
        QShortcut(QKeySequence("Escape"), self, self.reject)

        # ---------- Стили ----------
        self._apply_style()

        # ---------- Фокус ----------
        QTimer.singleShot(0, self.current_input.setFocus)

    # ------------------------------------------------------------------
    def _focus_new(self):
        self.new_input.setFocus()

    def _focus_repeat(self):
        self.repeat_input.setFocus()

    # ------------------------------------------------------------------
    def _apply_style(self):
        self.setStyleSheet("""
            QDialog#changePasswordWindow {
                background-color: #0f1419;
            }
            QLineEdit#pwdInput {
                background-color: #1c2530;
                border: 1px solid #2a3542;
                border-radius: 8px;
                padding: 8px 14px;
                color: #e6edf3;
                font-size: 13px;
            }
            QLineEdit#pwdInput:focus {
                border: 2px solid #3b82f6;
                padding: 7px 13px;
            }
            QPushButton#saveButton {
                background-color: #3b82f6;
                border: none;
                border-radius: 8px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#saveButton:hover {
                background-color: #2563eb;
            }
            QPushButton#saveButton:pressed {
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
    def try_change(self):
        current = self.current_input.text()
        new_pwd = self.new_input.text()
        repeat  = self.repeat_input.text()

        # 1. Текущий пароль — обязателен
        if not current:
            self.error_label.setText("Введите текущий пароль")
            self.current_input.setFocus()
            return

        # 2. Проверяем текущий пароль
        user = self.store.check_login(self.login, current)
        if user is None:
            self.error_label.setText("Неверный текущий пароль")
            self.current_input.clear()
            self.current_input.setFocus()
            self.store.log("Неудачная смена пароля",
                           operator=self.login, result="отказ")
            return

        # 3. Новый пароль — минимум 3 символа
        if len(new_pwd) < 3:
            self.error_label.setText("Новый пароль — минимум 3 символа")
            self.new_input.setFocus()
            return

        # 4. Новый пароль не должен совпадать с текущим
        if new_pwd == current:
            self.error_label.setText("Новый пароль совпадает с текущим")
            self.new_input.setFocus()
            return

        # 5. Повтор должен совпадать
        if new_pwd != repeat:
            self.error_label.setText("Пароли не совпадают")
            self.repeat_input.clear()
            self.repeat_input.setFocus()
            return

        # 6. Меняем пароль
        self.store.set_password(self.login, new_pwd)
        self.store.log("Смена пароля",
                       operator=self.login, result="успех")

        self.changed = True
        print(f"[ChestZn] Пароль пользователя {self.login!r} изменён")
        self.accept()