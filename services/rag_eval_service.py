import re
from typing import Any


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

    # If the case only provides one dimension, respect that dimension.
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
        expanded_payload = expand_query_to_multi_queries(
            original_query=query,
            rewritten_query=rewrite_payload.get("rewritten_query", query),
            session_context=session_context,
            llm_generator=llm_generator,
        )

        retrieved_results, retrieval_debug = retriever.retrieve_with_debug(
            rewrite_payload.get("rewritten_query", query),
            queries=expanded_payload.get("queries") or [rewrite_payload.get("rewritten_query", query)],
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

        per_case.append(
            {
                "query": query,
                "rewritten_query": rewrite_payload.get("rewritten_query", query),
                "rewrite_reason": rewrite_payload.get("rewrite_reason", ""),
                "query_strategy": expanded_payload.get("strategy", "single_query"),
                "expanded_queries": expanded_payload.get("queries") or [query],
                "first_hit_rank": first_hit_rank,
                "reciprocal_rank": reciprocal_rank,
                "hit_at": {f"{k}": bool(first_hit_rank is not None and first_hit_rank <= k) for k in k_values},
                "top_results": [
                    {
                        "rank": rank,
                        "score": item.get("score", 0.0),
                        "source": (item.get("metadata") or {}).get("source", ""),
                        "document_title": (item.get("metadata") or {}).get("document_title", ""),
                        "section_title": (item.get("metadata") or {}).get("section_title", ""),
                        "chunk_index": (item.get("metadata") or {}).get("chunk_index"),
                    }
                    for rank, item in enumerate(sliced, start=1)
                ],
                "retrieval_debug": retrieval_debug,
            }
        )

    case_count = len(per_case)
    if case_count == 0:
        return {
            "case_count": 0,
            "metrics": {"mrr": 0.0, "recall_at": {}},
            "low_quality_cases": [],
            "cases": [],
        }

    recall_at = {f"{k}": round(recall_hits[k] / case_count, 4) for k in k_values}
    mrr = round(reciprocal_rank_sum / case_count, 4)

    low_quality_cases = []
    for case in per_case:
        rr = case["reciprocal_rank"]
        rank = case["first_hit_rank"]
        if rr < low_quality_mrr_threshold:
            low_quality_cases.append(
                {
                    "query": case["query"],
                    "rewritten_query": case["rewritten_query"],
                    "reason": "no_hit" if rank is None else f"late_hit_rank_{rank}",
                    "reciprocal_rank": rr,
                    "top1": (case.get("top_results") or [{}])[0],
                }
            )

    return {
        "case_count": case_count,
        "metrics": {
            "mrr": mrr,
            "recall_at": recall_at,
            "top_k": top_k,
            "low_quality_mrr_threshold": low_quality_mrr_threshold,
        },
        "low_quality_cases": low_quality_cases,
        "cases": per_case,
    }


def build_eval_dataset_template(documents: list[dict]) -> list[dict]:
    template = []
    for doc in documents:
        title = doc.get("title") or doc.get("file_name") or ""
        if not title:
            continue
        source = doc.get("file_path") or doc.get("file_name") or title
        template.append(
            {
                "query": f"请解释 {title} 的核心概念和适用场景",
                "relevant_titles": [title],
                "relevant_sources": [source],
                "relevant_keywords": [title],
            }
        )
    return template
