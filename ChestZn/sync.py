"""
Модуль синхронизации: «MS SQL Server ↔ Мастер-ПК (связующее звено / шлюз) ↔ Обычный ПК (Рабочий пост)».

Архитектура работы:
1. Где хранятся коды:
   - Все коды (`ШК` товара и `ДМ` DataMatrix ЧЗ) хранятся централизованно на **MS SQL Server**.
   - **Мастер-ПК** НЕ хранит коды как склад, а выступает **связующим звеном (мостом/шлюзом)** между
     MS SQL сервером и обычными рабочими ПК.
2. Логика работы Обычного ПК во вкладке «Синхронизация»:
   - Обычный ПК нажимает кнопку «Запросить коды с сервера (через Мастер-ПК)» → открывается окно.
   - При открытии окна Мастер-ПК запрашивает актуальные партии с MS SQL сервера и формирует в транзитном
     каталоге `sync/master/packages/` понятные для старшего смены именованные JSON-файлы, например:
     `Заказ_101_Кроссовки_Черные__ШК_4607001234567__40шт.json`
   - Старший смены выбирает нужный JSON-файл → файл вытягивается с сервера и через Мастер-ПК
     отправляется на обычный ПК, где коды (`ДМ` + связка `ШК -> ДМ`) загружаются в локальную БД рабочего поста.
3. Логика обратной связи (Обычный ПК → Мастер-ПК → MS SQL Server):
   - Обычный ПК отправляет отчёт о напечатанных кодах через Мастер-ПК напрямую на MS SQL Server
     (и оставляет транзитную копию в `sync/master/inbox/`).
"""
import os
import re
import json
import threading
import socket
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List

from logger import get_logger
log = get_logger("sync")

SYNC_VERSION = 2


# ==============================================================================
# 1. РОЛЬ ПК: МАСТЕР-ПК (ШЛЮЗ К СЕРВЕРУ) vs ОБЫЧНЫЙ ПК (РАБОЧИЙ ПОСТ)
# ==============================================================================
def get_pc_role(store=None) -> str:
    """
    Возвращает роль текущего ПК:
    - 'master' — Мастер-ПК (связующее звено между MS SQL Server и обычными ПК)
    - 'worker' — Обычный ПК (рабочий пост упаковки, вытягивает JSON-пакеты с сервера через Мастер-ПК)
    """
    if store is not None:
        try:
            role = store.get_setting("pc_role")
            if role in ("master", "worker"):
                return role
        except Exception:
            pass

    cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            role = data.get("pc_role")
            if role in ("master", "worker"):
                return role
        except Exception:
            pass
    return "master"


def set_pc_role(store, role: str, pc_name: str = "") -> str:
    """Сохраняет выбранную роль ПК ('master' | 'worker') и имя узла в БД и config.json."""
    norm_role = "worker" if str(role).lower().strip() == "worker" else "master"
    node_name = (pc_name or "").strip() or socket.gethostname() or "PC-01"

    if store is not None:
        try:
            store.set_setting("pc_role", norm_role)
            store.set_setting("pc_node_name", node_name)
        except Exception:
            pass

    cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["pc_role"] = norm_role
        data["pc_node_name"] = node_name
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Не удалось сохранить pc_role в config.json: %s", e)

    return norm_role


def get_pc_node_name(store=None) -> str:
    """Возвращает человекочитаемое имя этого ПК (например, 'La1mer' или hostname)."""
    if store is not None:
        try:
            val = store.get_setting("pc_node_name")
            if val and val.strip():
                return val.strip()
        except Exception:
            pass
    return socket.gethostname() or "PC-01"


# ==============================================================================
# 2. СТРУКТУРА ТРАНЗИТНОГО КАНАЛА МАСТЕР-ПК (МОСТ МЕЖДУ СЕРВЕРОМ И ОБЫЧНЫМИ ПК)
# ==============================================================================
def _default_folder() -> Path:
    import db
    return db.APP_DIR / "sync"


def get_sync_folder(store=None) -> Path:
    """Транзитная папка шлюза Мастер-ПК. Переопределяется через settings['sync_folder']."""
    custom = None
    if store is not None:
        try:
            custom = store.get_setting("sync_folder")
        except Exception:
            custom = None
    if custom and str(custom).strip():
        return Path(str(custom).strip())
    return _default_folder()


def ensure_layout(folder: Path) -> None:
    (folder / "master" / "outbox").mkdir(parents=True, exist_ok=True)
    (folder / "master" / "inbox").mkdir(parents=True, exist_ok=True)
    (folder / "master" / "packages").mkdir(parents=True, exist_ok=True)
    (folder / "master" / "issued").mkdir(parents=True, exist_ok=True)


def packages_dir(folder: Path) -> Path:
    d = folder / "master" / "packages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def outbox_path(folder: Path, point_id: str) -> Path:
    return folder / "master" / "outbox" / f"to_{point_id}.json"


def inbox_path(folder: Path, point_id: str) -> Path:
    return folder / "master" / "inbox" / f"from_{point_id}.json"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("Не удалось прочитать %s: %s", path, e)
        return None


# ==============================================================================
# 3. ФОРМИРОВАНИЕ И ВЫТЯГИВАНИЕ ИМЕНОВАННЫХ JSON-ФАЙЛОВ С MS SQL ЧЕРЕЗ МАСТЕР-ПК
# ==============================================================================
def _sanitize_filename_part(text: str, max_len: int = 44) -> str:
    """Очищает название партии/заказа/ШК для безопасного и понятного имени JSON-файла."""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned.strip("_.")
    if not cleaned:
        return "Партия_Сервер"
    return cleaned[:max_len]


def build_readable_package_filename(
    batch_title: str,
    shk: str,
    gtin: str,
    count: int,
    include_timestamp: bool = False,
) -> str:
    """
    Формирует понятное старшему смены имя JSON-файла с сервера, например:
    `Заказ_101_Кроссовки_Черные__ШК_4607001234567__40шт.json`
    (Детерминированное имя без лишних секунд позволяет обновлять витрину пакетов с MS SQL сервера
    без создания дубликатов файлов при каждом открытии окна на обычном ПК).
    """
    safe_title = _sanitize_filename_part(batch_title or "Партия_Сервер", 40)
    shk_label = _sanitize_filename_part(shk or gtin or "БезШК", 20)
    if include_timestamp:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        return f"{safe_title}__ШК_{shk_label}__{int(count)}шт__{stamp}.json"
    return f"{safe_title}__ШК_{shk_label}__{int(count)}шт.json"


def create_named_json_packages_from_records(
    records: List[Dict[str, Any]],
    folder: Optional[Path] = None,
    store=None,
    custom_title: str = "",
    source_label: str = "MS SQL Server (через Мастер-ПК)",
) -> Dict[str, Any]:
    """
    Принимает список записей [{'code': '<ДМ>', 'gtin': '<GTIN>', 'sn': '<SN>', 'shk': '<ШК>', 'order_id': '...'}],
    полученных с MS SQL Server через Мастер-ПК, группирует их по (Заказ/Партия, ШК/GTIN)
    и выкладывает в транзитную витрину `sync/master/packages/` в виде понятных старшему смены JSON-файлов.
    Если пакет уже был ранее вытянут каким-то обычным ПК (`claimed_by`), сохраняет эту отметку.
    """
    if not records:
        return {"ok": False, "packages": [], "total_codes": 0, "info": "На сервере нет кодов для формирования JSON-пакетов"}

    folder = folder or get_sync_folder(store)
    ensure_layout(folder)
    pkg_dir = packages_dir(folder)

    gs1_re = re.compile(r"^(?:01|\(01\))(\d{14})(?:21|\(21\))([^\x1d\(\r\n]{1,24})")
    grouped: Dict[str, Dict[str, Any]] = {}

    for rec in records:
        dm_code = str(rec.get("code") or rec.get("mark_code") or "").strip()
        if not dm_code:
            continue

        raw_gtin = str(rec.get("gtin") or "").strip()
        raw_shk = str(rec.get("shk") or rec.get("barcode") or "").strip()
        sn = str(rec.get("sn") or rec.get("serial") or "").strip()
        batch_name = (
            custom_title.strip()
            or str(rec.get("order_id") or rec.get("batch") or rec.get("product_name") or "").strip()
            or "Партия_MS_SQL"
        )

        m = gs1_re.match(dm_code.lstrip("\x1d"))
        dm_gtin14 = m.group(1) if m else ""
        dm_sn = m.group(2) if m else ""

        if not sn and dm_sn:
            sn = dm_sn

        digits_gtin = re.sub(r"\D", "", raw_gtin)
        digits_shk = re.sub(r"\D", "", raw_shk)

        if len(digits_shk) == 13:
            shk = digits_shk
        elif len(digits_gtin) == 13:
            shk = digits_gtin
        elif len(dm_gtin14) == 14 and dm_gtin14.startswith("0"):
            shk = dm_gtin14[1:]
        else:
            shk = digits_shk or digits_gtin or dm_gtin14

        if len(dm_gtin14) == 14:
            cz_gtin = dm_gtin14
        elif len(digits_gtin) == 14:
            cz_gtin = digits_gtin
        elif len(digits_gtin) == 13:
            cz_gtin = "0" + digits_gtin
        elif len(shk) == 13:
            cz_gtin = "0" + shk
        else:
            cz_gtin = digits_gtin or shk or "00000000000000"

        group_key = f"{batch_name}|{shk}|{cz_gtin}"
        if group_key not in grouped:
            grouped[group_key] = {
                "batch_title": batch_name,
                "shk": shk,
                "gtin": cz_gtin,
                "codes": [],
            }
        grouped[group_key]["codes"].append({
            "code": dm_code,
            "gtin": cz_gtin,
            "shk": shk,
            "sn": sn,
        })

    created_packages = []
    total_codes = 0

    for g in grouped.values():
        codes_list = g["codes"]
        if not codes_list:
            continue
        cnt = len(codes_list)
        total_codes += cnt
        fname = build_readable_package_filename(
            batch_title=g["batch_title"],
            shk=g["shk"],
            gtin=g["gtin"],
            count=cnt,
            include_timestamp=False,
        )
        fpath = pkg_dir / fname

        # Сохраняем отметку claimed_by, если этот JSON-файл уже забирал обычный ПК
        prev_claimed_by = ""
        prev_claimed_at = ""
        if fpath.exists():
            old_data = _read_json(fpath)
            if isinstance(old_data, dict):
                prev_claimed_by = str(old_data.get("claimed_by") or "")
                prev_claimed_at = str(old_data.get("claimed_at") or "")

        gtin_map_list = []
        if g["shk"] and g["gtin"]:
            gtin_map_list.append({
                "foreign_gtin": g["shk"],
                "cz_gtin": g["gtin"],
            })

        payload = {
            "version": SYNC_VERSION,
            "direction": "server_via_master_to_worker",
            "source": source_label,
            "package_title": g["batch_title"],
            "shk": g["shk"],
            "gtin": g["gtin"],
            "total_codes": cnt,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "claimed_by": prev_claimed_by,
            "claimed_at": prev_claimed_at,
            "gtin_map": gtin_map_list,
            "codes": codes_list,
        }
        _write_json(fpath, payload)
        created_packages.append({
            "filename": fname,
            "path": str(fpath),
            "batch_title": g["batch_title"],
            "shk": g["shk"],
            "gtin": g["gtin"],
            "total_codes": cnt,
        })

    return {
        "ok": True,
        "packages": created_packages,
        "total_codes": total_codes,
        "info": (
            f"Через шлюз Мастер-ПК получено с MS SQL сервера пакетов: {len(created_packages)} "
            f"(всего кодов ДМ на сервере: {total_codes})"
        ),
    }


def pull_packages_from_mssql_via_master(
    store,
    folder: Optional[Path] = None,
    custom_title: str = "",
    where_filter: str = "",
    limit: int = 50000,
    save_to_master_db: bool = False,
) -> Dict[str, Any]:
    """
    Мастер-ПК как связующее звено обращается к MS SQL Server, получает актуальные партии кодов
    (ШК + ДМ) и формирует именованные JSON-файлы в транзитном каталоге `sync/master/packages/`,
    чтобы обычный ПК мог выбрать нужный JSON-файл и вытянуть его к себе.
    Важно: по умолчанию `save_to_master_db=False`, так как коды хранятся на MS SQL сервере,
    а Мастер-ПК выступает только связующим звеном между сервером и обычным ПК.
    """
    from mssql import MSSQLManager, load_mssql_config

    cfg = load_mssql_config(store)
    mapping = cfg.get("mapping", {}).get("import", {})
    table_name = mapping.get("table", "dbo.ChestnyZnakCodes")
    where_clause = where_filter.strip() or mapping.get("where", "")

    mgr = MSSQLManager(cfg)
    codes = mgr.import_codes_from_sql(
        mapping=mapping,
        table=table_name,
        limit=limit,
        where_clause=where_clause,
    )
    if not codes:
        return {
            "ok": False,
            "packages": [],
            "total_codes": 0,
            "info": "Сервер MS SQL не вернул ни одного кода по заданному условию",
        }

    db_added = 0
    if save_to_master_db:
        save_res = save_codes_and_shk_links_to_db(
            store=store,
            point_id="P01",
            codes=codes,
            batch_name=custom_title or f"MSSQL_{table_name}",
        )
        db_added = save_res.get("added", 0)

    pkg_res = create_named_json_packages_from_records(
        records=codes,
        folder=folder,
        store=store,
        custom_title=custom_title,
        source_label="MS SQL Server (транзит через Мастер-ПК)",
    )
    pkg_res["db_added"] = db_added
    return pkg_res


def generate_packages_from_master_db(
    store,
    folder: Optional[Path] = None,
    custom_title: str = "",
    point_filter: str = "",
) -> Dict[str, Any]:
    """Резервная функция: если нужно обернуть локальный пул в именованный JSON."""
    return pull_packages_from_mssql_via_master(
        store=store,
        folder=folder,
        custom_title=custom_title,
        save_to_master_db=False,
    )


def list_available_json_packages(
    store=None,
    folder: Optional[Path] = None,
    include_outbox: bool = True,
    auto_query_server: bool = True,
) -> List[Dict[str, Any]]:
    """
    Возвращает список доступных JSON-файлов с кодами с MS SQL сервера (через транзитный каталог Мастер-ПК).
    Если `auto_query_server=True`, предварительно запрашивает свежую витрину пакетов с MS SQL сервера
    через Мастер-ПК, чтобы обычный ПК всегда видел актуальные файлы сервера.
    """
    folder = folder or get_sync_folder(store)
    ensure_layout(folder)

    if auto_query_server:
        try:
            pull_packages_from_mssql_via_master(store=store, folder=folder, save_to_master_db=False)
        except Exception as e:
            log.debug("Автоопрос MS SQL при открытии списка пакетов: %s", e)

    search_dirs = [packages_dir(folder)]
    if include_outbox:
        search_dirs.append(folder / "master" / "outbox")

    items: List[Dict[str, Any]] = []
    for d in search_dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            data = _read_json(p)
            if not isinstance(data, dict):
                continue
            codes = data.get("codes", [])
            if not isinstance(codes, list) or len(codes) == 0:
                continue

            first_code = codes[0] if codes else {}
            shk = str(data.get("shk") or first_code.get("shk") or first_code.get("gtin") or "—")
            gtin = str(data.get("gtin") or first_code.get("gtin") or "—")
            batch_title = str(
                data.get("package_title")
                or data.get("point_id")
                or p.stem.split("__")[0]
            )
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            items.append({
                "filename": p.name,
                "path": str(p),
                "batch_title": batch_title,
                "shk": shk,
                "gtin": gtin,
                "total_codes": len(codes),
                "created_at": str(data.get("generated_at") or mtime.strftime("%Y-%m-%d %H:%M:%S")),
                "claimed_by": str(data.get("claimed_by") or ""),
                "claimed_at": str(data.get("claimed_at") or ""),
                "source": str(data.get("source") or "MS SQL Server (через Мастер-ПК)"),
                "sample_dm": str(first_code.get("code") or "")[:48],
                "template_code": str(data.get("template_code") or ""),
                "template_name": str(data.get("template_name") or ""),
                "mtime": mtime,
            })

    items.sort(key=lambda x: (x["batch_title"], x["shk"]))
    return items


def save_codes_and_shk_links_to_db(
    store,
    point_id: str,
    codes: List[Dict[str, Any]],
    gtin_map_extra: Optional[List[Dict[str, Any]]] = None,
    batch_name: str = "SYNC_JSON",
) -> Dict[str, Any]:
    """
    Быстро (одной транзакцией SQLite) сохраняет массив кодов ДМ и связки `ШК -> GTIN ЧЗ`
    в локальную базу данных Обычного ПК (`pool_codes` и `gtin_map`), на котором работают со сканером.
    """
    import db
    if not codes:
        return {"ok": True, "added": 0, "shk_links": 0}

    gs1_re = re.compile(r"^(?:01|\(01\))(\d{14})(?:21|\(21\))([^\x1d\(\r\n]{1,24})")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    shk_links = 0

    with db.get_conn() as conn:
        # 1. Сохраняем явные связки ШК -> GTIN ЧЗ из пакета (точная схема gtin_map: foreign_gtin, cz_gtin)
        if gtin_map_extra:
            for gm in gtin_map_extra:
                fg = str(gm.get("foreign_gtin") or "").strip()
                cz = str(gm.get("cz_gtin") or "").strip()
                if fg and cz:
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO gtin_map (foreign_gtin, cz_gtin) VALUES (?, ?)",
                            (fg, cz),
                        )
                        shk_links += 1
                    except Exception:
                        pass

        # 2. Сохраняем коды DataMatrix и автоматически создаём связку ШК <-> GTIN ЧЗ
        seen_pairs = set()
        for idx, c in enumerate(codes):
            raw_code = str(c.get("code") or c.get("mark_code") or "").strip()
            if not raw_code:
                continue

            raw_gtin = str(c.get("gtin") or "").strip()
            raw_shk = str(c.get("shk") or c.get("barcode") or "").strip()
            sn = str(c.get("sn") or c.get("serial") or "").strip()
            order_lbl = str(c.get("order_id") or batch_name or "SYNC_JSON").strip()

            m = gs1_re.match(raw_code.lstrip("\x1d"))
            dm_gtin14 = m.group(1) if m else ""
            dm_sn = m.group(2) if m else ""

            if not sn:
                sn = dm_sn or f"SN{idx + 1:06d}"

            digits_gtin = re.sub(r"\D", "", raw_gtin)
            digits_shk = re.sub(r"\D", "", raw_shk)

            if len(digits_shk) == 13:
                shk = digits_shk
            elif len(digits_gtin) == 13:
                shk = digits_gtin
            elif len(dm_gtin14) == 14 and dm_gtin14.startswith("0"):
                shk = dm_gtin14[1:]
            else:
                shk = digits_shk or digits_gtin

            if len(dm_gtin14) == 14:
                cz_gtin = dm_gtin14
            elif len(digits_gtin) == 14:
                cz_gtin = digits_gtin
            elif len(digits_gtin) == 13:
                cz_gtin = "0" + digits_gtin
            elif len(shk) == 13:
                cz_gtin = "0" + shk
            else:
                cz_gtin = digits_gtin or shk or "00000000000000"

            for candidate_shk in {shk, cz_gtin}:
                if candidate_shk and cz_gtin and (candidate_shk, cz_gtin) not in seen_pairs:
                    seen_pairs.add((candidate_shk, cz_gtin))
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO gtin_map (foreign_gtin, cz_gtin) VALUES (?, ?)",
                            (candidate_shk, cz_gtin),
                        )
                        shk_links += 1
                    except Exception:
                        pass

            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO pool_codes "
                    "(gtin, code, sn, assigned_point, used, printed, import_batch, created_at) "
                    "VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
                    (cz_gtin, raw_code, sn, point_id, order_lbl, now_str),
                )
                if cur.rowcount and cur.rowcount > 0:
                    added += 1
                else:
                    conn.execute(
                        "UPDATE pool_codes SET assigned_point = ? WHERE code = ? AND used = 0",
                        (point_id, raw_code),
                    )
            except Exception:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO pool_codes (gtin, code, sn, assigned_point, used) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (cz_gtin, raw_code, sn, point_id),
                )
                if cur.rowcount and cur.rowcount > 0:
                    added += 1

        conn.commit()

    return {"ok": True, "added": added, "shk_links": shk_links}


def claim_and_import_json_package(
    store,
    package_path: Path,
    point_id: str = "",
    worker_pc_name: str = "",
) -> Dict[str, Any]:
    """
    Вызывается на Обычном ПК, когда старший смены выбрал нужный JSON-файл в окне:
    1. Вытягивает выбранный JSON-файл с сервера через транзитный канал Мастер-ПК.
    2. Загружает коды ДМ и связку `ШК -> GTIN ЧЗ` в локальную базу Обычного ПК.
    3. Фиксирует в транзитном канале Мастер-ПК, что пакет отправлен на этот Обычный ПК (`claimed_by`).
    """
    package_path = Path(package_path)
    if not package_path.exists():
        return {"ok": False, "added": 0, "info": f"Файл пакета не найден: {package_path}"}

    data = _read_json(package_path)
    if not data:
        return {"ok": False, "added": 0, "info": f"Повреждённый JSON-файл: {package_path.name}"}

    codes = data.get("codes", [])
    if not codes:
        return {"ok": False, "added": 0, "info": f"В файле {package_path.name} нет кодов маркировки"}

    if not point_id:
        try:
            point_id = store.get_setting("worker_point_id") or store.get_setting("current_point") or "T-01"
        except Exception:
            point_id = "T-01"

    pc_label = worker_pc_name.strip() if worker_pc_name else get_pc_node_name(store)
    gtin_map_extra = data.get("gtin_map", [])
    batch_title = str(data.get("package_title") or package_path.stem.split("__")[0])

    save_res = save_codes_and_shk_links_to_db(
        store=store,
        point_id=point_id,
        codes=codes,
        gtin_map_extra=gtin_map_extra,
        batch_name=batch_title,
    )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        data["claimed_by"] = f"{pc_label} ({point_id})"
        data["claimed_at"] = now_str
        _write_json(package_path, data)
    except Exception as e:
        log.warning("Не удалось обновить отметку транзита claimed_by для %s: %s", package_path.name, e)

    try:
        if store is not None:
            store.set_setting(f"last_import_{point_id}", now_str)
            store.set_setting(f"last_export_{point_id}", now_str)
    except Exception:
        pass

    tpl_code = str(data.get("template_code") or "").strip()
    tpl_name = str(data.get("template_name") or "").strip()
    tpl_obj = data.get("template")
    if isinstance(tpl_obj, dict) and tpl_obj.get("code"):
        try:
            upsert_template_in_db(store, tpl_obj)
            if not tpl_code:
                tpl_code = str(tpl_obj.get("code") or "")
            if not tpl_name:
                tpl_name = str(tpl_obj.get("name") or "")
        except Exception as e:
            log.warning("Не удалось сохранить шаблон из пакета: %s", e)

    added = save_res.get("added", 0)
    shk_val = data.get("shk") or data.get("gtin") or "—"
    return {
        "ok": True,
        "added": added,
        "total_in_file": len(codes),
        "shk": shk_val,
        "filename": package_path.name,
        "template_code": tpl_code,
        "template_name": tpl_name,
        "info": (
            f"С сервера через Мастер-ПК на ПК «{pc_label}» (точка {point_id}) "
            f"отправлен файл «{package_path.name}»: загружено {added} новых ДМ "
            f"(всего в пакете: {len(codes)}, ШК: {shk_val})"
        ),
    }


# ==============================================================================
# 4. ОБРАТНАЯ ОТПРАВКА ОТЧЁТОВ ЧЕРЕЗ МАСТЕР-ПК НА MS SQL SERVER
# ==============================================================================
def export_for_point(
    store,
    point_id: str,
    folder: Optional[Path] = None,
    only_new: bool = False,
) -> Dict[str, Any]:
    """Запрашивает свежие пакеты с MS SQL сервера через шлюз Мастер-ПК для выдачи точкам."""
    res = pull_packages_from_mssql_via_master(store, folder=folder, save_to_master_db=False)
    now_iso = datetime.now().isoformat(timespec="seconds")
    try:
        store.set_setting(f"last_export_{point_id}", now_iso)
    except Exception:
        pass
    return {
        "ok": res.get("ok", False),
        "count": res.get("total_codes", 0),
        "path": str(packages_dir(folder or get_sync_folder(store))),
        "info": res.get("info", ""),
    }


def export_for_all_points(
    store,
    folder: Optional[Path] = None,
    only_new: bool = False,
) -> Dict[str, Any]:
    """Обновляет транзитную витрину JSON-пакетов с MS SQL сервера через Мастер-ПК."""
    folder = folder or get_sync_folder(store)
    pkg_res = pull_packages_from_mssql_via_master(store, folder=folder, save_to_master_db=False)
    pkg_cnt = len(pkg_res.get("packages", []))
    total = pkg_res.get("total_codes", 0)
    now_iso = datetime.now().isoformat(timespec="seconds")
    for p in getattr(store, "points", []) or []:
        try:
            store.set_setting(f"last_export_{p['id']}", now_iso)
        except Exception:
            pass
    return {
        "ok": True,
        "total": total,
        "info": f"Шлюз Мастер-ПК обновил с MS SQL сервера {pkg_cnt} JSON-пакетов (доступно {total} кодов ДМ)",
    }


def export_results_from_point(
    store,
    point_id: str,
    folder: Optional[Path] = None,
    only_new: bool = True,
) -> Dict[str, Any]:
    """
    Обычный ПК отправляет отчёт о напечатанных кодах через Мастер-ПК на MS SQL Server
    (и сохраняет транзитный файл в `sync/master/inbox/from_<точка>.json`).
    """
    folder = folder or get_sync_folder(store)
    ensure_layout(folder)

    since = store.get_setting(f"last_report_{point_id}") if only_new else None
    try:
        printed = store.printed_by_point(point_id, since=since)
    except Exception as e:
        log.error("export_results_from_point(%s): %s", point_id, e)
        return {"ok": False, "count": 0, "info": str(e)}

    payload = {
        "version": SYNC_VERSION,
        "direction": "worker_via_master_to_server",
        "point_id": point_id,
        "pc_name": get_pc_node_name(store),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "printed_count": len(printed),
        "printed": printed,
    }
    path = inbox_path(folder, point_id)
    try:
        _write_json(path, payload)
    except Exception as e:
        return {"ok": False, "count": 0, "info": f"Ошибка записи транзитного отчёта: {e}"}

    # Одновременно прокидываем статусы через Мастер-ПК в MS SQL Server
    sql_updated = 0
    try:
        from mssql import MSSQLManager, load_mssql_config
        cfg = load_mssql_config(store)
        mapping = cfg.get("mapping", {}).get("status", {})
        table_name = mapping.get("table", "dbo.ChestnyZnakCodes")
        mgr = MSSQLManager(cfg)
        if printed:
            res_sql = mgr.export_code_statuses(mapping, table_name, printed)
            sql_updated = res_sql.get("updated", 0)
    except Exception as e:
        log.debug("Транзит отчёта в MS SQL: %s", e)

    now_iso = datetime.now().isoformat(timespec="seconds")
    store.set_setting(f"last_report_{point_id}", now_iso)
    return {
        "ok": True,
        "count": len(printed),
        "path": str(path),
        "info": (
            f"Точка {point_id} ({get_pc_node_name(store)}): через Мастер-ПК отправлено на MS SQL сервер "
            f"напечатанных ДМ — {len(printed)} (обновлено на сервере: {sql_updated})"
        ),
    }


def import_from_master(
    store,
    point_id: str,
    folder: Optional[Path] = None,
) -> Dict[str, Any]:
    """Точка читает транзитный пакет outbox/to_{point}.json."""
    folder = folder or get_sync_folder(store)
    path = outbox_path(folder, point_id)
    data = _read_json(path)
    if not data:
        return {"ok": True, "added": 0, "info": "Нет транзитного пакета"}

    return claim_and_import_json_package(store, path, point_id=point_id)


def import_results_at_master(
    store,
    folder: Optional[Path] = None,
) -> Dict[str, Any]:
    """Мастер-ПК принимает входящие отчёты от обычных ПК из `inbox/*.json` и пересылает их в MS SQL Server."""
    folder = folder or get_sync_folder(store)
    inbox_dir = folder / "master" / "inbox"
    if not inbox_dir.exists():
        return {"ok": True, "total": 0, "info": "Нет входящих отчётов от обычных ПК для отправки на сервер"}

    total = 0
    sql_total = 0
    lines = []

    from mssql import MSSQLManager, load_mssql_config
    cfg = load_mssql_config(store)
    mapping = cfg.get("mapping", {}).get("status", {})
    table_name = mapping.get("table", "dbo.ChestnyZnakCodes")
    mgr = MSSQLManager(cfg)

    for path in sorted(inbox_dir.glob("from_*.json")):
        data = _read_json(path)
        if not data:
            continue
        point_id = data.get("point_id") or path.stem.replace("from_", "", 1)
        records = data.get("printed", [])
        if records:
            try:
                res_sql = mgr.export_code_statuses(mapping, table_name, records)
                sql_total += res_sql.get("updated", 0)
            except Exception as e:
                log.error("Ошибка транзита в MS SQL (%s): %s", point_id, e)

        total += len(records)
        lines.append(f"  {point_id}: переслано через Мастер-ПК на MS SQL сервер — {len(records)} ДМ")
        store.set_setting(
            f"last_import_{point_id}",
            datetime.now().isoformat(timespec="seconds"),
        )

    header = f"Через Мастер-ПК переслано в MS SQL Server отчётов печати: {total} (обновлено на сервере: {sql_total})"
    return {
        "ok": True,
        "total": total,
        "info": "\n".join([header] + lines) if lines else header,
    }


# ==============================================================================
# ЖИВОЙ МОНИТОРИНГ АКТИВНОСТИ ПК И ТОЧЕК (HEARTBEAT)
# ==============================================================================

def heartbeats_dir(folder: Path) -> Path:
    return Path(folder) / "heartbeats"


def publish_pc_heartbeat(store=None) -> Optional[Path]:
    """
    Публикует живой маячок текущего ПК в <sync_folder>/heartbeats/<node>.json,
    чтобы Мастер-ПК видел реальное онлайн-состояние каждой точки и её оборудования.
    """
    import socket
    import time
    try:
        folder = get_sync_folder(store)
        hb_dir = heartbeats_dir(folder)
        hb_dir.mkdir(parents=True, exist_ok=True)

        role = get_pc_role(store)
        node_name = get_pc_node_name(store)
        point_id = getattr(store, "current_point", None) or "T-01"
        hostname = socket.gethostname().strip()

        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.05)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                local_ip = socket.gethostbyname(hostname)
            except Exception:
                pass

        printers_online = 0
        scanners_online = 0
        printer_names: List[str] = []
        try:
            from device_discovery import detect_local_printers, detect_local_scanners
            pr_list = detect_local_printers(connected_only=True, force_refresh=False) or []
            sc_list = detect_local_scanners(connected_only=True, force_refresh=False) or []
            printers_online = len(pr_list)
            scanners_online = len(sc_list)
            printer_names = [str(p.get("name") or "") for p in pr_list if p.get("name")]
        except Exception:
            pass

        safe_key = _sanitize_filename_part(f"{point_id}_{node_name or hostname}", max_len=48)
        hb_path = hb_dir / f"{safe_key}.json"
        payload = {
            "point_id": point_id,
            "node_name": node_name or hostname,
            "hostname": hostname,
            "ip": local_ip,
            "role": role,
            "printers_online": printers_online,
            "scanners_online": scanners_online,
            "printer_names": printer_names,
            "ts": time.time(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _write_json(hb_path, payload)
        if role != "master":
            send_http_heartbeat(store, payload)
        return hb_path
    except Exception:
        return None


def read_pc_heartbeats(folder: Optional[Path] = None, max_age_sec: float = 90.0) -> Dict[str, dict]:
    """
    Читает активные маячки ПК из <sync_folder>/heartbeats/*.json.
    Возвращает словарь с ключами по node_name.lower(), hostname.lower(), ip и point_id.
    """
    import time
    result: Dict[str, dict] = {}
    try:
        if folder is None:
            folder = get_sync_folder(None)
        hb_dir = heartbeats_dir(folder)
        if not hb_dir.exists():
            return result
        now = time.time()
        for k_h, v_h in list(_HTTP_HEARTBEATS.items()):
            ts_h = float(v_h.get("ts") or 0.0)
            if (now - ts_h) <= max_age_sec:
                v_copy = dict(v_h)
                v_copy["_age_sec"] = round(now - ts_h, 1)
                result[k_h] = v_copy
        for f in hb_dir.glob("*.json"):
            data = _read_json(f)
            if not isinstance(data, dict):
                continue
            ts = float(data.get("ts") or 0.0)
            age = now - ts if ts > 0 else (now - f.stat().st_mtime)
            if age > max_age_sec:
                continue
            data["_age_sec"] = round(age, 1)
            for key_field in ("ip", "node_name", "hostname", "point_id"):
                val = str(data.get(key_field) or "").strip().lower()
                if val:
                    result[val] = data
    except Exception:
        pass
    return result


# ==============================================================================
# ПРЯМОЙ СЕТЕВОЙ HTTP/JSON-ОБМЕН В ЛОКАЛЬНОЙ СЕТИ (ПОРТ 8765)
# ==============================================================================

DEFAULT_SYNC_PORT = 8765
_HTTP_SERVER = None
_HTTP_SERVER_THREAD = None
_HTTP_SERVER_LOCK = threading.Lock()
_HTTP_HEARTBEATS: Dict[str, dict] = {}


def get_master_address(store=None) -> Tuple[str, int]:
    """Возвращает (ip_или_хост, порт) Мастер-ПК из настроек store или config.json."""
    host = ""
    port = DEFAULT_SYNC_PORT
    if store is not None and hasattr(store, "get_setting"):
        try:
            host = str(store.get_setting("master_host") or "").strip()
            p_raw = store.get_setting("master_port")
            if p_raw:
                port = int(p_raw)
        except Exception:
            pass
    if not host:
        try:
            cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                host = str(data.get("master_host") or "").strip()
                if data.get("master_port"):
                    port = int(data["master_port"])
        except Exception:
            pass
    return host, port


def set_master_address(store, host: str, port: int = DEFAULT_SYNC_PORT) -> None:
    """Сохраняет IP-адрес и порт Мастер-ПК в store и config.json."""
    host_clean = str(host or "").strip()
    port_val = int(port or DEFAULT_SYNC_PORT)
    if store is not None and hasattr(store, "set_setting"):
        try:
            store.set_setting("master_host", host_clean)
            store.set_setting("master_port", str(port_val))
        except Exception:
            pass
    try:
        cfg_path = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn" / "config.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["master_host"] = host_clean
        data["master_port"] = port_val
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _http_json_request(
    host: str,
    port: int,
    path: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """Выполняет HTTP/JSON-запрос к Мастер-ПК без внешних зависимостей (urllib)."""
    import urllib.request
    import urllib.error

    url = f"http://{host}:{int(port)}{path}"
    body_bytes = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {"ok": True}


def test_master_connection(host: str, port: int = DEFAULT_SYNC_PORT, timeout: float = 2.5) -> Dict[str, Any]:
    """Проверяет связь с Мастер-ПК по локальной сети на порту 8765."""
    import time
    h = str(host or "").strip()
    if not h:
        return {"ok": False, "info": "Укажите IP-адрес Мастер-ПК."}
    t0 = time.time()
    try:
        res = _http_json_request(h, port, "/api/ping", method="GET", timeout=timeout)
        ms = round((time.time() - t0) * 1000.0, 1)
        if res.get("ok"):
            node = res.get("node") or res.get("hostname") or h
            return {
                "ok": True,
                "latency_ms": ms,
                "master_node": node,
                "info": f"Связь с Мастер-ПК ({node} · {h}:{port}) установлена ({ms} мс)",
            }
        return {"ok": False, "info": f"Мастер-ПК {h}:{port} ответил с ошибкой: {res}"}
    except Exception as e:
        return {
            "ok": False,
            "info": (
                f"Не удалось подключиться к {h}:{port} ({e}). "
                f"Проверьте, что Dotmark запущен на Мастер-ПК и порт {port} открыт в брандмауэре."
            ),
        }


def _auto_register_worker_in_master(store, hb: dict) -> None:
    """Автоматически регистрирует новый Рабочий ПК и его точку на Мастер-ПК при первом heartbeat."""
    try:
        point_id = str(hb.get("point_id") or "").strip().upper() or "T-01"
        node_name = str(hb.get("node_name") or hb.get("hostname") or "").strip()
        ip_addr = str(hb.get("ip") or "").strip()
        if not node_name:
            return

        import db
        with db.get_conn() as conn:
            pt = conn.execute("SELECT id FROM points WHERE id = ?", (point_id,)).fetchone()
            if pt is None:
                conn.execute(
                    "INSERT INTO points (id, name, address) VALUES (?, ?, ?)",
                    (point_id, f"Пост {point_id}", "Добавлен автоматически по сети"),
                )

            comp = conn.execute(
                "SELECT id, ip FROM computers WHERE point_id = ? AND (LOWER(name) = LOWER(?) OR LOWER(sync_node) = LOWER(?) OR (ip != '' AND ip = ?)) LIMIT 1",
                (point_id, node_name, node_name, ip_addr),
            ).fetchone()
            if comp is None:
                conn.execute(
                    "INSERT INTO computers (point_id, name, ip, role, sync_node, sync_enabled, online) "
                    "VALUES (?, ?, ?, ?, ?, 1, 1)",
                    (point_id, node_name, ip_addr, "Рабочий пост", node_name),
                )
            elif ip_addr and comp["ip"] != ip_addr:
                conn.execute("UPDATE computers SET ip = ?, online = 1 WHERE id = ?", (ip_addr, comp["id"]))
            conn.commit()

        if store is not None and hasattr(store, "reload"):
            try:
                store.reload()
            except Exception:
                pass
    except Exception as e:
        log.debug("auto_register_worker_in_master: %s", e)


def start_master_http_server(store=None, port: int = DEFAULT_SYNC_PORT) -> bool:
    """Запускает встроенный HTTP/JSON-сервер на Мастер-ПК (порт 8765) в фоновом потоке."""
    global _HTTP_SERVER, _HTTP_SERVER_THREAD
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import time
    import socket

    with _HTTP_SERVER_LOCK:
        if _HTTP_SERVER is not None:
            return True

        folder = get_sync_folder(store)
        ensure_layout(folder)
        node_name = get_pc_node_name(store)
        hostname = socket.gethostname().strip()

        class DotmarkSyncHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Не засоряем stdout стандартными HTTP-логами
                return

            def _send_json(self, code: int, obj: dict):
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _read_json_body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw) if raw.strip() else {}

            def do_GET(self):
                try:
                    if self.path == "/api/ping":
                        self._send_json(200, {
                            "ok": True,
                            "role": "master",
                            "node": node_name,
                            "hostname": hostname,
                            "port": port,
                        })
                    elif self.path == "/api/packages":
                        pkgs = list_available_json_packages(folder=folder)
                        if not pkgs and store is not None:
                            try:
                                ensure_packages_from_local_pool(store, folder=folder)
                                pkgs = list_available_json_packages(folder=folder)
                            except Exception:
                                pass
                        self._send_json(200, {"ok": True, "packages": pkgs})
                    elif self.path == "/api/templates":
                        tpls = store.list_templates() if (store is not None and hasattr(store, "list_templates")) else []
                        bnds = store.list_bindings() if (store is not None and hasattr(store, "list_bindings")) else []
                        self._send_json(200, {"ok": True, "templates": tpls, "bindings": bnds})
                    elif self.path == "/api/heartbeats":
                        self._send_json(200, {"ok": True, "heartbeats": read_pc_heartbeats(folder)})
                    else:
                        self._send_json(404, {"ok": False, "error": "Not found"})
                except Exception as e:
                    self._send_json(500, {"ok": False, "error": str(e)})

            def do_POST(self):
                try:
                    data = self._read_json_body()
                    if self.path == "/api/heartbeat":
                        client_ip = str(data.get("ip") or self.client_address[0] or "").strip()
                        hb = {
                            "point_id": str(data.get("point_id") or "T-01").strip(),
                            "node_name": str(data.get("node_name") or data.get("hostname") or client_ip).strip(),
                            "hostname": str(data.get("hostname") or "").strip(),
                            "ip": client_ip,
                            "role": str(data.get("role") or "worker"),
                            "printers_online": int(data.get("printers_online") or 0),
                            "scanners_online": int(data.get("scanners_online") or 0),
                            "printer_names": list(data.get("printer_names") or []),
                            "ts": time.time(),
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        for k_f in ("ip", "node_name", "hostname", "point_id"):
                            val = str(hb.get(k_f) or "").strip().lower()
                            if val:
                                _HTTP_HEARTBEATS[val] = hb
                        hb_file = heartbeats_dir(folder) / (
                            _sanitize_filename_part(f"{hb['point_id']}_{hb['node_name']}", max_len=48) + ".json"
                        )
                        _write_json(hb_file, hb)
                        _auto_register_worker_in_master(store, hb)
                        self._send_json(200, {"ok": True})

                    elif self.path == "/api/packages/claim":
                        fname = str(data.get("file_name") or "").strip()
                        point_id = str(data.get("point_id") or "T-01").strip()
                        pkg_path = packages_dir(folder) / fname
                        if not fname or not pkg_path.exists():
                            self._send_json(404, {"ok": False, "error": "Пакет не найден на Мастер-ПК"})
                            return
                        pkg_data = _read_json(pkg_path) or {}
                        pkg_data["claimed_by"] = point_id
                        pkg_data["claimed_at"] = datetime.now().isoformat(timespec="seconds")
                        t_code = str(pkg_data.get("template_code") or "").strip()
                        if t_code and store is not None and hasattr(store, "list_templates"):
                            try:
                                for t in (store.list_templates() or []):
                                    if t.get("code") == t_code:
                                        pkg_data["template"] = t
                                        pkg_data["template_name"] = str(t.get("name") or "")
                                        break
                            except Exception:
                                pass
                        _write_json(pkg_path, pkg_data)
                        self._send_json(200, {"ok": True, "package": pkg_data})

                    elif self.path == "/api/report":
                        point_id = str(data.get("point_id") or "T-01").strip()
                        inbox_dir = folder / "master" / "inbox"
                        inbox_dir.mkdir(parents=True, exist_ok=True)
                        out_file = inbox_dir / f"from_{point_id}.json"
                        _write_json(out_file, data)
                        if store is not None and hasattr(store, "set_setting"):
                            store.set_setting(
                                f"last_report_{point_id}",
                                datetime.now().isoformat(timespec="seconds"),
                            )
                        # Автоматически пересылаем принятый отчёт в MS SQL Server в фоне
                        if store is not None:
                            threading.Thread(
                                target=lambda: import_results_at_master(store, folder=folder),
                                daemon=True,
                            ).start()
                        self._send_json(200, {"ok": True, "count": len(data.get("printed") or [])})

                    else:
                        self._send_json(404, {"ok": False, "error": "Not found"})
                except Exception as e:
                    self._send_json(500, {"ok": False, "error": str(e)})

        try:
            ThreadingHTTPServer.allow_reuse_address = True
            srv = ThreadingHTTPServer(("0.0.0.0", int(port)), DotmarkSyncHandler)
            _HTTP_SERVER = srv
            _HTTP_SERVER_THREAD = threading.Thread(target=srv.serve_forever, daemon=True)
            _HTTP_SERVER_THREAD.start()
            log.info("[SyncHTTP] Сервер Мастер-ПК запущен на порту %d", port)
            return True
        except Exception as e:
            log.error("[SyncHTTP] Не удалось запустить сервер на порту %d: %s", port, e)
            _HTTP_SERVER = None
            return False


def stop_master_http_server() -> None:
    global _HTTP_SERVER
    with _HTTP_SERVER_LOCK:
        if _HTTP_SERVER is not None:
            try:
                _HTTP_SERVER.shutdown()
                _HTTP_SERVER.server_close()
            except Exception:
                pass
            _HTTP_SERVER = None


def send_http_heartbeat(store, payload: dict) -> bool:
    """Отправляет живой маячок от Рабочего ПК на Мастер-ПК по HTTP."""
    host, port = get_master_address(store)
    if not host:
        return False
    try:
        res = _http_json_request(host, port, "/api/heartbeat", method="POST", payload=payload, timeout=1.5)
        return bool(res.get("ok"))
    except Exception:
        return False


def fetch_packages_via_http(store) -> Optional[List[Dict[str, Any]]]:
    """Запрашивает список доступных партий с Мастер-ПК по HTTP."""
    host, port = get_master_address(store)
    if not host:
        return None
    res = _http_json_request(host, port, "/api/packages", method="GET", timeout=3.0)
    if res.get("ok"):
        return list(res.get("packages") or [])
    return []


def claim_package_via_http(store, file_name: str, point_id: str) -> Dict[str, Any]:
    """Скачивает и бронирует JSON-пакет с Мастер-ПК по HTTP и сохраняет в локальную БД."""
    host, port = get_master_address(store)
    if not host:
        return {"ok": False, "info": "Не указан IP-адрес Мастер-ПК"}
    res = _http_json_request(
        host, port, "/api/packages/claim",
        method="POST",
        payload={"file_name": file_name, "point_id": point_id},
        timeout=5.0,
    )
    if not res.get("ok") or not isinstance(res.get("package"), dict):
        return {"ok": False, "info": res.get("error", "Не удалось скачать пакет с Мастер-ПК")}

    folder = get_sync_folder(store)
    ensure_layout(folder)
    try:
        sync_templates_via_http(store)
    except Exception:
        pass
    local_pkg = packages_dir(folder) / file_name
    _write_json(local_pkg, res["package"])
    return claim_and_import_json_package(store, local_pkg, point_id=point_id)


def send_report_via_http(store, point_id: str) -> Optional[Dict[str, Any]]:
    """Отправляет отчёт о напечатанных кодах с Рабочего ПК на Мастер-ПК по HTTP."""
    host, port = get_master_address(store)
    if not host:
        return None
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT code, gtin, sn, used, printed, used_point, created_at "
            "FROM pool_codes WHERE used = 1 OR printed = 1"
        ).fetchall()
    printed_list = [dict(r) if hasattr(r, "keys") else {
        "code": r[0], "gtin": r[1], "sn": r[2], "used": r[3],
        "printed": r[4], "used_point": r[5], "created_at": r[6],
    } for r in rows]

    payload = {
        "point_id": point_id,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "printed": printed_list,
    }
    res = _http_json_request(host, port, "/api/report", method="POST", payload=payload, timeout=4.0)
    if res.get("ok"):
        return {
            "ok": True,
            "count": len(printed_list),
            "info": f"Отправлено на Мастер-ПК ({host}:{port}): {len(printed_list)} кодов",
        }
    return {"ok": False, "info": f"Ошибка отправки отчёта на {host}:{port}: {res}"}
def upsert_template_in_db(store, tpl_obj: Dict[str, Any]) -> bool:
    """Создаёт или обновляет шаблон этикетки в локальной БД Рабочего ПК через Store."""
    if not isinstance(tpl_obj, dict) or not tpl_obj.get("code"):
        return False
    t_code = str(tpl_obj.get("code"))
    t_name = str(tpl_obj.get("name") or "Шаблон")
    t_mode = str(tpl_obj.get("print_mode") or "combined")
    t_def = bool(tpl_obj.get("is_default", False))
    t_cfg = tpl_obj.get("config") or {}
    if isinstance(t_cfg, str):
        try:
            t_cfg = json.loads(t_cfg)
        except Exception:
            t_cfg = {}
    if isinstance(t_cfg, dict) and t_mode and "print_mode" not in t_cfg:
        t_cfg = dict(t_cfg)
        t_cfg["print_mode"] = t_mode

    if store is not None:
        try:
            existing = {t.get("code"): t for t in (store.list_templates() or []) if isinstance(t, dict)}
            if t_code in existing and hasattr(store, "update_template"):
                store.update_template(code=t_code, name=t_name, config=t_cfg)
                if t_def:
                    store.update_template(code=t_code, is_default=True)
                return True
            elif hasattr(store, "create_template"):
                res = store.create_template(code=t_code, name=t_name, config=t_cfg)
                if t_def and hasattr(store, "update_template"):
                    store.update_template(code=t_code, is_default=True)
                return bool(res.get("ok", True)) if isinstance(res, dict) else True
        except Exception as e:
            log.warning("Ошибка upsert_template_in_db через Store (%s): %s", t_code, e)

    try:
        import db
        cfg_json = json.dumps(t_cfg, ensure_ascii=False)
        with db.get_conn() as conn:
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='templates'"
            ).fetchone()
            if not has_tbl:
                return False
            if t_def:
                conn.execute("UPDATE templates SET is_default = 0")
            row = conn.execute("SELECT code FROM templates WHERE code = ?", (t_code,)).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE templates SET name = ?, print_mode = ?, is_default = ?, config_json = ? WHERE code = ?",
                    (t_name, t_mode, 1 if t_def else 0, cfg_json, t_code),
                )
            else:
                conn.execute(
                    "INSERT INTO templates (code, name, print_mode, is_default, config_json) VALUES (?, ?, ?, ?, ?)",
                    (t_code, t_name, t_mode, 1 if t_def else 0, cfg_json),
                )
            conn.commit()
        if store is not None and hasattr(store, "reload"):
            try:
                store.reload()
            except Exception:
                pass
        return True
    except Exception as e:
        log.warning("Ошибка сохранения шаблона %s в локальную БД: %s", t_code, e)
        return False


def sync_templates_via_http(store) -> Dict[str, Any]:
    """Скачивает шаблоны и привязки GTIN с Мастер-ПК по HTTP и сохраняет в локальную БД Рабочего ПК."""
    host, port = get_master_address(store)
    if not host:
        return {"ok": False, "count": 0, "info": "Мастер-ПК не задан"}
    res = _http_json_request(host, port, "/api/templates", method="GET", timeout=4.0)
    if not res.get("ok"):
        return {"ok": False, "count": 0, "info": res.get("error", "Ошибка /api/templates")}
    tpls = list(res.get("templates") or [])
    saved = 0
    for t in tpls:
        if upsert_template_in_db(store, t):
            saved += 1
    bnds = list(res.get("bindings") or [])
    if bnds:
        try:
            import db
            with db.get_conn() as conn:
                for b in bnds:
                    pref = str(b.get("gtin_prefix") or "").strip()
                    t_code = str(b.get("template_code") or "").strip()
                    prio = int(b.get("priority") or 0)
                    if not pref or not t_code:
                        continue
                    row = conn.execute("SELECT id FROM bindings WHERE gtin_prefix = ?", (pref,)).fetchone()
                    if row is not None:
                        conn.execute(
                            "UPDATE bindings SET template_code = ?, priority = ? WHERE gtin_prefix = ?",
                            (t_code, prio, pref),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO bindings (gtin_prefix, template_code, priority) VALUES (?, ?, ?)",
                            (pref, t_code, prio),
                        )
                conn.commit()
        except Exception:
            pass
    return {"ok": True, "count": saved, "info": f"Синхронизировано шаблонов: {saved}"}


def bind_template_to_package(store, file_name: str, template_code: str, folder: Optional[Path] = None) -> Dict[str, Any]:
    """Привязывает шаблон (и вшивает его полный макет) в JSON-файл партии на Мастер-ПК."""
    fld = folder or get_sync_folder(store)
    pkg_path = packages_dir(fld) / file_name
    if not pkg_path.exists():
        return {"ok": False, "info": f"Файл партии не найден: {file_name}"}
    data = _read_json(pkg_path)
    if not isinstance(data, dict):
        return {"ok": False, "info": "Повреждённый JSON-файл партии"}

    tpl_obj = None
    if store is not None and hasattr(store, "list_templates"):
        for t in (store.list_templates() or []):
            if t.get("code") == template_code:
                tpl_obj = t
                break
    if tpl_obj is None:
        return {"ok": False, "info": f"Шаблон «{template_code}» не найден"}

    data["template_code"] = str(tpl_obj.get("code") or "")
    data["template_name"] = str(tpl_obj.get("name") or "")
    data["template"] = tpl_obj
    _write_json(pkg_path, data)
    return {
        "ok": True,
        "template_code": data["template_code"],
        "template_name": data["template_name"],
        "info": f"Шаблон «{data['template_name']}» привязан к партии «{data.get('package_title') or file_name}»",
    }
def ensure_packages_from_local_pool(store, folder: Optional[Path] = None) -> int:
    """
    Формирует JSON-пакеты в packages_dir из свободных кодов `pool_codes` и истории импортов (`import_batches`),
    если пользователь загрузил партию через «Импорт xlsx (ЦРПТ)» или «Импорт CSV», а не через MS SQL.
    Сохраняет существующие привязки шаблонов (template_code / template) и отметки claimed_by.
    """
    import db
    fld = folder or get_sync_folder(store)
    ensure_layout(fld)
    pkg_dir = packages_dir(fld)

    batches = []
    if store is not None and hasattr(store, "list_import_batches"):
        try:
            batches = store.list_import_batches(limit=200) or []
        except Exception:
            batches = []

    with db.get_conn() as conn:
        cols_raw = conn.execute("PRAGMA table_info(pool_codes)").fetchall()
        cols = [c[1] if isinstance(c, (list, tuple)) else c["name"] for c in cols_raw]
        if not cols or "code" not in cols:
            return 0

        sel = ["code"]
        for c in ("gtin", "sn", "point", "batch", "batch_name", "source_file", "product_name"):
            if c in cols:
                sel.append(c)

        where_free = "WHERE (used IS NULL OR used = 0)" if "used" in cols else ""
        rows = conn.execute(f"SELECT {', '.join(sel)} FROM pool_codes {where_free}").fetchall()
        if not rows:
            return 0

        gtin_to_shk = {}
        try:
            gm_rows = conn.execute("SELECT gtin, shk FROM gtin_map").fetchall()
            for gr in gm_rows:
                g_v = str(gr[0] if isinstance(gr, (list, tuple)) else gr["gtin"] or "").strip()
                s_v = str(gr[1] if isinstance(gr, (list, tuple)) else gr["shk"] or "").strip()
                if g_v and s_v and g_v not in gtin_to_shk:
                    gtin_to_shk[g_v] = s_v
        except Exception:
            pass

    # Группируем свободные коды по (партия, точка)
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    default_batch_name = ""
    if batches:
        default_batch_name = str(batches[0].get("name") or batches[0].get("file_name") or "").strip()

    for r in rows:
        rd = dict(r) if hasattr(r, "keys") else dict(zip(sel, r))
        pt = str(rd.get("point") or "T-01").strip() or "T-01"
        b_name = str(
            rd.get("batch_name")
            or rd.get("batch")
            or rd.get("source_file")
            or default_batch_name
            or f"Партия_{pt}"
        ).strip()
        gtin_v = str(rd.get("gtin") or "").strip()
        shk_v = gtin_to_shk.get(gtin_v, gtin_v)
        item = {
            "code": str(rd.get("code") or ""),
            "gtin": gtin_v,
            "sn": str(rd.get("sn") or ""),
            "shk": shk_v,
            "product_name": str(rd.get("product_name") or ""),
        }
        grouped.setdefault((b_name, pt), []).append(item)

    created_or_updated = 0
    now_str = datetime.now().isoformat(timespec="seconds")
    for (b_name, pt), code_list in grouped.items():
        if not code_list:
            continue
        safe_b = _sanitize_filename_part(b_name, max_len=40)
        safe_pt = _sanitize_filename_part(pt, max_len=16)
        fname = f"{safe_b}__{safe_pt}.json"
        fpath = pkg_dir / fname

        old_data = _read_json(fpath) if fpath.exists() else {}
        if not isinstance(old_data, dict):
            old_data = {}

        first = code_list[0]
        gtin_map_extra = []
        seen_gm = set()
        for c_item in code_list:
            g = c_item.get("gtin") or ""
            s = c_item.get("shk") or g
            if g and (s, g) not in seen_gm:
                seen_gm.add((s, g))
                gtin_map_extra.append({"shk": s, "gtin": g, "name": c_item.get("product_name") or b_name})

        pkg_obj = {
            "package_title": b_name,
            "point_id": pt,
            "shk": first.get("shk") or first.get("gtin") or "—",
            "gtin": first.get("gtin") or "—",
            "generated_at": old_data.get("generated_at") or now_str,
            "source": "Локальный склад кодов ЧЗ (Excel / CSV)",
            "codes": code_list,
            "gtin_map": gtin_map_extra,
            "claimed_by": str(old_data.get("claimed_by") or ""),
            "claimed_at": str(old_data.get("claimed_at") or ""),
        }
        if old_data.get("template_code"):
            pkg_obj["template_code"] = old_data["template_code"]
            pkg_obj["template_name"] = old_data.get("template_name", "")
        if isinstance(old_data.get("template"), dict):
            pkg_obj["template"] = old_data["template"]

        _write_json(fpath, pkg_obj)
        created_or_updated += 1

    return created_or_updated
