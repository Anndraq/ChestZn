"""Точка входа desktop-приложения (PySide6)."""
import sys
import os
import json
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QFrame, QMessageBox
)
from PySide6.QtCore import Qt

import docs_helper
import startup_check
from startup_check import has_critical, has_warnings, check_all
from startup_problems_window import StartupProblemsWindow
from banner import WarningBanner

from store import Store
from operator_window import OperatorWindow
from admin_window import AdminWindow
from styles import get_qss  # Импортируем функцию получения темы
from login_window import LoginWindow
from security_question_window import SecurityQuestionWindow


ADMIN_LOGIN = "admin"


class MainWindow(QMainWindow):
    def __init__(self, initial_problems: list = None):
        super().__init__()
        self.setWindowTitle("ChestZn · Маркировка")
        self.resize(1360, 900)

        # ---------- Загрузка конфига ----------
        from printer import load_config, save_config, detect_point, autodetect
        cfg = load_config()

        # ---------- Определение точки ----------
        point = detect_point(cfg)
        if not point:
            # Не задана — оставляем как есть, оператор увидит «T-01»
            # (админ может настроить точку в config.json)
            point = "T-01"
            print(f"[ChestZn] Точка не задана в config.json, "
                  f"используется по умолчанию: {point}")
        else:
            print(f"[ChestZn] Точка определена: {point}")

        # ---------- Создание Store ----------
        self.store = Store()
        self.store.current_point = point

        # ---------- Оператор по точке ----------
        operator = self._operator_for_point(point)
        self.store.current_operator = operator
        print(f"[ChestZn] Оператор: {operator}")

        # ---------- Автоопределение принтера ----------
        if not cfg.get("printer_name") and not cfg.get("printer_ip"):
            # Общий принтер не задан — попробуем автоопределить
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
            print(f"[ChestZn] Общий принтер из настроек: "
                  f"{cfg.get('printer_name')} ({cfg.get('printer_lang')})")

        # Проверим, есть ли принтер для текущей точки
        printers = cfg.get("printers") or {}
        if point in printers:
            p = printers[point]
            print(f"[ChestZn] Принтер для точки {point}: "
                  f"{p.get('printer_name', '?')} "
                  f"({p.get('printer_lang', 'auto')})")

        # ---------- Флаг: админ авторизован? ----------
        self.admin_authenticated = False

        # ---------- Оверлей для размытия ----------
        self._overlay = None

        # ---------- Проблемы при старте ----------
        self._initial_problems = initial_problems or []

        # ---------- Корневой виджет ----------
        root = QWidget()
        self._root_layout = QVBoxLayout(root)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        # ---------- Баннер предупреждений ----------
        self.banner = None
        if self._initial_problems:
            warnings_only = [
                p for p in self._initial_problems
                if p.get("status") != "ok"
            ]
            if warnings_only:
                self.banner = WarningBanner(warnings_only)
                self.banner.details_clicked.connect(self.show_problems_window)
                self._root_layout.addWidget(self.banner)

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

        h.addSpacing(20)

        # Точка в шапке
        point_badge = QLabel(f"● {point}")
        point_badge.setObjectName("brandRole")
        point_badge.setStyleSheet(
            "background:rgba(59,130,246,.18); color:#93c5fd; "
            "padding:3px 10px; border-radius:4px; "
            "font-size:11px; font-weight:600;"
        )
        h.addWidget(point_badge)

        # Название точки
        point_name = self._point_name(point)
        if point_name:
            point_label = QLabel(point_name)
            point_label.setStyleSheet("color:#8b97a6; font-size:12px;")
            h.addWidget(point_label)

        h.addStretch(1)

        meta_online = QLabel("Система:")
        meta_online.setObjectName("meta")
        h.addWidget(meta_online)

        ok = QLabel("● онлайн")
        ok.setObjectName("okDot")
        h.addWidget(ok)

        meta_ver = QLabel("Версия: v0.2")
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

        self._root_layout.addWidget(header)

        # ---------- Вкладки ----------
        self.tabs = QTabWidget()
        self.operator = OperatorWindow(self.store)
        self.admin    = AdminWindow(self.store)

        self.tabs.addTab(self.operator, "Оператор")
        self.tabs.addTab(self.admin,    "Админ")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._root_layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(root)

        print("[ChestZn] MainWindow создан, размер:", self.size())
        print("[ChestZn] вкладок:", self.tabs.count())
        print(f"[ChestZn] Точка работы: {point} · Оператор: {operator}")

    # ------------------------------------------------------------------
    def _point_name(self, point_id: str) -> str:
        """Название точки по ID."""
        for p in self.store.points:
            if p["id"] == point_id:
                return p["name"]
        return ""

    def _operator_for_point(self, point_id: str) -> str:
        """Найти ФИО оператора по точке."""
        for p in self.store.points:
            if p["id"] == point_id:
                return p.get("operator") or "Оператор"
        return "Оператор"

    # ------------------------------------------------------------------
    def show_problems_window(self):
        """Показать окно проблем."""
        win = StartupProblemsWindow(self.store, self)
        win.problems_changed.connect(self.update_banner)
        win.exec()
        self.update_banner(win.problems)

    # ------------------------------------------------------------------
    def update_banner(self, problems: list):
        """Обновить или скрыть баннер."""
        warnings_only = [p for p in problems if p.get("status") != "ok"]

        if warnings_only:
            if self.banner is None:
                self.banner = WarningBanner(warnings_only)
                self.banner.details_clicked.connect(self.show_problems_window)
                self._root_layout.insertWidget(0, self.banner)
            else:
                self.banner.update_problems(warnings_only)
                self.banner.show()
        else:
            if self.banner is not None:
                self.banner.hide()

    # ------------------------------------------------------------------
    def _on_tab_changed(self, idx: int):
        """При клике на «Админ» — требуем пароль."""
        if idx != 1:
            return

        if self.admin_authenticated:
            self.admin.switch("dashboard")
            return

        QApplication.processEvents()
        self._set_blur(True)
        QApplication.processEvents()

        login = LoginWindow(self.store, self)
        result = login.exec()

        self._set_blur(False)

        if result != LoginWindow.Accepted or not login.user:
            self._return_to_operator("Вход в админку отменён")
            return

        if login.user.get("role") != "Администратор":
            self._return_to_operator("Нет прав администратора")
            return

        # ---------- Секретный вопрос ----------
        if not self.store.has_security_question(login.user["login"]):
            print("[ChestZn] Секретный вопрос не настроен — принудительная настройка")

            QApplication.processEvents()
            self._set_blur(True)
            QApplication.processEvents()

            question_window = SecurityQuestionWindow(
                self.store, login.user["login"], self
            )
            q_result = question_window.exec()

            self._set_blur(False)

            if q_result != SecurityQuestionWindow.Accepted:
                self._return_to_operator("Настройка восстановления отменена")
                return

            if not self.store.has_security_question(login.user["login"]):
                self._return_to_operator("Настройка восстановления не завершена")
                return

        # ---------- Пускаем в админку ----------
        self.admin_authenticated = True
        self.admin.switch("dashboard")
        print(f"[ChestZn] Админ авторизован: {login.user['fio']}")

    # ------------------------------------------------------------------
    def _return_to_operator(self, message: str = ""):
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        if message:
            print(f"[ChestZn] {message}")

    # ------------------------------------------------------------------
    def _set_blur(self, enabled: bool):
        if enabled:
            self._overlay = self._create_blur_overlay()
        else:
            if self._overlay is not None:
                self._overlay.close()
                self._overlay.deleteLater()
                self._overlay = None

    def _create_blur_overlay(self):
        from PySide6.QtWidgets import (
            QWidget, QLabel, QGraphicsBlurEffect,
            QGraphicsScene, QGraphicsPixmapItem
        )
        from PySide6.QtGui import QPixmap, QPainter
        from PySide6.QtCore import Qt, QRectF

        screen = self.grab()
        w, h = screen.width(), screen.height()

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
    
    # ---------- Загрузка темы из настроек ----------
    cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
    theme = "dark"
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                theme = cfg.get("app_theme", "dark")
        except Exception:
            pass
            
    app.setStyleSheet(get_qss(theme))

    # ---------- 1. Скопировать руководства ----------
    docs_helper.ensure_docs_copied()

    # ---------- 2. Проверить состояние ----------
    problems = check_all()

    critical = has_critical(problems)
    warnings = has_warnings(problems)

    print(f"[ChestZn] Проверка при запуске: "
          f"критических={critical}, предупреждений={warnings}")

    # ---------- 3. Показать окно проблем ВСЕГДА ----------
    try:
        store_for_dialog = Store()
    except Exception as e:
        store_for_dialog = None
        print(f"[ChestZn] Store не создан: {e}")

    dlg = StartupProblemsWindow(store_for_dialog, parent=None)
    result = dlg.exec()

    # Если критические — не пускаем
    if critical:
        print("[ChestZn] Критические проблемы — выход")
        sys.exit(1)

    # Если оператор закрыл окно без «Продолжить» — выход
    if result != StartupProblemsWindow.Accepted:
        print("[ChestZn] Оператор закрыл окно проблем — выход")
        sys.exit(0)

    # ---------- 4. Открыть главное окно ----------
    win = MainWindow(initial_problems=problems)
    win.show()

    print("[ChestZn] Окно показано, visible:", win.isVisible())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()