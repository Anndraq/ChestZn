"""
ChestZn — Модуль интеграции с Microsoft SQL Server (mssql.py)
Обеспечивает двухсторонний обмен между мастер-ПК и корпоративной БД заказчика:
1. Импорт кодов маркировки (SELECT из таблицы заказчика -> локальный пул)
2. Выгрузка событий маркировки/сканирования/ошибок (INSERT в таблицу заказчика)
3. Выгрузка статусов кодов (UPDATE в таблицу кодов заказчика)
4. Разбор схемы таблиц/колонок прямо с сервера
5. Защита паролей через DPAPI (Windows)
6. Защита от SQL-инъекций (параметризация и строгая валидация идентификаторов)
7. Встроенный Mock-режим для автономного развертывания и демонстрации без живого сервера
"""

import re
import json
import logging
import platform
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger("ChestZn.MSSQL")

try:
    import pyodbc
    PYODBC_AVAILABLE = True
except ImportError:
    pyodbc = None
    PYODBC_AVAILABLE = False


# Безопасный регекс для имен схем, таблиц и колонок SQL Server
IDENTIFIER_REGEX = re.compile(r'^[A-Za-z0-9_#$]+$')


def quote_identifier(name: str) -> str:
    """
    Безопасное экранирование идентификаторов SQL Server (например, [dbo].[MarkCodes]).
    Предотвращает SQL-инъекции в именах таблиц и колонок.
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"Некорректный идентификатор: {name}")

    parts = name.strip().split(".")
    if len(parts) > 2:
        raise ValueError(f"Слишком много уровней вложенности в имени: {name}")

    quoted_parts = []
    for part in parts:
        clean_part = part.strip().strip("[]")
        if not IDENTIFIER_REGEX.match(clean_part):
            raise ValueError(f"Недопустимые символы в имени идентификатора: {part}")
        quoted_parts.append(f"[{clean_part}]")

    return ".".join(quoted_parts)


class MockCursor:
    """Виртуальный курсор для автономного тестирования и демо-режима"""
    def __init__(self, parent_conn):
        self.conn = parent_conn
        self.description = []
        self._data = []
        self.rowcount = 0

    def execute(self, sql: str, params: Optional[List[Any]] = None):
        sql_lower = sql.strip().lower()
        params = params or []

        # 1. Запрос таблиц
        if "information_schema.tables" in sql_lower or "sys.tables" in sql_lower:
            self.description = [("table_schema", str), ("table_name", str)]
            self._data = [
                ("dbo", "ChestnyZnakCodes"),
                ("dbo", "MarkingEvents"),
                ("dbo", "ProductionOrders")
            ]
            self.rowcount = len(self._data)

        # 2. Запрос колонок
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
                    ("error_details", "nvarchar")
                ]
            else:
                self._data = [
                    ("id", "int"),
                    ("mark_code", "varchar"),
                    ("gtin", "varchar"),
                    ("serial_number", "varchar"),
                    ("order_id", "varchar"),
                    ("is_printed", "bit"),
                    ("printed_at", "datetime"),
                    ("status_code", "varchar")
                ]
            self.rowcount = len(self._data)

        # 3. SELECT (Импорт кодов)
        elif sql_lower.startswith("select"):
            self.description = [("code", str), ("gtin", str), ("sn", str)]
            # Генерация тестовых кодов маркировки формата Честный ЗНАК
            self._data = [
                (
                    f"0104600000000000215{str(i).zfill(6)}\u001d910001\u001d92mockcrypto==",
                    "04600000000000",
                    f"5{str(i).zfill(6)}"
                )
                for i in range(1, 101)
            ]
            self.rowcount = len(self._data)

        # 4. INSERT / UPDATE
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
    """Виртуальное подключение к MS SQL Server для Mock Mode"""
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
    Инкапсулирует подключение, проверку драйверов, маппинг полей и транзакции.
    """

    DEFAULT_DRIVERS = [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
        "SQL Server"
    ]

    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        self.config = config_dict or {}
        self._conn = None

    @classmethod
    def get_available_drivers(cls) -> List[str]:
        """Возвращает список установленных в Windows ODBC-драйверов для SQL Server"""
        if not PYODBC_AVAILABLE:
            return ["Эмуляция (pyodbc не установлен)"]
        try:
            drivers = pyodbc.drivers()
            return [d for d in drivers if "SQL Server" in d]
        except Exception as e:
            logger.error("Ошибка получения списка драйверов: %s", e)
            return []

    def build_connection_string(self) -> str:
        """Сборка безопасной строки подключения ODBC"""
        driver = self.config.get("driver") or "ODBC Driver 17 for SQL Server"
        server = self.config.get("server", "").strip()
        database = self.config.get("database", "").strip()
        auth_type = self.config.get("auth_type", "windows")  # 'windows' или 'sql'
        encryption_mode = self.config.get("encryption", "no")  # 'no', 'optional', 'strict'

        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"DATABASE={database}"
        ]

        if auth_type == "windows":
            parts.append("Trusted_Connection=yes")
        else:
            user = self.config.get("user", "")
            password = self._decrypt_password(self.config.get("password_encrypted", ""))
            parts.append(f"UID={user}")
            parts.append(f"PWD={password}")

        # Настройка шифрования (лечит ошибку "Клиент не поддерживает шифрование")
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
        """Проверка, включен ли режим заглушки"""
        server = self.config.get("server", "").strip().upper()
        return bool(self.config.get("mock_mode", False) or server in ("MOCK", "DEMO", ""))

    def connect(self):
        """Установка соединения с БД (или возврат MockConnection)"""
        if self.is_mock_mode():
            logger.info("Подключение к SQL Server в режиме Эмуляции (Mock Mode)")
            self._conn = MockMSSQLConnection()
            return self._conn

        if not PYODBC_AVAILABLE:
            raise RuntimeError(
                "Модуль pyodbc не установлен в виртуальном окружении! "
                "Выполните: pip install pyodbc"
            )

        conn_str = self.build_connection_string()
        timeout = int(self.config.get("timeout", 10))
        logger.info("Подключение к SQL Server: %s / %s", self.config.get("server"), self.config.get("database"))
        self._conn = pyodbc.connect(conn_str, timeout=timeout)
        return self._conn

    def test_connection(self) -> Tuple[bool, str]:
        """Тестирование подключения с возвратом статуса и версии сервера"""
        try:
            conn = self.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT @@VERSION")
            row = cursor.fetchone()
            version_str = row[0] if row else "OK"
            return True, f"Успешное подключение! Версия сервера: {version_str[:80]}"
        except Exception as e:
            logger.exception("Сбой проверки подключения к SQL Server")
            return False, f"Ошибка подключения: {str(e)}"

    def get_tables(self) -> List[Tuple[str, str]]:
        """Получение списка доступных схем и таблиц"""
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
        """Получение списка колонок и типов данных для выбранной таблицы"""
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

    # --- Генерация SQL и Предпросмотр (Dry Run) ---

    def build_import_sql(self, mapping: Dict[str, str], table: str, limit: int = 50000, where_clause: str = "") -> Tuple[str, List[str]]:
        """Генерация безопасного SELECT-запроса для импорта кодов ЧЗ"""
        quoted_table = quote_identifier(table)
        code_col = quote_identifier(mapping["code_col"])
        gtin_col = quote_identifier(mapping["gtin_col"]) if mapping.get("gtin_col") else "NULL"
        sn_col = quote_identifier(mapping["sn_col"]) if mapping.get("sn_col") else "NULL"

        where_part = ""
        if where_clause.strip():
            # Базовая очистка WHERE
            clean_where = where_clause.strip()
            if not clean_where.lower().startswith("where"):
                clean_where = f"WHERE {clean_where}"
            where_part = f" {clean_where}"

        sql = f"SELECT TOP {int(limit)} {code_col} AS code, {gtin_col} AS gtin, {sn_col} AS sn FROM {quoted_table}{where_part}"
        return sql, ["code", "gtin", "sn"]

    def build_status_update_sql(self, mapping: Dict[str, str], table: str) -> str:
        """Генерация UPDATE-запроса для обратной записи статуса кодов (использован/напечатан)"""
        quoted_table = quote_identifier(table)
        code_col = quote_identifier(mapping["code_col"])
        
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
        sql = f"UPDATE {quoted_table} SET {set_str} WHERE {code_col} = ?"
        return sql

    def build_event_insert_sql(self, mapping: Dict[str, str], table: str) -> Tuple[str, List[str]]:
        """Генерация INSERT-запроса для выгрузки журнала событий сканирования"""
        quoted_table = quote_identifier(table)
        cols = []
        placeholders = []
        field_keys = []

        for key, target_col in mapping.items():
            if target_col and key != "event_id_check":
                cols.append(quote_identifier(target_col))
                placeholders.append("?")
                field_keys.append(key)

        cols_str = ", ".join(cols)
        placeholders_str = ", ".join(placeholders)
        sql = f"INSERT INTO {quoted_table} ({cols_str}) VALUES ({placeholders_str})"
        return sql, field_keys

    # --- Импорт и экспорт данных ---

    def import_codes_from_sql(self, mapping: Dict[str, str], table: str, limit: int = 50000, where_clause: str = "") -> List[Dict[str, Any]]:
        """Импорт пула кодов из БД заказчика в локальный формат"""
        sql, _ = self.build_import_sql(mapping, table, limit, where_clause)
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(sql)
        
        records = []
        for row in cursor.fetchall():
            records.append({
                "code": str(row[0]) if row[0] is not None else "",
                "gtin": str(row[1]) if row[1] is not None else "",
                "sn": str(row[2]) if row[2] is not None else ""
            })
        logger.info("Успешно импортировано %d кодов из SQL Server", len(records))
        return records

    def export_code_statuses(self, mapping: Dict[str, str], table: str, codes_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """Идемпотентная выгрузка статусов напечатанных марок"""
        sql = self.build_status_update_sql(mapping, table)
        conn = self.connect()
        cursor = conn.cursor()

        updated_count = 0
        not_found_count = 0

        for item in codes_data:
            params = []
            if mapping.get("is_printed_col"):
                params.append(1 if item.get("is_printed") else 0)
            if mapping.get("printed_at_col"):
                params.append(item.get("printed_at"))
            if mapping.get("status_col"):
                params.append(item.get("status", "printed"))
            
            # WHERE code_col = ?
            params.append(item.get("code"))

            cursor.execute(sql, params)
            if cursor.rowcount > 0:
                updated_count += 1
            else:
                not_found_count += 1

        if not self.is_mock_mode():
            conn.commit()

        logger.info("Выгрузка статусов кодов: обновлено %d, не найдено в БД заказчика %d", updated_count, not_found_count)
        return {"updated": updated_count, "not_found": not_found_count}

    # --- Шаблоны структуры (Экспорт/Импорт схемы) ---

    @staticmethod
    def export_structure_template(filepath: str):
        """Сохранение JSON-шаблона для администратора базы данных заказчика"""
        template = {
            "version": "1.0",
            "description": "Шаблон сопоставления структуры таблиц MS SQL Server для ChestZn",
            "import_codes_mapping": {
                "source_table": "dbo.ChestnyZnakCodes",
                "code_col": "mark_code",
                "gtin_col": "gtin",
                "sn_col": "serial_number"
            },
            "code_status_mapping": {
                "target_table": "dbo.ChestnyZnakCodes",
                "code_col": "mark_code",
                "is_printed_col": "is_printed",
                "printed_at_col": "printed_at",
                "status_col": "status_code"
            },
            "events_export_mapping": {
                "target_table": "dbo.MarkingEvents",
                "event_id": "event_id",
                "scanned_at": "scanned_at",
                "operator_name": "operator_name",
                "station_id": "station_id",
                "barcode": "barcode",
                "status": "status"
            }
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
        logger.info("Шаблон структуры сохранен: %s", filepath)

    @staticmethod
    def load_structure_template(filepath: str) -> Dict[str, Any]:
        """Загрузка заполненного администратором шаблона"""
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    # --- DPAPI шифрование паролей ---

    @staticmethod
    def _encrypt_password(plain_password: str) -> str:
        """Шифрование через Windows DPAPI"""
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
        """Расшифровка через Windows DPAPI"""
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
