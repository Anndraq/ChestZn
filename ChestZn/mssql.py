r"""
ChestZn — Модуль интеграции с Microsoft SQL Server (mssql.py).
Обеспечивает двухсторонний обмен между Мастер-ПК и сервером БД MS SQL:
1. Импорт пар «ШК товара (EAN-13 / GTIN) ↔ ДМ (DataMatrix Честного Знака)» + название партии/заказа
   с автоматическим разбором GTIN/SN из строки DataMatrix и формированием именованных JSON-пакетов для обычных ПК.
2. Выгрузка статусов напечатанных ДМ (UPDATE в таблицу MS SQL Server).
3. Выгрузка журнала событий сканирования и печати (INSERT в таблицу MS SQL Server).
4. Надёжное сохранение конфигурации подключения в таблице `settings` (SQLite) и `%APPDATA%\ChestZn\mssql_config.json`
   с защитой пароля через Windows DPAPI.
"""

import os
import re
import json
import logging
import platform
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger("ChestZn.MSSQL")

try:
    import pyodbc
    PYODBC_AVAILABLE = True
except ImportError:
    pyodbc = None
    PYODBC_AVAILABLE = False


IDENTIFIER_REGEX = re.compile(r'^[A-Za-z0-9_#$А-Яа-яЁё]+$')


def quote_identifier(name: str) -> str:
    """
    Безопасное экранирование идентификаторов SQL Server (например, [dbo].[ChestnyZnakCodes]).
    Предотвращает SQL-инъекции в именах таблиц и колонок.
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"Некорректный идентификатор: {name}")

    parts = name.strip().split(".")
    if len(parts) > 3:
        raise ValueError(f"Слишком много уровней вложенности в имени: {name}")

    quoted_parts = []
    for part in parts:
        clean_part = part.strip().strip("[]")
        if not IDENTIFIER_REGEX.match(clean_part):
            raise ValueError(f"Недопустимые символы в имени идентификатора: {part}")
        quoted_parts.append(f"[{clean_part}]")

    return ".".join(quoted_parts)


def _config_file_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) / "ChestZn"
    base.mkdir(parents=True, exist_ok=True)
    return base / "mssql_config.json"


def load_mssql_config(store: Any = None) -> Dict[str, Any]:
    """Загружает конфигурацию MS SQL Server из Store (settings['mssql_config']) или файла mssql_config.json."""
    if store is not None:
        try:
            if hasattr(store, "get_mssql_config"):
                cfg = store.get_mssql_config()
                if isinstance(cfg, dict) and cfg:
                    return cfg
            if hasattr(store, "get_setting"):
                raw = store.get_setting("mssql_config")
                if raw:
                    cfg = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(cfg, dict) and cfg:
                        return cfg
        except Exception as e:
            logger.debug("Ошибка чтения mssql_config из Store: %s", e)

    p = _config_file_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.debug("Ошибка чтения %s: %s", p, e)

    return {
        "mock_mode": True,
        "driver": "ODBC Driver 17 for SQL Server",
        "server": "MOCK",
        "database": "ChestnyZnakDB",
        "auth_type": "windows",
        "user": "sa",
        "password_encrypted": "",
        "encryption": "no",
        "timeout": 10,
        "mapping": {
            "import": {
                "table": "dbo.ChestnyZnakCodes",
                "code_col": "mark_code",
                "gtin_col": "gtin",
                "sn_col": "serial_number",
                "order_col": "order_id",
                "where": "is_printed = 0",
            },
            "status": {
                "table": "dbo.ChestnyZnakCodes",
                "code_col": "mark_code",
                "is_printed_col": "is_printed",
                "printed_at_col": "printed_at",
                "status_col": "status_code",
            },
        },
    }


def save_mssql_config(store: Any, cfg: Dict[str, Any]) -> None:
    """Сохраняет конфигурацию MS SQL Server и в таблицу settings (SQLite), и в mssql_config.json."""
    if not isinstance(cfg, dict):
        return
    raw_json = json.dumps(cfg, ensure_ascii=False, indent=2)

    if store is not None:
        try:
            if hasattr(store, "save_mssql_config"):
                store.save_mssql_config(cfg)
            if hasattr(store, "set_setting"):
                store.set_setting("mssql_config", raw_json)
        except Exception as e:
            logger.debug("Ошибка сохранения mssql_config в Store: %s", e)

    try:
        _config_file_path().write_text(raw_json, encoding="utf-8")
    except Exception as e:
        logger.debug("Ошибка сохранения mssql_config.json: %s", e)


class MockCursor:
    """
    Виртуальный курсор для домашней проверки и автономного тестирования без живого сервера MS SQL.
    Возвращает реалистичные партии товаров с привязанными парами «ШК (EAN-13) <-> ДМ (GS1 DataMatrix)».
    """
    def __init__(self, parent_conn):
        self.conn = parent_conn
        self.description = []
        self._data = []
        self.rowcount = 0

    def execute(self, sql: str, params: Optional[List[Any]] = None):
        sql_lower = sql.strip().lower()
        params = params or []

        if "information_schema.tables" in sql_lower or "sys.tables" in sql_lower:
            self.description = [("table_schema", str), ("table_name", str)]
            self._data = [
                ("dbo", "ChestnyZnakCodes"),
                ("dbo", "MarkingEvents"),
                ("dbo", "ProductionOrders"),
            ]
            self.rowcount = len(self._data)

        elif "information_schema.columns" in sql_lower or "sys.columns" in sql_lower:
            self.description = [("column_name", str), ("data_type", str)]
            if any("markingevents" in str(p).lower() for p in params) or "markingevents" in sql_lower:
                self._data = [
                    ("event_id", "varchar"),
                    ("scanned_at", "datetime"),
                    ("operator_name", "nvarchar"),
                    ("station_id", "varchar"),
                    ("barcode", "varchar"),
                    ("status", "varchar"),
                    ("order_id", "varchar"),
                    ("error_details", "nvarchar"),
                ]
            else:
                self._data = [
                    ("id", "int"),
                    ("mark_code", "varchar"),
                    ("gtin", "varchar"),
                    ("shk", "varchar"),
                    ("serial_number", "varchar"),
                    ("order_id", "nvarchar"),
                    ("is_printed", "bit"),
                    ("printed_at", "datetime"),
                    ("status_code", "varchar"),
                ]
            self.rowcount = len(self._data)

        elif sql_lower.startswith("select @@version"):
            self.description = [("version", str)]
            self._data = [("Microsoft SQL Server 2022 (Mock Mode — локальная проверка)",)]
            self.rowcount = 1

        elif sql_lower.startswith("select"):
            self.description = [("code", str), ("gtin", str), ("sn", str), ("order_id", str)]
            rows = []
            # 3 понятные партии товаров с разными ШК (EAN-13) и настоящими GS1 DataMatrix (ДМ)
            batches = [
                ("Заказ_101_Кроссовки_Черные", "4607001234567", "04607001234567", 40),
                ("Заказ_102_Ботинки_Зимние",   "4607009876543", "04607009876543", 35),
                ("Заказ_103_Куртки_Спорт",     "4607112233445", "04607112233445", 25),
            ]
            for batch_title, shk_ean13, cz_gtin14, cnt in batches:
                for i in range(1, cnt + 1):
                    sn_str = f"SN{cz_gtin14[-4:]}{str(i).zfill(5)}"
                    dm_str = (
                        f"01{cz_gtin14}21{sn_str}"
                        f"\x1d91EE06\x1d92dGVzdGNyeXB0b3RhaWx2ZXJ5bG9uZ3N0cmluZ_{i:03d}=="
                    )
                    # В колонке gtin возвращаем 13-значный ШК товара, чтобы проверить связывание ШК -> ДМ
                    rows.append((dm_str, shk_ean13, sn_str, batch_title))
            self._data = rows
            self.rowcount = len(self._data)

        elif sql_lower.startswith("insert") or sql_lower.startswith("update"):
            self.rowcount = 1
            self._data = []
        else:
            self.description = [("status", str)]
            self._data = [("OK",)]
            self.rowcount = 1

        return self

    def fetchall(self):
        return list(self._data)

    def fetchone(self):
        return self._data[0] if self._data else None

    def close(self):
        pass


class MockMSSQLConnection:
    """Виртуальное подключение к MS SQL Server для Mock Mode."""
    def __init__(self):
        self.autocommit = True

    def cursor(self):
        return MockCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class MSSQLManager:
    """
    Основной сервис взаимодействия с Microsoft SQL Server.
    """

    DEFAULT_DRIVERS = [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
        "SQL Server",
    ]

    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        self.config = config_dict or load_mssql_config()
        self._conn = None

    @classmethod
    def get_available_drivers(cls) -> List[str]:
        if not PYODBC_AVAILABLE:
            return ["ODBC Driver 17 for SQL Server (Эмуляция — pyodbc не установлен)"]
        try:
            drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
            return drivers or cls.DEFAULT_DRIVERS
        except Exception as e:
            logger.error("Ошибка получения списка драйверов: %s", e)
            return cls.DEFAULT_DRIVERS

    def build_connection_string(self) -> str:
        driver = self.config.get("driver") or "ODBC Driver 17 for SQL Server"
        if "(" in driver:
            driver = driver.split("(")[0].strip()
        server = self.config.get("server", "").strip()
        database = self.config.get("database", "").strip()
        auth_type = self.config.get("auth_type", "windows")
        encryption_mode = self.config.get("encryption", "no")

        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"DATABASE={database}",
        ]

        if auth_type == "windows":
            parts.append("Trusted_Connection=yes")
        else:
            user = self.config.get("user", "")
            password = self._decrypt_password(self.config.get("password_encrypted", ""))
            parts.append(f"UID={user}")
            parts.append(f"PWD={password}")

        if encryption_mode == "no":
            parts.append("Encrypt=no")
            parts.append("TrustServerCertificate=yes")
        elif encryption_mode == "optional":
            parts.append("Encrypt=yes")
            parts.append("TrustServerCertificate=yes")
        elif encryption_mode == "strict":
            parts.append("Encrypt=yes")
            parts.append("TrustServerCertificate=no")

        return ";".join(parts)

    def is_mock_mode(self) -> bool:
        server = self.config.get("server", "").strip().upper()
        return bool(self.config.get("mock_mode", False) or server in ("MOCK", "DEMO", ""))

    def connect(self):
        if self.is_mock_mode():
            logger.info("Подключение к SQL Server в режиме Эмуляции (Mock Mode)")
            self._conn = MockMSSQLConnection()
            return self._conn

        if not PYODBC_AVAILABLE:
            raise RuntimeError(
                "Модуль pyodbc не установлен в текущем окружении Python! "
                "Установите его командой: pip install pyodbc (либо включите режим эмуляции Mock Mode)"
            )

        conn_str = self.build_connection_string()
        timeout = int(self.config.get("timeout", 10))
        logger.info("Подключение к SQL Server: %s / %s", self.config.get("server"), self.config.get("database"))
        self._conn = pyodbc.connect(conn_str, timeout=timeout)
        return self._conn

    def test_connection(self) -> Tuple[bool, str]:
        try:
            conn = self.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT @@VERSION")
            row = cursor.fetchone()
            version_str = str(row[0]) if row else "OK"
            return True, f"Успешное подключение! Сервер: {version_str[:90]}"
        except Exception as e:
            logger.exception("Сбой проверки подключения к SQL Server")
            return False, f"Ошибка подключения: {str(e)}"

    def get_tables(self) -> List[Tuple[str, str]]:
        conn = self.connect()
        cursor = conn.cursor()
        query = """
            SELECT TABLE_SCHEMA, TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
        cursor.execute(query)
        return cursor.fetchall()

    def get_columns(self, schema_name: str, table_name: str) -> List[Tuple[str, str]]:
        conn = self.connect()
        cursor = conn.cursor()
        query = """
            SELECT COLUMN_NAME, DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """
        cursor.execute(query, [schema_name, table_name])
        return cursor.fetchall()

    def build_import_sql(
        self,
        mapping: Dict[str, str],
        table: str,
        limit: int = 50000,
        where_clause: str = ""
    ) -> Tuple[str, List[str]]:
        """
        Генерация безопасного SELECT-запроса для выгрузки кодов ДМ и связанных ШК с MS SQL Server.
        """
        quoted_table = quote_identifier(table or mapping.get("table") or "dbo.ChestnyZnakCodes")
        code_col = quote_identifier(mapping.get("code_col") or "mark_code")
        gtin_col = quote_identifier(mapping["gtin_col"]) if mapping.get("gtin_col") else "NULL"
        sn_col = quote_identifier(mapping["sn_col"]) if mapping.get("sn_col") else "NULL"
        order_col = quote_identifier(mapping["order_col"]) if mapping.get("order_col") else "NULL"

        where_part = ""
        if where_clause and where_clause.strip():
            clean_where = where_clause.strip()
            if not clean_where.lower().startswith("where"):
                clean_where = f"WHERE {clean_where}"
            where_part = f" {clean_where}"

        sql = (
            f"SELECT TOP {int(limit)} "
            f"{code_col} AS code, {gtin_col} AS gtin, {sn_col} AS sn, {order_col} AS order_id "
            f"FROM {quoted_table}{where_part}"
        )
        return sql, ["code", "gtin", "sn", "order_id"]

    def build_status_update_sql(self, mapping: Dict[str, str], table: str) -> str:
        quoted_table = quote_identifier(table or mapping.get("table") or "dbo.ChestnyZnakCodes")
        code_col = quote_identifier(mapping.get("code_col") or "mark_code")

        set_clauses = []
        if mapping.get("is_printed_col"):
            set_clauses.append(f"{quote_identifier(mapping['is_printed_col'])} = ?")
        if mapping.get("printed_at_col"):
            set_clauses.append(f"{quote_identifier(mapping['printed_at_col'])} = ?")
        if mapping.get("status_col"):
            set_clauses.append(f"{quote_identifier(mapping['status_col'])} = ?")

        if not set_clauses:
            raise ValueError("Не указаны поля для обновления статуса в сопоставлении")

        set_str = ", ".join(set_clauses)
        return f"UPDATE {quoted_table} SET {set_str} WHERE {code_col} = ?"

    def build_event_insert_sql(self, mapping: Dict[str, str], table: str) -> Tuple[str, List[str]]:
        quoted_table = quote_identifier(table or "dbo.MarkingEvents")
        cols = []
        placeholders = []
        field_keys = []

        for key, target_col in mapping.items():
            if target_col and key not in ("event_id_check", "table"):
                cols.append(quote_identifier(target_col))
                placeholders.append("?")
                field_keys.append(key)

        cols_str = ", ".join(cols)
        placeholders_str = ", ".join(placeholders)
        sql = f"INSERT INTO {quoted_table} ({cols_str}) VALUES ({placeholders_str})"
        return sql, field_keys

    def import_codes_from_sql(
        self,
        mapping: Dict[str, str],
        table: str,
        limit: int = 50000,
        where_clause: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Вытягивает коды ДМ и привязанные к ним ШК из MS SQL Server.
        Автоматически разбирает GTIN (14 цифр) и серийный номер (SN) из строки DataMatrix,
        если в колонке gtin передан 13-значный EAN-13 штрихкод товара (ШК).
        """
        sql, _ = self.build_import_sql(mapping, table, limit, where_clause)
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(sql)

        records: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            code_dm = str(row[0]).strip() if row[0] is not None else ""
            if not code_dm:
                continue
            raw_gtin_or_shk = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
            raw_sn = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
            raw_order = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""

            dm_gtin = ""
            dm_sn = ""
            m_dm = re.match(r"^(?:01|\(01\))(\d{14})(?:21|\(21\))([^\x1d\(\r\n]{1,24})", code_dm.lstrip("\x1d"))
            if m_dm:
                dm_gtin = m_dm.group(1)
                dm_sn = m_dm.group(2).strip()

            final_gtin = dm_gtin or (
                raw_gtin_or_shk.zfill(14)
                if raw_gtin_or_shk.isdigit() and len(raw_gtin_or_shk) == 13
                else raw_gtin_or_shk
            )
            final_shk = raw_gtin_or_shk or (
                final_gtin[1:] if len(final_gtin) == 14 and final_gtin.startswith("0") else final_gtin
            )
            final_sn = raw_sn or dm_sn or "—"

            records.append({
                "code": code_dm,
                "gtin": final_gtin,
                "shk": final_shk,
                "sn": final_sn,
                "order_id": raw_order or f"Партия_ШК_{final_shk}",
            })

        logger.info("Успешно получено %d кодов (ШК + ДМ) из MS SQL Server", len(records))
        return records

    def export_code_statuses(
        self,
        mapping: Dict[str, str],
        table: str,
        codes_data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """Идемпотентная выгрузка статусов напечатанных марок обратно в MS SQL Server."""
        if not codes_data:
            return {"updated": 0, "not_found": 0}

        sql = self.build_status_update_sql(mapping, table)
        conn = self.connect()
        cursor = conn.cursor()

        updated_count = 0
        not_found_count = 0

        for item in codes_data:
            params = []
            if mapping.get("is_printed_col"):
                params.append(1 if item.get("is_printed", True) else 0)
            if mapping.get("printed_at_col"):
                params.append(item.get("printed_at"))
            if mapping.get("status_col"):
                params.append(item.get("status", "printed"))

            params.append(item.get("code"))
            cursor.execute(sql, params)
            if cursor.rowcount > 0:
                updated_count += 1
            else:
                not_found_count += 1

        if not self.is_mock_mode():
            conn.commit()

        logger.info("Выгрузка статусов кодов: обновлено %d, не найдено %d", updated_count, not_found_count)
        return {"updated": updated_count, "not_found": not_found_count}

    @staticmethod
    def export_structure_template(filepath: str):
        template = {
            "version": "1.1",
            "description": "Шаблон сопоставления структуры таблиц MS SQL Server для ChestZn (ШК + ДМ)",
            "import_codes_mapping": {
                "source_table": "dbo.ChestnyZnakCodes",
                "code_col": "mark_code",
                "gtin_col": "gtin",
                "sn_col": "serial_number",
                "order_col": "order_id",
            },
            "code_status_mapping": {
                "target_table": "dbo.ChestnyZnakCodes",
                "code_col": "mark_code",
                "is_printed_col": "is_printed",
                "printed_at_col": "printed_at",
                "status_col": "status_code",
            },
            "events_export_mapping": {
                "target_table": "dbo.MarkingEvents",
                "event_id": "event_id",
                "scanned_at": "scanned_at",
                "operator_name": "operator_name",
                "station_id": "station_id",
                "barcode": "barcode",
                "status": "status",
            },
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_structure_template(filepath: str) -> Dict[str, Any]:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _encrypt_password(plain_password: str) -> str:
        if not plain_password:
            return ""
        if platform.system() == "Windows":
            try:
                import win32crypt
                import base64
                enc = win32crypt.CryptProtectData(plain_password.encode("utf-8"), None, None, None, None, 0)
                return "dpapi:" + base64.b64encode(enc).decode("ascii")
            except Exception as e:
                logger.warning("Не удалось зашифровать пароль через DPAPI: %s", e)
        return plain_password

    @staticmethod
    def _decrypt_password(enc_password: str) -> str:
        if not enc_password:
            return ""
        if enc_password.startswith("dpapi:") and platform.system() == "Windows":
            try:
                import win32crypt
                import base64
                raw = base64.b64decode(enc_password[6:])
                _, decrypted = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
                return decrypted.decode("utf-8")
            except Exception as e:
                logger.error("Ошибка расшифровки пароля DPAPI: %s", e)
                return ""
        return enc_password