import json
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_study_session(user_id: str, session_name: str, topic: str = "", goal: str = "", tags=None):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO study_sessions (user_id, session_name, topic, goal, session_date, tags_json)
        VALUES (?, ?, ?, ?, date('now'), ?)
        """,
        (user_id, session_name, topic, goal, json.dumps(tags or [], ensure_ascii=False)),
    )
    session_id = cursor.lastrowid
    conn.commit()
    cursor.execute("SELECT * FROM study_sessions WHERE id = ?", (session_id,))
    row = dict(cursor.fetchone())
    conn.close()
    return row


def update_study_session(session_id: int, session_name: str, topic: str = "", goal: str = "", tags=None):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE study_sessions
        SET session_name = ?, topic = ?, goal = ?, tags_json = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (session_name, topic, goal, json.dumps(tags or [], ensure_ascii=False), session_id),
    )
    conn.commit()
    cursor.execute("SELECT * FROM study_sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_study_sessions(user_id: str):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM study_sessions
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_study_session(session_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM study_sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_study_session(session_id: int, user_id: str) -> bool:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM study_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    )
    if cursor.fetchone() is None:
        conn.close()
        return False

    cursor.execute("DELETE FROM quiz_attempts WHERE session_id = ?", (session_id,))
    cursor.execute(
        "DELETE FROM quiz_questions WHERE quiz_set_id IN (SELECT id FROM quiz_sets WHERE session_id = ?)",
        (session_id,),
    )
    cursor.execute("DELETE FROM quiz_sets WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM review_items WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM knowledge_points WHERE session_id = ?", (session_id,))
    cursor.execute(
        "DELETE FROM document_summaries WHERE document_id IN (SELECT id FROM study_documents WHERE session_id = ?)",
        (session_id,),
    )
    cursor.execute(
        "DELETE FROM document_chunks WHERE document_id IN (SELECT id FROM study_documents WHERE session_id = ?)",
        (session_id,),
    )
    cursor.execute("DELETE FROM study_documents WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM study_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    conn.commit()
    conn.close()
    return True
