"""
Рендер GS1 DataMatrix в QPixmap.
С кэшированием — не запускает Ghostscript для уже отрендеренных кодов.
"""
import io
import re
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

try:
    import treepoem
    _HAS_TREEPOEM = True
except ImportError:
    _HAS_TREEPOEM = False


_CACHE = {}
_CACHE_LIMIT = 200


# ============================================================
# Санитайз SN для bwipp (без скобок и запрещённых символов)
# ============================================================

def _sanitize_sn_for_bwipp(sn: str) -> str:
    """
    bwipp требует CSET 82 и не терпит скобок в SN.
    Убираем всё, что ломает парсер.
    """
    if not sn:
        return sn
    allowed = set(
        "0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "!\"%&'*+,-./:;<=>?_ "   # ← БЕЗ ( )
    )
    return "".join(ch for ch in sn if ch in allowed)


# ============================================================
# Преобразование кода ЧЗ в скобочный формат bwipp
# ============================================================

def to_gs1_paren(code: str) -> str:
    """
    Преобразовать код ЧЗ из '01...21...\\x1d91...\\x1d92...'
    в формат treepoem: '(01)...(21)...(91)...(92)...'.

    SN, verify, crypto очищаются от ( ), чтобы bwipp не путался.
    """
    if not code:
        return code

    s = str(code).strip()

    # Уже в формате со скобками? Возвращаем как есть
    if s.startswith("("):
        return s

    # Нормализуем разделители
    s = s.replace("_x001D_", "\x1d")

    # Основной шаблон: 01 <gtin> 21 <sn> [GS] 91 <verify> [GS] 92 <crypto>
    m = re.match(
        r"^01(\d{14})21(.+?)(?:\x1d)?91([^\x1d]+)(?:\x1d)?92(.+)$",
        s, re.DOTALL,
    )
    if m:
        gtin, sn, verify, crypto = m.groups()
        sn = _sanitize_sn_for_bwipp(sn)
        verify = _sanitize_sn_for_bwipp(verify)
        crypto = _sanitize_sn_for_bwipp(crypto)
        return f"(01){gtin}(21){sn}(91){verify}(92){crypto}"

    # Fallback: только 01 и 21
    m = re.match(r"^01(\d{14})21(.+)$", s, re.DOTALL)
    if m:
        gtin, sn = m.groups()
        sn = _sanitize_sn_for_bwipp(sn)
        return f"(01){gtin}(21){sn}"

    # Fallback: только 01
    m = re.match(r"^01(\d{14})$", s)
    if m:
        return f"(01){m.group(1)}"

    return s


def _placeholder(size: int, text: str = "") -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.white)
    return px


def clear_cache():
    _CACHE.clear()


def render_datamatrix(code: str, size: int = 120) -> QPixmap:
    """
    Вернуть QPixmap с DataMatrix.
    Автоматически конвертирует код в GS1-формат со скобками.
    """
    if not code:
        return _placeholder(size)

    cache_key = (code, size)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    if not _HAS_TREEPOEM:
        return _placeholder(size)

    try:
        # Конвертируем в формат treepoem (со скобками)
        gs1_code = to_gs1_paren(code)

        img = treepoem.generate_barcode(
            barcode_type="gs1datamatrix",
            data=gs1_code,
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pix = QPixmap()
        if not pix.loadFromData(buf.getvalue(), "PNG"):
            return _placeholder(size)

        result = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        if len(_CACHE) >= _CACHE_LIMIT:
            try:
                _CACHE.pop(next(iter(_CACHE)))
            except Exception:
                pass
        _CACHE[cache_key] = result

        return result
    except Exception as e:
        print(f"[barcode] {e}")
        return _placeholder(size)