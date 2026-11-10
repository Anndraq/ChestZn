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
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

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

    added = save_res.get("added", 0)
    shk_val = data.get("shk") or data.get("gtin") or "—"
    return {
        "ok": True,
        "added": added,
        "total_in_file": len(codes),
        "shk": shk_val,
        "filename": package_path.name,
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
