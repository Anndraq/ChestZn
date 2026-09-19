"""
Логирование ChestZn.
Пишет в %APPDATA%\ChestZn\logs\chestzn.log с ротацией по дням.
При разработке (не .exe) дублирует в консоль.
"""
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "chestzn.log"

_initialized = False


def setup_logging(debug: bool = False):
    """Инициализировать логирование. Вызывать один раз при старте."""
    global _initialized
    if _initialized:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("chestzn")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # --- Файл с ротацией по дням ---
    file_handler = TimedRotatingFileHandler(
        str(LOG_FILE),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # --- Консоль (только при запуске из Python, не из .exe) ---
    if not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    _initialized = True
    root.info("Логирование инициализировано")
    root.info("Файл логов: %s", LOG_FILE)


def get_logger(name: str) -> logging.Logger:
    """Вернуть логгер для модуля. Пример: get_logger('printer')."""
    if not _initialized:
        setup_logging()
    return logging.getLogger(f"chestzn.{name}")


def log_file_path() -> str:
    return str(LOG_FILE)


def log_dir_path() -> str:
    return str(LOG_DIR)