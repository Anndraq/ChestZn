"""
Работа с SQLite для ChestZn.
Одна база, один файл — %APPDATA%\ChestZn\chestzn.db
"""
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
DB_FILE = APP_DIR / "chestzn.db"


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS counters (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    scans       INTEGER NOT NULL DEFAULT 0,
    prints      INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0,
    limit_value INTEGER NOT NULL DEFAULT 500
);

CREATE TABLE IF NOT EXISTS pool_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    gtin       TEXT NOT NULL,
    code       TEXT NOT NULL UNIQUE,
    sn         TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    printed    INTEGER NOT NULL DEFAULT 0,
    point      TEXT,
    printed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pool_gtin_used ON pool_codes(gtin, used);
CREATE INDEX IF NOT EXISTS idx_pool_printed   ON pool_codes(printed);

CREATE TABLE IF NOT EXISTS queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    time       TEXT NOT NULL,
    gtin       TEXT NOT NULL,
    sn         TEXT NOT NULL,
    point      TEXT,
    operator   TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    time       TEXT NOT NULL,
    action     TEXT NOT NULL,
    point      TEXT,
    operator   TEXT,
    gtin       TEXT,
    sn         TEXT,
    result     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at DESC);

CREATE TABLE IF NOT EXISTS gtin_map (
    foreign_gtin TEXT PRIMARY KEY,
    cz_gtin      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT NOT NULL UNIQUE,
    fio           TEXT NOT NULL,
    role          TEXT NOT NULL,
    point         TEXT,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    last_login    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS security_questions (
    login          TEXT PRIMARY KEY,
    question       TEXT NOT NULL,
    answer_hash    TEXT NOT NULL,
    answer_salt    TEXT NOT NULL,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def init_db():
    """Создать папку и схему БД, если её ещё нет."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE))
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO counters (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    """Контекстный менеджер соединения — коммит при успехе, ролбэк при ошибке."""
    conn = sqlite3.connect(str(DB_FILE), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_path() -> str:
    return str(DB_FILE)