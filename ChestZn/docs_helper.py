"""
Работа с документацией ChestZn.
Копирование docs/ в %APPDATA% и открытие файлов.
"""
import os
import shutil
import sys
import webbrowser
from pathlib import Path


# ---------- Пути ----------
APP_DIR  = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
DOCS_DIR = APP_DIR / "docs"


def _project_docs_dir() -> Path:
    """
    Папка docs/ в проекте или внутри .exe (PyInstaller).
    """
    if hasattr(sys, "_MEIPASS"):
        # Собранный .exe — ресурсы распакованы во временную папку
        return Path(sys._MEIPASS) / "docs"
    # Запуск из исходников — рядом с этим файлом
    return Path(__file__).parent / "docs"


def ensure_docs_copied():
    """
    Скопировать docs/ из проекта в %APPDATA%\ChestZn\docs\.
    Копирует ВСЕГДА, чтобы обновления руководства подхватывались.
    Возвращает путь к итоговой папке.
    """
    src = _project_docs_dir()

    if not src.exists():
        print(f"[docs] папка docs не найдена: {src}")
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        return DOCS_DIR

    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Копируем все .md файлы
    for f in src.iterdir():
        if f.is_file():
            try:
                shutil.copy2(f, DOCS_DIR / f.name)
            except Exception as e:
                print(f"[docs] не удалось скопировать {f.name}: {e}")

    print(f"[docs] руководство обновлено: {DOCS_DIR}")
    return DOCS_DIR


def open_doc(name: str) -> bool:
    """
    Открыть файл руководства из %APPDATA%\ChestZn\docs\.
    name — например, 'install-gs.md' или 'problems.md'.
    Возвращает True, если файл открылся.
    """
    path = DOCS_DIR / name

    if not path.exists():
        print(f"[docs] файл не найден: {path}")
        return False

    try:
        os.startfile(str(path))
        return True
    except Exception as e:
        print(f"[docs] не удалось открыть {name}: {e}")
        return False


def open_docs_folder() -> bool:
    """Открыть папку с документацией в проводнике."""
    if not DOCS_DIR.exists():
        DOCS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        os.startfile(str(DOCS_DIR))
        return True
    except Exception as e:
        print(f"[docs] не удалось открыть папку: {e}")
        return False


def open_url(url: str) -> bool:
    """Открыть URL в браузере по умолчанию."""
    try:
        webbrowser.open(url)
        return True
    except Exception as e:
        print(f"[docs] не удалось открыть URL: {e}")
        return False


def open_app_folder() -> bool:
    """Открыть папку %APPDATA%\\ChestZn\\ в проводнике."""
    if not APP_DIR.exists():
        APP_DIR.mkdir(parents=True, exist_ok=True)

    try:
        os.startfile(str(APP_DIR))
        return True
    except Exception as e:
        print(f"[docs] не удалось открыть папку приложения: {e}")
        return False


# ---------- Тест из командной строки ----------
if __name__ == "__main__":
    print("=== docs_helper ===")
    print(f"APP_DIR:  {APP_DIR}")
    print(f"DOCS_DIR: {DOCS_DIR}")
    print()

    print("Копируем docs/...")
    ensure_docs_copied()

    print()
    print("Файлы в %APPDATA%\\ChestZn\\docs\\:")
    if DOCS_DIR.exists():
        for f in sorted(DOCS_DIR.iterdir()):
            size = f.stat().st_size
            print(f"  {f.name:25} {size:>6} байт")
    print()

    print("Все доступные функции:")
    print("  ensure_docs_copied()  — скопировать docs/ в APPDATA")
    print("  open_doc('name.md')   — открыть файл руководства")
    print("  open_docs_folder()    — открыть папку docs")
    print("  open_app_folder()     — открыть папку ChestZn")
    print("  open_url('https://')  — открыть ссылку в браузере")