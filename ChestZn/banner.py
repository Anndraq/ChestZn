"""
banner.py — Верхний баннер предупреждений при наличии проблем с оборудованием или конфигурацией.
"""
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy


class WarningBanner(QFrame):
    """Компактный баннер предупреждений в верхней части главного окна."""

    details_clicked = Signal()

    def __init__(self, problems: Optional[List[Dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("warningBanner")
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(10)

        self.icon_label = QLabel("⚠️")
        self.icon_label.setObjectName("bannerIcon")
        self.icon_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(self.icon_label)

        self.text_label = QLabel()
        self.text_label.setObjectName("bannerLabel")
        self.text_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.text_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.text_label, stretch=1)

        self.btn_details = QPushButton("Подробнее")
        self.btn_details.setObjectName("bannerButton")
        self.btn_details.setCursor(Qt.PointingHandCursor)
        self.btn_details.setFixedHeight(28)
        self.btn_details.setMinimumWidth(104)
        self.btn_details.clicked.connect(self.details_clicked.emit)
        layout.addWidget(self.btn_details, alignment=Qt.AlignVCenter)

        self.update_problems(problems or [])

    def update_problems(self, problems: List[Dict[str, Any]]) -> None:
        """Обновляет текст баннера по списку активных проблем."""
        active = [p for p in (problems or []) if p.get("status") != "ok"]
        if not active:
            self.text_label.setText("Обнаружены предупреждения")
            return

        names = []
        for p in active:
            name = str(p.get("name") or p.get("title") or "").strip()
            if name and name not in names:
                names.append(name)

        if names:
            self.text_label.setText("Проблемы: " + ", ".join(names))
        else:
            self.text_label.setText(f"Обнаружено проблем: {len(active)}")
