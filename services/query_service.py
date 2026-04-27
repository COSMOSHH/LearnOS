import json
import re


PRONOUN_TOKENS = ("这个", "这个东西", "这个问题", "它", "它们", "前者", "后者", "上面", "这里", "那个", "那些")
COMPARE_TOKENS = ("区别", "不同", "对比", "比较", "联系", "关系")
WHY_TOKENS = ("为什么", "原因", "为啥")
SUMMARY_TOKENS = ("总结", "概括", "梳理", "介绍", "说说", "展开讲讲")


def _extract_json_object(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _normalize_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", (query or "").strip())
    return cleaned


def _summarize_history(history: list[dict] | None) -> str:
    if not history:
        return ""
    recent_user_turns = [item.get("content", "").strip() for item in history if item.get("role") == "user" and item.get("content")]
    return recent_user_turns[-1] if recent_user_turns else ""


def _build_fallback_rewrite(query: str, history_summary: str, session_context: str) -> tuple[str, str]:
    normalized = _normalize_query(query)
    lowered = normalized.lower()

    if not normalized:
        return "", "empty_query"

    needs_context = any(token in normalized for token in PRONOUN_TOKENS) or len(normalized) <= 8
    if needs_context and history_summary:
        rewritten = f"{history_summary}。补充追问：{normalized}"
        return rewritten, "history_context"

    if session_context and any(token in normalized for token in COMPARE_TOKENS + WHY_TOKENS):
        rewritten = f"围绕 {session_context}，回答：{normalized}"
        return rewritten, "session_context"

    if session_context and not any(token in lowered for token in ("mysql", "redis", "rag", "锁", "事务", "索引", "面试")):
        rewritten = f"{session_context}：{normalized}"
        return rewritten, "topic_prefix"

    return normalized, "normalized_only"


def rewrite_query(
    query: str,
    history: list[dict] | None = None,
    session_context: str = "",
    llm_generator=None,
) -> dict:
    original_query = _normalize_query(query)
    history_summary = _summarize_history(history)
    fallback_query, fallback_reason = _build_fallback_rewrite(original_query, history_summary, session_context)

    payload = {
        "original_query": original_query,
        "rewritten_query": fallback_query,
        "rewrite_reason": fallback_reason,
        "history_summary": history_summary,
        "session_context": session_context,
    }

    if llm_generator is None or not original_query:
        return payload

    prompt = f"""
你是一个学习系统的检索查询改写助手。请把用户问题改写成更适合知识库检索的中文 query。
要求：
1. 保留原意，不要回答问题。
2. 如果用户问题存在代词、省略或口语化表达，请结合最近上下文补全。
3. 输出严格 JSON：
{{
  "rewritten_query": "改写后的检索问题",
  "rewrite_reason": "改写原因，简短描述"
}}

当前学习主题：{session_context or "无"}
最近用户问题：{history_summary or "无"}
当前问题：{original_query}
"""
    try:
        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是中文检索优化助手，擅长把学习问答问题改写为更适合知识库召回的 query。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        parsed = _extract_json_object(raw)
        rewritten = _normalize_query((parsed or {}).get("rewritten_query", ""))
        if rewritten:
            payload["rewritten_query"] = rewritten
            payload["rewrite_reason"] = (parsed or {}).get("rewrite_reason", fallback_reason) or fallback_reason
    except Exception:
        pass

    return payload


def expand_query_to_multi_queries(
    original_query: str,
    rewritten_query: str = "",
    session_context: str = "",
    llm_generator=None,
) -> dict:
    base_query = _normalize_query(rewritten_query or original_query)
    original_query = _normalize_query(original_query)
    lowered = base_query.lower()

    queries = [base_query] if base_query else []
    reason = "single_query"

    if any(token in base_query for token in COMPARE_TOKENS):
        reason = "compare_query"
        compare_parts = re.split(r"[和与及、/]|vs|VS", original_query)
        compare_parts = [item.strip(" ：:，,。?？") for item in compare_parts if item.strip(" ：:，,。?？")]
        meaningful_parts = [item for item in compare_parts if len(item) >= 2][:3]
        for part in meaningful_parts:
            queries.append(f"{part} 的定义、作用和特点")
        queries.append(f"{original_query} 的核心区别和联系")
    elif any(token in base_query for token in WHY_TOKENS):
        reason = "why_query"
        queries.extend(
            [
                f"{original_query} 的原因",
                f"{original_query} 的底层机制",
                f"{original_query} 的适用场景和设计取舍",
            ]
        )
    elif any(token in base_query for token in SUMMARY_TOKENS):
        reason = "summary_query"
        queries.extend(
            [
                f"{original_query} 的核心概念",
                f"{original_query} 的关键机制",
                f"{original_query} 的典型场景",
            ]
        )

    if session_context and len(queries) == 1:
        queries.append(f"围绕 {session_context}，{base_query}")
        reason = "session_expanded"

    normalized = []
    seen = set()
    for item in queries:
        cleaned = _normalize_query(item)
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)

    payload = {
        "strategy": reason,
        "queries": normalized[:4] if normalized else ([base_query] if base_query else []),
    }

    if llm_generator is None or not base_query:
        return payload

    prompt = f"""
你是学习系统的检索扩展助手。请把当前问题扩展成 2 到 4 个更适合召回不同知识点的检索问题。
要求：
1. 保留原意，不要回答问题。
2. 如果是对比题，要拆成对比双方与差异问题。
3. 如果是原理/原因题，要补充机制、场景、取舍。
4. 输出严格 JSON：
{{
  "strategy": "扩展策略",
  "queries": ["问题1", "问题2", "问题3"]
}}

学习主题：{session_context or "无"}
原始问题：{original_query}
改写问题：{base_query}
"""
    try:
        raw = llm_generator.chat_once(
            messages=[
                {"role": "system", "content": "你擅长把学习问答问题扩展为多个互补检索 query。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=400,
        )
        parsed = _extract_json_object(raw) or {}
        llm_queries = parsed.get("queries") or []
        normalized_llm = []
        seen = set()
        for item in llm_queries:
            cleaned = _normalize_query(str(item))
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized_llm.append(cleaned)
        if normalized_llm:
            payload["queries"] = normalized_llm[:4]
            payload["strategy"] = parsed.get("strategy", payload["strategy"]) or payload["strategy"]
    except Exception:
        pass

    return payload
