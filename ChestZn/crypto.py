"""
Управление ключом шифрования БД ChestZn.

Ключ:
- генерируется случайно (256 бит) при первом запуске;
- хранится в %APPDATA%\ChestZn\.keyfile;
- шифруется через Windows DPAPI (CryptProtectData);
- может быть экспортирован/импортирован в base64 для переноса.

ВАЖНО: ключ НИКОГДА не пересоздаётся, если БД уже существует.
Если .keyfile потерян, но БД есть — выдаётся ошибка.
"""
import os
import secrets
import base64
from pathlib import Path

from logger import get_logger

log = get_logger("crypto")


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
KEY_FILE = APP_DIR / ".keyfile"
DB_FILE = APP_DIR / "chestzn.db"
KEY_DPAPI_DESC = "ChestZn DB Encryption Key v1"


class KeyLostError(RuntimeError):
    """Ключ шифрования потерян, но БД существует."""
    pass


class KeyCorruptedError(RuntimeError):
    """Файл ключа повреждён."""
    pass


# ==========================================================
# DPAPI обёртки
# ==========================================================
def _dpapi_protect(data: bytes) -> bytes:
    """Зашифровать данные через DPAPI (привязка к Windows-пользователю)."""
    try:
        import win32crypt
    except ImportError:
        raise RuntimeError(
            "Модуль pywin32 не установлен. "
            "Установите: pip install pywin32"
        )
    return win32crypt.CryptProtectData(
        data, KEY_DPAPI_DESC, None, None, None, 0
    )


def _dpapi_unprotect(blob: bytes) -> bytes:
    """Расшифровать данные через DPAPI."""
    try:
        import win32crypt
    except ImportError:
        raise RuntimeError("Модуль pywin32 не установлен")
    _desc, data = win32crypt.CryptUnprotectData(
        blob, None, None, None, 0
    )
    return data


# ==========================================================
# Генерация и хранение ключа
# ==========================================================
def generate_key() -> str:
    """Сгенерировать случайный ключ (hex, 64 символа = 256 бит)."""
    return secrets.token_hex(32)


def save_key(key: str):
    """Сохранить ключ в .keyfile, зашифровав через DPAPI."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    raw = key.encode("utf-8")
    encrypted = _dpapi_protect(raw)
    KEY_FILE.write_bytes(encrypted)
    log.info("Ключ БД сохранён в %s", KEY_FILE)


def load_key():
    """
    Прочитать ключ из .keyfile.
    Возвращает:
      - str  — ключ, если файл есть и расшифрован;
      - None — если файла нет;
    Исключения:
      - KeyCorruptedError — если файл есть, но не расшифровывается.
    """
    if not KEY_FILE.exists():
        return None
    try:
        blob = KEY_FILE.read_bytes()
        raw = _dpapi_unprotect(blob)
        key = raw.decode("utf-8")
        # Проверяем формат — 64 hex-символа
        if len(key) != 64:
            raise ValueError(f"Неверная длина ключа: {len(key)}")
        int(key, 16)
        return key
    except Exception as e:
        log.error("Не удалось расшифровать .keyfile: %s", e)
        raise KeyCorruptedError(
            f"Файл ключа повреждён или создан на другом ПК/пользователе.\n"
            f"Файл: {KEY_FILE}\n"
            f"Ошибка: {e}\n\n"
            f"Восстановите .keyfile из резервной копии."
        )


def get_or_create_key() -> str:
    """
    Загрузить ключ или создать новый (ТОЛЬКО при первом запуске).

    Логика:
      - Если .keyfile есть и расшифрован → вернуть его.
      - Если .keyfile есть, но не расшифрован → ошибка KeyCorruptedError.
      - Если .keyfile нет, но БД есть → ошибка KeyLostError.
      - Если .keyfile нет и БД нет → создать новый ключ (первый запуск).
    """
    # 1. Пробуем загрузить существующий ключ
    key = load_key()   # может поднять KeyCorruptedError

    if key is not None:
        log.debug("Ключ БД загружен")
        return key

    # 2. Ключа нет. Смотрим — есть ли БД?
    if DB_FILE.exists():
        log.critical(
            "Ключ шифрования отсутствует, но БД существует: %s",
            DB_FILE
        )
        raise KeyLostError(
            f"Ключ шифрования потерян, но база данных существует.\n\n"
            f"Файл БД: {DB_FILE}\n"
            f"Файл ключа: {KEY_FILE} (отсутствует)\n\n"
            f"Восстановите .keyfile из резервной копии\n"
            f"или обратитесь к администратору системы.\n\n"
            f"ВНИМАНИЕ: НЕ удаляйте БД — данные ещё можно восстановить."
        )

    # 3. Ни ключа, ни БД — это первый запуск. Создаём новый ключ.
    key = generate_key()
    save_key(key)
    log.info("Создан новый ключ БД (первый запуск)")
    return key


# ==========================================================
# Проверка соответствия ключа БД
# ==========================================================
def key_matches_db(key: str) -> bool:
    """
    Проверить, что ключ подходит к существующей БД.
    Возвращает True, если БД открывается с этим ключом.
    """
    if not DB_FILE.exists():
        return True   # БД ещё нет — любой ключ подойдёт

    try:
        from sqlcipher3 import dbapi2 as sqlcipher
        conn = sqlcipher.connect(str(DB_FILE))
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        # Пробуем выполнить запрос — если ключ неверный, упадёт
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.close()
        return True
    except Exception as e:
        log.error("Ключ не подходит к БД: %s", e)
        return False


# ==========================================================
# Экспорт / импорт для переноса
# ==========================================================
def export_key_for_backup() -> str:
    """
    Экспортировать ключ в base64 для резервной копии / переноса.
    ВНИМАНИЕ: сохраняйте в защищённое место!
    """
    key = load_key()
    if not key:
        raise RuntimeError("Ключ не найден")
    return base64.b64encode(key.encode("utf-8")).decode("ascii")


def import_key_from_backup(b64: str) -> bool:
    """Импортировать ключ из резервной копии (base64)."""
    try:
        key = base64.b64decode(b64).decode("utf-8")
        if len(key) != 64:
            raise ValueError(f"Неверная длина ключа: {len(key)} (ожидается 64)")
        # Проверить, что это hex
        int(key, 16)
        save_key(key)
        log.info("Ключ БД импортирован из резервной копии")
        return True
    except Exception as e:
        log.error("Ошибка импорта ключа: %s", e)
        return False


def key_exists() -> bool:
    """Проверить, есть ли .keyfile."""
    return KEY_FILE.exists()


def key_file_path() -> str:
    """Путь к файлу ключа — для отображения в UI."""
    return str(KEY_FILE)


# ==========================================================
# Производные ключи
# ==========================================================
def get_hmac_key() -> bytes:
    """
    Производный ключ для HMAC журнала audit.
    Не совпадает с ключом шифрования — если один скомпрометируют, другой останется.
    """
    import hmac as hmac_mod
    import hashlib

    base_key = get_or_create_key()
    key_bytes = bytes.fromhex(base_key)

    return hmac_mod.new(
        key_bytes,
        b"chestzn-audit-hmac-v1",
        hashlib.sha256
    ).digest()