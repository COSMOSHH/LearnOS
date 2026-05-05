import json
import sqlite3
from collections import Counter
from pathlib import Path

from services.db import connect_study_db
from services.observability_service import list_recent_events, list_recent_runs
from services.query_service import classify_question_type


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    return connect_study_db(DB_PATH)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def save_low_quality_samples(
    session_id: int,
    user_id: str,
    low_quality_cases: list[dict],
    metrics: dict | None = None,
    source_run_id: int | None = None,
) -> int:
    if not low_quality_cases:
        return 0

    conn = _connect()
    cursor = conn.cursor()
    saved_count = 0
    for item in low_quality_cases:
        query_text = (item.get("query") or "").strip()
        if not query_text:
            continue
        question_type = item.get("question_type") or classify_question_type(query_text).get("question_type", "unknown")
        cursor.execute(
            """
            INSERT INTO rag_quality_samples
            (session_id, user_id, query_text, rewritten_query, question_type, reason, reciprocal_rank, top1_json, metrics_json, source_run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                user_id,
                query_text,
                item.get("rewritten_query", ""),
                question_type,
                item.get("reason", ""),
                _safe_float(item.get("reciprocal_rank"), 0.0),
                json.dumps(item.get("top1", {}), ensure_ascii=False),
                json.dumps(metrics or {}, ensure_ascii=False),
                source_run_id,
            ),
        )
        saved_count += 1
    conn.commit()
    conn.close()
    return saved_count


def list_low_quality_samples(session_id: int, limit: int = 50, status: str | None = None) -> list[dict]:
    conn = _connect()
    cursor = conn.cursor()
    params = [session_id]
    query = """
        SELECT *
        FROM rag_quality_samples
        WHERE session_id = ?
    """
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit or 50)))
    cursor.execute(query, tuple(params))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for row in rows:
        row["top1"] = _load_json(row.get("top1_json"), {})
        row["metrics"] = _load_json(row.get("metrics_json"), {})
    return rows


def build_rag_quality_dashboard(session_id: int, limit: int = 50) -> dict:
    runs = list_recent_runs(session_id=session_id, limit=max(1, int(limit or 50)))
    rag_eval_runs = [item for item in runs if item.get("run_type") == "rag.evaluate" and item.get("status") == "success"]
    chat_runs = [item for item in runs if item.get("run_type") == "study_chat" and item.get("status") == "success"]

    mrr_values = []
    recall_at_1_values = []
    ndcg_at_5_values = []
    low_quality_counts = []
    question_type_counter = Counter()
    route_strategy_counter = Counter()
    low_quality_reason_counter = Counter()
    low_quality_type_counter = Counter()

    for run in rag_eval_runs:
        metadata = run.get("metadata") or {}
        mrr_values.append(_safe_float(metadata.get("mrr"), 0.0))
        recall_at = metadata.get("recall_at") or {}
        recall_at_1_values.append(_safe_float(recall_at.get("1"), 0.0))
        ndcg_at = metadata.get("ndcg_at") or {}
        ndcg_at_5_values.append(_safe_float(ndcg_at.get("5"), 0.0))
        low_quality_counts.append(int(metadata.get("low_quality_count") or 0))

    for run in chat_runs:
        metadata = run.get("metadata") or {}
        question_type = (metadata.get("question_type") or "unknown").strip() or "unknown"
        route_name = ((metadata.get("route_strategy") or {}).get("strategy_name") or "default").strip() or "default"
        question_type_counter[question_type] += 1
        route_strategy_counter[route_name] += 1

    low_quality_samples = list_low_quality_samples(session_id=session_id, limit=limit)
    for sample in low_quality_samples:
        low_quality_reason_counter[sample.get("reason") or "unknown"] += 1
        low_quality_type_counter[sample.get("question_type") or "unknown"] += 1

    events = list_recent_events(limit=max(1, int(limit or 50)), session_id=session_id)
    eval_events = [item for item in events if item.get("event_type") == "rag.evaluate"]
    low_quality_trend = []
    for item in eval_events[:10]:
        metadata = item.get("metadata") or {}
        low_quality_trend.append(
            {
                "created_at": item.get("created_at", ""),
                "mrr": _safe_float(metadata.get("mrr"), 0.0),
                "low_quality_count": int(metadata.get("low_quality_count") or 0),
            }
        )

    avg_mrr = round(sum(mrr_values) / len(mrr_values), 4) if mrr_values else 0.0
    avg_recall_at_1 = round(sum(recall_at_1_values) / len(recall_at_1_values), 4) if recall_at_1_values else 0.0
    avg_ndcg_at_5 = round(sum(ndcg_at_5_values) / len(ndcg_at_5_values), 4) if ndcg_at_5_values else 0.0
    avg_low_quality_count = round(sum(low_quality_counts) / len(low_quality_counts), 2) if low_quality_counts else 0.0

    return {
        "session_id": session_id,
        "summary": {
            "eval_run_count": len(rag_eval_runs),
            "chat_run_count": len(chat_runs),
            "avg_mrr": avg_mrr,
            "avg_recall_at_1": avg_recall_at_1,
            "avg_ndcg_at_5": avg_ndcg_at_5,
            "avg_low_quality_count": avg_low_quality_count,
            "low_quality_sample_count": len(low_quality_samples),
        },
        "distributions": {
            "question_type": dict(question_type_counter),
            "route_strategy": dict(route_strategy_counter),
            "low_quality_reason": dict(low_quality_reason_counter),
            "low_quality_question_type": dict(low_quality_type_counter),
        },
        "low_quality_trend": low_quality_trend,
        "low_quality_samples": low_quality_samples[:20],
        "recent_eval_runs": rag_eval_runs[:10],
    }
