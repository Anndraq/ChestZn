"""Точка входа desktop-приложения (PySide6)."""
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QFrame
)
from PySide6.QtCore import Qt

from logger import setup_logging, get_logger
setup_logging(debug=True)
log = get_logger("app")

from store import Store
from operator_window import OperatorWindow
from admin_window import AdminWindow
from styles import APP_QSS
from login_window import LoginWindow
from security_question_window import SecurityQuestionWindow


ADMIN_LOGIN = "admin"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChestZn · Маркировка")
        self.resize(1360, 900)

        log.info("Инициализация MainWindow")

        self.store = Store()
        log.info("Store инициализирован")

                # ---------- Защита прав папки ----------
        try:
            from security import apply_permissions_if_needed
            result = apply_permissions_if_needed()
            if result.get("ok") and not result.get("skipped"):
                log.info("Права папки: %s", result.get("info"))
            elif not result.get("ok"):
                log.warning("Права папки: %s", result.get("info"))
        except Exception as e:
            log.error("Настройка прав: исключение %s", e)

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
                log.info("Принтер определён: %s (%s)", s["name"], s["lang"])
            else:
                log.warning("Принтеры не найдены — настройте вручную в админке")
        else:
            log.info("Принтер из настроек: %s (%s)",
                     cfg["printer_name"], cfg["printer_lang"])

            # ---------- Автобэкап БД ----------
        try:
            from backup import auto_backup_if_needed
            result = auto_backup_if_needed()
            if result and result["ok"]:
                log.info("Автобэкап: %s", result["info"])
            elif result:
                log.warning("Автобэкап не удался: %s", result["info"])
        except Exception as e:
            log.error("Автобэкап: исключение %s", e)

                    # ---------- Автоочистка журнала ----------
        try:
            result = self.store.auto_cleanup_if_needed()
            if result and result["ok"]:
                log.info("Автоочистка журнала: %s", result["info"])
            elif result:
                log.warning("Автоочистка журнала: %s", result["info"])
        except Exception as e:
            log.error("Автоочистка журнала: исключение %s", e)

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

        log.info("MainWindow создан, размер: %s", self.size())
        log.info("Вкладок: %s", self.tabs.count())

    # ------------------------------------------------------------------
    def _on_tab_changed(self, idx: int):
        """Переключение вкладок. При клике на «Админ» — требуем пароль."""
        if idx != 1:
            return

        # Уже авторизован — просто переключаемся
        if self.admin_authenticated:
            self.admin.switch("dashboard")
            return

        log.info("Запрос доступа к админке")

        # Даём окну отрисоваться перед скриншотом
        QApplication.processEvents()

        # Размываем главное окно
        self._set_blur(True)

        # Даём оверлею отрисоваться
        QApplication.processEvents()

        # ---------- Шаг 1: окно входа ----------
        login = LoginWindow(self.store, self)
        result = login.exec()

        # Убираем размытие
        self._set_blur(False)

        if result != LoginWindow.Accepted or not login.user:
            # Отмена — возвращаем Оператора
            self._return_to_operator("Вход в админку отменён")
            return

        # Проверяем, что вошёл действительно админ
        if login.user.get("role") != "Администратор":
            self._return_to_operator("Нет прав администратора")
            return

        # ---------- Шаг 2: секретный вопрос ----------
        if not self.store.has_security_question(login.user["login"]):
            # Вопрос не настроен — принудительно настраиваем
            log.info("Секретный вопрос не настроен — принудительная настройка")

            QApplication.processEvents()
            self._set_blur(True)
            QApplication.processEvents()

            question_window = SecurityQuestionWindow(
                self.store, login.user["login"], self
            )
            q_result = question_window.exec()

            self._set_blur(False)

            if q_result != SecurityQuestionWindow.Accepted:
                # Не настроил — не пускаем в админку
                self._return_to_operator("Настройка восстановления отменена")
                return

            # Проверяем ещё раз на всякий случай
            if not self.store.has_security_question(login.user["login"]):
                self._return_to_operator("Настройка восстановления не завершена")
                return

        # ---------- Шаг 3: пускаем в админку ----------
        self.admin_authenticated = True
        self.admin.switch("dashboard")
        log.info("Админ авторизован: %s", login.user['fio'])

    # ------------------------------------------------------------------
    def _return_to_operator(self, message: str = ""):
        """Вернуться на вкладку Оператор."""
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        if message:
            log.info("%s", message)

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
    log.info("Запуск приложения")
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)

    # ---------- Глобальный обработчик исключений ----------
    from error_handler import install_exception_hook

    win = MainWindow()

    # Устанавливаем хук ПОСЛЕ создания окна (нужен store)
    install_exception_hook(store=win.store)

    win.show()

    log.info("Окно показано, visible: %s", win.isVisible())
    log.info("Приложение готово к работе")

    exit_code = app.exec()
    log.info("Приложение завершено, код выхода: %s", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()