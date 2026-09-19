"""
Хэширование и проверка паролей.
Использует PBKDF2-HMAC-SHA256 из стандартной библиотеки.
"""
import hashlib
import hmac
import os
import base64


ITERATIONS = 200_000
SALT_SIZE = 16
HASH_SIZE = 32


def hash_password(password: str) -> str:
    """
    Вернуть строку вида: pbkdf2_sha256$200000$<salt_b64>$<hash_b64>
    Эту строку можно безопасно хранить в БД.
    """
    salt = os.urandom(SALT_SIZE)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt, ITERATIONS, dklen=HASH_SIZE)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(dk).decode("ascii")
    return f"pbkdf2_sha256${ITERATIONS}${salt_b64}${hash_b64}"


def verify_password(password: str, stored: str) -> bool:
    """
    Проверить пароль по сохранённой строке.
    Возвращает True, если совпадает.
    """
    try:
        algo, iters_str, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iters_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception:
        return False

    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt, iterations, dklen=len(expected))
    return hmac.compare_digest(dk, expected)


# ---------- Утилита: создать хэши для дефолтных паролей ----------
def print_default_hashes():
    """Запусти файл напрямую, чтобы получить хэши для store.py."""
    defaults = [
        ("i.ivanov",     "ivanov123"),
        ("a.petrova",    "petrova123"),
        ("v.sidorov",    "sidorov123"),
        ("d.kuznetsov",  "kuznetsov123"),
        ("e.morozova",   "morozova123"),
    ]
    print("# Скопируй это в store.py")
    for login, pwd in defaults:
        h = hash_password(pwd)
        print(f'    "{login}": "{h}",')


if __name__ == "__main__":
    print_default_hashes()
