from collections import Counter

from services.observability_service import list_recent_events, list_recent_runs


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def build_rag_quality_dashboard(session_id: int, limit: int = 50) -> dict:
    runs = list_recent_runs(session_id=session_id, limit=max(1, int(limit or 50)))
    rag_eval_runs = [item for item in runs if item.get("run_type") == "rag.evaluate" and item.get("status") == "success"]
    chat_runs = [item for item in runs if item.get("run_type") == "study_chat" and item.get("status") == "success"]

    mrr_values = []
    recall_at_1_values = []
    low_quality_counts = []
    question_type_counter = Counter()
    route_strategy_counter = Counter()

    for run in rag_eval_runs:
        metadata = run.get("metadata") or {}
        mrr_values.append(_safe_float(metadata.get("mrr"), 0.0))
        recall_at = metadata.get("recall_at") or {}
        recall_at_1_values.append(_safe_float(recall_at.get("1"), 0.0))
        low_quality_counts.append(int(metadata.get("low_quality_count") or 0))

    for run in chat_runs:
        metadata = run.get("metadata") or {}
        question_type = (metadata.get("question_type") or "unknown").strip() or "unknown"
        route_name = ((metadata.get("route_strategy") or {}).get("strategy_name") or "default").strip() or "default"
        question_type_counter[question_type] += 1
        route_strategy_counter[route_name] += 1

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
    avg_low_quality_count = round(sum(low_quality_counts) / len(low_quality_counts), 2) if low_quality_counts else 0.0

    return {
        "session_id": session_id,
        "summary": {
            "eval_run_count": len(rag_eval_runs),
            "chat_run_count": len(chat_runs),
            "avg_mrr": avg_mrr,
            "avg_recall_at_1": avg_recall_at_1,
            "avg_low_quality_count": avg_low_quality_count,
        },
        "distributions": {
            "question_type": dict(question_type_counter),
            "route_strategy": dict(route_strategy_counter),
        },
        "low_quality_trend": low_quality_trend,
        "recent_eval_runs": rag_eval_runs[:10],
    }
