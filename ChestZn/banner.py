"""
Баннер предупреждений для главного окна.
Жёлтая полоса сверху с кнопкой «Подробнее».
Без кнопки закрытия — предупреждение нельзя убрать.
"""
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy
)
from PySide6.QtCore import Qt, Signal


class WarningBanner(QFrame):
    """
    Жёлтая полоса с предупреждениями.
    Показывается, если при старте остались предупреждения
    (например, принтер не настроен, Ghostscript не найден).
    """

    details_clicked = Signal()

    def __init__(self, problems: list, parent=None):
        super().__init__(parent)
        self.problems = problems
        self.setObjectName("warningBanner")
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # ---------- Layout ----------
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 4, 16, 4)
        h.setSpacing(10)

        # Иконка
        icon = QLabel("⚠")
        icon.setObjectName("bannerIcon")
        h.addWidget(icon)

        # Текст
        text = self._make_text(problems)
        self.label = QLabel(text)
        self.label.setObjectName("bannerLabel")
        self.label.setWordWrap(False)
        h.addWidget(self.label, stretch=1)

        # Кнопка «Подробнее»
        self.btn_details = QPushButton("Подробнее")
        self.btn_details.setObjectName("bannerButton")
        self.btn_details.setCursor(Qt.PointingHandCursor)
        self.btn_details.clicked.connect(self.details_clicked.emit)
        h.addWidget(self.btn_details)

    # ------------------------------------------------------------------
    def _make_text(self, problems: list) -> str:
        """Составить текст баннера из списка проблем."""
        active = [p for p in problems if p.get("status") != "ok"]
        if not active:
            return ""

        if len(active) == 1:
            p = active[0]
            return f"{p['name']}: {p['message']}"
        else:
            names = ", ".join(p["name"] for p in active)
            return f"Проблемы: {names}"

    # ------------------------------------------------------------------
    def update_problems(self, problems: list):
        """Обновить текст при изменении проблем."""
        self.problems = problems
        self.label.setText(self._make_text(problems))