import json
import sqlite3
from pathlib import Path


DB_FILE = Path(__file__).resolve().parent.parent / "chat_history.sqlite3"


class ThreadState:
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.messages = []
        self.agent_stack = ["primary_assistant"]
        self.user_info = {}

    def to_dict(self):
        return {
            "thread_id": self.thread_id,
            "messages": self.messages,
            "agent_stack": self.agent_stack,
            "user_info": self.user_info,
        }

    @classmethod
    def from_dict(cls, data: dict):
        state = cls(data.get("thread_id", ""))
        state.messages = data.get("messages", [])
        state.agent_stack = data.get("agent_stack", ["primary_assistant"])
        state.user_info = data.get("user_info", {})
        return state


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            query TEXT NOT NULL,
            response TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    if not _column_exists(cursor, "chat_history", "session_id"):
        cursor.execute("ALTER TABLE chat_history ADD COLUMN session_id INTEGER")
    if not _column_exists(cursor, "chat_history", "sources_json"):
        cursor.execute("ALTER TABLE chat_history ADD COLUMN sources_json TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_state (
            thread_id TEXT PRIMARY KEY,
            state_data TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.commit()
    conn.close()


def save_thread_state(state: ThreadState):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO thread_state (thread_id, state_data, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (state.thread_id, json.dumps(state.to_dict(), ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def load_thread_state(thread_id: str) -> ThreadState:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT state_data FROM thread_state WHERE thread_id = ?", (thread_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return ThreadState.from_dict(json.loads(row[0]))
    return ThreadState(thread_id)


def save_chat_history(user_id: str, query: str, response: str, session_id: int | None = None, sources=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO chat_history (user_id, session_id, query, response, sources_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, session_id, query, response, json.dumps(sources or [], ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def get_user_history(user_id: str, session_id: int | None = None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if session_id is None:
        cursor.execute(
            """
            SELECT query, response, timestamp, session_id, sources_json FROM chat_history
            WHERE user_id = ? ORDER BY timestamp ASC
            """,
            (user_id,),
        )
    else:
        cursor.execute(
            """
            SELECT query, response, timestamp, session_id, sources_json FROM chat_history
            WHERE user_id = ? AND session_id = ? ORDER BY timestamp ASC
            """,
            (user_id, session_id),
        )
    rows = cursor.fetchall()
    conn.close()

    history = []
    for row in rows:
        history.append(
            {
                "query": row[0],
                "response": row[1],
                "timestamp": row[2],
                "session_id": row[3],
                "sources": json.loads(row[4]) if row[4] else [],
            }
        )
    return history
