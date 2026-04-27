import json
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_PATH = ROOT_DIR / "rag_eval_cases.json"
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_]+")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _contains_any(haystack: str, needles: list[str]) -> bool:
    normalized_haystack = _normalize(haystack)
    if not normalized_haystack:
        return False
    for item in needles:
        token = _normalize(item)
        if token and token in normalized_haystack:
            return True
    return False


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text or "") if len(token.strip()) >= 2}


def _build_session_keyword_text(session: dict, documents: list[dict], knowledge_points: list[dict]) -> str:
    parts = [
        session.get("session_name", ""),
        session.get("topic", ""),
        session.get("goal", ""),
    ]
    parts.extend(item.get("title", "") for item in documents)
    parts.extend(item.get("file_name", "") for item in documents)
    parts.extend(item.get("title", "") for item in knowledge_points)
    return " ".join(part for part in parts if part)


def load_default_eval_cases(path: Path | None = None) -> list[dict]:
    dataset_path = path or DEFAULT_DATASET_PATH
    if not dataset_path.exists():
        return []
    try:
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get("query")]


def build_eval_dataset_template(documents: list[dict]) -> list[dict]:
    template = []
    for doc in documents:
        title = doc.get("title") or doc.get("file_name") or ""
        if not title:
            continue
        source = doc.get("file_path") or doc.get("file_name") or title
        template.append(
            {
                "query": f"请解释 {title} 的核心概念、关键机制和适用场景",
                "relevant_titles": [title],
                "relevant_sources": [source],
                "relevant_keywords": [title],
                "case_type": "template",
            }
        )
    return template


def build_session_eval_cases(
    session: dict,
    documents: list[dict],
    knowledge_points: list[dict],
    *,
    limit: int = 8,
    include_template_cases: bool = True,
) -> list[dict]:
    session_keywords = _tokenize(_build_session_keyword_text(session, documents, knowledge_points))
    default_cases = load_default_eval_cases()
    selected_cases = []

    for case in default_cases:
        topic_keywords = case.get("topic_keywords") or []
        if not topic_keywords:
            continue
        if session_keywords & {token.lower() for token in topic_keywords}:
            selected_cases.append({**case, "case_type": "default"})

    if include_template_cases:
        selected_cases.extend(build_eval_dataset_template(documents))

    deduped = []
    seen_queries = set()
    for case in selected_cases:
        query = (case.get("query") or "").strip()
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        deduped.append(case)

    return deduped[: max(1, int(limit or 8))]


def _is_relevant(result: dict, case: dict) -> bool:
    metadata = result.get("metadata") or {}
    source = metadata.get("source", "")
    title = metadata.get("document_title", "")
    heading_path = metadata.get("heading_path", "")
    section_title = metadata.get("section_title", "")
    document = result.get("document", "")

    relevant_sources = case.get("relevant_sources") or []
    relevant_titles = case.get("relevant_titles") or []
    relevant_keywords = case.get("relevant_keywords") or []

    source_text = f"{source} {title} {heading_path} {section_title}"
    doc_text = f"{title} {heading_path} {section_title} {document}"

    source_match = _contains_any(source_text, relevant_sources)
    title_match = _contains_any(source_text, relevant_titles)
    keyword_match = _contains_any(doc_text, relevant_keywords)

    if relevant_sources and source_match:
        return True
    if relevant_titles and title_match:
        return True
    if relevant_keywords and keyword_match:
        return True

    if relevant_sources and not (relevant_titles or relevant_keywords):
        return source_match
    if relevant_titles and not (relevant_sources or relevant_keywords):
        return title_match
    if relevant_keywords and not (relevant_sources or relevant_titles):
        return keyword_match

    return source_match or title_match or keyword_match


def evaluate_retrieval_cases(
    retriever,
    cases: list[dict],
    *,
    rewrite_query,
    expand_query_to_multi_queries,
    plan_retrieval_route=None,
    session_context: str,
    llm_generator=None,
    top_k: int = 5,
    low_quality_mrr_threshold: float = 0.5,
) -> dict[str, Any]:
    k_values = [1, 3, 5]
    top_k = max(1, int(top_k or 5))
    if top_k not in k_values:
        k_values.append(top_k)
        k_values = sorted(set(k_values))

    per_case = []
    recall_hits = {k: 0 for k in k_values}
    reciprocal_rank_sum = 0.0

    for case in cases:
        query = (case.get("query") or "").strip()
        if not query:
            continue

        rewrite_payload = rewrite_query(
            query,
            history=[],
            session_context=session_context,
            llm_generator=llm_generator,
        )
        rewritten_query = rewrite_payload.get("rewritten_query", query)
        if plan_retrieval_route:
            route_payload = plan_retrieval_route(
                query,
                rewritten_query=rewritten_query,
                session_context=session_context,
                mode="eval",
            )
        else:
            route_payload = {
                "classification": {"question_type": "unknown", "confidence": 0.0, "signals": []},
                "route_strategy": {},
            }
        route_strategy = route_payload.get("route_strategy") or {}
        classification = route_payload.get("classification") or {}

        if route_strategy.get("use_multi_query", True):
            expanded_payload = expand_query_to_multi_queries(
                original_query=query,
                rewritten_query=rewritten_query,
                session_context=session_context,
                llm_generator=llm_generator,
            )
        else:
            expanded_payload = {"strategy": "routed_single_query", "queries": [rewritten_query or query]}

        retrieved_results, retrieval_debug = retriever.retrieve_with_debug(
            rewritten_query,
            queries=expanded_payload.get("queries") or [rewritten_query],
            vector_top_k=route_strategy.get("vector_top_k"),
            bm25_top_k=route_strategy.get("bm25_top_k"),
            final_top_k=max(top_k, int(route_strategy.get("final_top_k") or top_k)),
            parent_window=route_strategy.get("parent_window"),
            parent_max_chars=route_strategy.get("parent_max_chars"),
        )
        retrieval_debug.update(
            {
                "question_type": classification.get("question_type", "unknown"),
                "question_type_confidence": classification.get("confidence", 0.0),
                "question_type_signals": classification.get("signals", []),
                "route_strategy": route_strategy,
            }
        )
        sliced = retrieved_results[:top_k]

        first_hit_rank = None
        for index, item in enumerate(sliced, start=1):
            if _is_relevant(item, case):
                first_hit_rank = index
                break

        reciprocal_rank = 0.0 if first_hit_rank is None else 1.0 / float(first_hit_rank)
        reciprocal_rank_sum += reciprocal_rank

        for k in k_values:
            if first_hit_rank is not None and first_hit_rank <= k:
                recall_hits[k] += 1

        top_results = [
            {
                "rank": rank,
                "score": item.get("score", 0.0),
                "source": (item.get("metadata") or {}).get("source", ""),
                "document_title": (item.get("metadata") or {}).get("document_title", ""),
                "section_title": (item.get("metadata") or {}).get("section_title", ""),
                "chunk_index": (item.get("metadata") or {}).get("chunk_index"),
            }
            for rank, item in enumerate(sliced, start=1)
        ]

        per_case.append(
            {
                "query": query,
                "rewritten_query": rewritten_query,
                "rewrite_reason": rewrite_payload.get("rewrite_reason", ""),
                "question_type": classification.get("question_type", "unknown"),
                "route_strategy": route_strategy,
                "query_strategy": expanded_payload.get("strategy", "single_query"),
                "expanded_queries": expanded_payload.get("queries") or [query],
                "first_hit_rank": first_hit_rank,
                "reciprocal_rank": reciprocal_rank,
                "hit_at": {f"{k}": bool(first_hit_rank is not None and first_hit_rank <= k) for k in k_values},
                "top_results": top_results,
                "retrieval_debug": retrieval_debug,
            }
        )

    case_count = len(per_case)
    if case_count == 0:
        return {
            "case_count": 0,
            "metrics": {
                "mrr": 0.0,
                "recall_at": {},
                "buckets": {},
                "top_k": top_k,
                "low_quality_mrr_threshold": low_quality_mrr_threshold,
            },
            "low_quality_cases": [],
            "cases": [],
        }

    recall_at = {f"{k}": round(recall_hits[k] / case_count, 4) for k in k_values}
    mrr = round(reciprocal_rank_sum / case_count, 4)
    buckets: dict[str, dict[str, Any]] = {}
    for case in per_case:
        question_type = case.get("question_type") or "unknown"
        bucket = buckets.setdefault(question_type, {"case_count": 0, "reciprocal_rank_sum": 0.0, "recall_at_1_hits": 0})
        bucket["case_count"] += 1
        bucket["reciprocal_rank_sum"] += case["reciprocal_rank"]
        if case["first_hit_rank"] is not None and case["first_hit_rank"] <= 1:
            bucket["recall_at_1_hits"] += 1
    for bucket in buckets.values():
        count = max(1, bucket["case_count"])
        bucket["mrr"] = round(bucket.pop("reciprocal_rank_sum") / count, 4)
        bucket["recall_at_1"] = round(bucket.pop("recall_at_1_hits") / count, 4)

    low_quality_cases = []
    for case in per_case:
        rr = case["reciprocal_rank"]
        rank = case["first_hit_rank"]
        reason = None
        if rank is None:
            reason = "no_hit"
        elif rr < low_quality_mrr_threshold:
            reason = f"late_hit_rank_{rank}"
        elif case.get("top_results") and (case["top_results"][0].get("score", 0) or 0) < 0.3:
            reason = "weak_top1_score"

        if reason:
            low_quality_cases.append(
                {
                    "query": case["query"],
                    "rewritten_query": case["rewritten_query"],
                    "question_type": case.get("question_type", "unknown"),
                    "route_strategy": case.get("route_strategy", {}),
                    "reason": reason,
                    "reciprocal_rank": rr,
                    "top1": (case.get("top_results") or [{}])[0],
                }
            )

    return {
        "case_count": case_count,
        "metrics": {
            "mrr": mrr,
            "recall_at": recall_at,
            "buckets": buckets,
            "top_k": top_k,
            "low_quality_mrr_threshold": low_quality_mrr_threshold,
        },
        "low_quality_cases": low_quality_cases,
        "cases": per_case,
    }
