import json
import math
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


def _contains_doc_id(haystack: str, doc_ids: list[str]) -> bool:
    normalized_haystack = _normalize(haystack)
    if not normalized_haystack:
        return False
    for doc_id in doc_ids:
        token = _normalize(str(doc_id))
        if not token:
            continue
        pattern = rf"(?<![a-z0-9_-]){re.escape(token)}(?![a-z0-9_-])"
        if re.search(pattern, normalized_haystack):
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
    return _relevance_grade(result, case) > 0


def _source_text_for_result(result: dict) -> str:
    metadata = result.get("metadata") or {}
    return (
        f"{metadata.get('source', '')} "
        f"{metadata.get('document_title', '')} "
        f"{metadata.get('heading_path', '')} "
        f"{metadata.get('section_title', '')} "
        f"{metadata.get('document_id', '')} "
        f"{metadata.get('corpus_id', '')}"
    )


def _matched_relevant_doc_id(result: dict, case: dict) -> str | None:
    source_text = _source_text_for_result(result)
    for doc_id in [str(item) for item in case.get("relevant_doc_ids") or []]:
        if _contains_doc_id(source_text, [doc_id]):
            return doc_id
    return None


def _relevance_grade(result: dict, case: dict) -> float:
    metadata = result.get("metadata") or {}
    title = metadata.get("document_title", "")
    heading_path = metadata.get("heading_path", "")
    section_title = metadata.get("section_title", "")
    document = result.get("document", "")

    relevant_doc_ids = [str(item) for item in case.get("relevant_doc_ids") or []]
    relevant_scores = {str(key): float(value) for key, value in (case.get("relevant_scores") or {}).items()}
    relevant_sources = case.get("relevant_sources") or []
    relevant_titles = case.get("relevant_titles") or []
    relevant_keywords = case.get("relevant_keywords") or []

    source_text = _source_text_for_result(result)
    doc_text = f"{title} {heading_path} {section_title} {document}"

    if relevant_doc_ids:
        matched_doc_id = _matched_relevant_doc_id(result, case)
        if matched_doc_id:
            return relevant_scores.get(matched_doc_id, 1.0)

    source_match = _contains_any(source_text, relevant_sources)
    title_match = _contains_any(source_text, relevant_titles)
    keyword_match = _contains_any(doc_text, relevant_keywords)

    if relevant_sources and source_match:
        return 1.0
    if relevant_titles and title_match:
        return 1.0
    if relevant_keywords and keyword_match:
        return 1.0

    if relevant_sources and not (relevant_titles or relevant_keywords):
        return 1.0 if source_match else 0.0
    if relevant_titles and not (relevant_sources or relevant_keywords):
        return 1.0 if title_match else 0.0
    if relevant_keywords and not (relevant_sources or relevant_titles):
        return 1.0 if keyword_match else 0.0

    return 1.0 if source_match or title_match or keyword_match else 0.0


def _dcg(relevance_grades: list[float], k: int) -> float:
    score = 0.0
    for rank, grade in enumerate(relevance_grades[:k], start=1):
        score += (2**grade - 1) / math.log2(rank + 1)
    return score


def _ideal_relevance_grades(case: dict) -> list[float]:
    relevant_scores = case.get("relevant_scores") or {}
    if relevant_scores:
        return sorted([float(value) for value in relevant_scores.values()], reverse=True)
    if case.get("relevant_doc_ids"):
        return [1.0 for _ in case.get("relevant_doc_ids")]
    if case.get("relevant_sources") or case.get("relevant_titles") or case.get("relevant_keywords"):
        return [1.0]
    return []


def _dedup_relevance_grades_for_ndcg(results: list[dict], case: dict) -> list[float]:
    grades = []
    seen_doc_ids = set()
    for item in results:
        matched_doc_id = _matched_relevant_doc_id(item, case)
        if matched_doc_id:
            if matched_doc_id in seen_doc_ids:
                grades.append(0.0)
                continue
            seen_doc_ids.add(matched_doc_id)
        grades.append(_relevance_grade(item, case))
    return grades


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
    progress_callback=None,
) -> dict[str, Any]:
    k_values = [1, 3, 5]
    top_k = max(1, int(top_k or 5))
    if top_k not in k_values:
        k_values.append(top_k)
        k_values = sorted(set(k_values))

    per_case = []
    recall_sums = {k: 0.0 for k in k_values}
    ndcg_sums = {k: 0.0 for k in k_values}
    reciprocal_rank_sum = 0.0

    valid_cases = [case for case in cases if (case.get("query") or "").strip()]
    total_cases = len(valid_cases)

    for case_index, case in enumerate(valid_cases, start=1):
        query = (case.get("query") or "").strip()

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
        relevance_grades = []
        matched_doc_ids_by_rank = []
        for index, item in enumerate(sliced, start=1):
            relevance_grade = _relevance_grade(item, case)
            relevance_grades.append(relevance_grade)
            matched_doc_ids_by_rank.append(_matched_relevant_doc_id(item, case))
            if relevance_grade > 0 and first_hit_rank is None:
                first_hit_rank = index
        ndcg_relevance_grades = _dedup_relevance_grades_for_ndcg(sliced, case)

        reciprocal_rank = 0.0 if first_hit_rank is None else 1.0 / float(first_hit_rank)
        reciprocal_rank_sum += reciprocal_rank

        for k in k_values:
            if case.get("relevant_doc_ids"):
                matched = {doc_id for doc_id in matched_doc_ids_by_rank[:k] if doc_id}
                relevant_total = max(1, len(set(str(item) for item in case.get("relevant_doc_ids") or [])))
                recall_sums[k] += len(matched) / relevant_total
            elif first_hit_rank is not None and first_hit_rank <= k:
                recall_sums[k] += 1.0
            ideal = _ideal_relevance_grades(case)
            ideal_dcg = _dcg(ideal, k)
            ndcg_sums[k] += 0.0 if ideal_dcg <= 0 else min(1.0, _dcg(ndcg_relevance_grades, k) / ideal_dcg)

        top_results = [
            {
                "rank": rank,
                "score": item.get("score", 0.0),
                "relevance_grade": _relevance_grade(item, case),
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
                "ndcg_at": {
                    f"{k}": round(
                        0.0
                        if _dcg(_ideal_relevance_grades(case), k) <= 0
                        else min(1.0, _dcg(ndcg_relevance_grades, k) / _dcg(_ideal_relevance_grades(case), k)),
                        4,
                    )
                    for k in k_values
                },
                "hit_at": {f"{k}": bool(first_hit_rank is not None and first_hit_rank <= k) for k in k_values},
                "top_results": top_results,
                "retrieval_debug": retrieval_debug,
            }
        )
        if progress_callback:
            progress_callback(case_index, total_cases, query)

    case_count = len(per_case)
    if case_count == 0:
        return {
            "case_count": 0,
            "metrics": {
                "mrr": 0.0,
                "recall_at": {},
                "ndcg_at": {},
                "buckets": {},
                "top_k": top_k,
                "low_quality_mrr_threshold": low_quality_mrr_threshold,
            },
            "low_quality_cases": [],
            "cases": [],
        }

    recall_at = {f"{k}": round(recall_sums[k] / case_count, 4) for k in k_values}
    ndcg_at = {f"{k}": round(ndcg_sums[k] / case_count, 4) for k in k_values}
    mrr = round(reciprocal_rank_sum / case_count, 4)
    buckets: dict[str, dict[str, Any]] = {}
    for case in per_case:
        question_type = case.get("question_type") or "unknown"
        bucket = buckets.setdefault(
            question_type,
            {"case_count": 0, "reciprocal_rank_sum": 0.0, "recall_at_1_hits": 0, "ndcg_at_5_sum": 0.0},
        )
        bucket["case_count"] += 1
        bucket["reciprocal_rank_sum"] += case["reciprocal_rank"]
        bucket["ndcg_at_5_sum"] += (case.get("ndcg_at") or {}).get("5", 0.0)
        if case["first_hit_rank"] is not None and case["first_hit_rank"] <= 1:
            bucket["recall_at_1_hits"] += 1
    for bucket in buckets.values():
        count = max(1, bucket["case_count"])
        bucket["mrr"] = round(bucket.pop("reciprocal_rank_sum") / count, 4)
        bucket["recall_at_1"] = round(bucket.pop("recall_at_1_hits") / count, 4)
        bucket["ndcg_at_5"] = round(bucket.pop("ndcg_at_5_sum") / count, 4)

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
            "ndcg_at": ndcg_at,
            "buckets": buckets,
            "top_k": top_k,
            "low_quality_mrr_threshold": low_quality_mrr_threshold,
        },
        "low_quality_cases": low_quality_cases,
        "cases": per_case,
    }
