import argparse
import sqlite3
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.db import connect_study_db, is_mysql
from tools.init_db import init_study_db
from Zero_RAG.chat_history_service import init_db as init_chat_db


STUDY_SQLITE_PATH = ROOT_DIR / "study_agent.sqlite3"
CHAT_SQLITE_PATH = ROOT_DIR / "chat_history.sqlite3"

STUDY_TABLES = [
    "study_sessions",
    "study_documents",
    "document_chunks",
    "document_summaries",
    "knowledge_points",
    "review_items",
    "quiz_sets",
    "quiz_questions",
    "quiz_attempts",
    "study_plans",
    "study_plan_items",
    "wrong_question_attempts",
    "event_logs",
    "answer_evaluations",
    "interview_sessions",
    "interview_turns",
    "agent_runs",
    "agent_run_steps",
    "rag_quality_samples",
]

CHAT_TABLES = [
    "chat_history",
    "thread_state",
]


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _fetch_sqlite_rows(sqlite_path: Path, table_name: str) -> tuple[list[str], list[tuple]]:
    if not sqlite_path.exists():
        return [], []
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    if not _sqlite_table_exists(conn, table_name):
        conn.close()
        return [], []

    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {table_name}")
    rows = cursor.fetchall()
    columns = [item[0] for item in cursor.description] if cursor.description else []
    payload = [tuple(row[column] for column in columns) for row in rows]
    conn.close()
    return columns, payload


def _clear_mysql_tables(cursor, tables: list[str]) -> None:
    for table_name in reversed(tables):
        cursor.execute(f"DELETE FROM {table_name}")


def _reset_auto_increment(cursor, table_name: str) -> None:
    cursor.execute(f"SHOW COLUMNS FROM {table_name} LIKE ?", ("id",))
    if cursor.fetchone() is None:
        return
    cursor.execute(f"SELECT MAX(id) AS max_id FROM {table_name}")
    row = cursor.fetchone()
    max_id = int((row or {}).get("max_id") or 0)
    cursor.execute(f"ALTER TABLE {table_name} AUTO_INCREMENT = {max_id + 1}")


def _insert_rows(cursor, table_name: str, columns: list[str], rows: list[tuple]) -> int:
    if not columns or not rows:
        return 0
    column_sql = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join(["?"] * len(columns))
    cursor.executemany(
        f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def migrate(clear_before_import: bool = False, dry_run: bool = False) -> dict:
    if not is_mysql():
        raise RuntimeError("Please set DB_TYPE=mysql before running migration.")

    init_study_db()
    init_chat_db()

    conn = connect_study_db()
    cursor = conn.cursor()
    all_tables = STUDY_TABLES + CHAT_TABLES

    if clear_before_import and not dry_run:
        _clear_mysql_tables(cursor, all_tables)
        conn.commit()

    summary = {}
    for table_name in STUDY_TABLES:
        columns, rows = _fetch_sqlite_rows(STUDY_SQLITE_PATH, table_name)
        summary[table_name] = len(rows)
        if not dry_run:
            _insert_rows(cursor, table_name, columns, rows)

    for table_name in CHAT_TABLES:
        columns, rows = _fetch_sqlite_rows(CHAT_SQLITE_PATH, table_name)
        summary[table_name] = len(rows)
        if not dry_run:
            _insert_rows(cursor, table_name, columns, rows)

    if not dry_run:
        for table_name in all_tables:
            _reset_auto_increment(cursor, table_name)
        conn.commit()

    conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description="Migrate LearnOS data from SQLite to MySQL.")
    parser.add_argument("--clear", action="store_true", help="Clear MySQL tables before importing data.")
    parser.add_argument("--dry-run", action="store_true", help="Only count rows without importing.")
    args = parser.parse_args()

    summary = migrate(clear_before_import=args.clear, dry_run=args.dry_run)
    for table_name, count in summary.items():
        print(f"{table_name}: {count}")


if __name__ == "__main__":
    main()
