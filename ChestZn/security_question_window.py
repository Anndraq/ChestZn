"""
Окно настройки секретного вопроса.
Показывается при первом входе админа, если вопрос ещё не задан.
Принудительное — без кнопки «Позже».
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence


# ---------- Список вопросов ----------
QUESTIONS = [
    "Город, где вы впервые увидели море",
    "Какой ваш любимый фильм из детства?",
    "На каком этаже вы жили в детстве?",
    "Кличка первого питомца",
    "Марка первой машины",
    "Любимое блюдо в детстве",
    "Имя первого учителя",
]

CUSTOM_OPTION = "— Свой вопрос —"


class SecurityQuestionWindow(QDialog):
    """
    Модальное окно настройки секретного вопроса.
    accept() — если вопрос и ответ сохранены.
    reject() — если пользователь закрыл окно (но админка не пустит).
    """

    def __init__(self, store, login: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.login = login

        self.setWindowTitle("ChestZn · Настройка восстановления")
        self.setModal(True)
        self.setFixedSize(520, 460)
        self.setObjectName("questionWindow")

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(14)

        # ---------- Заголовок ----------
        title = QLabel("НАСТРОЙКА ВОССТАНОВЛЕНИЯ")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            color: #e6edf3;
            font-size: 14px;
            font-weight: 600;
            letter-spacing: 2px;
        """)
        root.addWidget(title)

        subtitle = QLabel(
            "Если вы забудете пароль, потребуется ответ\n"
            "на секретный вопрос. Запомните его."
        )
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #8b97a6; font-size: 12px;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        root.addSpacing(10)

        # ---------- Выбор вопроса ----------
        label_q = QLabel("Выберите вопрос:")
        label_q.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_q)

        self.question_combo = QComboBox()
        self.question_combo.setObjectName("questionInput")
        self.question_combo.setMinimumHeight(38)
        self.question_combo.addItems(QUESTIONS + [CUSTOM_OPTION])
        self.question_combo.currentIndexChanged.connect(self._on_question_changed)
        root.addWidget(self.question_combo)

        # ---------- Свой вопрос (скрыто по умолчанию) ----------
        self.custom_label = QLabel("Свой вопрос:")
        self.custom_label.setStyleSheet("color: #e6edf3; font-size: 12px;")
        self.custom_label.hide()
        root.addWidget(self.custom_label)

        self.custom_input = QLineEdit()
        self.custom_input.setObjectName("questionInput")
        self.custom_input.setPlaceholderText("Введите свой вопрос")
        self.custom_input.setMinimumHeight(38)
        self.custom_input.hide()
        root.addWidget(self.custom_input)

        # ---------- Ответ ----------
        label_a = QLabel("Ответ:")
        label_a.setStyleSheet("color: #e6edf3; font-size: 12px;")
        root.addWidget(label_a)

        self.answer_input = QLineEdit()
        self.answer_input.setObjectName("questionInput")
        self.answer_input.setPlaceholderText("Ваш ответ (учитывается регистр)")
        self.answer_input.setMinimumHeight(38)
        root.addWidget(self.answer_input)

        # ---------- Ошибка ----------
        self.error_label = QLabel("")
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setStyleSheet("color: #ef4444; font-size: 12px;")
        self.error_label.setMinimumHeight(18)
        root.addWidget(self.error_label)

        # ---------- Кнопка ----------
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setObjectName("saveButton")
        self.btn_save.setMinimumHeight(42)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self.try_save)
        root.addWidget(self.btn_save)

        root.addStretch(1)

        # ---------- Горячие клавиши ----------
        self.answer_input.returnPressed.connect(self.try_save)
        self.custom_input.returnPressed.connect(self.try_save)
        # Escape НЕ закрывает окно — принудительно
        # QShortcut(QKeySequence("Escape"), self, self.reject)   # закомментировано

        # ---------- Стили ----------
        self._apply_style()

        # ---------- Фокус ----------
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.question_combo.setFocus)

    # ------------------------------------------------------------------
    def _on_question_changed(self):
        """Показать/скрыть поле «Свой вопрос»."""
        is_custom = self.question_combo.currentText() == CUSTOM_OPTION
        self.custom_label.setVisible(is_custom)
        self.custom_input.setVisible(is_custom)
        if is_custom:
            self.custom_input.setFocus()

    # ------------------------------------------------------------------
    def _apply_style(self):
        self.setStyleSheet("""
            QDialog#questionWindow {
                background-color: #0f1419;
            }
            QLineEdit#questionInput, QComboBox#questionInput {
                background-color: #1c2530;
                border: 1px solid #2a3542;
                border-radius: 8px;
                padding: 8px 14px;
                color: #e6edf3;
                font-size: 13px;
            }
            QLineEdit#questionInput:focus, QComboBox#questionInput:focus {
                border: 2px solid #3b82f6;
                padding: 7px 13px;
            }
            QComboBox#questionInput::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                background-color: #1c2530;
                color: #e6edf3;
                selection-background-color: #3b82f6;
                border: 1px solid #2a3542;
            }
            QPushButton#saveButton {
                background-color: #3b82f6;
                border: none;
                border-radius: 8px;
                color: #ffffff;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#saveButton:hover {
                background-color: #2563eb;
            }
            QPushButton#saveButton:pressed {
                background-color: #1d4ed8;
            }
        """)

    # ------------------------------------------------------------------
    def try_save(self):
        # 1. Определяем вопрос
        selected = self.question_combo.currentText()
        if selected == CUSTOM_OPTION:
            question = self.custom_input.text().strip()
            if not question:
                self.error_label.setText("Введите свой вопрос")
                self.custom_input.setFocus()
                return
        else:
            question = selected

        # 2. Ответ
        answer = self.answer_input.text().strip()
        if not answer:
            self.error_label.setText("Введите ответ")
            self.answer_input.setFocus()
            return

        if len(answer) < 2:
            self.error_label.setText("Ответ слишком короткий")
            self.answer_input.setFocus()
            return

        # 3. Сохраняем
        self.store.set_security_question(self.login, question, answer)
        self.store.log("Настроен секретный вопрос",
                       operator=self.login, result="успех")

        print(f"[ChestZn] Секретный вопрос настроен: {question!r}")
        self.accept()