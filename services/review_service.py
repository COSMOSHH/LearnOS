import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.db import connect_study_db


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"
REVIEW_OUTCOME_RULES = {
    "again": {"hours": 6, "priority_delta": 2.0, "confidence_delta": -1, "mastery_delta": -0.15, "status": "retrying"},
    "hard": {"hours": 24, "priority_delta": 0.8, "confidence_delta": 0, "mastery_delta": 0.05, "status": "queued"},
    "good": {"hours": 72, "priority_delta": -1.0, "confidence_delta": 1, "mastery_delta": 0.2, "status": "reviewed"},
    "easy": {"hours": 168, "priority_delta": -2.0, "confidence_delta": 2, "mastery_delta": 0.35, "status": "mastered"},
}


def _connect():
    return connect_study_db(DB_PATH)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text.lower())


def _overlap_score(query: str, text: str) -> int:
    query_counter = Counter(_tokenize(query))
    text_counter = Counter(_tokenize(text))
    return sum(min(query_counter[token], text_counter[token]) for token in query_counter)


def _load_metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _dump_metadata(payload: dict) -> str:
    return json.dumps(payload or {}, ensure_ascii=False)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_score(value: float) -> float:
    return round(float(value or 0), 2)


def _compute_due_state(next_review_at: str | None, now: datetime) -> tuple[str, float]:
    if not next_review_at:
        return "new", 1.5
    try:
        due_time = datetime.fromisoformat(str(next_review_at).replace(" ", "T"))
        if due_time.tzinfo is None:
            due_time = due_time.replace(tzinfo=timezone.utc)
    except ValueError:
        return "unknown", 0.5

    if due_time <= now:
        overdue_hours = max(0.0, (now - due_time).total_seconds() / 3600)
        return "due", min(4.0, 2.0 + overdue_hours / 24)

    remaining_hours = (due_time - now).total_seconds() / 3600
    if remaining_hours <= 24:
        return "soon", 1.2
    return "scheduled", 0.3


def _compute_queue_score(row: dict, now: datetime) -> tuple[float, str]:
    priority = float(row.get("priority_score", 0) or 0)
    confidence = int(row.get("confidence_score", 3) or 3)
    error_count = int(row.get("error_count", 0) or 0)
    retry_count = int(row.get("retry_count", 0) or 0)
    mastery_level = float(row.get("mastery_level", 0) or 0)
    due_state, due_boost = _compute_due_state(row.get("next_review_at"), now)

    score = priority * 1.5
    score += min(error_count, 4) * 0.9
    score += min(retry_count, 4) * 0.6
    score += max(0, 5 - confidence) * 0.7
    score += (1 - mastery_level) * 2.0
    score += due_boost
    if row.get("source_type") == "quiz_feedback":
        score += 1.0
    if row.get("review_status") in {"retrying", "due", "new"}:
        score += 1.2

    return _round_score(score), due_state


def _serialize_review_row(row: sqlite3.Row | dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    payload = dict(row)
    metadata = _load_metadata(payload.get("metadata_json"))
    queue_score, due_state = _compute_queue_score(payload, now)
    payload["metadata"] = metadata
    payload["queue_score"] = queue_score
    payload["due_state"] = due_state
    payload["mastery_level"] = _round_score(payload.get("mastery_level", 0) or 0)
    payload["last_score"] = _round_score(payload.get("last_score", 0) or 0)
    payload["best_score"] = _round_score(payload.get("best_score", 0) or 0)
    payload["retry_count"] = int(payload.get("retry_count", 0) or 0)
    payload["review_count"] = int(payload.get("review_count", 0) or 0)
    payload["error_count"] = int(payload.get("error_count", 0) or 0)
    payload["question_type"] = metadata.get("question_type", "short_answer")
    payload["question_text"] = metadata.get("question_text", payload.get("topic", ""))
    payload["question_options"] = metadata.get("question_metadata", {}).get("options", [])
    payload["reference_answer"] = metadata.get("reference_answer", "")
    payload["latest_feedback"] = metadata.get("feedback", "")
    payload["latest_suggestion"] = metadata.get("suggestion", "")
    payload["retry_history"] = metadata.get("retry_history", [])
    return payload


def _build_question_payload(metadata: dict) -> dict:
    question_metadata = metadata.get("question_metadata", {}) or {}
    return {
        "question_index": metadata.get("question_index", 1),
        "question_type": metadata.get("question_type", "short_answer"),
        "question_text": metadata.get("question_text", ""),
        "reference_answer": metadata.get("reference_answer", ""),
        "scoring_rubric": metadata.get("scoring_rubric", ""),
        "metadata": question_metadata,
    }


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
        metadata = {
            "origin": "knowledge_point",
            "importance": item.get("importance", 3),
            "difficulty": item.get("difficulty", 3),
        }
        cursor.execute(
            """
            INSERT INTO review_items
            (user_id, session_id, knowledge_point_id, topic, summary, source_type, confidence_score, next_review_at, priority_score, mastery_level, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'knowledge_point', ?, ?, ?, ?, ?)
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
                0.2,
                _dump_metadata(metadata),
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
        question_type = question.get("question_type", "short_answer")
        question_metadata = question.get("metadata")
        if question_metadata is None:
            question_metadata = _load_metadata(question.get("metadata_json"))
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
            "question_type": question_type,
            "question_metadata": question_metadata or {},
            "score": score,
            "max_score": float(item.get("max_score", 5) or 5),
            "feedback": feedback,
            "suggestion": suggestion,
            "reference_answer": reference_answer,
            "scoring_rubric": question.get("scoring_rubric", ""),
            "retry_history": [],
        }

        cursor.execute(
            """
            SELECT id, error_count, priority_score, retry_count, best_score
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
                    review_status = 'due',
                    confidence_score = ?,
                    error_count = ?,
                    next_review_at = ?,
                    priority_score = ?,
                    last_score = ?,
                    best_score = ?,
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
                    score,
                    max(float(existing["best_score"] or 0), score),
                    _dump_metadata(metadata),
                    existing["id"],
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO review_items
                (user_id, session_id, knowledge_point_id, topic, summary, source_type, review_status, confidence_score, error_count, review_count, next_review_at, priority_score, mastery_level, last_score, best_score, retry_count, metadata_json)
                VALUES (?, ?, NULL, ?, ?, 'quiz_feedback', 'due', ?, ?, 0, ?, ?, ?, ?, ?, 0, ?)
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
                    0.05,
                    score,
                    score,
                    _dump_metadata(metadata),
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
        SELECT *
        FROM review_items
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    )
    rows = [_serialize_review_row(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def list_review_queue(user_id: str, session_id: int | None = None, limit: int = 10, due_only: bool = False):
    conn = _connect()
    cursor = conn.cursor()
    params = [user_id]
    query = """
        SELECT *
        FROM review_items
        WHERE user_id = ?
    """
    if session_id is not None:
        query += " AND session_id = ?"
        params.append(session_id)
    query += " ORDER BY updated_at DESC, id DESC"
    cursor.execute(query, tuple(params))
    rows = [_serialize_review_row(row) for row in cursor.fetchall()]
    conn.close()

    if due_only:
        rows = [row for row in rows if row["due_state"] in {"due", "soon", "new"}]
    rows.sort(
        key=lambda item: (
            -float(item.get("queue_score", 0) or 0),
            item.get("next_review_at") or "",
            item.get("id", 0),
        )
    )
    return rows[: max(1, int(limit or 10))]


def get_wrong_question_attempts(review_item_id: int, limit: int = 5):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM wrong_question_attempts
        WHERE review_item_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (review_item_id, max(1, int(limit or 5))),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    attempts = []
    for row in rows:
        attempts.append(
            {
                "id": row["id"],
                "created_at": row.get("created_at"),
                "status": row.get("status", "retrying"),
                "total_score": _round_score(row.get("total_score", 0) or 0),
                "answer": _load_metadata(row.get("answer_json")).get("value"),
                "result": _load_metadata(row.get("result_json")),
            }
        )
    return attempts


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
    rows = [_serialize_review_row(row) for row in cursor.fetchall()]
    conn.close()

    now = datetime.now(timezone.utc)
    filtered = []
    for row in rows:
        score = float(row["metadata"].get("score", 0) or 0)
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
                "queue_score": row.get("queue_score", 0),
                "error_count": row.get("error_count", 0),
                "retry_count": row.get("retry_count", 0),
                "review_status": row.get("review_status", "new"),
                "due_state": row.get("due_state", "new"),
                "mastery_level": row.get("mastery_level", 0),
                "best_score": row.get("best_score", 0),
                "last_score": row.get("last_score", 0),
                "question_index": row["metadata"].get("question_index"),
                "question_text": row.get("question_text", ""),
                "question_type": row.get("question_type", "short_answer"),
                "question_options": row.get("question_options", []),
                "score": score,
                "max_score": float(row["metadata"].get("max_score", 5) or 5),
                "feedback": row.get("latest_feedback", ""),
                "suggestion": row.get("latest_suggestion", ""),
                "reference_answer": row.get("reference_answer", ""),
                "retry_history": get_wrong_question_attempts(row["id"], limit=3),
            }
        )
    return filtered


def update_review_item_progress(item_id: int, outcome: str, notes: str = "") -> dict | None:
    outcome = (outcome or "").strip().lower()
    if outcome not in REVIEW_OUTCOME_RULES:
        raise ValueError(f"Unsupported review outcome: {outcome}")

    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM review_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return None

    raw = dict(row)
    metadata = _load_metadata(raw.get("metadata_json"))
    rule = REVIEW_OUTCOME_RULES[outcome]
    now = datetime.now(timezone.utc)
    next_review_at = (now + timedelta(hours=rule["hours"])).isoformat()
    mastery_level = _clamp(float(raw.get("mastery_level", 0) or 0) + rule["mastery_delta"], 0.0, 1.0)
    confidence_score = int(_clamp(int(raw.get("confidence_score", 3) or 3) + rule["confidence_delta"], 1, 5))
    priority_score = _clamp(float(raw.get("priority_score", 0) or 0) + rule["priority_delta"], 0.5, 12.0)
    error_count = int(raw.get("error_count", 0) or 0) + (1 if outcome == "again" else 0)
    review_status = rule["status"]
    if outcome == "easy" and mastery_level >= 0.85:
        review_status = "mastered"
    elif outcome == "good" and mastery_level >= 0.55:
        review_status = "improving"

    history = metadata.get("review_history", [])
    history.append(
        {
            "reviewed_at": now.isoformat(),
            "outcome": outcome,
            "notes": (notes or "").strip(),
        }
    )
    metadata["review_history"] = history[-6:]
    metadata["last_outcome"] = outcome

    cursor.execute(
        """
        UPDATE review_items
        SET review_status = ?,
            confidence_score = ?,
            error_count = ?,
            review_count = ?,
            last_reviewed_at = ?,
            next_review_at = ?,
            priority_score = ?,
            mastery_level = ?,
            metadata_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            review_status,
            confidence_score,
            error_count,
            int(raw.get("review_count", 0) or 0) + 1,
            now.isoformat(),
            next_review_at,
            priority_score,
            mastery_level,
            _dump_metadata(metadata),
            item_id,
        ),
    )
    conn.commit()
    cursor.execute("SELECT * FROM review_items WHERE id = ?", (item_id,))
    updated = cursor.fetchone()
    conn.close()
    return _serialize_review_row(updated) if updated is not None else None


def retry_wrong_question(review_item_id: int, user_id: str, answer, llm_generator=None) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM review_items
        WHERE id = ? AND user_id = ? AND source_type = 'quiz_feedback'
        """,
        (review_item_id, user_id),
    )
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return None

    raw = dict(row)
    metadata = _load_metadata(raw.get("metadata_json"))
    question = _build_question_payload(metadata)
    from services.quiz_service import grade_single_question

    result = grade_single_question(question, answer, llm_generator=llm_generator)
    score = float(result.get("score", 0) or 0)
    retry_count = int(raw.get("retry_count", 0) or 0) + 1
    best_score = max(float(raw.get("best_score", 0) or 0), score)
    now = datetime.now(timezone.utc)

    if score < 3:
        review_status = "retrying"
        next_review_at = (now + timedelta(hours=12)).isoformat()
        priority_score = _clamp(max(float(raw.get("priority_score", 0) or 0), 6.0) + 0.8, 0.5, 12.0)
        mastery_delta = 0.05
    elif score < 4.5:
        review_status = "improving"
        next_review_at = (now + timedelta(days=3)).isoformat()
        priority_score = _clamp(float(raw.get("priority_score", 0) or 0) - 0.8, 0.5, 12.0)
        mastery_delta = 0.2
    else:
        review_status = "mastered"
        next_review_at = (now + timedelta(days=7)).isoformat()
        priority_score = _clamp(float(raw.get("priority_score", 0) or 0) - 2.0, 0.5, 12.0)
        mastery_delta = 0.35

    mastery_level = _clamp(float(raw.get("mastery_level", 0) or 0) + mastery_delta, 0.0, 1.0)
    confidence_score = int(_clamp(round(score), 1, 5))
    summary_parts = []
    if result.get("feedback"):
        summary_parts.append(f"评分反馈：{result['feedback']}")
    if result.get("suggestion"):
        summary_parts.append(f"改进建议：{result['suggestion']}")
    if metadata.get("reference_answer"):
        summary_parts.append(f"参考答案要点：{metadata['reference_answer'][:200]}")
    summary = "\n".join(summary_parts).strip() or raw.get("summary", "")

    retry_history = metadata.get("retry_history", [])
    retry_history.append(
        {
            "retried_at": now.isoformat(),
            "score": score,
            "max_score": float(result.get("max_score", 5) or 5),
            "status": review_status,
        }
    )
    metadata["retry_history"] = retry_history[-5:]
    metadata["last_retry_answer"] = answer
    metadata["feedback"] = result.get("feedback", "")
    metadata["suggestion"] = result.get("suggestion", "")
    metadata["status"] = review_status

    cursor.execute(
        """
        INSERT INTO wrong_question_attempts
        (review_item_id, session_id, user_id, question_type, answer_json, result_json, total_score, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_item_id,
            raw.get("session_id"),
            user_id,
            metadata.get("question_type", "short_answer"),
            _dump_metadata({"value": answer}),
            _dump_metadata(result),
            score,
            review_status,
        ),
    )
    attempt_id = cursor.lastrowid

    cursor.execute(
        """
        UPDATE review_items
        SET summary = ?,
            review_status = ?,
            confidence_score = ?,
            review_count = ?,
            last_reviewed_at = ?,
            next_review_at = ?,
            priority_score = ?,
            mastery_level = ?,
            last_score = ?,
            best_score = ?,
            retry_count = ?,
            metadata_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            summary,
            review_status,
            confidence_score,
            int(raw.get("review_count", 0) or 0) + 1,
            now.isoformat(),
            next_review_at,
            priority_score,
            mastery_level,
            score,
            best_score,
            retry_count,
            _dump_metadata(metadata),
            review_item_id,
        ),
    )
    conn.commit()
    cursor.execute("SELECT * FROM review_items WHERE id = ?", (review_item_id,))
    updated = cursor.fetchone()
    conn.close()

    return {
        "attempt_id": attempt_id,
        "status": review_status,
        "result": result,
        "review_item": _serialize_review_row(updated) if updated is not None else None,
    }


def build_review_context(user_id: str, query: str, current_session_id: int | None = None, limit: int = 2):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM review_items
        WHERE user_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (user_id,),
    )
    rows = [_serialize_review_row(row) for row in cursor.fetchall()]
    conn.close()

    if not rows:
        return {"items": [], "text": ""}

    scored = []
    for row in rows:
        score = _overlap_score(query, f"{row['topic']} {row['summary']}")
        score += float(row.get("queue_score", 0) or 0)
        if current_session_id is not None and row.get("session_id") == current_session_id:
            score += 1.0
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
