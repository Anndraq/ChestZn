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
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    gtin           TEXT NOT NULL,
    code           TEXT NOT NULL UNIQUE,
    sn             TEXT NOT NULL,
    used           INTEGER NOT NULL DEFAULT 0,
    printed        INTEGER NOT NULL DEFAULT 0,
    point          TEXT,
    used_point     TEXT,
    assigned_point TEXT,
    import_batch   TEXT,
    printed_at     TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pool_gtin_used ON pool_codes(gtin, used);
CREATE INDEX IF NOT EXISTS idx_pool_printed   ON pool_codes(printed);

-- ВАЖНО: foreign_code БЕЗ UNIQUE.
-- Один и тот же иностранный ШК может быть на нескольких единицах товара
-- (например, одежда без серийников). Повторные сканы разрешены.
CREATE TABLE IF NOT EXISTS scanned_shk (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    foreign_code TEXT NOT NULL,
    foreign_gtin TEXT NOT NULL,
    foreign_sn   TEXT,
    cz_code      TEXT NOT NULL,
    cz_gtin      TEXT NOT NULL,
    cz_sn        TEXT NOT NULL,
    point        TEXT NOT NULL,
    operator     TEXT,
    scanned_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_scanned_foreign ON scanned_shk(foreign_code);
CREATE INDEX IF NOT EXISTS idx_scanned_cz      ON scanned_shk(cz_code);

-- Счётчик сканов по GTIN + точка.
-- Нужен для «умного варианта»: если сканов > кодов в пуле —
-- значит оператор пикнул лишний раз, ушёл лишний код.
CREATE TABLE IF NOT EXISTS gtin_counters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    gtin         TEXT NOT NULL,
    point        TEXT NOT NULL,
    scans        INTEGER NOT NULL DEFAULT 0,
    last_scan_at TEXT,
    UNIQUE(gtin, point)
);
CREATE INDEX IF NOT EXISTS idx_gtin_counters_gtin
    ON gtin_counters(gtin, point);

CREATE TABLE IF NOT EXISTS import_batches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    file_name    TEXT NOT NULL,
    point        TEXT NOT NULL,
    total_codes  INTEGER NOT NULL DEFAULT 0,
    imported_by  TEXT,
    imported_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

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

    
CREATE TABLE IF NOT EXISTS points (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    address        TEXT,
    ip             TEXT,
    printer        TEXT,
    operator       TEXT,
    online         INTEGER NOT NULL DEFAULT 1,
    template       TEXT,
    computers_json TEXT NOT NULL DEFAULT '[]',
    sort_order     INTEGER NOT NULL DEFAULT 100
);

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
# Миграция: убрать UNIQUE на scanned_shk.foreign_code
# ==========================================================
def _migrate_scanned_shk_drop_unique(conn):
    """
    Пересоздать таблицу scanned_shk без UNIQUE на foreign_code.
    Нужно для одежды — один и тот же иностранный ШК
    может быть на нескольких единицах товара.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scanned_shk'"
    ).fetchone()

    if not row:
        # Таблицы нет — SCHEMA создаст её сразу без UNIQUE
        return

    table_sql = (row["sql"] if isinstance(row, dict) or hasattr(row, "keys")
                 else row[0]) or ""

    if "UNIQUE" not in table_sql.upper():
        # Уже без UNIQUE — ничего не делаем
        return

    log.info("Миграция scanned_shk: убираю UNIQUE на foreign_code")

    # 1. Проверяем, какие колонки реально есть (на случай старых версий)
    existing_cols = [
        r[1] for r in conn.execute("PRAGMA table_info(scanned_shk)").fetchall()
    ]
    log.debug("scanned_shk колонки: %s", existing_cols)

    # 2. Создаём новую таблицу БЕЗ UNIQUE
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanned_shk_new (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            foreign_code TEXT NOT NULL,
            foreign_gtin TEXT NOT NULL,
            foreign_sn   TEXT,
            cz_code      TEXT NOT NULL,
            cz_gtin      TEXT NOT NULL,
            cz_sn        TEXT NOT NULL,
            point        TEXT NOT NULL,
            operator     TEXT,
            scanned_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 3. Переносим данные (только те колонки, которые есть)
    common = [c for c in [
        "id", "foreign_code", "foreign_gtin", "foreign_sn",
        "cz_code", "cz_gtin", "cz_sn", "point", "operator", "scanned_at"
    ] if c in existing_cols]

    cols_csv = ",".join(common)
    conn.execute(
        f"INSERT INTO scanned_shk_new ({cols_csv}) "
        f"SELECT {cols_csv} FROM scanned_shk"
    )

    # 4. Удаляем старую, переименовываем новую
    conn.execute("DROP TABLE scanned_shk")
    conn.execute("ALTER TABLE scanned_shk_new RENAME TO scanned_shk")

    # 5. Восстанавливаем индексы
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scanned_foreign "
        "ON scanned_shk(foreign_code)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scanned_cz "
        "ON scanned_shk(cz_code)"
    )

    log.info("Миграция scanned_shk: UNIQUE убран, индексы пересозданы")


# ==========================================================
# Миграция: создать gtin_counters (если ещё нет)
# ==========================================================
def _migrate_gtin_counters_create(conn):
    """
    Создать таблицу gtin_counters, если её нет.
    На свежих БД её создаст SCHEMA, но для существующих БД — нужна миграция.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='gtin_counters'"
    ).fetchone()

    if row:
        return  # уже есть

    log.info("Миграция: создаю таблицу gtin_counters")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gtin_counters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            gtin         TEXT NOT NULL,
            point        TEXT NOT NULL,
            scans        INTEGER NOT NULL DEFAULT 0,
            last_scan_at TEXT,
            UNIQUE(gtin, point)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gtin_counters_gtin "
        "ON gtin_counters(gtin, point)"
    )
    log.info("Таблица gtin_counters создана")


# ==========================================================
# Миграция схемы (для существующих БД)
# ==========================================================
def _migrate_schema(conn):
    """
    Добавить новые колонки / таблицы в существующие БД.
    Безопасно вызывается при каждом запуске.
    """
    # --- audit.hmac / prev_hmac ---
    cols = [r[1] for r in conn.execute("PRAGMA table_info(audit)").fetchall()]
    if "hmac" not in cols:
        conn.execute("ALTER TABLE audit ADD COLUMN hmac TEXT")
        log.info("Добавлена колонка audit.hmac")
    if "prev_hmac" not in cols:
        conn.execute("ALTER TABLE audit ADD COLUMN prev_hmac TEXT")
        log.info("Добавлена колонка audit.prev_hmac")

    # --- Миграция: добавить config_json в label_templates ---
    cols = [r[1] for r in conn.execute("PRAGMA table_info(label_templates)").fetchall()]
    if "config_json" not in cols:
            conn.execute("ALTER TABLE label_templates ADD COLUMN config_json TEXT")
            log.info("Добавлена колонка label_templates.config_json")

            # Перенести старые поля в новый формат
            import json
            rows = conn.execute(
                "SELECT id, code, name, is_default, width_mm, height_mm, dpi, fields_json "
                "FROM label_templates"
            ).fetchall()

            for r in rows:
                try:
                    old_fields = json.loads(r["fields_json"] or "[]")
                except Exception:
                    old_fields = []

                # По умолчанию — режим combined
                config = {
                    "print_mode": "combined",
                    "combined": {
                        "w": r["width_mm"] or 58,
                        "h": r["height_mm"] or 40,
                        "dpi": r["dpi"] or 203,
                        "fields": old_fields,
                    },
                    "cz": {
                        "w": 30, "h": 30, "dpi": 203,
                        "fields": [
                            {"name": "dataMatrix", "label": "",
                             "required": True, "font_size": 0, "x": 0, "y": 0}
                        ],
                    },
                    "label": {
                        "w": r["width_mm"] or 58,
                        "h": r["height_mm"] or 40,
                        "dpi": r["dpi"] or 203,
                        "fields": old_fields,
                    },
                }
                conn.execute(
                    "UPDATE label_templates SET config_json = ? WHERE id = ?",
                    (json.dumps(config, ensure_ascii=False), r["id"])
                )
            log.info("Миграция шаблонов: %s записей", len(rows))

    # --- pool_codes.assigned_point / import_batch / used_point ---
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pool_codes)").fetchall()]
    if "assigned_point" not in cols:
        conn.execute("ALTER TABLE pool_codes ADD COLUMN assigned_point TEXT")
        log.info("Добавлена колонка pool_codes.assigned_point")
    if "import_batch" not in cols:
        conn.execute("ALTER TABLE pool_codes ADD COLUMN import_batch TEXT")
        log.info("Добавлена колонка pool_codes.import_batch")
    if "used_point" not in cols:
        conn.execute("ALTER TABLE pool_codes ADD COLUMN used_point TEXT")
        log.info("Добавлена колонка pool_codes.used_point")

    # --- Заполнить assigned_point для старых кодов ---
    try:
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM pool_codes "
            "WHERE assigned_point IS NULL OR assigned_point = ''"
        ).fetchone()["c"]

        if before > 0:
            conn.execute(
                "UPDATE pool_codes "
                "SET assigned_point = COALESCE(point, 'T-01') "
                "WHERE assigned_point IS NULL OR assigned_point = ''"
            )
            log.info("Заполнено assigned_point для %s старых кодов", before)
    except Exception as e:
        log.warning("Не удалось заполнить assigned_point: %s", e)

    # --- Индекс (только после ALTER TABLE) ---
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pool_gtin_point_used "
        "ON pool_codes(gtin, assigned_point, used)"
    )

    # --- NEW: убрать UNIQUE на scanned_shk.foreign_code ---
    _migrate_scanned_shk_drop_unique(conn)

    # --- NEW: создать gtin_counters ---
    _migrate_gtin_counters_create(conn)


# ==========================================================
# Инициализация
# ==========================================================
def init_db():
    """
    Создать/открыть БД.
    При необходимости — мигрировать в зашифрованный формат,
    затем добавить новые колонки.
    """
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

        # Миграции существующих таблиц
        _migrate_schema(conn)
        conn.commit()

        log.info("Схема БД актуальна")
    finally:
        conn.close()


def is_encrypted() -> bool:
    """Публичная функция — проверить, зашифрована ли БД."""
    return _is_encrypted_db()