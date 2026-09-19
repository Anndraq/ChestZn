"""
Окно восстановления доступа администратора.
Показывает секретный вопрос, проверяет ответ,
позволяет задать новый пароль.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence


class RecoveryWindow(QDialog):
    """
    Окно восстановления пароля.
    accept() — если ответ верный и пароль изменён.
    reject() — если отменили.
    """

    def __init__(self, store, login: str = "admin", parent=None):
        super().__init__(parent)
        self.store = store
        self.login = login
        self.new_password_set = False

        self.setWindowTitle("ChestZn · Восстановление доступа")
        self.setModal(True)
        self.setFixedSize(460, 480)
        self.setObjectName("recoveryWindow")

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(12)

        # ---------- Заголовок ----------
        title = QLabel("ВОССТАНОВЛЕНИЕ ДОСТУПА")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            color: #e6edf3;
            font-size: 14px;
            font-weight: 600;
            letter-spacing: 2px;
        """)
        root.addWidget(title)

        subtitle = QLabel("Ответьте на секретный вопрос\nи задайте новый пароль")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #8b97a6; font-size: 12px;")
        root.addWidget(subtitle)

        root.addSpacing(6)

        # ---------- Вопрос ----------
        question = store.get_security_question(login)
        if not question:
            question = "(вопрос не настроен)"

        label_q = QLabel(question)
        label_q.setAlignment(Qt.AlignCenter)
        label_q.setWordWrap(True)
        label_q.setStyleSheet("""
            color: #3b82f6;
            font-size: 13px;
            font-weight: 600;
            padding: 10px;
            background-color: #1c2530;
            border-radius: 8px;
            border: 1px solid #2a3542;
        """)
        root.addWidget(label_q)

        # ---------- Ответ ----------
        label_a = QLabel("Ответ:")
        label_a.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_a)

        self.answer_input = QLineEdit()
        self.answer_input.setObjectName("recoveryInput")
        self.answer_input.setPlaceholderText("Ваш ответ (учитывается регистр)")
        self.answer_input.setMinimumHeight(38)
        root.addWidget(self.answer_input)

        # ---------- Новый пароль ----------
        label_p1 = QLabel("Новый пароль:")
        label_p1.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_p1)

        self.password_input = QLineEdit()
        self.password_input.setObjectName("recoveryInput")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Минимум 3 символа")
        self.password_input.setMinimumHeight(38)
        root.addWidget(self.password_input)

        # ---------- Повтор пароля ----------
        label_p2 = QLabel("Повторите пароль:")
        label_p2.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_p2)

        self.password_repeat_input = QLineEdit()
        self.password_repeat_input.setObjectName("recoveryInput")
        self.password_repeat_input.setEchoMode(QLineEdit.Password)
        self.password_repeat_input.setPlaceholderText("Повторите новый пароль")
        self.password_repeat_input.setMinimumHeight(38)
        root.addWidget(self.password_repeat_input)

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

        self.btn_reset = QPushButton("Сбросить пароль")
        self.btn_reset.setObjectName("resetButton")
        self.btn_reset.setMinimumHeight(40)
        self.btn_reset.setDefault(True)
        self.btn_reset.clicked.connect(self.try_reset)

        buttons.addWidget(self.btn_cancel, stretch=1)
        buttons.addWidget(self.btn_reset, stretch=2)
        root.addLayout(buttons)

        root.addStretch(1)

        # ---------- Горячие клавиши ----------
        self.answer_input.returnPressed.connect(self._focus_password)
        self.password_input.returnPressed.connect(self._focus_repeat)
        self.password_repeat_input.returnPressed.connect(self.try_reset)
        QShortcut(QKeySequence("Escape"), self, self.reject)

        # ---------- Стили ----------
        self._apply_style()

        # ---------- Фокус ----------
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.answer_input.setFocus)

    # ------------------------------------------------------------------
    def _focus_password(self):
        self.password_input.setFocus()

    def _focus_repeat(self):
        self.password_repeat_input.setFocus()

    # ------------------------------------------------------------------
    def _apply_style(self):
        self.setStyleSheet("""
            QDialog#recoveryWindow {
                background-color: #0f1419;
            }
            QLineEdit#recoveryInput {
                background-color: #1c2530;
                border: 1px solid #2a3542;
                border-radius: 8px;
                padding: 8px 14px;
                color: #e6edf3;
                font-size: 13px;
            }
            QLineEdit#recoveryInput:focus {
                border: 2px solid #3b82f6;
                padding: 7px 13px;
            }
            QPushButton#resetButton {
                background-color: #3b82f6;
                border: none;
                border-radius: 8px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#resetButton:hover {
                background-color: #2563eb;
            }
            QPushButton#resetButton:pressed {
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
    def try_reset(self):
        answer = self.answer_input.text()
        new_password = self.password_input.text()
        repeat_password = self.password_repeat_input.text()

        # 1. Проверка ответа
        if not answer.strip():
            self.error_label.setText("Введите ответ на секретный вопрос")
            self.answer_input.setFocus()
            return

        if not self.store.check_security_answer(self.login, answer):
            self.error_label.setText("Неверный ответ")
            self.answer_input.clear()
            self.answer_input.setFocus()
            self.store.log("Неудачная попытка восстановления",
                           operator=self.login, result="отказ")
            return

        # 2. Проверка нового пароля
        if len(new_password) < 3:
            self.error_label.setText("Пароль должен быть не короче 3 символов")
            self.password_input.setFocus()
            return

        if new_password != repeat_password:
            self.error_label.setText("Пароли не совпадают")
            self.password_repeat_input.clear()
            self.password_repeat_input.setFocus()
            return

        # 3. Сбрасываем пароль
        self.store.reset_password(self.login, new_password)
        self.store.log("Восстановление пароля",
                       operator=self.login, result="успех")

        self.new_password_set = True
        print(f"[ChestZn] Пароль для {self.login!r} изменён через восстановление")
        self.accept()