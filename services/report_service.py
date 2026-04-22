def generate_session_report(
    session: dict,
    documents: list[dict],
    summaries: list[dict],
    knowledge_points: list[dict],
    review_items: list[dict],
    history: list[dict],
    latest_quiz_attempt: dict | None = None,
    llm_generator=None,
) -> dict:
    short_summaries = [item["summary_text"] for item in summaries if item.get("summary_type") == "short_summary"]
    interview_takeaways = [item["summary_text"] for item in summaries if item.get("summary_type") == "interview_takeaways"]
    knowledge_titles = [item.get("title", "") for item in knowledge_points[:5]]

    if llm_generator is not None:
        prompt = f"""
请根据以下学习会话信息，输出一份简洁的中文学习报告。

要求：
1. 输出必须是合法 JSON。
2. 内容必须是中文。
3. 语气简洁、可执行。

JSON 结构：
{{
  "title": "报告标题",
  "overview": "整体总结",
  "progress_snapshot": ["进展1", "进展2"],
  "strengths": ["优势1", "优势2"],
  "risks": ["风险1", "风险2"],
  "next_actions": ["下一步1", "下一步2"],
  "interview_focus": ["面试重点1", "面试重点2"]
}}

学习会话：
名称：{session.get("session_name", "")}
主题：{session.get("topic", "")}
目标：{session.get("goal", "")}

资料数：{len(documents)}
知识点数：{len(knowledge_points)}
复习项数：{len(review_items)}
问答轮数：{len(history)}
最近测验：{latest_quiz_attempt.get('feedback_text', '暂无') if latest_quiz_attempt else '暂无'}

摘要：
{" ".join(short_summaries[:3])}

知识点：
{", ".join(knowledge_titles)}

面试要点：
{" ".join(interview_takeaways[:2])}
"""
        try:
            from services.quiz_service import _extract_json_object  # local import to avoid cycles

            raw = llm_generator.chat_once(
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个中文学习复盘助手，擅长把学习过程整理成结构化复盘报告。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1200,
            )
            parsed = _extract_json_object(raw)
            if parsed and parsed.get("overview"):
                return {
                    "title": parsed.get("title", f"{session.get('session_name', '学习会话')} 学习报告"),
                    "overview": parsed.get("overview", ""),
                    "progress_snapshot": parsed.get("progress_snapshot", []),
                    "strengths": parsed.get("strengths", []),
                    "risks": parsed.get("risks", []),
                    "next_actions": parsed.get("next_actions", []),
                    "interview_focus": parsed.get("interview_focus", []),
                }
        except Exception:
            pass

    progress_snapshot = [
        f"已沉淀 {len(documents)} 份资料。",
        f"当前累计 {len(knowledge_points)} 个知识点、{len(review_items)} 个复习项。",
        f"本会话已进行 {len(history)} 轮问答。",
    ]
    if latest_quiz_attempt:
        score = latest_quiz_attempt.get("total_score")
        max_score = latest_quiz_attempt.get("result", {}).get("max_total_score") or latest_quiz_attempt.get("result", {}).get("max_total_score", 0)
        progress_snapshot.append(f"最近一次测验总分为 {score}/{max_score or '未知'}。")

    strengths = knowledge_titles[:3] or ["已经形成基础学习沉淀。"]
    risks = []
    if not history:
        risks.append("问答轮数还较少，建议通过追问把理解再压实。")
    if latest_quiz_attempt is None:
        risks.append("还没有完成自测，建议先做一轮测验检查理解深度。")
    if len(review_items) > 8:
        risks.append("待复习项较多，建议优先处理高频混淆点。")
    if not risks:
        risks.append("当前风险较低，重点继续巩固细节和表达。")

    next_actions = [
        "优先回顾当前会话中的高频知识点，并补上容易混淆的定义。",
        "围绕当前资料再进行 2 到 3 轮追问，检查是否真正理解原理。",
        "完成一轮自测，把薄弱点沉淀成下一轮复习重点。",
    ]

    interview_focus = []
    for text in interview_takeaways[:2]:
        interview_focus.extend([line.strip("- ").strip() for line in text.splitlines() if line.strip()])
    if not interview_focus:
        interview_focus = [
            "尝试用自己的话解释本次学习的核心概念。",
            "准备一个能说明原理、场景和取舍的回答版本。",
        ]

    overview = short_summaries[0] if short_summaries else f"本次学习围绕“{session.get('session_name', '学习会话')}”展开，已经形成初步的知识沉淀。"
    return {
        "title": f"{session.get('session_name', '学习会话')} 学习报告",
        "overview": overview,
        "progress_snapshot": progress_snapshot,
        "strengths": strengths,
        "risks": risks,
        "next_actions": next_actions,
        "interview_focus": interview_focus[:4],
    }
