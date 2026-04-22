import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _ensure_column(cursor, table_name: str, column_name: str, column_definition: str):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if column_name not in existing_columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def init_study_db() -> str:
    """Initialize the SQLite schema required for the study-agent MVP."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_name TEXT NOT NULL,
            topic TEXT,
            goal TEXT,
            status TEXT DEFAULT 'active',
            session_date DATE,
            tags_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS study_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            file_name TEXT,
            file_path TEXT,
            file_type TEXT,
            file_size INTEGER,
            content_hash TEXT,
            ingest_status TEXT DEFAULT 'pending',
            source_type TEXT DEFAULT 'upload',
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            token_count INTEGER,
            chroma_doc_id TEXT,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            summary_type TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            extra_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            document_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            category TEXT,
            importance INTEGER DEFAULT 3,
            difficulty INTEGER DEFAULT 3,
            source_chunk_id INTEGER,
            status TEXT DEFAULT 'active',
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id INTEGER,
            knowledge_point_id INTEGER,
            topic TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_type TEXT,
            review_status TEXT DEFAULT 'new',
            confidence_score INTEGER DEFAULT 3,
            error_count INTEGER DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            last_reviewed_at DATETIME,
            next_review_at DATETIME,
            priority_score REAL DEFAULT 0,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON study_sessions(user_id, session_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_session_id ON study_documents(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_session_id ON knowledge_points(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_review_user_next_review ON review_items(user_id, next_review_at)")

    _ensure_column(cursor, "study_documents", "source_type", "TEXT DEFAULT 'upload'")
    _ensure_column(cursor, "study_documents", "metadata_json", "TEXT")

    conn.commit()
    conn.close()
    return str(DB_PATH)


if __name__ == "__main__":
    print(init_study_db())
