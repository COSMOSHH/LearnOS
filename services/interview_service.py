import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from services.db import connect_study_db
from services.evaluation_service import evaluate_answer


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    return connect_study_db(DB_PATH)


def _load_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _dump_json(payload: dict) -> str:
    return json.dumps(payload or {}, ensure_ascii=False)


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


def generate_interview_blueprint(
    session: dict,
    knowledge_points: list[dict],
    summaries: list[dict],
    total_rounds: int = 3,
    difficulty: str = "medium",
    llm_generator=None,
) -> dict:
    total_rounds = max(1, min(int(total_rounds or 3), 6))
    base_points = knowledge_points[: max(1, total_rounds)]
    if not base_points:
        base_points = [{"title": session.get("topic") or session.get("session_name", "核心知识点"), "description": session.get("goal", "")}]

    questions = []
    for index in range(total_rounds):
        point = base_points[index % len(base_points)]
        questions.append(
            {
                "round_index": index + 1,
                "question_text": f"请你像面试中一样，解释一下“{point.get('title', '核心知识点')}”，并说明它的作用、适用场景和取舍。",
                "ideal_answer": point.get("description", "") or f"应涵盖 {point.get('title', '该知识点')} 的定义、原理、场景和边界。",
                "focus": point.get("title", "核心知识点"),
            }
        )

    fallback = {
        "title": f"{session.get('session_name', '学习会话')} 模拟面试",
        "intro_text": "请用面试表达方式回答：先给结论，再解释原理，最后补充场景和取舍。",
        "questions": questions,
    }
    if llm_generator is None:
        return fallback

    short_summaries = [item["summary_text"] for item in summaries if item.get("summary_type") == "short_summary"][:2]
    prompt = f"""
请基于以下学习会话，生成一轮中文模拟面试题。

要求：
1. 输出必须是合法 JSON。
2. 共 {total_rounds} 轮。
3. 每轮都给出 question_text、ideal_answer、focus。
4. 问题风格要更像技术面试官。

JSON 结构：
{{
  "title": "模拟面试标题",
  "intro_text": "面试说明",
  "questions": [
    {{
      "question_text": "问题",
      "ideal_answer": "理想回答要点",
      "focus": "考察点"
    }}
  ]
}}

学习会话：{session.get("session_name", "")}
主题：{session.get("topic", "")}
目标：{session.get("goal", "")}
摘要：{" ".join(short_summaries)}
知识点：{", ".join([item.get("title", "") for item in base_points])}
"""
    try:
        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个中文技术面试官，擅长根据学习资料设计循序渐进的模拟面试问题。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1400,
        )
        parsed = _extract_json_object(raw)
        if parsed and parsed.get("questions"):
            questions = []
            for index, item in enumerate(parsed.get("questions", [])[:total_rounds], start=1):
                questions.append(
                    {
                        "round_index": index,
                        "question_text": item.get("question_text", fallback["questions"][index - 1]["question_text"]),
                        "ideal_answer": item.get("ideal_answer", fallback["questions"][index - 1]["ideal_answer"]),
                        "focus": item.get("focus", fallback["questions"][index - 1]["focus"]),
                    }
                )
            return {
                "title": parsed.get("title", fallback["title"]),
                "intro_text": parsed.get("intro_text", fallback["intro_text"]),
                "questions": questions,
            }
    except Exception:
        pass

    return fallback


def create_interview_session(
    session_id: int,
    user_id: str,
    title: str,
    difficulty: str,
    total_rounds: int,
    intro_text: str,
    questions: list[dict],
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE interview_sessions SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ? AND status = 'active'", (session_id, user_id))
    cursor.execute(
        """
        INSERT INTO interview_sessions
        (session_id, user_id, title, difficulty, total_rounds, current_round, status, intro_text, metadata_json)
        VALUES (?, ?, ?, ?, ?, 1, 'active', ?, ?)
        """,
        (session_id, user_id, title, difficulty, total_rounds, intro_text, _dump_json({"questions": questions})),
    )
    interview_session_id = cursor.lastrowid
    first_question = questions[0]
    cursor.execute(
        """
        INSERT INTO interview_turns
        (interview_session_id, round_index, question_text, ideal_answer, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            interview_session_id,
            1,
            first_question.get("question_text", ""),
            first_question.get("ideal_answer", ""),
            _dump_json({"focus": first_question.get("focus", "")}),
        ),
    )
    conn.commit()
    conn.close()
    return interview_session_id


def _serialize_session(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    item["metadata"] = _load_json(item.get("metadata_json"))
    return item


def _serialize_turn(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    item["metadata"] = _load_json(item.get("metadata_json"))
    return item


def get_interview_session(interview_session_id: int) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM interview_sessions WHERE id = ?", (interview_session_id,))
    row = cursor.fetchone()
    conn.close()
    return _serialize_session(row) if row else None


def get_latest_interview_session(session_id: int, user_id: str) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM interview_sessions
        WHERE session_id = ? AND user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (session_id, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    return _serialize_session(row) if row else None


def get_interview_turns(interview_session_id: int) -> list[dict]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM interview_turns
        WHERE interview_session_id = ?
        ORDER BY round_index ASC, id ASC
        """,
        (interview_session_id,),
    )
    rows = [_serialize_turn(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def _build_follow_up_question(question_text: str, evaluation: dict, focus: str) -> str:
    if evaluation.get("overall_score", 0) >= 4.0:
        return f"如果在线上高并发场景里继续追问，你会如何补充“{focus or question_text[:18]}”的边界条件？"
    return f"如果面试官继续追问，你会如何更清晰地解释“{focus or question_text[:18]}”的定义、原理和例子？"


def submit_interview_answer(interview_session_id: int, user_id: str, answer_text: str, llm_generator=None) -> dict | None:
    session = get_interview_session(interview_session_id)
    if session is None or session.get("user_id") != user_id:
        return None

    questions = session.get("metadata", {}).get("questions", [])
    current_round = int(session.get("current_round", 1) or 1)
    question = questions[current_round - 1] if current_round - 1 < len(questions) else None
    if question is None:
        return None

    evaluation = evaluate_answer(
        query_text=question.get("question_text", ""),
        answer_text=answer_text,
        sources=[],
        llm_generator=llm_generator,
        source_type="interview",
    )
    follow_up_question = _build_follow_up_question(question.get("question_text", ""), evaluation, question.get("focus", ""))
    score = float(evaluation.get("overall_score", 0) or 0)
    answered_at = datetime.now(timezone.utc).isoformat()

    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE interview_turns
        SET answer_text = ?, follow_up_question = ?, feedback_text = ?, score = ?, metadata_json = ?, answered_at = ?
        WHERE interview_session_id = ? AND round_index = ?
        """,
        (
            answer_text,
            follow_up_question,
            evaluation.get("feedback_text", ""),
            score,
            _dump_json(
                {
                    "focus": question.get("focus", ""),
                    "strengths": evaluation.get("strengths", []),
                    "risks": evaluation.get("risks", []),
                    "next_actions": evaluation.get("next_actions", []),
                    "ideal_answer": question.get("ideal_answer", ""),
                }
            ),
            answered_at,
            interview_session_id,
            current_round,
        ),
    )

    next_round = current_round + 1
    status = "completed" if next_round > int(session.get("total_rounds", len(questions)) or len(questions)) else "active"
    summary_text = ""
    next_question = None
    if status == "active" and next_round - 1 < len(questions):
        next_question = questions[next_round - 1]
        cursor.execute(
            """
            INSERT INTO interview_turns
            (interview_session_id, round_index, question_text, ideal_answer, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                interview_session_id,
                next_round,
                next_question.get("question_text", ""),
                next_question.get("ideal_answer", ""),
                _dump_json({"focus": next_question.get("focus", "")}),
            ),
        )
    else:
        summary_text = "本轮模拟面试已完成，可以回看每一轮反馈，继续加强定义、原理、场景和取舍表达。"

    cursor.execute(
        """
        UPDATE interview_sessions
        SET current_round = ?, status = ?, summary_text = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (min(next_round, int(session.get("total_rounds", len(questions)) or len(questions))), status, summary_text, interview_session_id),
    )
    conn.commit()
    conn.close()
    return {
        "evaluation": evaluation,
        "score": score,
        "question_text": question.get("question_text", ""),
        "follow_up_question": follow_up_question,
        "status": status,
        "next_question": next_question,
    }


def summarize_interview_session(interview_session_id: int) -> dict:
    session = get_interview_session(interview_session_id)
    turns = get_interview_turns(interview_session_id)
    completed_turns = [turn for turn in turns if turn.get("answer_text")]
    if not completed_turns:
        return {
            "title": session.get("title", "模拟面试总结") if session else "模拟面试总结",
            "overview": "当前还没有完成面试作答。",
            "average_score": 0.0,
            "strengths": [],
            "risks": [],
            "next_actions": [],
        }

    average_score = round(sum(float(turn.get("score", 0) or 0) for turn in completed_turns) / len(completed_turns), 2)
    strengths = []
    risks = []
    for turn in completed_turns[:3]:
        metadata = turn.get("metadata", {})
        strengths.extend(metadata.get("strengths", []))
        risks.extend(metadata.get("risks", []))
    if not strengths:
        strengths.append("已经完成了一轮模拟面试，具备基础表达素材。")
    if not risks:
        risks.append("仍建议继续强化定义、原理、场景和取舍的完整表达。")

    next_actions = [
        "重新梳理低分题的回答，补上定义、原理、场景和边界条件。",
        "练习先给结论、再分点展开的技术面试表达方式。",
        "挑一题再做一次口头复述，确认自己能脱离原文作答。",
    ]
    return {
        "title": session.get("title", "模拟面试总结") if session else "模拟面试总结",
        "overview": session.get("summary_text", "") if session else "",
        "average_score": average_score,
        "completed_rounds": len(completed_turns),
        "total_rounds": session.get("total_rounds", len(turns)) if session else len(turns),
        "strengths": strengths[:4],
        "risks": risks[:4],
        "next_actions": next_actions,
    }
