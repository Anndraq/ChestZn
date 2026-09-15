"""
Рендер GS1 DataMatrix в QPixmap.
Требует: treepoem + Ghostscript.
"""
import io
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

try:
    import treepoem
    _HAS_TREEPOEM = True
except ImportError:
    _HAS_TREEPOEM = False


def render_datamatrix(code: str, size: int = 120) -> QPixmap:
    """Вернуть QPixmap с DataMatrix. При ошибке — белый плейсхолдер."""
    if not code:
        return _placeholder(size)

    if not _HAS_TREEPOEM:
        print("[barcode] treepoem не установлен")
        return _placeholder(size)

    try:
        # BWIPP ждёт формат с круглыми скобками: (01)1234...(21)SN...(91)...(92)...
        # Он сам вставит FNC1. Никаких ^FNC1 не нужно.
        print(f"[barcode] рендер: {code[:60]}...")

        img = treepoem.generate_barcode(
            barcode_type="gs1datamatrix",
            data=code,
        )

        buf = io.BytesIO()
        img.save(buf, format="PNG")

        pix = QPixmap()
        if not pix.loadFromData(buf.getvalue(), "PNG"):
            print("[barcode] не удалось загрузить PNG")
            return _placeholder(size)

        return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    except Exception as e:
        print(f"[barcode] ошибка: {type(e).__name__}: {e}")
        return _placeholder(size)


def _placeholder(size: int, text: str = "") -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.white)
    return px