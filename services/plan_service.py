import json
import sqlite3
from pathlib import Path

from services.db import connect_study_db


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"
PLAN_ITEM_TYPES = ["today_focus", "priority_review", "next_questions", "action_steps"]


def _connect():
    return connect_study_db(DB_PATH)


def _round_score(value: float) -> float:
    return round(float(value or 0), 2)


def _make_item(text: str, priority_score: float, source_kind: str, reason: str = "", metadata: dict | None = None) -> dict:
    return {
        "text": str(text).strip(),
        "priority_score": _round_score(priority_score),
        "metadata": {
            "source_kind": source_kind,
            "reason": reason,
            **(metadata or {}),
        },
    }


def _normalize_generated_items(items, default_priority: float, source_kind: str):
    normalized = []
    for index, item in enumerate(items or [], start=1):
        if isinstance(item, dict):
            text = item.get("text", "").strip()
            priority_score = item.get("priority_score", max(1.0, default_priority - (index - 1) * 0.3))
            metadata = item.get("metadata", {})
        else:
            text = str(item).strip()
            priority_score = max(1.0, default_priority - (index - 1) * 0.3)
            metadata = {}
        if text:
            normalized.append(_make_item(text, priority_score, metadata.get("source_kind", source_kind), metadata.get("reason", ""), metadata))
    return normalized


def _sort_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            -float(item.get("priority_score", 0) or 0),
            item.get("text", ""),
        ),
    )[:4]


def generate_learning_plan(
    session: dict,
    knowledge_points: list[dict],
    review_items: list[dict],
    history: list[dict],
    latest_quiz_attempt: dict | None = None,
    llm_generator=None,
) -> dict:
    knowledge_points = knowledge_points or []
    review_items = review_items or []
    history = history or []
    latest_quiz_attempt = latest_quiz_attempt or {}

    weak_points = []
    quiz_result = latest_quiz_attempt.get("result") or {}
    for item in quiz_result.get("item_feedback", []):
        score = float(item.get("score", 0) or 0)
        if score < 4:
            weak_points.append(item)
    weak_points.sort(key=lambda item: float(item.get("score", 0) or 0))

    sorted_review_items = sorted(
        review_items,
        key=lambda item: (
            -float(item.get("queue_score", item.get("priority_score", 0)) or 0),
            -int(item.get("error_count", 0) or 0),
            float(item.get("mastery_level", 0) or 0),
        ),
    )
    sorted_knowledge_points = sorted(
        knowledge_points,
        key=lambda item: (
            -int(item.get("importance", 3) or 3),
            int(item.get("difficulty", 3) or 3),
        ),
    )
    latest_questions = [item.get("query", "") for item in history[-4:] if item.get("query")]

    today_focus = []
    for weak_point in weak_points[:3]:
        today_focus.append(
            _make_item(
                f"优先补强第 {weak_point.get('question_index')} 题涉及的知识点，重点修正：{weak_point.get('feedback', '回答不完整。')}",
                9.5 - float(weak_point.get("score", 0) or 0),
                "quiz_weak_point",
                "最近测验低分，应该优先补强。",
                {"question_index": weak_point.get("question_index")},
            )
        )
    for point in sorted_knowledge_points[:3]:
        title = point.get("title", "核心知识点")
        today_focus.append(
            _make_item(
                f"围绕“{title}”整理定义、原理、适用场景和一个例子。",
                6.5 + float(point.get("importance", 3) or 3) * 0.4,
                "knowledge_point",
                "当前资料中的高价值知识点。",
                {"knowledge_point_title": title},
            )
        )
    today_focus = _sort_items(today_focus) or [_make_item("先回看当前资料摘要，重新梳理核心概念。", 5.0, "fallback", "当前资料量较少，先做总览。")]

    priority_review = []
    for item in sorted_review_items[:4]:
        topic = item.get("topic", "")
        if topic:
            priority_review.append(
                _make_item(
                    f"优先复习：{topic}",
                    float(item.get("queue_score", item.get("priority_score", 0)) or 0),
                    "review_queue",
                    f"当前复习优先级为 {item.get('queue_score', item.get('priority_score', 0))}。",
                    {"review_item_id": item.get("id")},
                )
            )
    if not priority_review:
        priority_review = [_make_item("当前待复习项较少，可优先回顾最新导入资料的核心知识点。", 4.0, "fallback", "当前没有高优先级复习项。")]

    next_questions = []
    for weak_point in weak_points[:3]:
        next_questions.append(
            _make_item(
                f"如果现在重新回答第 {weak_point.get('question_index')} 题，你会怎样补上 {weak_point.get('suggestion', '关键定义和例子')}？",
                7.8 - float(weak_point.get("score", 0) or 0),
                "quiz_retry",
                "围绕低分题继续追问，最容易暴露理解缺口。",
                {"question_index": weak_point.get("question_index")},
            )
        )
    for point in sorted_knowledge_points[:2]:
        title = point.get("title", "")
        if title:
            next_questions.append(
                _make_item(
                    f"你能不用原文，直接解释“{title}”并说明它为什么重要吗？",
                    5.8 + float(point.get("importance", 3) or 3) * 0.3,
                    "knowledge_point",
                    "把知识点转成自己的表达，能帮助查漏补缺。",
                    {"knowledge_point_title": title},
                )
            )
    if latest_questions:
        next_questions.append(
            _make_item(
                f"基于刚才的提问“{latest_questions[-1]}”，还能继续追问哪一个原理细节？",
                5.0,
                "history",
                "沿着最近的问题继续深挖，能形成连续学习链路。",
            )
        )
    next_questions = _sort_items(next_questions) or [_make_item("下一步可以围绕当前最核心的知识点继续追问原因、场景和取舍。", 4.5, "fallback", "暂无明确追问目标。")]

    action_steps = _sort_items(
        [
            _make_item("先处理今天最高优先级的 1 到 2 个复习项，再开始新一轮提问。", 6.8, "scheduler", "先补薄弱点，效率更高。"),
            _make_item("完成一轮针对错题的重练或口头复述，确认自己能脱离原文回答。", 6.5, "quiz_retry", "把低分题真正转成会做的题。"),
            _make_item("在学习计划里勾掉已完成项后，再重排一次优先级。", 5.2, "plan_feedback", "保持计划跟着当前进度变化。"),
        ]
    )

    fallback_plan = {
        "title": f"{session.get('session_name', '学习会话')} 学习计划",
        "overview": "围绕当前资料先补薄弱点，再通过追问把核心概念讲清楚。",
        "today_focus": today_focus,
        "priority_review": priority_review,
        "next_questions": next_questions,
        "action_steps": action_steps,
    }

    if llm_generator is None:
        return fallback_plan

    prompt = f"""
请根据以下学习会话信息，生成一份简洁的中文学习计划。

要求：
1. 输出必须是合法 JSON。
2. 内容必须是中文。
3. 重点围绕“今天学什么 / 先复习什么 / 下一步问什么”。

JSON 结构：
{{
  "title": "计划标题",
  "overview": "一句话总览",
  "today_focus": ["今天学什么1", "今天学什么2"],
  "priority_review": ["先复习什么1", "先复习什么2"],
  "next_questions": ["下一步问什么1", "下一步问什么2"],
  "action_steps": ["行动步骤1", "行动步骤2"]
}}

学习会话名称：{session.get("session_name", "")}
学习主题：{session.get("topic", "")}
学习目标：{session.get("goal", "")}

知识点：
{", ".join([item.get("title", "") for item in sorted_knowledge_points[:5]])}

高优先级复习项：
{", ".join([item.get("topic", "") for item in sorted_review_items[:4]])}

最近薄弱点：
{", ".join([item.get("feedback", "") for item in weak_points[:3]])}

最近提问：
{", ".join(latest_questions)}
"""
    try:
        from services.quiz_service import _extract_json_object

        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个中文学习规划助手，擅长把当前学习进度整理成清晰、可执行的短计划。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        parsed = _extract_json_object(raw)
        if parsed and parsed.get("today_focus"):
            return {
                "title": parsed.get("title", fallback_plan["title"]),
                "overview": parsed.get("overview", fallback_plan["overview"]),
                "today_focus": _normalize_generated_items(parsed.get("today_focus", []), 7.0, "llm_today_focus") or fallback_plan["today_focus"],
                "priority_review": _normalize_generated_items(parsed.get("priority_review", []), 6.8, "llm_priority_review") or fallback_plan["priority_review"],
                "next_questions": _normalize_generated_items(parsed.get("next_questions", []), 6.2, "llm_next_questions") or fallback_plan["next_questions"],
                "action_steps": _normalize_generated_items(parsed.get("action_steps", []), 5.8, "llm_action_steps") or fallback_plan["action_steps"],
            }
    except Exception:
        pass

    return fallback_plan


def save_learning_plan(
    session_id: int,
    user_id: str,
    plan: dict,
    source_type: str = "generated",
    preserve_completion: bool = False,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    completion_map = {}
    if preserve_completion:
        cursor.execute(
            """
            SELECT spi.item_type, spi.item_text, spi.is_completed
            FROM study_plan_items spi
            JOIN study_plans sp ON sp.id = spi.plan_id
            WHERE sp.session_id = ? AND sp.user_id = ? AND sp.status = 'active'
            """,
            (session_id, user_id),
        )
        completion_map = {
            (row["item_type"], row["item_text"]): bool(row["is_completed"])
            for row in cursor.fetchall()
        }

    cursor.execute(
        """
        UPDATE study_plans
        SET status = 'archived', updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ? AND user_id = ? AND status = 'active'
        """,
        (session_id, user_id),
    )
    cursor.execute(
        """
        INSERT INTO study_plans (session_id, user_id, title, overview, source_type, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            session_id,
            user_id,
            plan.get("title", "学习计划"),
            plan.get("overview", ""),
            source_type,
        ),
    )
    plan_id = cursor.lastrowid

    for item_type in PLAN_ITEM_TYPES:
        items = _normalize_generated_items(plan.get(item_type, []) or [], 5.0, item_type)
        for sort_order, item in enumerate(_sort_items(items), start=1):
            text = item["text"]
            priority_score = float(item.get("priority_score", 0) or 0)
            is_completed = 1 if completion_map.get((item_type, text), False) else 0
            cursor.execute(
                """
                INSERT INTO study_plan_items (plan_id, item_type, item_text, sort_order, is_completed, priority_score, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    item_type,
                    text,
                    sort_order,
                    is_completed,
                    priority_score,
                    json.dumps(item.get("metadata", {}), ensure_ascii=False),
                ),
            )

    conn.commit()
    conn.close()
    return plan_id


def _serialize_plan(plan_row: sqlite3.Row | dict, item_rows: list[sqlite3.Row | dict], only_incomplete: bool = False) -> dict:
    plan_row = dict(plan_row)
    grouped = {key: [] for key in PLAN_ITEM_TYPES}
    total_items = 0
    completed_items = 0
    for raw_item in item_rows:
        item = dict(raw_item)
        is_completed = bool(item.get("is_completed", 0))
        if is_completed:
            completed_items += 1
        total_items += 1
        if only_incomplete and is_completed:
            continue
        grouped.setdefault(item["item_type"], []).append(
            {
                "id": item["id"],
                "text": item["item_text"],
                "is_completed": is_completed,
                "sort_order": item.get("sort_order", 0),
                "priority_score": _round_score(item.get("priority_score", 0) or 0),
                "metadata": json.loads(item.get("metadata_json") or "{}"),
            }
        )

    for key in PLAN_ITEM_TYPES:
        grouped[key].sort(
            key=lambda item: (
                -float(item.get("priority_score", 0) or 0),
                int(item.get("sort_order", 0) or 0),
                item.get("id", 0),
            )
        )

    completion_rate = round((completed_items / total_items) * 100, 1) if total_items else 0.0
    return {
        "plan_id": plan_row["id"],
        "session_id": plan_row["session_id"],
        "title": plan_row.get("title", "学习计划"),
        "overview": plan_row.get("overview", ""),
        "status": plan_row.get("status", "active"),
        "created_at": plan_row.get("created_at"),
        "progress": {
            "total_items": total_items,
            "completed_items": completed_items,
            "completion_rate": completion_rate,
        },
        "today_focus": grouped.get("today_focus", []),
        "priority_review": grouped.get("priority_review", []),
        "next_questions": grouped.get("next_questions", []),
        "action_steps": grouped.get("action_steps", []),
    }


def get_latest_learning_plan(session_id: int, only_incomplete: bool = False) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM study_plans
        WHERE session_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (session_id,),
    )
    plan_row = cursor.fetchone()
    if plan_row is None:
        conn.close()
        return None

    cursor.execute(
        """
        SELECT *
        FROM study_plan_items
        WHERE plan_id = ?
        ORDER BY item_type ASC, priority_score DESC, sort_order ASC, id ASC
        """,
        (plan_row["id"],),
    )
    item_rows = cursor.fetchall()
    conn.close()
    return _serialize_plan(plan_row, item_rows, only_incomplete=only_incomplete)


def update_plan_item_completion(item_id: int, is_completed: bool) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE study_plan_items
        SET is_completed = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (1 if is_completed else 0, item_id),
    )
    conn.commit()
    cursor.execute("SELECT * FROM study_plan_items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    conn.close()
    if item is None:
        return None
    row = dict(item)
    row["is_completed"] = bool(row.get("is_completed", 0))
    row["priority_score"] = _round_score(row.get("priority_score", 0) or 0)
    row["metadata"] = json.loads(row.get("metadata_json") or "{}")
    return row
