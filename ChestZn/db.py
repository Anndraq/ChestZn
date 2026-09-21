"""
Работа с SQLite для ChestZn.
Шифрование через SQLCipher + ключ DPAPI (см. crypto.py).
Одна база, один файл — %APPDATA%\ChestZn\chestzn.db
"""
import os
import shutil
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from logger import get_logger
from crypto import get_or_create_key

log = get_logger("db")


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
    hmac       TEXT,
    prev_hmac  TEXT,
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

CREATE TABLE IF NOT EXISTS label_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    version     TEXT NOT NULL DEFAULT '1.0',
    width_mm    REAL NOT NULL DEFAULT 58,
    height_mm   REAL NOT NULL DEFAULT 40,
    dpi         INTEGER NOT NULL DEFAULT 203,
    fields_json TEXT NOT NULL DEFAULT '[]',
    sort_order  INTEGER NOT NULL DEFAULT 100,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS template_bindings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_code TEXT NOT NULL,
    gtin_prefix   TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 100,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_tpl_bindings_gtin
    ON template_bindings(gtin_prefix);
"""


# ==========================================================
# Определение состояния БД
# ==========================================================
def _is_encrypted_db() -> bool:
    """
    Проверить, зашифрована ли БД.
    Незашифрованный SQLite начинается с 'SQLite format 3\\x00'.
    """
    if not DB_FILE.exists():
        return False
    try:
        with open(DB_FILE, "rb") as f:
            header = f.read(16)
        return not header.startswith(b"SQLite format 3\x00")
    except Exception:
        return False


def _is_valid_plain_sqlite() -> bool:
    """Файл существует и это открытый SQLite?"""
    if not DB_FILE.exists():
        return False
    try:
        with open(DB_FILE, "rb") as f:
            header = f.read(16)
        return header.startswith(b"SQLite format 3\x00")
    except Exception:
        return False


# ==========================================================
# Открытие БД через SQLCipher
# ==========================================================
def _open_encrypted():
    """Открыть зашифрованную БД через SQLCipher."""
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        log.critical(
            "SQLCipher не установлен! "
            "Установите: pip install sqlcipher3"
        )
        raise RuntimeError(
            "Не установлен SQLCipher.\n"
            "Выполните: pip install sqlcipher3"
        )

    key = get_or_create_key()

    conn = sqlcipher.connect(str(DB_FILE), timeout=10)
    conn.row_factory = sqlcipher.Row

    # Ключ задаётся через PRAGMA key в формате raw hex
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("PRAGMA foreign_keys=ON")

    # Проверка ключа: пробуем прочитать sqlite_master
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except Exception as e:
        conn.close()
        log.critical("Не удалось открыть БД — неверный ключ: %s", e)
        raise RuntimeError(
            "Неверный ключ БД.\n\n"
            "Возможно:\n"
            "  • .keyfile скопирован с другого компьютера;\n"
            "  • .keyfile повреждён;\n"
            "  • БД не зашифрована, но открывается через SQLCipher.\n\n"
            "Восстановите .keyfile из резервной копии."
        ) from e

    return conn


@contextmanager
def get_conn():
    """Контекстный менеджер соединения."""
    conn = _open_encrypted()
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


# ==========================================================
# Миграция открытой БД → зашифрованную
# ==========================================================
def _migrate_to_encrypted():
    """
    Перенести данные из открытой БД в зашифрованную.
    Старая БД сохраняется как .db.plain_backup.
    """
    log.info("Начата миграция БД → зашифрованный формат")

    # 1. Резервная копия старой БД
    backup = DB_FILE.with_suffix(".db.plain_backup")
    if backup.exists():
        backup.unlink()
    shutil.copy2(str(DB_FILE), str(backup))
    log.info("Резервная копия открытой БД: %s", backup.name)

    # 2. Читаем все данные из старой БД
    old_conn = sqlite3.connect(str(DB_FILE))
    old_conn.row_factory = sqlite3.Row

    tables = ["counters", "pool_codes", "queue", "audit",
              "gtin_map", "meta", "users", "security_questions"]

    # Собираем все строки ДО удаления файла
    old_data = {}
    for t in tables:
        try:
            rows = old_conn.execute(f"SELECT * FROM {t}").fetchall()
            old_data[t] = [dict(r) for r in rows]
            log.debug("Прочитано %s строк из %s", len(old_data[t]), t)
        except Exception as e:
            log.warning("Не удалось прочитать таблицу %s: %s", t, e)
            old_data[t] = []

    old_conn.close()

    # 3. Удаляем старые WAL/SHM
    for suffix in ("-wal", "-shm"):
        f = Path(str(DB_FILE) + suffix)
        if f.exists():
            f.unlink()

    # 4. Удаляем старый файл, чтобы SQLCipher создал новый
    DB_FILE.unlink()

    # 5. Создаём новую зашифрованную БД
    from sqlcipher3 import dbapi2 as sqlcipher
    key = get_or_create_key()

    new_conn = sqlcipher.connect(str(DB_FILE))
    new_conn.execute(f"PRAGMA key = \"x'{key}'\"")
    new_conn.executescript(SCHEMA)

    # 6. Переносим данные
    total = 0
    for t, rows in old_data.items():
        if not rows:
            continue
        cols = list(rows[0].keys())
        col_list = ",".join(cols)
        placeholders = ",".join("?" * len(cols))
        for row in rows:
            try:
                new_conn.execute(
                    f"INSERT INTO {t} ({col_list}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols)
                )
                total += 1
            except Exception as e:
                log.warning("Пропущена строка в %s: %s", t, e)
        log.info("Таблица %s: перенесено %s строк", t, len(rows))

    new_conn.commit()
    new_conn.close()

    log.info("Миграция завершена. Всего строк: %s", total)
    log.info("Резервная копия открытой БД: %s", backup)


# ==========================================================
# Инициализация
# ==========================================================
def init_db():
    """Создать/открыть БД. При необходимости — мигрировать в зашифрованный формат."""
    APP_DIR.mkdir(parents=True, exist_ok=True)

    # Если есть открытая БД — мигрируем
    if _is_valid_plain_sqlite():
        _migrate_to_encrypted()

    # Открываем зашифрованную (или создаём новую)
    conn = _open_encrypted()
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO counters (id) VALUES (1)")
        conn.commit()
        log.info("Схема БД актуальна")
                # --- Миграция: добавить колонки hmac в audit, если их нет ---
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audit)").fetchall()]
        if "hmac" not in cols:
            conn.execute("ALTER TABLE audit ADD COLUMN hmac TEXT")
            log.info("Добавлена колонка audit.hmac")
        if "prev_hmac" not in cols:
            conn.execute("ALTER TABLE audit ADD COLUMN prev_hmac TEXT")
            log.info("Добавлена колонка audit.prev_hmac")
    finally:
        conn.close()


def is_encrypted() -> bool:
    """Публичная функция — проверить, зашифрована ли БД."""
    return _is_encrypted_db()