import json
import sqlite3
from pathlib import Path

from services.db import connect_study_db


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    return connect_study_db(DB_PATH)


def record_event(
    event_type: str,
    status: str = "success",
    session_id: int | None = None,
    user_id: str | None = None,
    duration_ms: int | None = None,
    message: str = "",
    metadata: dict | None = None,
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO event_logs
        (event_type, status, session_id, user_id, duration_ms, message, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            status,
            session_id,
            user_id,
            duration_ms,
            message,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()


def list_recent_events(limit: int = 50, session_id: int | None = None, status: str | None = None):
    conn = _connect()
    cursor = conn.cursor()
    params = []
    query = """
        SELECT *
        FROM event_logs
        WHERE 1 = 1
    """
    if session_id is not None:
        query += " AND session_id = ?"
        params.append(session_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit or 50)))
    cursor.execute(query, tuple(params))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for row in rows:
        try:
            row["metadata"] = json.loads(row.get("metadata_json") or "{}")
        except Exception:
            row["metadata"] = {}
    return rows


def create_run(
    run_type: str,
    session_id: int | None = None,
    user_id: str | None = None,
    title: str = "",
    input_summary: str = "",
    metadata: dict | None = None,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO agent_runs
        (run_type, session_id, user_id, status, title, input_summary, metadata_json)
        VALUES (?, ?, ?, 'running', ?, ?, ?)
        """,
        (run_type, session_id, user_id, title, input_summary, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return run_id


def add_run_step(
    run_id: int,
    step_name: str,
    step_status: str = "success",
    duration_ms: int | None = None,
    message: str = "",
    metadata: dict | None = None,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO agent_run_steps
        (run_id, step_name, step_status, duration_ms, message, metadata_json, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (run_id, step_name, step_status, duration_ms, message, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    step_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return step_id


def finish_run(
    run_id: int,
    status: str = "success",
    output_summary: str = "",
    duration_ms: int | None = None,
    metadata: dict | None = None,
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT metadata_json FROM agent_runs WHERE id = ?", (run_id,))
    row = cursor.fetchone()
    existing_metadata = {}
    if row and row[0]:
        try:
            existing_metadata = json.loads(row[0])
        except Exception:
            existing_metadata = {}
    merged_metadata = {**existing_metadata, **(metadata or {})}
    cursor.execute(
        """
        UPDATE agent_runs
        SET status = ?, output_summary = ?, duration_ms = ?, metadata_json = ?, finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, output_summary, duration_ms, json.dumps(merged_metadata, ensure_ascii=False), run_id),
    )
    conn.commit()
    conn.close()


def list_recent_runs(session_id: int | None = None, run_type: str | None = None, limit: int = 20):
    conn = _connect()
    cursor = conn.cursor()
    params = []
    query = """
        SELECT *
        FROM agent_runs
        WHERE 1 = 1
    """
    if session_id is not None:
        query += " AND session_id = ?"
        params.append(session_id)
    if run_type:
        query += " AND run_type = ?"
        params.append(run_type)
    query += " ORDER BY started_at DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    cursor.execute(query, tuple(params))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    for row in rows:
        try:
            row["metadata"] = json.loads(row.get("metadata_json") or "{}")
        except Exception:
            row["metadata"] = {}
    return rows


def get_run_steps(run_id: int) -> list[dict]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM agent_run_steps
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    for row in rows:
        try:
            row["metadata"] = json.loads(row.get("metadata_json") or "{}")
        except Exception:
            row["metadata"] = {}
    return rows
