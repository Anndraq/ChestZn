"""
Окно сводки проблем при запуске (startup_problems_window.py).
Показывает состояние ключа, БД, принтера, сканера, Ghostscript.
Полностью адаптировано под мягкую светлую и тёмную темы Dotmark.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QScrollArea, QWidget, QApplication, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut, QKeySequence, QGuiApplication, QIcon

import docs_helper
import admin_guard
from startup_check import (
    check_all, has_critical, has_warnings, CRITICAL, WARNING
)

ICONS = {
    "ok":      ("✓", "#22c55e"),
    "warning": ("⚠️", "#d97706"),
    "error":   ("❌", "#ef4444"),
}


class StartupProblemsWindow(QDialog):
    """
    Окно с проблемами при запуске.
    accept() — «Продолжить».
    reject() — «Закрыть приложение».
    """

    problems_changed = Signal(list)

    def __init__(self, store, parent=None, initial_problems=None):
        super().__init__(parent)
        self.store = store
        self.problems = list(initial_problems) if initial_problems is not None else []

        self.setWindowTitle("Dotmark · Проверка при запуске")
        base_dir = Path(__file__).resolve().parent
        for icon_name in ("app.ico", "logo.png"):
            ip = base_dir / icon_name
            if ip.exists():
                self.setWindowIcon(QIcon(str(ip)))
                break

        self.setModal(True)
        self.setMinimumSize(640, 620)
        self.setObjectName("startupWindow")

        # --------- Корневой layout ---------
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        # --------- Заголовок ---------
        self.title = QLabel("")
        self.title.setObjectName("startupTitle")
        self.title.setAlignment(Qt.AlignCenter)
        root.addWidget(self.title)

        self.subtitle = QLabel("")
        self.subtitle.setObjectName("startupSubtitle")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)

        root.addSpacing(4)

        # --------- Область скролла ---------
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 4, 0)
        self.scroll_layout.setSpacing(10)
        self.scroll_layout.addStretch(1)

        self.scroll.setWidget(self.scroll_content)
        root.addWidget(self.scroll, stretch=1)

        # --------- Кнопки внизу ---------
        aux_row = QHBoxLayout()
        aux_row.setSpacing(8)

        self.btn_refresh = QPushButton("🔄 Обновить список")
        self.btn_refresh.setObjectName("auxButton")
        self.btn_refresh.clicked.connect(self.refresh_problems)

        self.btn_save = QPushButton("📄 Сохранить отчёт")
        self.btn_save.setObjectName("auxButton")
        self.btn_save.clicked.connect(self.save_report)

        self.btn_copy = QPushButton("📋 Скопировать текст")
        self.btn_copy.setObjectName("auxButton")
        self.btn_copy.clicked.connect(self.copy_text)

        self.btn_docs = QPushButton("📖 Все руководства")
        self.btn_docs.setObjectName("auxButton")
        self.btn_docs.clicked.connect(lambda: docs_helper.open_doc("README.md"))

        aux_row.addWidget(self.btn_refresh, stretch=1)
        aux_row.addWidget(self.btn_save, stretch=1)
        aux_row.addWidget(self.btn_copy, stretch=1)
        aux_row.addWidget(self.btn_docs, stretch=1)
        root.addLayout(aux_row)

        self.btn_restart = QPushButton("🔁 Перезапустить приложение")
        self.btn_restart.setObjectName("restartButton")
        self.btn_restart.setMinimumHeight(36)
        self.btn_restart.clicked.connect(self.restart_app)
        root.addWidget(self.btn_restart)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)

        self.btn_quit = QPushButton("Закрыть приложение")
        self.btn_quit.setObjectName("quitButton")
        self.btn_quit.setMinimumHeight(42)
        self.btn_quit.setAutoDefault(False)
        self.btn_quit.clicked.connect(self.reject)

        self.btn_continue = QPushButton("Продолжить")
        self.btn_continue.setObjectName("continueButton")
        self.btn_continue.setProperty("primary", True)
        self.btn_continue.setMinimumHeight(42)
        self.btn_continue.setDefault(True)
        self.btn_continue.clicked.connect(self.accept)

        buttons.addWidget(self.btn_quit, stretch=1)
        buttons.addWidget(self.btn_continue, stretch=2)
        root.addLayout(buttons)

        # --------- Тема окна ---------
        self._apply_theme()

        # --------- Горячие клавиши ---------
        QShortcut(QKeySequence("Escape"), self, self.reject)
        QShortcut(QKeySequence("F5"),     self, self.refresh_problems)

        # --------- Первичная отрисовка без блокировки UI ---------
        if self.problems:
            self._render()
        else:
            self.problems = check_all(force_refresh=False)
            self._render()
        # Запускаем точный аппаратный опрос в фоне и обновляем карточки по готовности
        self._trigger_async_hardware_check()

    # ------------------------------------------------------------------
    def _apply_theme(self):
        try:
            from styles import is_light_theme
            is_lt = is_light_theme()
        except Exception:
            is_lt = False

        if is_lt:
            win_bg = "#dce2ea"
            card_bg = "#e8ecf1"
            card_bd = "#c2cbd6"
            text_main = "#1e293b"
            text_sub = "#475569"
            title_warn = "#9a5b13"
            title_crit = "#9f3a44"
            title_ok = "#216149"
            msg_ok = "#216149"
            msg_warn = "#9a5b13"
            msg_err = "#9f3a44"
        else:
            win_bg = "#0f1419"
            card_bg = "#161b22"
            card_bd = "#30363d"
            text_main = "#e6edf3"
            text_sub = "#8b949e"
            title_warn = "#f59e0b"
            title_crit = "#f87171"
            title_ok = "#4ade80"
            msg_ok = "#4ade80"
            msg_warn = "#fbbf24"
            msg_err = "#f87171"

        self.setStyleSheet(f"""
            QDialog#startupWindow {{
                background-color: {win_bg};
                color: {text_main};
            }}
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            QWidget#scrollContent {{
                background-color: transparent;
            }}
            QLabel#startupTitle {{
                font-size: 18px;
                font-weight: 700;
                background: transparent;
                color: {text_main};
            }}
            QLabel#startupTitle[state="warning"] {{
                color: {title_warn};
            }}
            QLabel#startupTitle[state="critical"] {{
                color: {title_crit};
            }}
            QLabel#startupTitle[state="ok"] {{
                color: {title_ok};
            }}
            QLabel#startupSubtitle {{
                font-size: 13px;
                color: {text_sub};
                background: transparent;
            }}
            QFrame#problemCard {{
                background-color: {card_bg};
                border: 1px solid {card_bd};
                border-radius: 10px;
            }}
            QLabel#problemIcon {{
                font-size: 15px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#problemIcon[color="ok"] {{ color: {msg_ok}; }}
            QLabel#problemIcon[color="warning"] {{ color: {msg_warn}; }}
            QLabel#problemIcon[color="error"] {{ color: {msg_err}; }}
            QLabel#problemName {{
                font-size: 14px;
                font-weight: 700;
                color: {text_main};
                background: transparent;
            }}
            QLabel#problemMessage {{
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#problemMessage[color="ok"] {{ color: {msg_ok}; }}
            QLabel#problemMessage[color="warning"] {{ color: {msg_warn}; }}
            QLabel#problemMessage[color="error"] {{ color: {msg_err}; }}
            QLabel#problemHint {{
                font-size: 12px;
                color: {text_sub};
                background: transparent;
            }}
        """)

    # ------------------------------------------------------------------
    def _trigger_async_hardware_check(self):
        """Фоновое обновление статуса оборудования без зависания окна при старте."""
        import threading
        from PySide6.QtCore import QTimer

        def _bg():
            try:
                from device_discovery import _refresh_all_caches_sync
                _refresh_all_caches_sync()
            except Exception:
                pass

        threading.Thread(target=_bg, daemon=True).start()
        QTimer.singleShot(450, self._apply_cached_check)
        QTimer.singleShot(1400, self._apply_cached_check)

    def _apply_cached_check(self):
        if not self.isVisible():
            return
        try:
            new_probs = check_all(force_refresh=False)
            if new_probs != self.problems:
                self.problems = new_probs
                self._render()
                self.problems_changed.emit(self.problems)
        except Exception:
            pass

    def refresh_problems(self):
        """Перепроверить состояние и обновить окно в фоновом потоке без зависания UI."""
        import threading
        from PySide6.QtCore import QTimer

        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("⏳ Проверка...")

        def _bg():
            try:
                from device_discovery import _refresh_all_caches_sync
                _refresh_all_caches_sync()
            except Exception:
                pass

            def _done():
                self.problems = check_all(force_refresh=False)
                self._render()
                self.problems_changed.emit(self.problems)
                self.btn_refresh.setEnabled(True)
                self.btn_refresh.setText("🔄 Обновить список")
                print("[startup] проверка обновлена")

            QTimer.singleShot(0, _done)

        threading.Thread(target=_bg, daemon=True).start()

    # ------------------------------------------------------------------
    def _render(self):
        """Отрисовать заголовок и карточки проблем."""
        problems = self.problems
        self._critical = has_critical(problems)
        self._warnings = has_warnings(problems)

        if self._critical:
            self.title.setText("❌  КРИТИЧЕСКИЕ ПРОБЛЕМЫ")
            self.title.setProperty("state", "critical")
        elif self._warnings:
            self.title.setText("⚠️  ОБНАРУЖЕНЫ ПРОБЛЕМЫ")
            self.title.setProperty("state", "warning")
        else:
            self.title.setText("✓  ВСЁ В ПОРЯДКЕ")
            self.title.setProperty("state", "ok")

        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)

        if self._critical:
            self.subtitle.setText(
                "Запуск заблокирован. Обратитесь к администратору."
            )
        elif self._warnings:
            self.subtitle.setText(
                "Можно продолжить, но некоторые функции не будут работать."
            )
        else:
            self.subtitle.setText("Можно продолжать работу.")

        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for p in problems:
            card = self._make_problem_card(p)
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, card)

        self.btn_continue.setEnabled(not self._critical)
        if self._critical:
            self.btn_continue.setToolTip(
                "Запуск заблокирован из-за критических проблем"
            )

    # ------------------------------------------------------------------
    def _make_problem_card(self, p: dict) -> QFrame:
        """Создать карточку проблемы с кнопками действий."""
        card = QFrame()
        card.setObjectName("problemCard")

        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(6)

        icon, _color = ICONS.get(p["status"], ("?", "#8b97a6"))

        head = QHBoxLayout()
        head.setSpacing(8)

        icon_label = QLabel(icon)
        icon_label.setObjectName("problemIcon")
        icon_label.setProperty("color", p["status"])
        head.addWidget(icon_label)

        name_label = QLabel(p["name"])
        name_label.setObjectName("problemName")
        head.addWidget(name_label)
        head.addStretch(1)
        v.addLayout(head)

        msg = QLabel(p["message"])
        msg.setObjectName("problemMessage")
        msg.setProperty("color", p["status"])
        msg.setWordWrap(True)
        v.addWidget(msg)

        if p.get("hint"):
            hint = QLabel(p["hint"])
            hint.setObjectName("problemHint")
            hint.setWordWrap(True)
            v.addWidget(hint)

        actions = self._build_actions(p)
        if actions:
            actions_row = QHBoxLayout()
            actions_row.setSpacing(8)
            actions_row.addStretch(1)
            for btn in actions:
                actions_row.addWidget(btn)
            v.addLayout(actions_row)

        return card

    # ------------------------------------------------------------------
    def _build_actions(self, p: dict) -> list:
        """Список кнопок для конкретной проблемы."""
        name = p["name"]
        buttons = []

        if p.get("doc"):
            btn_doc = QPushButton("📖 " + self._doc_label(name))
            btn_doc.setObjectName("actionButton")
            btn_doc.clicked.connect(
                lambda _, d=p["doc"]: docs_helper.open_doc(d)
            )
            buttons.append(btn_doc)

        if name == "Ghostscript" and p.get("url"):
            btn_url = QPushButton("🌐 Скачать Ghostscript")
            btn_url.setObjectName("actionButton")
            btn_url.clicked.connect(
                lambda _, u=p["url"]: docs_helper.open_url(u)
            )
            buttons.append(btn_url)

        if name == "База данных" and p.get("status") != "ok":
            btn_folder = QPushButton("📁 Открыть папку с данными")
            btn_folder.setObjectName("actionButton")
            btn_folder.clicked.connect(self.open_data_folder)
            buttons.append(btn_folder)

        if name == "Ключ шифрования" and p.get("status") != "ok":
            btn_folder = QPushButton("📁 Открыть папку с данными")
            btn_folder.setObjectName("actionButton")
            btn_folder.clicked.connect(self.open_data_folder)
            buttons.append(btn_folder)

        return buttons

    def _doc_label(self, problem_name: str) -> str:
        return {
            "Ключ шифрования": "Как восстановить ключ",
            "База данных":     "Как восстановить БД",
            "Принтер":         "Как настроить принтер",
            "Ghostscript":     "Как установить",
        }.get(problem_name, "Открыть руководство")

    # ------------------------------------------------------------------
    def open_data_folder(self):
        """Открыть папку с данными — ТОЛЬКО после пароля админа."""
        ok = admin_guard.require_admin(
            self.store,
            parent=self,
            reason="Открытие папки с данными"
        )
        if ok:
            docs_helper.open_app_folder()

    # ------------------------------------------------------------------
    def save_report(self):
        """Сохранить отчёт в файл на рабочий стол."""
        try:
            desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
            if not desktop.exists():
                desktop = Path.home()

            ts = datetime.now().strftime("%Y-%m-%d-%H%M")
            path = desktop / f"dotmark-problems-{ts}.txt"

            lines = []
            lines.append("Dotmark — Отчёт о проблемах при запуске")
            lines.append(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"Компьютер: {os.environ.get('COMPUTERNAME', '?')}")
            lines.append(f"Пользователь: {os.environ.get('USERNAME', '?')}")
            lines.append("")
            lines.append("=" * 60)
            lines.append("")

            for p in self.problems:
                icon = {"ok": "[OK]", "warning": "[!]", "error": "[X]"}.get(p["status"], "[?]")
                lines.append(f"{icon} {p['name']}: {p['message']}")
                if p.get("hint"):
                    for l in p["hint"].splitlines():
                        lines.append(f"    {l}")
                if p.get("doc"):
                    lines.append(f"    [руководство: {p['doc']}]")
                lines.append("")

            path.write_text("\n".join(lines), encoding="utf-8")

            QMessageBox.information(
                self,
                "Отчёт сохранён",
                f"Файл сохранён:\n{path}\n\n"
                f"Отправьте его администратору."
            )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить отчёт:\n{e}")

    # ------------------------------------------------------------------
    def copy_text(self):
        """Скопировать текст проблем в буфер обмена."""
        try:
            lines = ["Dotmark — Проблемы при запуске", ""]
            for p in self.problems:
                icon = {"ok": "[OK]", "warning": "[!]", "error": "[X]"}.get(p["status"], "[?]")
                lines.append(f"{icon} {p['name']}: {p['message']}")
                if p.get("hint"):
                    for l in p["hint"].splitlines():
                        lines.append(f"    {l}")
                if p.get("doc"):
                    lines.append(f"    [руководство: {p['doc']}]")
                lines.append("")
            QGuiApplication.clipboard().setText("\n".join(lines))
            QMessageBox.information(self, "Скопировано", "Текст проблем скопирован в буфер обмена.")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось скопировать:\n{e}")

    # ------------------------------------------------------------------
    def restart_app(self):
        """Перезапустить приложение."""
        reply = QMessageBox.question(
            self,
            "Перезапуск",
            "Перезапустить приложение?\n\n"
            "Все несохранённые данные будут потеряны.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            from PySide6.QtCore import QProcess
            QProcess.startDetached(sys.executable, sys.argv)
            self.accept()
            QApplication.quit()
            sys.exit(0)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось перезапустить:\n{e}")
