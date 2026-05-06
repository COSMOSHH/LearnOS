import json
import re


FORBIDDEN_REWRITE_TERMS = (
    "T2Retrieval",
    "Benchmark",
    "benchmark",
    "corpus",
    "语料库",
    "知识库",
    "数据集",
    "评测集",
    "benchmark corpus",
)
RIDDLE_LITERAL_TOKENS = (
    "打一肖",
    "打一生肖",
    "打一动物",
    "打一字",
    "谜语",
    "字谜",
    "灯谜",
    "歇后语",
    "欲钱买",
    "猜一",
)


PRONOUN_TOKENS = ("这个", "这个东西", "这个问题", "它", "它们", "前者", "后者", "上面", "这里", "那个", "那些")
COMPARE_TOKENS = ("区别", "不同", "对比", "比较", "联系", "关系")
WHY_TOKENS = ("为什么", "原因", "为啥")
SUMMARY_TOKENS = ("总结", "概括", "梳理", "介绍", "说说", "展开讲讲")
INTERVIEW_TOKENS = ("面试", "追问", "考察", "八股", "回答要点", "高频问题")
PLAN_TOKENS = ("学习计划", "怎么学", "先学", "下一步", "复习顺序", "安排")
QUIZ_TOKENS = ("出题", "测验", "自测", "练习题", "选择题", "填空题", "简答题")
DEFINITION_TOKENS = ("是什么", "定义", "概念", "作用", "怎么理解")


ROUTE_PROFILES = {
    "fact": {
        "strategy_name": "focused_single_query",
        "use_multi_query": False,
        "vector_top_k": 5,
        "bm25_top_k": 5,
        "final_top_k": 3,
        "parent_window": 1,
        "parent_max_chars": 900,
        "max_context_chars": 1800,
        "per_chunk_max_chars": 420,
    },
    "compare": {
        "strategy_name": "compare_multi_query",
        "use_multi_query": True,
        "vector_top_k": 7,
        "bm25_top_k": 7,
        "final_top_k": 4,
        "parent_window": 1,
        "parent_max_chars": 1100,
        "max_context_chars": 2400,
        "per_chunk_max_chars": 500,
    },
    "why": {
        "strategy_name": "mechanism_multi_query",
        "use_multi_query": True,
        "vector_top_k": 7,
        "bm25_top_k": 6,
        "final_top_k": 4,
        "parent_window": 2,
        "parent_max_chars": 1300,
        "max_context_chars": 2600,
        "per_chunk_max_chars": 520,
    },
    "summary": {
        "strategy_name": "summary_parent_context",
        "use_multi_query": True,
        "vector_top_k": 8,
        "bm25_top_k": 6,
        "final_top_k": 5,
        "parent_window": 2,
        "parent_max_chars": 1500,
        "max_context_chars": 3000,
        "per_chunk_max_chars": 560,
    },
    "interview": {
        "strategy_name": "interview_broad_context",
        "use_multi_query": True,
        "vector_top_k": 8,
        "bm25_top_k": 6,
        "final_top_k": 5,
        "parent_window": 2,
        "parent_max_chars": 1500,
        "max_context_chars": 2800,
        "per_chunk_max_chars": 520,
    },
    "plan": {
        "strategy_name": "plan_learning_context",
        "use_multi_query": True,
        "vector_top_k": 6,
        "bm25_top_k": 5,
        "final_top_k": 4,
        "parent_window": 1,
        "parent_max_chars": 1100,
        "max_context_chars": 2300,
        "per_chunk_max_chars": 480,
    },
    "quiz": {
        "strategy_name": "quiz_grounded_context",
        "use_multi_query": True,
        "vector_top_k": 6,
        "bm25_top_k": 6,
        "final_top_k": 4,
        "parent_window": 1,
        "parent_max_chars": 1100,
        "max_context_chars": 2300,
        "per_chunk_max_chars": 480,
    },
    "general": {
        "strategy_name": "balanced_default",
        "use_multi_query": False,
        "vector_top_k": 5,
        "bm25_top_k": 5,
        "final_top_k": 3,
        "parent_window": 1,
        "parent_max_chars": 900,
        "max_context_chars": 1800,
        "per_chunk_max_chars": 420,
    },
    "numeric_entity": {
        "strategy_name": "numeric_exact_boost",
        "use_multi_query": True,
        "vector_top_k": 3,
        "bm25_top_k": 20,
        "final_top_k": 5,
        "parent_window": 1,
        "parent_max_chars": 900,
        "max_context_chars": 1800,
        "per_chunk_max_chars": 420,
    },
    "literal_riddle": {
        "strategy_name": "literal_bm25_heavy",
        "use_multi_query": True,
        "vector_top_k": 3,
        "bm25_top_k": 20,
        "final_top_k": 5,
        "parent_window": 1,
        "parent_max_chars": 900,
        "max_context_chars": 1800,
        "per_chunk_max_chars": 420,
    },
}


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


def _contains_any(query: str, tokens: tuple[str, ...]) -> bool:
    return any(token in query for token in tokens)


def _contains_numeric_entity(query: str) -> bool:
    return bool(re.search(r"\d{6,}", query or ""))


def _extract_numeric_entities(query: str) -> list[str]:
    return re.findall(r"\d{6,}", query or "")


def _is_literal_riddle_query(query: str) -> bool:
    return _contains_any(query or "", RIDDLE_LITERAL_TOKENS)


def _has_forbidden_rewrite_term(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term.lower() in lowered for term in FORBIDDEN_REWRITE_TERMS)


def _apply_rewrite_guard(original_query: str, rewritten_query: str, rewrite_reason: str) -> tuple[str, str]:
    original_query = _normalize_query(original_query)
    rewritten_query = _normalize_query(rewritten_query or original_query)
    rewrite_reason = rewrite_reason or "normalized_only"

    if not original_query or not rewritten_query:
        return original_query, "rewrite_guard: empty_query"

    if _has_forbidden_rewrite_term(rewritten_query) and not _has_forbidden_rewrite_term(original_query):
        return original_query, f"rewrite_guard: forbidden_system_term; {rewrite_reason}"

    if _contains_numeric_entity(original_query):
        original_numbers = set(_extract_numeric_entities(original_query))
        rewritten_numbers = set(_extract_numeric_entities(rewritten_query))
        if not original_numbers.issubset(rewritten_numbers):
            return original_query, f"rewrite_guard: numeric_entity_changed; {rewrite_reason}"
        if rewritten_query != original_query:
            return original_query, f"rewrite_guard: numeric_entity_exact; {rewrite_reason}"

    if _is_literal_riddle_query(original_query) and rewritten_query != original_query:
        return original_query, f"rewrite_guard: literal_query_exact; {rewrite_reason}"

    if (
        len(original_query) <= 12
        and len(rewritten_query) > max(24, len(original_query) * 3)
        and not rewrite_reason.startswith("history_context")
    ):
        return original_query, f"rewrite_guard: short_query_overexpanded; {rewrite_reason}"

    return rewritten_query, rewrite_reason


def _finalize_rewrite_payload(payload: dict) -> dict:
    guarded_query, guarded_reason = _apply_rewrite_guard(
        payload.get("original_query", ""),
        payload.get("rewritten_query", ""),
        payload.get("rewrite_reason", ""),
    )
    payload["rewritten_query"] = guarded_query
    payload["rewrite_reason"] = guarded_reason
    return payload


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
        return _finalize_rewrite_payload(payload)

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

    return _finalize_rewrite_payload(payload)


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

    if _contains_numeric_entity(original_query):
        numbers = _extract_numeric_entities(original_query)
        for number in numbers:
            queries.extend([number, f"电话号码 {number}", f"{number} 归属地"])
        normalized = []
        seen = set()
        for item in queries:
            cleaned = _normalize_query(item)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return {"strategy": "numeric_entity_exact", "queries": normalized[:4]}

    if _is_literal_riddle_query(original_query):
        literal = re.sub(r"(打一肖|打一生肖|打一动物|打一字|谜语|字谜|灯谜|歇后语)", " ", original_query)
        literal = _normalize_query(literal)
        queries.extend([literal, f"{literal} 打一生肖", f"{literal} 谜语"])
        normalized = []
        seen = set()
        for item in queries:
            cleaned = _normalize_query(item)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return {"strategy": "literal_riddle_exact", "queries": normalized[:4]}

    if any(token in base_query for token in INTERVIEW_TOKENS):
        reason = "interview_query"
        queries.extend(
            [
                f"{original_query} 的核心概念和高频面试考点",
                f"{original_query} 的常见追问和回答要点",
                f"{original_query} 的原理、场景和易错点",
            ]
        )
    elif any(token in base_query for token in COMPARE_TOKENS):
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


def classify_question_type(query: str, session_context: str = "", mode: str = "chat") -> dict:
    normalized = _normalize_query(query)
    signals = []

    if _contains_numeric_entity(normalized):
        question_type = "numeric_entity"
        signals.append("numeric_entity")
    elif _is_literal_riddle_query(normalized):
        question_type = "literal_riddle"
        signals.append("literal_riddle")
    elif mode == "interview" or _contains_any(normalized, INTERVIEW_TOKENS):
        question_type = "interview"
        signals.append("interview_token")
    elif _contains_any(normalized, PLAN_TOKENS):
        question_type = "plan"
        signals.append("plan_token")
    elif _contains_any(normalized, QUIZ_TOKENS):
        question_type = "quiz"
        signals.append("quiz_token")
    elif _contains_any(normalized, COMPARE_TOKENS):
        question_type = "compare"
        signals.append("compare_token")
    elif _contains_any(normalized, WHY_TOKENS):
        question_type = "why"
        signals.append("why_token")
    elif _contains_any(normalized, SUMMARY_TOKENS):
        question_type = "summary"
        signals.append("summary_token")
    elif _contains_any(normalized, DEFINITION_TOKENS):
        question_type = "fact"
        signals.append("definition_token")
    else:
        question_type = "general"
        signals.append("fallback")

    confidence = 0.55 if question_type == "general" else 0.75
    if question_type in {"numeric_entity", "literal_riddle"}:
        confidence = 0.85
    if len(normalized) <= 8 and question_type == "general":
        confidence = 0.45

    return {
        "question_type": question_type,
        "confidence": confidence,
        "signals": signals,
    }


def plan_retrieval_route(
    original_query: str,
    rewritten_query: str = "",
    session_context: str = "",
    mode: str = "chat",
) -> dict:
    original_query = _normalize_query(original_query)
    rewritten_query = _normalize_query(rewritten_query)
    classification_query = original_query if (
        _contains_numeric_entity(original_query) or _is_literal_riddle_query(original_query)
    ) else (rewritten_query or original_query)
    classification = classify_question_type(classification_query, session_context=session_context, mode=mode)
    question_type = classification["question_type"]
    profile = {**ROUTE_PROFILES["general"], **ROUTE_PROFILES.get(question_type, {})}
    return {
        "classification": classification,
        "route_strategy": profile,
    }
