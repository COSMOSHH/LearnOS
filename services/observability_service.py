import json
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
