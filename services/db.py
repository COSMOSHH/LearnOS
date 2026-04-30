import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = ROOT_DIR / "study_agent.sqlite3"
DEFAULT_CHAT_SQLITE_PATH = ROOT_DIR / "chat_history.sqlite3"


def get_db_type() -> str:
    return os.getenv("DB_TYPE", "mysql").strip().lower()


def is_mysql() -> bool:
    return get_db_type() == "mysql"


def _mysql_config() -> dict:
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3307")),
        "user": os.getenv("MYSQL_USER", "rag_user"),
        "password": os.getenv("MYSQL_PASSWORD", "rag123456"),
        "database": os.getenv("MYSQL_DATABASE", "rag_db"),
        "charset": "utf8mb4",
        "autocommit": False,
    }


def _connect_mysql():
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise RuntimeError("PyMySQL is required when DB_TYPE=mysql. Run: pip install pymysql") from exc

    return MySQLConnectionAdapter(pymysql.connect(cursorclass=DictCursor, **_mysql_config()))


def normalize_db_value(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def normalize_db_row(row: dict):
    return Row({key: normalize_db_value(value) for key, value in row.items()})


def connect_study_db(sqlite_path: str | Path | None = None):
    if is_mysql():
        return _connect_mysql()
    path = Path(sqlite_path or os.getenv("SQLITE_DB_PATH") or DEFAULT_SQLITE_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def connect_chat_db(sqlite_path: str | Path | None = None):
    if is_mysql():
        return _connect_mysql()
    path = Path(sqlite_path or os.getenv("CHAT_SQLITE_DB_PATH") or DEFAULT_CHAT_SQLITE_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


class Row(dict):
    """Dict row that also supports sqlite-style numeric indexes."""

    def __getitem__(self, key: Any):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class MySQLCursorAdapter:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def execute(self, query: str, params=None):
        return self._cursor.execute(_translate_query(query), params)

    def executemany(self, query: str, params=None):
        return self._cursor.executemany(_translate_query(query), params)

    def fetchone(self):
        row = self._cursor.fetchone()
        return normalize_db_row(row) if isinstance(row, dict) else row

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [normalize_db_row(row) if isinstance(row, dict) else row for row in rows]

    def close(self):
        return self._cursor.close()


class MySQLConnectionAdapter:
    def __init__(self, connection):
        self._connection = connection
        self.row_factory = None

    def cursor(self):
        return MySQLCursorAdapter(self._connection.cursor())

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()


def _translate_query(query: str) -> str:
    translated = query
    translated = translated.replace("INSERT OR REPLACE INTO", "REPLACE INTO")
    translated = translated.replace("date('now')", "CURRENT_DATE")
    translated = translated.replace("?", "%s")
    return translated
