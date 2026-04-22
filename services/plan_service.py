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
        if score < 3:
            weak_points.append(item)

    sorted_review_items = sorted(
        review_items,
        key=lambda item: (
            -float(item.get("priority_score", 0) or 0),
            -int(item.get("error_count", 0) or 0),
        ),
    )
    top_review_topics = [item.get("topic", "") for item in sorted_review_items[:3] if item.get("topic")]
    top_knowledge_points = [item.get("title", "") for item in knowledge_points[:5] if item.get("title")]
    latest_questions = [item.get("query", "") for item in history[-3:] if item.get("query")]

    fallback_plan = {
        "title": f"{session.get('session_name', '学习会话')} 学习计划",
        "overview": "围绕当前资料先补薄弱点，再通过追问把核心概念讲清楚。",
        "today_focus": [],
        "priority_review": [],
        "next_questions": [],
        "action_steps": [],
    }

    for weak_point in weak_points[:3]:
        fallback_plan["today_focus"].append(
            f"优先补强第 {weak_point.get('question_index')} 题涉及的知识点，重点修正：{weak_point.get('feedback', '回答不完整。')}"
        )
    for title in top_knowledge_points[:3]:
        fallback_plan["today_focus"].append(f"围绕“{title}”整理定义、原理、适用场景和一个例子。")
    fallback_plan["today_focus"] = fallback_plan["today_focus"][:4] or ["先回看当前资料摘要，重新梳理核心概念。"]

    for topic in top_review_topics[:4]:
        fallback_plan["priority_review"].append(f"优先复习：{topic}")
    if not fallback_plan["priority_review"]:
        fallback_plan["priority_review"] = ["当前待复习项较少，可优先回顾最新导入资料的核心知识点。"]

    for weak_point in weak_points[:3]:
        fallback_plan["next_questions"].append(
            f"如果现在重新回答第 {weak_point.get('question_index')} 题，你会怎样补上 {weak_point.get('suggestion', '关键定义和例子')}？"
        )
    for title in top_knowledge_points[:2]:
        fallback_plan["next_questions"].append(f"你能不用原文，直接解释“{title}”并说明它为什么重要吗？")
    if latest_questions:
        fallback_plan["next_questions"].append(f"基于刚才的提问“{latest_questions[-1]}”，还能继续追问哪一个原理细节？")
    fallback_plan["next_questions"] = fallback_plan["next_questions"][:4] or ["下一步可以围绕当前最核心的知识点继续追问原因、场景和取舍。"]

    fallback_plan["action_steps"] = [
        "先复习高优先级复习项，重新组织自己的表达。",
        "完成一次针对薄弱题的口头复述或重新作答。",
        "继续追问 2 到 3 个为什么，确认自己是否真的理解原理。",
    ]

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
{", ".join(top_knowledge_points)}

高优先级复习项：
{", ".join(top_review_topics)}

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
                "today_focus": parsed.get("today_focus", fallback_plan["today_focus"]),
                "priority_review": parsed.get("priority_review", fallback_plan["priority_review"]),
                "next_questions": parsed.get("next_questions", fallback_plan["next_questions"]),
                "action_steps": parsed.get("action_steps", fallback_plan["action_steps"]),
            }
    except Exception:
        pass

    return fallback_plan
