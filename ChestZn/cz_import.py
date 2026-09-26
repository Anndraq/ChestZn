"""
Парсер файлов ЦРПТ (.xlsx) для импорта кодов «Честного знака».

Формат файла «КМ для поставщика»:
  Лист 1 «Лист_1»:
    A: Ключ
    B: GTIN
    C: Артикул
    D: Размер
    E: Цвет
    F: Код маркировки
    G: Длина ключа
    H: Заказ

Код маркировки имеет структуру GS1:
  01<GTIN(14)>21<SN><GS>91<verify><GS>92<crypto>

где <GS> — символ \x1d (Group Separator).
В Excel он показан как _x001D_ или похожим escape.
"""
import re
from pathlib import Path

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False


# ==========================================================
# Парсер кода ЧЗ
# ==========================================================
def parse_cz_code(raw: str) -> dict:
    """
    Разобрать код ЧЗ.
    Возвращает {gtin, sn, verify, crypto} или пустые поля.

    Поддерживает разделители:
    - \x1d (реальный GS)
    - _x001D_ (escape от Excel)
    - просто склеенные AI (без разделителей)
    """
    if not raw:
        return {"gtin": None, "sn": None, "verify": None, "crypto": None}

    s = str(raw)

    # Заменяем escape-последовательность на реальный GS
    s = s.replace("_x001D_", "\x1d")

    # Убираем пробелы и переводы строк
    s = s.strip()

    # Основной шаблон: 01<gtin>21<sn>[\x1d]91<verify>[\x1d]92<crypto>
    # Разделитель \x1d необязателен
    pattern = re.compile(
        r"^01(\d{14})21(.+?)(?:\x1d)?91([^\x1d]+)(?:\x1d)?92(.+)$",
        re.DOTALL,
    )
    m = pattern.match(s)
    if m:
        return {
            "gtin": m.group(1),
            "sn": m.group(2),
            "verify": m.group(3),
            "crypto": m.group(4),
        }

    # Fallback: только GTIN и SN (без 91/92)
    m = re.match(r"^01(\d{14})21(.+)$", s, re.DOTALL)
    if m:
        return {
            "gtin": m.group(1),
            "sn": m.group(2),
            "verify": None,
            "crypto": None,
        }

    # Fallback: просто GTIN
    m = re.match(r"^01(\d{14})", s)
    if m:
        return {
            "gtin": m.group(1),
            "sn": None,
            "verify": None,
            "crypto": None,
        }

    return {"gtin": None, "sn": None, "verify": None, "crypto": None}


# ==========================================================
# Парсер xlsx
# ==========================================================
def _find_header_row(ws) -> tuple:
    """
    Найти строку заголовков.
    Возвращает (row_index, {имя_колонки: индекс}).
    """
    expected = {"gtin", "код маркировки", "артикул", "заказ"}
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=10,
                                          values_only=True), start=1):
        if not row:
            continue
        headers = {}
        for j, cell in enumerate(row):
            if cell is None:
                continue
            key = str(cell).strip().lower()
            headers[key] = j
        # Если есть GTIN и Код маркировки — считаем это заголовком
        if "gtin" in headers and "код маркировки" in headers:
            return i, headers
    return None, None


def parse_xlsx(path: str) -> list:
    """
    Прочитать xlsx от ЦРПТ (Лист 1).
    Возвращает список словарей:
      {gtin, code, sn, article, size, color, order, raw}
    """
    if not _HAS_OPENPYXL:
        raise RuntimeError(
            "Не установлен openpyxl.\n"
            "Выполните: pip install openpyxl"
        )

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # Выбираем первый лист (обычно «Лист_1»)
    ws = None
    for name in wb.sheetnames:
        if name.lower().startswith("лист"):
            ws = wb[name]
            break
    if ws is None:
        ws = wb[wb.sheetnames[0]]

    header_idx, headers = _find_header_row(ws)
    if header_idx is None:
        wb.close()
        raise ValueError(
            "Не найдены заголовки «GTIN» и «Код маркировки» на первом листе"
        )

    # Индексы колонок
    col = {
        "gtin":   headers.get("gtin"),
        "code":   headers.get("код маркировки"),
        "article": headers.get("артикул"),
        "size":   headers.get("размер"),
        "color":  headers.get("цвет"),
        "order":  headers.get("заказ"),
        "key":    headers.get("ключ"),
    }

    rows = []
    for row in ws.iter_rows(min_row=header_idx + 1, values_only=True):
        if not row or not any(row):
            continue

        def get(name):
            idx = col.get(name)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        raw_code = get("code")
        if not raw_code:
            continue

        raw_code = str(raw_code).strip()
        parsed = parse_cz_code(raw_code)

        gtin = parsed["gtin"]
        if not gtin:
            continue

        rows.append({
            "gtin":    gtin,
            "code":    raw_code,
            "sn":      parsed["sn"] or "—",
            "verify":  parsed["verify"],
            "crypto":  parsed["crypto"],
            "article": str(get("article") or "").strip() or None,
            "size":    str(get("size") or "").strip() or None,
            "color":   str(get("color") or "").strip() or None,
            "order":   str(get("order") or "").strip() or None,
        })

    wb.close()

    # Проставляем order, если где-то пусто (берём самый частый)
    orders = [r["order"] for r in rows if r["order"]]
    if orders:
        most_common = max(set(orders), key=orders.count)
        for r in rows:
            if not r["order"]:
                r["order"] = most_common

    return rows


# ==========================================================
# CLI для проверки
# ==========================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Использование: python cz_import.py <файл.xlsx>")
        sys.exit(1)

    try:
        data = parse_xlsx(sys.argv[1])
    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)

    print(f"Прочитано строк: {len(data)}")

    # Группировка по GTIN
    from collections import Counter
    gtins = Counter(r["gtin"] for r in data)
    print(f"Уникальных GTIN: {len(gtins)}")
    for g, c in gtins.most_common():
        print(f"  {g}: {c}")

    # Заказы
    orders = set(r["order"] for r in data if r["order"])
    print(f"Заказы: {', '.join(orders)}")

    # Пример первой строки
    if data:
        r = data[0]
        print()
        print("Пример первой строки:")
        print(f"  GTIN:    {r['gtin']}")
        print(f"  SN:      {r['sn']}")
        print(f"  Артикул: {r['article']}")
        print(f"  Размер:  {r['size']}")
        print(f"  Цвет:    {r['color']}")
        print(f"  Заказ:   {r['order']}")