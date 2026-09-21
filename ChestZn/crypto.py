"""
Управление ключом шифрования БД ChestZn.

Ключ:
- генерируется случайно (256 бит) при первом запуске;
- хранится в %APPDATA%\ChestZn\.keyfile;
- шифруется через Windows DPAPI (CryptProtectData);
- может быть экспортирован/импортирован в base64 для переноса.
"""
import os
import secrets
import base64
from pathlib import Path

from logger import get_logger

log = get_logger("crypto")


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
KEY_FILE = APP_DIR / ".keyfile"
KEY_DPAPI_DESC = "ChestZn DB Encryption Key v1"


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
    """Прочитать ключ из .keyfile. None, если файла нет или ошибка."""
    if not KEY_FILE.exists():
        return None
    try:
        blob = KEY_FILE.read_bytes()
        raw = _dpapi_unprotect(blob)
        return raw.decode("utf-8")
    except Exception as e:
        log.error("Не удалось расшифровать .keyfile: %s", e)
        return None


def get_or_create_key() -> str:
    """Загрузить ключ или создать новый."""
    key = load_key()
    if key:
        log.debug("Ключ БД загружен")
        return key
    key = generate_key()
    save_key(key)
    log.info("Создан новый ключ БД")
    return key


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

    # HMAC-SHA256 от базового ключа с контекстной меткой
    return hmac_mod.new(
        key_bytes,
        b"chestzn-audit-hmac-v1",
        hashlib.sha256
    ).digest()