import json
import re
import sqlite3
from pathlib import Path

from services.db import connect_study_db


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    return connect_study_db(DB_PATH)


def _extract_json_object(text: str) -> dict | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text or "")}


def _round_score(value: float) -> float:
    return round(float(value or 0), 2)


def evaluate_answer(
    query_text: str,
    answer_text: str,
    sources: list[dict] | None = None,
    llm_generator=None,
    source_type: str = "chat",
) -> dict:
    sources = sources or []
    answer_text = (answer_text or "").strip()
    query_text = (query_text or "").strip()

    answer_tokens = _tokenize(answer_text)
    query_tokens = _tokenize(query_text)
    overlap = len(answer_tokens & query_tokens)
    source_bonus = min(2.0, len(sources) * 0.5)
    length_bonus = 1.0 if len(answer_text) >= 80 else 0.4 if len(answer_text) >= 20 else 0.0

    fallback = {
        "overall_score": 0.0,
        "accuracy_score": 0.0,
        "grounding_score": 0.0,
        "completeness_score": 0.0,
        "clarity_score": 0.0,
        "feedback_text": "",
        "strengths": [],
        "risks": [],
        "next_actions": [],
    }

    if answer_text:
        fallback["accuracy_score"] = min(5.0, 1.8 + overlap * 0.45)
        fallback["grounding_score"] = min(5.0, 1.5 + source_bonus)
        fallback["completeness_score"] = min(5.0, 1.5 + length_bonus + min(1.5, overlap * 0.2))
        fallback["clarity_score"] = min(5.0, 2.0 + length_bonus + (0.5 if "1." in answer_text or "-" in answer_text else 0))
    fallback["overall_score"] = _round_score(
        (
            fallback["accuracy_score"]
            + fallback["grounding_score"]
            + fallback["completeness_score"]
            + fallback["clarity_score"]
        )
        / 4
    )
    fallback["feedback_text"] = (
        "回答整体较完整，继续加强基于资料的引用与结构化表达。"
        if fallback["overall_score"] >= 3.5
        else "回答已经覆盖部分要点，但还可以进一步增强准确性、资料依据和结构化表达。"
    )
    if fallback["accuracy_score"] >= 3:
        fallback["strengths"].append("覆盖了问题中的部分关键概念。")
    if fallback["grounding_score"] >= 3:
        fallback["strengths"].append("回答和资料来源有一定关联。")
    if fallback["grounding_score"] < 3:
        fallback["risks"].append("来源支撑还不够明显，建议更多引用当前资料。")
    if fallback["completeness_score"] < 3:
        fallback["risks"].append("回答偏简略，建议补上定义、机制和例子。")
    if fallback["clarity_score"] < 3:
        fallback["risks"].append("表达结构可以更清晰，建议用分点或先总后分。")
    fallback["next_actions"] = [
        "下次回答时先给一句核心定义，再补原理和场景。",
        "尽量把引用来源中的关键信息转成自己的表达。",
    ]

    if llm_generator is None:
        return fallback

    prompt = f"""
请评估下面这段学习问答的回答质量，并输出合法 JSON。

评分维度：
1. accuracy_score：是否准确回答问题
2. grounding_score：是否明显基于当前资料
3. completeness_score：是否覆盖关键点
4. clarity_score：表达是否清晰

每项满分 5 分。

JSON 结构：
{{
  "overall_score": 4.2,
  "accuracy_score": 4,
  "grounding_score": 4,
  "completeness_score": 4,
  "clarity_score": 5,
  "feedback_text": "整体反馈",
  "strengths": ["优点1"],
  "risks": ["风险1"],
  "next_actions": ["建议1", "建议2"]
}}

问题：
{query_text}

回答：
{answer_text}

来源数量：
{len(sources)}
"""
    try:
        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个中文回答质量评测助手，擅长基于学习场景给出简洁、稳定、可执行的评分反馈。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        parsed = _extract_json_object(raw)
        if parsed:
            return {
                "overall_score": _round_score(parsed.get("overall_score", fallback["overall_score"])),
                "accuracy_score": _round_score(parsed.get("accuracy_score", fallback["accuracy_score"])),
                "grounding_score": _round_score(parsed.get("grounding_score", fallback["grounding_score"])),
                "completeness_score": _round_score(parsed.get("completeness_score", fallback["completeness_score"])),
                "clarity_score": _round_score(parsed.get("clarity_score", fallback["clarity_score"])),
                "feedback_text": parsed.get("feedback_text", fallback["feedback_text"]),
                "strengths": parsed.get("strengths", fallback["strengths"]),
                "risks": parsed.get("risks", fallback["risks"]),
                "next_actions": parsed.get("next_actions", fallback["next_actions"]),
            }
    except Exception:
        pass

    return fallback


def save_answer_evaluation(
    session_id: int | None,
    user_id: str,
    query_text: str,
    answer_text: str,
    evaluation: dict,
    source_type: str = "chat",
    metadata: dict | None = None,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO answer_evaluations
        (session_id, user_id, source_type, query_text, answer_text, overall_score, accuracy_score, grounding_score, completeness_score, clarity_score, feedback_text, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            user_id,
            source_type,
            query_text,
            answer_text,
            float(evaluation.get("overall_score", 0) or 0),
            float(evaluation.get("accuracy_score", 0) or 0),
            float(evaluation.get("grounding_score", 0) or 0),
            float(evaluation.get("completeness_score", 0) or 0),
            float(evaluation.get("clarity_score", 0) or 0),
            evaluation.get("feedback_text", ""),
            json.dumps(
                {
                    "strengths": evaluation.get("strengths", []),
                    "risks": evaluation.get("risks", []),
                    "next_actions": evaluation.get("next_actions", []),
                    **(metadata or {}),
                },
                ensure_ascii=False,
            ),
        ),
    )
    evaluation_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return evaluation_id


def list_answer_evaluations(session_id: int, source_type: str | None = None, limit: int = 20) -> list[dict]:
    conn = _connect()
    cursor = conn.cursor()
    params = [session_id]
    query = """
        SELECT *
        FROM answer_evaluations
        WHERE session_id = ?
    """
    if source_type:
        query += " AND source_type = ?"
        params.append(source_type)
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    cursor.execute(query, tuple(params))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    for row in rows:
        try:
            row["metadata"] = json.loads(row.get("metadata_json") or "{}")
        except Exception:
            row["metadata"] = {}
    return rows


def summarize_evaluations(session_id: int, source_type: str | None = None) -> dict:
    items = list_answer_evaluations(session_id, source_type=source_type, limit=100)
    if not items:
        return {
            "count": 0,
            "overall_score": 0.0,
            "accuracy_score": 0.0,
            "grounding_score": 0.0,
            "completeness_score": 0.0,
            "clarity_score": 0.0,
        }

    def avg(key: str) -> float:
        return _round_score(sum(float(item.get(key, 0) or 0) for item in items) / len(items))

    return {
        "count": len(items),
        "overall_score": avg("overall_score"),
        "accuracy_score": avg("accuracy_score"),
        "grounding_score": avg("grounding_score"),
        "completeness_score": avg("completeness_score"),
        "clarity_score": avg("clarity_score"),
    }
