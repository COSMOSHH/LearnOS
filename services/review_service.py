import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
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
    next_review_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

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


def create_review_items_from_quiz_feedback(
    user_id: str,
    session_id: int,
    questions: list[dict],
    result: dict,
    score_threshold: float = 3.0,
):
    item_feedback = result.get("item_feedback") or []
    if not questions or not item_feedback:
        return []

    question_map = {}
    for question in questions:
        question_map[int(question.get("question_index", 0) or 0)] = question

    conn = _connect()
    cursor = conn.cursor()
    next_review_at = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    created_topics = []

    for item in item_feedback:
        score = float(item.get("score", 0) or 0)
        if score >= score_threshold:
            continue

        question_index = int(item.get("question_index", 0) or 0)
        question = question_map.get(question_index, {})
        question_text = (question.get("question_text") or f"第 {question_index} 题").strip()
        topic = f"测验薄弱点：{question_text[:60]}"
        feedback = (item.get("feedback") or "").strip()
        suggestion = (item.get("suggestion") or "").strip()
        reference_answer = (question.get("reference_answer") or "").strip()

        summary_parts = []
        if feedback:
            summary_parts.append(f"评分反馈：{feedback}")
        if suggestion:
            summary_parts.append(f"改进建议：{suggestion}")
        if reference_answer:
            summary_parts.append(f"参考答案要点：{reference_answer[:200]}")
        summary = "\n".join(summary_parts).strip() or "本题得分偏低，建议回看相关知识点并重新组织回答。"
        priority_score = max(6.0, 9.0 - score)
        metadata = {
            "question_index": question_index,
            "question_text": question_text,
            "score": score,
            "max_score": item.get("max_score", 5),
            "feedback": feedback,
            "suggestion": suggestion,
            "reference_answer": reference_answer,
        }

        cursor.execute(
            """
            SELECT id, error_count, priority_score
            FROM review_items
            WHERE user_id = ? AND session_id = ? AND topic = ? AND source_type = 'quiz_feedback'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, session_id, topic),
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE review_items
                SET summary = ?,
                    review_status = 'new',
                    confidence_score = ?,
                    error_count = ?,
                    next_review_at = ?,
                    priority_score = ?,
                    metadata_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    summary,
                    2,
                    int(existing["error_count"] or 0) + 1,
                    next_review_at,
                    max(float(existing["priority_score"] or 0), priority_score),
                    json.dumps(metadata, ensure_ascii=False),
                    existing["id"],
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO review_items
                (user_id, session_id, knowledge_point_id, topic, summary, source_type, review_status, confidence_score, error_count, next_review_at, priority_score, metadata_json)
                VALUES (?, ?, NULL, ?, ?, 'quiz_feedback', 'new', ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    topic,
                    summary,
                    2,
                    1,
                    next_review_at,
                    priority_score,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
        created_topics.append(topic)

    conn.commit()
    conn.close()
    return created_topics


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


def get_quiz_feedback_items(
    user_id: str,
    session_id: int | None = None,
    max_score: float | None = None,
    recent_days: int | None = None,
):
    conn = _connect()
    cursor = conn.cursor()
    params = [user_id]
    query = """
        SELECT *
        FROM review_items
        WHERE user_id = ? AND source_type = 'quiz_feedback'
    """
    if session_id is not None:
        query += " AND session_id = ?"
        params.append(session_id)
    query += " ORDER BY created_at DESC, id DESC"
    cursor.execute(query, tuple(params))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    now = datetime.now(timezone.utc)
    filtered = []
    for row in rows:
        metadata = json.loads(row.get("metadata_json") or "{}")
        score = float(metadata.get("score", 0) or 0)
        if max_score is not None and score > float(max_score):
            continue

        created_at_raw = row.get("created_at")
        created_at = None
        if created_at_raw:
            try:
                created_at = datetime.fromisoformat(str(created_at_raw).replace(" ", "T"))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except ValueError:
                created_at = None
        if recent_days is not None and created_at is not None:
            if created_at < now - timedelta(days=int(recent_days)):
                continue

        filtered.append(
            {
                "id": row["id"],
                "session_id": row.get("session_id"),
                "topic": row.get("topic", ""),
                "summary": row.get("summary", ""),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "priority_score": row.get("priority_score", 0),
                "error_count": row.get("error_count", 0),
                "question_index": metadata.get("question_index"),
                "question_text": metadata.get("question_text", ""),
                "score": score,
                "max_score": float(metadata.get("max_score", 5) or 5),
                "feedback": metadata.get("feedback", ""),
                "suggestion": metadata.get("suggestion", ""),
                "reference_answer": metadata.get("reference_answer", ""),
            }
        )
    return filtered


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

    now = datetime.now(timezone.utc)
    scored = []
    for row in rows:
        score = _overlap_score(query, f"{row['topic']} {row['summary']}")
        if current_session_id is not None and row.get("session_id") != current_session_id:
            score += 1
        next_review_raw = row.get("next_review_at")
        if next_review_raw:
            try:
                next_review_time = datetime.fromisoformat(next_review_raw)
                if next_review_time.tzinfo is None:
                    next_review_time = next_review_time.replace(tzinfo=timezone.utc)
                if next_review_time <= now:
                    score += 2
            except (TypeError, ValueError):
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
