"""Простой toast-виджет в правом верхнем углу родителя."""
from PySide6.QtWidgets import QLabel, QWidget
from PySide6.QtCore import QTimer, Qt


class Toast(QLabel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, kind: str = "", duration: int = 2000):
        self.setText(text)
        color = {
            "ok":   ("#22c55e", "rgba(34,197,94,.12)"),
            "err":  ("#ef4444", "rgba(239,68,68,.12)"),
            "warn": ("#f59e0b", "rgba(245,158,11,.12)"),
            "":     ("#e6edf3", "#161c24"),
        }.get(kind, ("#e6edf3", "#161c24"))
        fg, bg = color

        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {fg};
                border-radius: 8px;
                padding: 10px 16px;
                font-size: 12.5px;
            }}
        """)
        self.adjustSize()
        self.setFixedWidth(min(360, self.sizeHint().width() + 20))

        parent = self.parentWidget()
        self.move(parent.width() - self.width() - 20, 70)
        self.show()
        self.raise_()
        self._timer.start(duration)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        parent = self.parentWidget()
        if parent:
            self.move(parent.width() - self.width() - 20, 70)