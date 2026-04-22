import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text.lower())


def _overlap_score(query: str, text: str) -> int:
    query_counter = Counter(_tokenize(query))
    text_counter = Counter(_tokenize(text))
    return sum(min(query_counter[token], text_counter[token]) for token in query_counter)


def create_review_items_from_knowledge_points(
    user_id: str,
    session_id: int,
    knowledge_points: list[dict],
    knowledge_point_ids: list[int],
):
    if not knowledge_points:
        return

    conn = _connect()
    cursor = conn.cursor()
    next_review_at = (datetime.utcnow() + timedelta(days=1)).isoformat()

    for item, knowledge_point_id in zip(knowledge_points, knowledge_point_ids):
        cursor.execute(
            """
            INSERT INTO review_items
            (user_id, session_id, knowledge_point_id, topic, summary, source_type, confidence_score, next_review_at, priority_score, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'knowledge_point', ?, ?, ?, ?)
            """,
            (
                user_id,
                session_id,
                knowledge_point_id,
                item.get("title", "Untitled review item"),
                item.get("description", ""),
                item.get("difficulty", 3),
                next_review_at,
                float(item.get("importance", 3)),
                json.dumps(item.get("metadata", {}), ensure_ascii=False),
            ),
        )

    conn.commit()
    conn.close()


def get_review_items_for_session(session_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM review_items
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def build_review_context(user_id: str, query: str, current_session_id: int | None = None, limit: int = 2):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM review_items
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not rows:
        return {"items": [], "text": ""}

    now = datetime.utcnow()
    scored = []
    for row in rows:
        score = _overlap_score(query, f"{row['topic']} {row['summary']}")
        if current_session_id is not None and row.get("session_id") != current_session_id:
            score += 1
        next_review_raw = row.get("next_review_at")
        if next_review_raw:
            try:
                next_review_time = datetime.fromisoformat(next_review_raw)
                if next_review_time <= now:
                    score += 2
            except ValueError:
                pass
        score += min(int(row.get("error_count", 0)), 3)
        scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [row for score, row in scored[:limit] if score > 0]
    if not selected:
        selected = [row for _, row in scored[:1]]

    review_lines = []
    for item in selected:
        review_lines.append(f"- {item['topic']}: {item['summary']}")

    return {
        "items": selected,
        "text": "Relevant review notes:\n" + "\n".join(review_lines) if review_lines else "",
    }
