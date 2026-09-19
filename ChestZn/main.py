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
from login_window import LoginWindow


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChestZn · Маркировка")
        self.resize(1360, 900)

        self.store = Store()

        # ---------- Автоопределение принтера ----------
        from printer import autodetect, load_config, save_config
        cfg = load_config()
        if not cfg.get("printer_name") and not cfg.get("printer_ip"):
            result = autodetect()
            if result["suggested"]:
                s = result["suggested"]
                cfg["printer_name"] = s["name"]
                cfg["printer_lang"] = s["lang"]
                save_config(cfg)
                print(f"[ChestZn] Принтер определён: {s['name']} ({s['lang']})")
            else:
                print("[ChestZn] Принтеры не найдены — настройте вручную в админке")
        else:
            print(f"[ChestZn] Принтер из настроек: {cfg['printer_name']} ({cfg['printer_lang']})")

        # ---------- Флаг: админ авторизован? ----------
        self.admin_authenticated = False

        # ---------- Оверлей для размытия ----------
        self._overlay = None

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
        self.tabs = QTabWidget()
        self.operator = OperatorWindow(self.store)
        self.admin    = AdminWindow(self.store)

        self.tabs.addTab(self.operator, "Оператор")
        self.tabs.addTab(self.admin,    "Админ")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        root_layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(root)

        print("[ChestZn] MainWindow создан, размер:", self.size())
        print("[ChestZn] вкладок:", self.tabs.count())

    # ------------------------------------------------------------------
    def _on_tab_changed(self, idx: int):
        """Переключение вкладок. При клике на «Админ» — требуем пароль."""
        if idx != 1:
            return

        # Уже авторизован — просто переключаемся
        if self.admin_authenticated:
            self.admin.switch("dashboard")
            return

        # Даём окну отрисоваться перед скриншотом
        QApplication.processEvents()

        # Размываем главное окно
        self._set_blur(True)

        # Даём оверлею отрисоваться
        QApplication.processEvents()

        # Показываем окно входа
        login = LoginWindow(self.store, self)
        result = login.exec()

        # Убираем размытие
        self._set_blur(False)

        if result == LoginWindow.Accepted and login.user:
            self.admin_authenticated = True
            self.admin.switch("dashboard")
            print(f"[ChestZn] Админ авторизован: {login.user['fio']}")
        else:
            # Отмена — возвращаем Оператора
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(0)
            self.tabs.blockSignals(False)
            print("[ChestZn] Вход в админку отменён")

    # ------------------------------------------------------------------
    def _set_blur(self, enabled: bool):
        """Размытие главного окна через скриншот + overlay."""
        if enabled:
            self._overlay = self._create_blur_overlay()
        else:
            if self._overlay is not None:
                self._overlay.close()
                self._overlay.deleteLater()
                self._overlay = None

    def _create_blur_overlay(self):
        """Создать оверлей с размытой копией окна."""
        from PySide6.QtWidgets import (
            QWidget, QLabel, QGraphicsBlurEffect,
            QGraphicsScene, QGraphicsPixmapItem
        )
        from PySide6.QtGui import QPixmap, QPainter
        from PySide6.QtCore import Qt, QRectF

        # 1. Скриншот главного окна
        screen = self.grab()
        w, h = screen.width(), screen.height()

        # 2. Размываем картинку
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(18)
        blur.setBlurHints(QGraphicsBlurEffect.QualityHint)

        scene = QGraphicsScene()
        pixmap_item = QGraphicsPixmapItem(screen)
        pixmap_item.setGraphicsEffect(blur)
        scene.addItem(pixmap_item)

        blurred_pixmap = QPixmap(w, h)
        blurred_pixmap.fill(Qt.transparent)
        painter = QPainter(blurred_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        scene.render(painter, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
        painter.end()

        # 3. Оверлей поверх окна
        overlay = QWidget(self)
        overlay.setGeometry(self.rect())
        overlay.setStyleSheet("background: transparent;")

        label = QLabel(overlay)
        label.setPixmap(blurred_pixmap)
        label.setGeometry(0, 0, w, h)

        overlay.show()
        overlay.raise_()
        return overlay


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)

    win = MainWindow()
    win.show()

    print("[ChestZn] окно показано, visible:", win.isVisible())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()