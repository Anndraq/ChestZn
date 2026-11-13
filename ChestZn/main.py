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



def _ensure_store_compat(store) -> None:
    """Гарантирует наличие check_security_answer в Store для recovery_window.py."""
    if store is None:
        return
    if not hasattr(store, "check_security_answer"):
        def _check_security_answer(login: str, answer: str) -> bool:
            if hasattr(store, "verify_security_answer"):
                try:
                    return bool(store.verify_security_answer(login, answer))
                except Exception:
                    pass
            ans_clean = str(answer or "").strip()
            if not ans_clean:
                return False
            try:
                import hashlib
                import db
                ans_low = ans_clean.lower()
                h_raw = hashlib.sha256(ans_clean.encode("utf-8")).hexdigest()
                h_low = hashlib.sha256(ans_low.encode("utf-8")).hexdigest()
                with db.get_conn() as conn:
                    cols_raw = conn.execute("PRAGMA table_info(users)").fetchall()
                    cols = [c[1] if isinstance(c, (list, tuple)) else c["name"] for c in cols_raw]
                    ans_cols = [c for c in ("security_answer_hash", "security_answer", "answer_hash", "answer") if c in cols]
                    if not ans_cols:
                        return False
                    row = conn.execute(
                        f"SELECT {', '.join(ans_cols)} FROM users WHERE login = ? LIMIT 1",
                        (login,),
                    ).fetchone()
                    if row is None:
                        return False
                    for idx, col in enumerate(ans_cols):
                        val = row[idx] if isinstance(row, (list, tuple)) else row[col]
                        if not val:
                            continue
                        val_s = str(val).strip()
                        if val_s in (ans_clean, ans_low, h_raw, h_low) or val_s.lower() == ans_low:
                            return True
            except Exception:
                pass
            return False

        store.check_security_answer = _check_security_answer

    if not hasattr(store, "reset_password"):
        def _reset_password(login: str, new_password: str) -> bool:
            import inspect
            for alt_fn in ("set_password", "update_password", "change_user_password", "set_user_password", "update_user_password"):
                if hasattr(store, alt_fn):
                    try:
                        fn = getattr(store, alt_fn)
                        sig = inspect.signature(fn)
                        if len(sig.parameters) >= 2:
                            fn(login, new_password)
                            return True
                    except Exception:
                        pass
            try:
                import hashlib
                import os as _os
                import db
                import store as store_mod

                pw_str = str(new_password or "")
                login_str = str(login or "").strip()

                with db.get_conn() as conn:
                    cols_raw = conn.execute("PRAGMA table_info(users)").fetchall()
                    cols = [c[1] if isinstance(c, (list, tuple)) else c["name"] for c in cols_raw]
                    pw_col = next((c for c in ("password_hash", "pass_hash", "password") if c in cols), None)
                    if not pw_col:
                        return False
                    salt_col = next((c for c in ("salt", "password_salt", "pass_salt") if c in cols), None)

                    sel_cols = [pw_col] + ([salt_col] if salt_col else [])
                    existing = conn.execute(
                        f"SELECT {', '.join(sel_cols)} FROM users WHERE login = ? LIMIT 1",
                        (login_str,),
                    ).fetchone()
                    existing_pw = None
                    existing_salt = None
                    if existing is not None:
                        existing_pw = existing[0] if isinstance(existing, (list, tuple)) else existing[pw_col]
                        if salt_col:
                            existing_salt = existing[1] if isinstance(existing, (list, tuple)) else existing[salt_col]

                hash_fn = getattr(store_mod, "hash_password", None) or getattr(store, "_hash_password", None)
                pw_out = None
                salt_out = None
                passed_salt = None

                if hash_fn is not None:
                    try:
                        sig = inspect.signature(hash_fn)
                        params = list(sig.parameters.values())
                        if len(params) >= 2 and ("salt" in sig.parameters or salt_col):
                            passed_salt = _os.urandom(16).hex()
                            res = hash_fn(pw_str, passed_salt)
                        else:
                            res = hash_fn(pw_str)
                    except TypeError:
                        res = hash_fn(pw_str)

                    if isinstance(res, (tuple, list)):
                        if len(res) >= 2:
                            a, b = res[0], res[1]
                            if passed_salt is not None and str(a) == passed_salt:
                                salt_out, pw_out = a, b
                            elif passed_salt is not None and str(b) == passed_salt:
                                pw_out, salt_out = a, b
                            elif existing_pw is not None and existing_salt is not None:
                                if len(str(a)) == len(str(existing_salt)) and len(str(b)) == len(str(existing_pw)):
                                    salt_out, pw_out = a, b
                                else:
                                    pw_out, salt_out = a, b
                            else:
                                # Обычно hash (64+ симв.) длиннее salt (16-32 симв.)
                                if len(str(a)) >= len(str(b)):
                                    pw_out, salt_out = a, b
                                else:
                                    salt_out, pw_out = a, b
                        elif len(res) == 1:
                            pw_out = res[0]
                    elif isinstance(res, dict):
                        pw_out = res.get("password_hash") or res.get("pass_hash") or res.get("hash") or res.get("password")
                        salt_out = res.get("salt") or res.get("password_salt") or res.get("pass_salt")
                    else:
                        pw_out = res

                if pw_out is None:
                    pw_out = hashlib.sha256(pw_str.encode("utf-8")).hexdigest()

                def _to_sql_scalar(val):
                    if val is None or isinstance(val, (str, bytes, int, float)):
                        return val
                    if isinstance(val, memoryview):
                        return bytes(val)
                    return str(val)

                pw_out = _to_sql_scalar(pw_out)
                if salt_col and salt_out is None and passed_salt is not None:
                    salt_out = passed_salt
                salt_out = _to_sql_scalar(salt_out) if salt_out is not None else None

                with db.get_conn() as conn:
                    if salt_col and salt_out is not None:
                        conn.execute(
                            f"UPDATE users SET {pw_col} = ?, {salt_col} = ? WHERE login = ?",
                            (pw_out, salt_out, login_str),
                        )
                    else:
                        conn.execute(
                            f"UPDATE users SET {pw_col} = ? WHERE login = ?",
                            (pw_out, login_str),
                        )
                    conn.commit()
                if hasattr(store, "reload"):
                    try:
                        store.reload()
                    except Exception:
                        pass
                return True
            except Exception:
                return False

        store.reset_password = _reset_password


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
        point = detect_point(cfg, store=store)
        if not point:
            point = "T-01"
            print(f"[Dotmark] Точка не задана в config.json, используется по умолчанию: {point}")
        else:
            print(f"[Dotmark] Точка определена: {point}")

        # --------- Создание / переиспользование Store ---------
        self.store = store if store is not None else Store()
        _ensure_store_compat(self.store)
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

        meta_ver = QLabel("Версия: v0.2")
        meta_ver.setObjectName("meta")
        h.addWidget(meta_ver)

        self.root_layout.addWidget(header)

        # --------- Вкладки (вкладка «Админ» создаётся лениво при входе для мгновенного запуска) ---------
        self.tabs = QTabWidget()
        self.operator = OperatorWindow(self.store)
        self.admin: Optional[AdminWindow] = None
        self._admin_placeholder = QWidget()

        self.tabs.addTab(self.operator, "Оператор")
        self.tabs.addTab(self._admin_placeholder, "Админ")

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

        # На Мастер-ПК сразу поднимаем встроенный HTTP-сервер на порту 8765
        try:
            import sync as sync_mod
            if sync_mod.get_pc_role(self.store) == "master":
                sync_mod.start_master_http_server(self.store, port=sync_mod.DEFAULT_SYNC_PORT)
        except Exception:
            pass

        # Публикация живого heartbeat в фоне для мониторинга онлайн-статуса точек на Мастер-ПК
        self._hb_timer = QTimer(self)
        self._hb_timer.setInterval(20000)
        self._hb_timer.timeout.connect(self._publish_heartbeat_async)
        self._hb_timer.start()
        QTimer.singleShot(1500, self._publish_heartbeat_async)

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
    def _publish_heartbeat_async(self):
        import threading
        def _bg():
            try:
                import sync as sync_mod
                sync_mod.publish_pc_heartbeat(self.store)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

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
            self._ensure_admin_tab()
            self.admin.switch("dashboard")
            return

        QApplication.processEvents()
        self._set_blur(True)
        QApplication.processEvents()

        login = LoginWindow(self.store, self)
        if "ChestZn" in login.windowTitle() or not login.windowTitle():
            login.setWindowTitle(
                login.windowTitle().replace("ChestZn", "Dotmark")
                if login.windowTitle()
                else "Dotmark · Вход администратора"
            )
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
            if "ChestZn" in question_window.windowTitle():
                question_window.setWindowTitle(
                    question_window.windowTitle().replace("ChestZn", "Dotmark")
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
        self._ensure_admin_tab()
        self.admin.switch("dashboard")
        print(f"[Dotmark] Админ авторизован: {login.user['fio']}")

    def _ensure_admin_tab(self):
        """Создаёт AdminWindow при первом входе администратора, не нагружая старт приложения."""
        if self.admin is not None:
            return
        self.admin = AdminWindow(self.store)
        self.tabs.blockSignals(True)
        idx = self.tabs.indexOf(self._admin_placeholder)
        if idx != -1:
            self.tabs.removeTab(idx)
            self._admin_placeholder.deleteLater()
            self.tabs.insertTab(idx, self.admin, "Админ")
            self.tabs.setCurrentIndex(idx)
        else:
            self.tabs.addTab(self.admin, "Админ")
        self.tabs.blockSignals(False)

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




class FirstRunSetupDialog(QMessageBox):
    pass


def _needs_first_run_setup(store) -> bool:
    """Проверяет, выполнялась ли уже первичная настройка роли и точки на этом компьютере."""
    try:
        cfg_p = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
        if cfg_p.exists():
            data = json.loads(cfg_p.read_text(encoding="utf-8"))
            if data.get("setup_completed"):
                return False
    except Exception:
        pass
    if store is not None and hasattr(store, "get_setting"):
        try:
            if store.get_setting("setup_completed") == "1":
                return False
        except Exception:
            pass
    return True


def _run_first_run_wizard(store) -> bool:
    """Мастер первичной настройки при первом запуске exe на новом ПК: роль (Мастер/Рабочий) и точка (T-01..T-03)."""
    import socket
    import sync as sync_mod
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
        QLineEdit, QPushButton, QFrame, QFormLayout, QRadioButton, QButtonGroup
    )

    dlg = QDialog()
    dlg.setWindowTitle("Dotmark · Первичная настройка компьютера")
    dlg.setMinimumWidth(520)
    icon = _resolve_app_icon()
    if not icon.isNull():
        dlg.setWindowIcon(icon)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 20, 24, 20)
    lay.setSpacing(14)

    hdr = QLabel("Первичная настройка рабочего места")
    hdr.setStyleSheet("font-size: 18px; font-weight: 700;")
    lay.addWidget(hdr)

    sub = QLabel(
        "Этот компьютер запускается впервые. Укажите его роль в сети и номер точки, "
        "чтобы программа знала, куда отправлять и откуда брать заказы."
    )
    sub.setWordWrap(True)
    sub.setStyleSheet("color: #8b949e; font-size: 13px;")
    lay.addWidget(sub)

    role_card = QFrame()
    role_card.setObjectName("card")
    rc_lay = QVBoxLayout(role_card)
    rc_lay.setContentsMargins(16, 14, 16, 14)
    rc_lay.setSpacing(8)

    rb_master = QRadioButton("👑 Мастер-ПК (офис · главный компьютер с базой и раздачей заказов)")
    rb_worker = QRadioButton("💻 Рабочий ПК (склад · сканер + принтер, подключается к Мастер-ПК)")
    rb_worker.setChecked(True)
    grp = QButtonGroup(dlg)
    grp.addButton(rb_master)
    grp.addButton(rb_worker)
    rc_lay.addWidget(rb_master)
    rc_lay.addWidget(rb_worker)
    lay.addWidget(role_card)

    form_card = QFrame()
    form_card.setObjectName("card")
    form = QFormLayout(form_card)
    form.setContentsMargins(16, 14, 16, 14)
    form.setSpacing(10)

    cmb_point = QComboBox()
    cmb_point.setEditable(True)
    pts = getattr(store, "points", []) or []
    if pts:
        for p in pts:
            cmb_point.addItem(f"{p['id']} — {p.get('name', '')}", p["id"])
    else:
        for pid, pname in [("T-01", "Пост 1"), ("T-02", "Пост 2"), ("T-03", "Пост 3")]:
            cmb_point.addItem(f"{pid} — {pname}", pid)

    ed_pc_name = QLineEdit(socket.gethostname().strip() or "POST-01")
    lbl_master_ip = QLabel("IP-адрес Мастер-ПК:")
    ed_master_ip = QLineEdit()
    ed_master_ip.setPlaceholderText("Например: 192.168.1.10 (указан в окне Мастер-ПК)")

    form.addRow("Точка / пост работы:", cmb_point)
    form.addRow("Имя этого ПК:", ed_pc_name)
    form.addRow(lbl_master_ip, ed_master_ip)
    lay.addWidget(form_card)

    def _toggle_role():
        is_w = rb_worker.isChecked()
        lbl_master_ip.setVisible(is_w)
        ed_master_ip.setVisible(is_w)

    rb_master.toggled.connect(_toggle_role)
    rb_worker.toggled.connect(_toggle_role)
    _toggle_role()

    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_ok = QPushButton("✓ Сохранить и запустить")
    btn_ok.setProperty("primary", True)
    btn_ok.setMinimumHeight(38)
    btn_row.addWidget(btn_ok)
    lay.addLayout(btn_row)

    def _on_save():
        role = "master" if rb_master.isChecked() else "worker"
        raw_pt = str(cmb_point.currentText() or "").strip()
        if not raw_pt:
            raw_pt = str(cmb_point.currentData() or "T-01").strip()
        pt_id = raw_pt.split("—")[0].strip().upper() or "T-01"
        pc_name = ed_pc_name.text().strip() or socket.gethostname().strip()
        m_ip = ed_master_ip.text().strip()

        # Создаём точку (если её ещё нет) и привязываем к ней этот компьютер в «Точки и ПК»
        try:
            import db
            local_ip = "127.0.0.1"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.05)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                pass

            with db.get_conn() as conn:
                # Если пользователь задал собственную точку при первом запуске — очищаем стартовые демо-точки
                if pt_id not in ("T-01", "T-02", "T-03"):
                    try:
                        conn.execute(
                            "DELETE FROM points WHERE id IN ('T-01', 'T-02', 'T-03') "
                            "AND id NOT IN (SELECT DISTINCT point_id FROM computers)"
                        )
                    except Exception:
                        pass
                conn.execute(
                    "INSERT OR IGNORE INTO points (id, name, address) VALUES (?, ?, ?)",
                    (pt_id, f"Пост {pt_id}", ""),
                )
                comp_exists = conn.execute(
                    "SELECT id FROM computers WHERE point_id = ? AND (LOWER(name) = LOWER(?) OR LOWER(sync_node) = LOWER(?)) LIMIT 1",
                    (pt_id, pc_name, pc_name),
                ).fetchone()
                if comp_exists is None:
                    comp_role = "Основной оператор" if role == "master" else "Рабочий пост"
                    conn.execute(
                        "INSERT INTO computers (point_id, name, ip, role, sync_node, sync_enabled, online) "
                        "VALUES (?, ?, ?, ?, ?, 1, 1)",
                        (pt_id, pc_name, local_ip, comp_role, pc_name),
                    )
                conn.commit()
            if store is not None and hasattr(store, "reload"):
                store.reload()
        except Exception:
            pass

        if role == "worker" and not m_ip:
            if QMessageBox.question(
                dlg,
                "IP Мастер-ПК не указан",
                "Вы не ввели IP-адрес Мастер-ПК. Указать его позже в разделе «Админ → Синхронизация»?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            ) != QMessageBox.Yes:
                return

        sync_mod.set_pc_role(store, role, pc_name)
        if role == "worker" and m_ip:
            sync_mod.set_master_address(store, m_ip, sync_mod.DEFAULT_SYNC_PORT)

        if store is not None and hasattr(store, "set_setting"):
            try:
                store.set_setting("worker_point_id", pt_id)
                store.set_setting("setup_completed", "1")
                store.current_point = pt_id
            except Exception:
                pass

        try:
            cfg_p = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
            cfg_p.parent.mkdir(parents=True, exist_ok=True)
            c_data = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
            c_data["worker_point_id"] = pt_id
            c_data["current_point"] = pt_id
            c_data["pc_role"] = role
            c_data["pc_node_name"] = pc_name
            c_data["setup_completed"] = True
            cfg_p.write_text(json.dumps(c_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        dlg.accept()

    btn_ok.clicked.connect(_on_save)
    return dlg.exec() == QDialog.Accepted


def main():
    app = QApplication(sys.argv)
    icon = _resolve_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # --------- Загрузка темы из настроек ---------
    theme = get_current_theme(force_reload=True)
    app.setStyleSheet(get_qss(theme))

    # --------- 1. Скопировать руководства в фоне (не задерживая запуск) ---------
    import threading
    threading.Thread(target=docs_helper.ensure_docs_copied, daemon=True).start()

    # --------- 2. Быстрая проверка состояния без блокировки PowerShell ---------
    problems = check_all(force_refresh=False)

    critical = has_critical(problems)
    warnings = has_warnings(problems)

    print(f"[Dotmark] Проверка при запуске: критических={critical}, предупреждений={warnings}")

    # --------- 3. Создаём единый Store и показываем окно проверки (передавая уже собранные problems) ---------
    try:
        store_for_dialog = Store()
        _ensure_store_compat(store_for_dialog)
    except Exception as e:
        store_for_dialog = None
        print(f"[Dotmark] Store не создан: {e}")

    dlg = StartupProblemsWindow(store_for_dialog, parent=None, initial_problems=problems)
    result = dlg.exec()

    # Если критические — не пускаем
    if has_critical(dlg.problems):
        print("[Dotmark] Критические проблемы — выход")
        sys.exit(1)

    # Если оператор закрыл окно без «Продолжить» — выход
    if result != StartupProblemsWindow.Accepted:
        print("[Dotmark] Оператор закрыл окно проблем — выход")
        sys.exit(0)

    # --------- 3.5. Первичная настройка роли и точки (только при первом запуске на новом ПК) ---------
    if _needs_first_run_setup(store_for_dialog):
        if not _run_first_run_wizard(store_for_dialog):
            sys.exit(0)

    # --------- 4. Открыть главное окно ---------
    win = MainWindow(initial_problems=dlg.problems, store=store_for_dialog)
    win.show()

    print(f"[Dotmark] Окно показано, visible:", win.isVisible())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
