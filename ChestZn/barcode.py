"""
Рендер GS1 DataMatrix в QPixmap.
С кэшированием — не запускает Ghostscript для уже отрендеренных кодов.
"""
import io
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

try:
    import treepoem
    _HAS_TREEPOEM = True
except ImportError:
    _HAS_TREEPOEM = False


# ==========================================================
# Кэш отрендеренных DataMatrix
# ==========================================================
# { (code, size): QPixmap }
_CACHE = {}
_CACHE_LIMIT = 200


def _placeholder(size: int, text: str = "") -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.white)
    return px


def clear_cache():
    """Очистить кэш DataMatrix."""
    _CACHE.clear()


def render_datamatrix(code: str, size: int = 120) -> QPixmap:
    """
    Вернуть QPixmap с DataMatrix.
    При повторном вызове с тем же кодом и размером — берёт из кэша.
    """
    if not code:
        return _placeholder(size, "нет кода")

    # Ключ кэша
    cache_key = (code, size)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    if not _HAS_TREEPOEM:
        return _placeholder(size, "treepoem?")

    try:
        img = treepoem.generate_barcode(
            barcode_type="gs1datamatrix",
            data=code,
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pix = QPixmap()
        if not pix.loadFromData(buf.getvalue(), "PNG"):
            return _placeholder(size, "load err")

        result = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        # Сохранить в кэш
        if len(_CACHE) >= _CACHE_LIMIT:
            # Удалить первый добавленный (FIFO)
            try:
                _CACHE.pop(next(iter(_CACHE)))
            except Exception:
                pass
        _CACHE[cache_key] = result

        return result
    except Exception as e:
        print(f"[barcode] {e}")
        return _placeholder(size, "err")