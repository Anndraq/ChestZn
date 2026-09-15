"""Точка входа desktop-приложения (PySide6)."""
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QFrame
)
from PySide6.QtCore import Qt

from store import Store
from operator_window import OperatorWindow
from admin_window import AdminWindow
from styles import APP_QSS


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChestZn · Маркировка")
        self.resize(1360, 900)

        self.store = Store()

        # ---------- Корневой виджет ----------
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---------- Шапка ----------
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(50)

        h = QHBoxLayout(header)
        h.setContentsMargins(20, 8, 20, 8)
        h.setSpacing(12)

        brand = QLabel("📦  CHESTZN · МАРКИРОВКА")
        brand.setObjectName("brand")
        h.addWidget(brand)

        role = QLabel("DESKTOP")
        role.setObjectName("brandRole")
        h.addWidget(role)

        h.addStretch(1)

        meta_online = QLabel("Система:")
        meta_online.setObjectName("meta")
        h.addWidget(meta_online)

        ok = QLabel("● онлайн")
        ok.setObjectName("okDot")
        h.addWidget(ok)

        meta_ver = QLabel("Версия: v0.1")
        meta_ver.setObjectName("meta")
        h.addWidget(meta_ver)

        avatar = QLabel("АД")
        avatar.setFixedSize(26, 26)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet("""
            background:#232e3c; color:#e6edf3;
            border:1px solid #3a4a5c; border-radius:13px;
            font-size:11px;
        """)
        h.addWidget(avatar)

        root_layout.addWidget(header)

        # ---------- Вкладки ----------
        tabs = QTabWidget()
        self.operator = OperatorWindow(self.store)
        self.admin    = AdminWindow(self.store)
        tabs.addTab(self.operator, "Оператор")
        tabs.addTab(self.admin,    "Админ")
        tabs.currentChanged.connect(self._on_tab_changed)

        root_layout.addWidget(tabs, stretch=1)

        self.setCentralWidget(root)

        # Отладка: показать, что вообще создалось
        print("[ChestZn] MainWindow создан, размер:", self.size())
        print("[ChestZn] вкладок:", tabs.count())

    def _on_tab_changed(self, idx: int):
        if idx == 1:
            self.admin.switch("dashboard")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)

    win = MainWindow()
    win.show()

    print("[ChestZn] окно показано, visible:", win.isVisible())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()