"""
Глобальная обработка исключений для ChestZn.
Все необработанные исключения:
  - пишутся в лог с полным traceback;
  - попадают в audit (таблица БД);
  - показываются пользователю через QMessageBox.
"""
import sys
import traceback
from datetime import datetime

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt


# Ссылка на Store — установим при инициализации
_store = None
_log = None


def install_exception_hook(store=None):
    """
    Установить глобальный обработчик исключений.
    Вызывать после создания QApplication, но до запуска главного окна.
    """
    global _store, _log

    _store = store

    from logger import get_logger
    _log = get_logger("errors")

    # 1. Стандартный sys.excepthook — для исключений в основном потоке
    sys.excepthook = _handle_exception

    # 2. Qt-хук — для исключений в слотах сигналов PySide6
    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_message_handler)
    except ImportError:
        pass

    _log.info("Глобальный обработчик исключений установлен")


def _handle_exception(exc_type, exc_value, exc_tb):
    """Обработать необработанное исключение."""
    if issubclass(exc_type, KeyboardInterrupt):
        # Ctrl+C — не считаем ошибкой
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    # --- 1. Формируем traceback ---
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_text = "".join(tb_lines)

    # --- 2. Пишем в лог ---
    if _log:
        _log.error("Необработанное исключение:\n%s", tb_text)

    # --- 3. Пишем в audit ---
    if _store:
        try:
            short_msg = f"{exc_type.__name__}: {exc_value}"
            _store.log(
                "Ошибка",
                point="—",
                operator="система",
                gtin=None,
                sn=None,
                result=short_msg[:200],
            )
        except Exception as e:
            if _log:
                _log.error("Не удалось записать ошибку в audit: %s", e)

    # --- 4. Показываем оператору ---
    _show_error_dialog(exc_type, exc_value, tb_text)


def _show_error_dialog(exc_type, exc_value, tb_text):
    """Показать диалог с ошибкой."""
    app = QApplication.instance()
    if app is None:
        return

    try:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Внутренняя ошибка")
        msg.setText(
            "Произошла внутренняя ошибка.\n"
            "Она записана в журнал. Обратитесь к администратору."
        )
        msg.setInformativeText(f"{exc_type.__name__}: {exc_value}")
        msg.setDetailedText(tb_text)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()
    except Exception:
        # Если даже диалог не показать — просто молчим
        pass


def _qt_message_handler(mode, context, message):
    """Обработчик сообщений Qt (для отладки)."""
    if _log:
        # Игнорируем «безобидные» сообщения
        if "Could not parse stylesheet" in message:
            return
        if mode.name in ("QtDebugMsg", "QtInfoMsg"):
            return
        _log.warning("Qt: %s", message)