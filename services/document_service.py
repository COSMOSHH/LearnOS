import hashlib
import json
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def calculate_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_document(
    session_id: int,
    title: str,
    file_name: str,
    file_path: str,
    file_type: str,
    file_size: int,
    content_hash: str,
    source_type: str = "upload",
    metadata: dict | None = None,
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO study_documents
        (session_id, title, file_name, file_path, file_type, file_size, content_hash, ingest_status, source_type, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?)
        """,
        (
            session_id,
            title,
            file_name,
            file_path,
            file_type,
            file_size,
            content_hash,
            source_type,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    document_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return document_id


def mark_document_ingested(document_id: int, status: str = "completed"):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE study_documents
        SET ingest_status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, document_id),
    )
    conn.commit()
    conn.close()


def save_document_chunks(document_id: int, chunks: list[str], chroma_ids: list[str], base_metadata: dict):
    conn = _connect()
    cursor = conn.cursor()
    for index, chunk in enumerate(chunks):
        cursor.execute(
            """
            INSERT INTO document_chunks
            (document_id, chunk_index, chunk_text, token_count, chroma_doc_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                index,
                chunk,
                len(chunk),
                chroma_ids[index],
                json.dumps(base_metadata, ensure_ascii=False),
            ),
        )
    conn.commit()
    conn.close()


def save_document_summary(document_id: int, summary_type: str, summary_text: str, extra: dict | None = None):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO document_summaries (document_id, summary_type, summary_text, extra_json)
        VALUES (?, ?, ?, ?)
        """,
        (document_id, summary_type, summary_text, json.dumps(extra or {}, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def save_knowledge_points(session_id: int, document_id: int, knowledge_points: list[dict]):
    conn = _connect()
    cursor = conn.cursor()
    created_ids = []
    for item in knowledge_points:
        cursor.execute(
            """
            INSERT INTO knowledge_points
            (session_id, document_id, title, description, category, importance, difficulty, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                document_id,
                item.get("title", "Untitled knowledge point"),
                item.get("description", ""),
                item.get("category", "general"),
                item.get("importance", 3),
                item.get("difficulty", 3),
                json.dumps(item.get("metadata", {}), ensure_ascii=False),
            ),
        )
        created_ids.append(cursor.lastrowid)
    conn.commit()
    conn.close()
    return created_ids


def get_session_documents(session_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM study_documents
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_session_summaries(session_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT ds.*, sd.title AS document_title
        FROM document_summaries ds
        JOIN study_documents sd ON ds.document_id = sd.id
        WHERE sd.session_id = ?
        ORDER BY ds.created_at ASC
        """,
        (session_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_session_knowledge_points(session_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM knowledge_points
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
