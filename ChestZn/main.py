"""Точка входа desktop-приложения (PySide6)."""
import sys
import os
import json
from pathlib import Path
from typing import Optional
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QFrame, QMessageBox
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, QTimer

import docs_helper
import startup_check
from startup_check import has_critical, has_warnings, check_all
from startup_problems_window import StartupProblemsWindow
from banner import WarningBanner

from store import Store
from operator_window import OperatorWindow
from admin_window import AdminWindow
from styles import get_qss, get_current_theme
from login_window import LoginWindow
from security_question_window import SecurityQuestionWindow


ADMIN_LOGIN = "admin"


def _resolve_app_icon() -> QIcon:
    base_dir = Path(__file__).resolve().parent
    for name in ("app.ico", "logo.png"):
        p = base_dir / name
        if p.exists():
            return QIcon(str(p))
    return QIcon()


class MainWindow(QMainWindow):
    def __init__(self, initial_problems: list = None, store: Optional[Store] = None):
        super().__init__()
        self.setWindowTitle("Dotmark · Маркировка")
        icon = _resolve_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1360, 900)

        # --------- Загрузка конфига ---------
        from printer import load_config, save_config, detect_point, autodetect
        cfg = load_config()

        # --------- Определение точки ---------
        point = detect_point(cfg)
        if not point:
            point = "T-01"
            print(f"[Dotmark] Точка не задана в config.json, используется по умолчанию: {point}")
        else:
            print(f"[Dotmark] Точка определена: {point}")

        # --------- Создание / переиспользование Store ---------
        self.store = store if store is not None else Store()
        self.store.current_point = point

        # --------- Оператор по точке ---------
        operator = self._operator_for_point(point)
        self.store.current_operator = operator
        print(f"[Dotmark] Оператор: {operator}")

        # --------- Автоопределение принтера ---------
        if not cfg.get("printer_name") and not cfg.get("printer_ip"):
            result = autodetect()
            if result.get("suggested"):
                s = result["suggested"]
                p_name = s["name"] if isinstance(s, dict) else str(s)
                p_lang = s.get("lang", "auto") if isinstance(s, dict) else result.get("printer_lang", "auto")
                cfg["printer_name"] = p_name
                cfg["printer_lang"] = p_lang
                save_config(cfg)
                print(f"[Dotmark] Принтер определён: {p_name} ({p_lang})")
            else:
                print("[Dotmark] Принтеры не найдены — настройте вручную в админке")
        else:
            print(f"[Dotmark] Общий принтер из настроек: {cfg.get('printer_name')} ({cfg.get('printer_lang')})")

        printers = cfg.get("printers") or {}
        if point in printers:
            p = printers[point]
            print(f"[Dotmark] Принтер для точки {point}: {p.get('printer_name', '?')} ({p.get('printer_lang', 'auto')})")

        # --------- Флаг: админ авторизован? ---------
        self.admin_authenticated = False

        # --------- Оверлей для размытия ---------
        self.overlay = None

        # --------- Проблемы при старте ---------
        self._initial_problems = initial_problems or []

        # --------- Корневой виджет ---------
        root = QWidget()
        self.root_layout = QVBoxLayout(root)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        # --------- Баннер предупреждений ---------
        self.banner = None
        if self._initial_problems:
            warnings_only = [
                p for p in self._initial_problems
                if p.get("status") != "ok"
            ]
            if warnings_only:
                self.banner = WarningBanner(warnings_only)
                self.banner.details_clicked.connect(self.show_problems_window)
                self.root_layout.addWidget(self.banner)

        # --------- Шапка ---------
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(50)

        h = QHBoxLayout(header)
        h.setContentsMargins(20, 8, 20, 8)
        h.setSpacing(12)

        brand = QLabel("📦  DOTMARK · МАРКИРОВКА")
        brand.setObjectName("brand")
        h.addWidget(brand)

        role = QLabel("DESKTOP")
        role.setObjectName("brandRole")
        h.addWidget(role)

        h.addSpacing(16)

        # Точка в шапке
        point_badge = QLabel(f"• {point}")
        point_badge.setObjectName("pointBadge")
        h.addWidget(point_badge)

        # Название точки
        point_name = self._point_name(point)
        if point_name:
            point_label = QLabel(point_name)
            point_label.setObjectName("pointLabel")
            h.addWidget(point_label)

        h.addSpacing(14)

        # Динамические индикаторы оборудования (на одной строке с принтером и сканером)
        self.header_printer_badge = QLabel("🖨 Принтер: Опрос...")
        self.header_printer_badge.setObjectName("headerDeviceBadge")
        self.header_printer_badge.setProperty("state", "warn")
        self.header_printer_badge.setFixedHeight(26)
        self.header_printer_badge.setAlignment(Qt.AlignCenter)
        self.header_printer_badge.setCursor(Qt.PointingHandCursor)
        self.header_printer_badge.mousePressEvent = lambda _: self.show_problems_window()
        h.addWidget(self.header_printer_badge)

        self.header_scanner_badge = QLabel("📡 Сканер: Опрос...")
        self.header_scanner_badge.setObjectName("headerDeviceBadge")
        self.header_scanner_badge.setProperty("state", "warn")
        self.header_scanner_badge.setFixedHeight(26)
        self.header_scanner_badge.setAlignment(Qt.AlignCenter)
        self.header_scanner_badge.setCursor(Qt.PointingHandCursor)
        self.header_scanner_badge.mousePressEvent = lambda _: self.show_problems_window()
        h.addWidget(self.header_scanner_badge)

        h.addStretch(1)

        meta_online = QLabel("Система:")
        meta_online.setObjectName("meta")
        h.addWidget(meta_online)

        ok = QLabel("• онлайн")
        ok.setObjectName("okDot")
        h.addWidget(ok)

        meta_ver = QLabel("Версия: v0.2")
        meta_ver.setObjectName("meta")
        h.addWidget(meta_ver)

        avatar = QLabel("АД")
        avatar.setObjectName("headerAvatar")
        avatar.setFixedSize(26, 26)
        avatar.setAlignment(Qt.AlignCenter)
        h.addWidget(avatar)

        self.root_layout.addWidget(header)

        # --------- Вкладки ---------
        self.tabs = QTabWidget()
        self.operator = OperatorWindow(self.store)
        self.admin    = AdminWindow(self.store)

        self.tabs.addTab(self.operator, "Оператор")
        self.tabs.addTab(self.admin,    "Админ")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.root_layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(root)

        # Неблокирующий опрос кэша оборудования (раз в 3.0 сек, занимает < 0.1 мс в UI потоке)
        self._last_banner_sig = None
        self._device_poll_timer = QTimer(self)
        self._device_poll_timer.setInterval(3000)
        self._device_poll_timer.timeout.connect(self.poll_devices_status)
        self._device_poll_timer.start()

        QTimer.singleShot(150, self.poll_devices_status)

        print("[Dotmark] MainWindow создан, размер:", self.size())
        print("[Dotmark] вкладок:", self.tabs.count())
        print(f"[Dotmark] Точка работы: {point} · Оператор: {operator}")

    # -----------------------------------------------------------------
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

    def _set_badge_state(self, badge: QLabel, state: str, text: str, tooltip: str):
        if badge.text() == text and badge.property("state") == state:
            return
        badge.setText(text)
        badge.setToolTip(tooltip)
        if badge.property("state") != state:
            badge.setProperty("state", state)
            badge.style().unpolish(badge)
            badge.style().polish(badge)

    # -----------------------------------------------------------------
    def poll_devices_status(self):
        """Неблокирующее обновление статусов принтера и сканера из асинхронного кэша."""
        try:
            from device_discovery import detect_local_printers, detect_local_scanners
            from startup_check import check_all

            printers = detect_local_printers(connected_only=True, force_refresh=False)
            has_pr = bool(printers)
            pr_name = printers[0].get("name", "Принтер") if has_pr else ""

            scanners = detect_local_scanners(connected_only=True, force_refresh=False)
            has_sc = bool(scanners)
            sc_name = scanners[0].get("name", "Сканер") if has_sc else ""

            if hasattr(self, "header_printer_badge"):
                if has_pr:
                    self._set_badge_state(
                        self.header_printer_badge,
                        "ok",
                        f"🟢 🖨 {pr_name}",
                        f"Принтер подключён: {pr_name} (кликните для подробностей)",
                    )
                else:
                    self._set_badge_state(
                        self.header_printer_badge,
                        "err",
                        "🔴 🖨 Принтер отключён",
                        "Внимание: принтер физически выключен или отсоединён от ПК!",
                    )

            if hasattr(self, "header_scanner_badge"):
                if has_sc:
                    self._set_badge_state(
                        self.header_scanner_badge,
                        "ok",
                        f"🟢 📡 {sc_name}",
                        f"Сканер подключён: {sc_name} (кликните для подробностей)",
                    )
                else:
                    self._set_badge_state(
                        self.header_scanner_badge,
                        "warn",
                        "🔴 📡 Сканер офлайн",
                        "Предупреждение: сканер штрихкодов не отвечает или не подключён!",
                    )

            current_problems = check_all(force_refresh=False)
            sig = tuple((p.get("name"), p.get("status"), p.get("message")) for p in current_problems)
            if sig != self._last_banner_sig:
                self._last_banner_sig = sig
                self.update_banner(current_problems)

            if hasattr(self, "operator") and hasattr(self.operator, "set_scanner_status"):
                self.operator.set_scanner_status(has_sc, sc_name)
        except Exception:
            pass

    # -----------------------------------------------------------------
    def show_problems_window(self):
        """Показать окно проблем."""
        win = StartupProblemsWindow(self.store, self)
        win.problems_changed.connect(self.update_banner)
        win.exec()
        self.update_banner(win.problems)

    # -----------------------------------------------------------------
    def update_banner(self, problems: list):
        """Обновить или скрыть баннер."""
        warnings_only = [p for p in problems if p.get("status") != "ok"]

        if warnings_only:
            if self.banner is None:
                self.banner = WarningBanner(warnings_only)
                self.banner.details_clicked.connect(self.show_problems_window)
                self.root_layout.insertWidget(0, self.banner)
            else:
                self.banner.update_problems(warnings_only)
                self.banner.show()
        else:
            if self.banner is not None:
                self.banner.hide()

    # -----------------------------------------------------------------
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

        # --------- Секретный вопрос ---------
        if not self.store.has_security_question(login.user["login"]):
            print("[Dotmark] Секретный вопрос не настроен — принудительная настройка")

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

        # --------- Пускаем в админку ---------
        self.admin_authenticated = True
        self.admin.switch("dashboard")
        print(f"[Dotmark] Админ авторизован: {login.user['fio']}")

    # -----------------------------------------------------------------
    def _return_to_operator(self, message: str = ""):
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        if message:
            print(f"[Dotmark] {message}")

    # -----------------------------------------------------------------
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

        # Ускоряем размытие в 4 раза: уменьшаем копию в 2 раза, применяем QGraphicsBlurEffect и растягиваем обратно
        small_w, small_h = max(1, w // 2), max(1, h // 2)
        small_pix = screen.scaled(small_w, small_h, Qt.IgnoreAspectRatio, Qt.FastTransformation)

        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(10)
        blur.setBlurHints(QGraphicsBlurEffect.PerformanceHint)

        scene = QGraphicsScene()
        pixmap_item = QGraphicsPixmapItem(small_pix)
        pixmap_item.setGraphicsEffect(blur)
        scene.addItem(pixmap_item)

        blurred_small = QPixmap(small_w, small_h)
        blurred_small.fill(Qt.transparent)
        painter = QPainter(blurred_small)
        scene.render(painter, QRectF(0, 0, small_w, small_h), QRectF(0, 0, small_w, small_h))
        painter.end()

        blurred_pixmap = blurred_small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

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
    icon = _resolve_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # --------- Загрузка темы из настроек ---------
    theme = get_current_theme(force_reload=True)
    app.setStyleSheet(get_qss(theme))

    # --------- 1. Скопировать руководства ---------
    docs_helper.ensure_docs_copied()

    # --------- 2. Проверить состояние ---------
    problems = check_all()

    critical = has_critical(problems)
    warnings = has_warnings(problems)

    print(f"[Dotmark] Проверка при запуске: критических={critical}, предупреждений={warnings}")

    # --------- 3. Показать окно проблем ВСЕГДА ---------
    try:
        store_for_dialog = Store()
    except Exception as e:
        store_for_dialog = None
        print(f"[Dotmark] Store не создан: {e}")

    dlg = StartupProblemsWindow(store_for_dialog, parent=None)
    result = dlg.exec()

    # Если критические — не пускаем
    if has_critical(dlg.problems):
        print("[Dotmark] Критические проблемы — выход")
        sys.exit(1)

    # Если оператор закрыл окно без «Продолжить» — выход
    if result != StartupProblemsWindow.Accepted:
        print("[Dotmark] Оператор закрыл окно проблем — выход")
        sys.exit(0)

    # --------- 4. Открыть главное окно ---------
    win = MainWindow(initial_problems=dlg.problems, store=store_for_dialog)
    win.show()

    print(f"[Dotmark] Окно показано, visible:", win.isVisible())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
