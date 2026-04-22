import json
import re
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text or "")]


def _normalize_questions(questions: list[dict], fallback_source: list[dict], question_count: int) -> list[dict]:
    normalized = []
    for index, item in enumerate(questions[:question_count], start=1):
        normalized.append(
            {
                "question_index": index,
                "question_type": item.get("question_type", "short_answer"),
                "question_text": item.get("question_text") or item.get("question") or f"请解释第 {index} 个核心知识点。",
                "reference_answer": item.get("reference_answer", ""),
                "scoring_rubric": item.get("scoring_rubric", "回答应包含定义、原理、影响与例子。"),
            }
        )

    while len(normalized) < question_count:
        source = fallback_source[len(normalized) % len(fallback_source)] if fallback_source else {}
        index = len(normalized) + 1
        normalized.append(
            {
                "question_index": index,
                "question_type": "short_answer",
                "question_text": source.get("question_text") or f"请总结本次学习中第 {index} 个最关键的知识点。",
                "reference_answer": source.get("reference_answer", ""),
                "scoring_rubric": source.get("scoring_rubric", "回答应包含核心概念、作用和适用场景。"),
            }
        )
    return normalized


def _fallback_generate_quiz(session_name: str, knowledge_points: list[dict], question_count: int, difficulty: str) -> dict:
    base_points = knowledge_points[:question_count]
    if not base_points:
        base_points = [
            {
                "title": "本次学习资料的核心概念",
                "description": "请结合你导入的资料，总结核心概念、关键机制和适用场景。",
            }
        ]

    questions = []
    for index in range(question_count):
        point = base_points[index % len(base_points)]
        questions.append(
            {
                "question_index": index + 1,
                "question_type": "short_answer",
                "question_text": f"请解释“{point.get('title', '核心知识点')}”，并说明它为什么重要。",
                "reference_answer": point.get("description", ""),
                "scoring_rubric": "回答应包含定义、原理、作用和一个具体例子。",
            }
        )

    return {
        "title": f"{session_name} 自测题",
        "difficulty": difficulty,
        "instructions": "请尽量用自己的语言回答，回答后可根据反馈继续补充理解。",
        "questions": questions,
    }


def generate_quiz_bundle(
    session: dict,
    knowledge_points: list[dict],
    summaries: list[dict],
    llm_generator=None,
    question_count: int = 3,
    difficulty: str = "medium",
) -> dict:
    question_count = max(1, min(int(question_count or 3), 8))
    difficulty = difficulty or "medium"
    fallback = _fallback_generate_quiz(session.get("session_name", "学习会话"), knowledge_points, question_count, difficulty)

    if llm_generator is None:
        return fallback

    short_summaries = [item["summary_text"] for item in summaries if item.get("summary_type") == "short_summary"][:3]
    kp_lines = [f"- {item.get('title', '')}: {item.get('description', '')}" for item in knowledge_points[:6]]
    prompt = f"""
请根据以下学习会话内容，生成一组中文自测题。

要求：
1. 输出必须是合法 JSON。
2. 题目数量为 {question_count} 道。
3. 题型以简答题为主，适合学习后自测。
4. 所有内容必须是中文。
5. 每道题都提供参考答案和评分要点。

JSON 结构：
{{
  "title": "测验标题",
  "difficulty": "{difficulty}",
  "instructions": "作答说明",
  "questions": [
    {{
      "question_type": "short_answer",
      "question_text": "题目内容",
      "reference_answer": "参考答案",
      "scoring_rubric": "评分要点"
    }}
  ]
}}

学习会话名称：
{session.get("session_name", "")}

摘要：
{" ".join(short_summaries)}

知识点：
{chr(10).join(kp_lines)}
"""

    try:
        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个中文学习测验助手，擅长根据学习资料生成高质量自测题。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1400,
        )
        parsed = _extract_json_object(raw)
        if not parsed:
            return fallback
        return {
            "title": parsed.get("title", fallback["title"]),
            "difficulty": parsed.get("difficulty", difficulty),
            "instructions": parsed.get("instructions", fallback["instructions"]),
            "questions": _normalize_questions(parsed.get("questions", []), fallback["questions"], question_count),
        }
    except Exception:
        return fallback


def create_quiz_set(session_id: int, title: str, question_count: int, difficulty: str, metadata: dict | None = None) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO quiz_sets (session_id, title, question_count, difficulty, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, title, question_count, difficulty, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    quiz_set_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return quiz_set_id


def save_quiz_questions(quiz_set_id: int, questions: list[dict]):
    conn = _connect()
    cursor = conn.cursor()
    created_ids = []
    for item in questions:
        cursor.execute(
            """
            INSERT INTO quiz_questions
            (quiz_set_id, question_index, question_type, question_text, reference_answer, scoring_rubric, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quiz_set_id,
                item.get("question_index"),
                item.get("question_type", "short_answer"),
                item.get("question_text", ""),
                item.get("reference_answer", ""),
                item.get("scoring_rubric", ""),
                json.dumps(item.get("metadata", {}), ensure_ascii=False),
            ),
        )
        created_ids.append(cursor.lastrowid)
    conn.commit()
    conn.close()
    return created_ids


def get_quiz_set_with_questions(quiz_set_id: int) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM quiz_sets WHERE id = ?", (quiz_set_id,))
    quiz_set = cursor.fetchone()
    if quiz_set is None:
        conn.close()
        return None

    cursor.execute(
        """
        SELECT * FROM quiz_questions
        WHERE quiz_set_id = ?
        ORDER BY question_index ASC, id ASC
        """,
        (quiz_set_id,),
    )
    questions = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {
        "quiz_set": dict(quiz_set),
        "questions": questions,
    }


def get_latest_quiz_for_session(session_id: int) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id FROM quiz_sets
        WHERE session_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (session_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return get_quiz_set_with_questions(row[0])


def save_quiz_attempt(
    quiz_set_id: int,
    session_id: int,
    user_id: str,
    answers: list[str],
    result: dict,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO quiz_attempts
        (quiz_set_id, session_id, user_id, answers_json, result_json, total_score, feedback_text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quiz_set_id,
            session_id,
            user_id,
            json.dumps(answers, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            float(result.get("total_score", 0)),
            result.get("overall_feedback", ""),
        ),
    )
    attempt_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return attempt_id


def get_latest_quiz_attempt_for_session(session_id: int, user_id: str) -> dict | None:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM quiz_attempts
        WHERE session_id = ? AND user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (session_id, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    result = dict(row)
    result["answers"] = json.loads(result.get("answers_json") or "[]")
    result["result"] = json.loads(result.get("result_json") or "{}")
    return result


def _fallback_grade_quiz(questions: list[dict], answers: list[str]) -> dict:
    item_feedback = []
    total_score = 0.0
    for question, answer in zip(questions, answers):
        reference = question.get("reference_answer", "")
        overlap = len(set(_tokenize(reference)) & set(_tokenize(answer)))
        answer_length = len((answer or "").strip())
        base_score = 1.5 if answer_length > 20 else 0.5 if answer_length > 0 else 0.0
        overlap_score = min(2.5, overlap * 0.5)
        score = round(min(5.0, base_score + overlap_score), 1)
        total_score += score
        item_feedback.append(
            {
                "question_index": question.get("question_index"),
                "score": score,
                "max_score": 5,
                "feedback": "回答覆盖了部分关键点。" if score >= 2.5 else "回答偏简略，建议补充定义、原理和例子。",
                "suggestion": question.get("scoring_rubric", "补充核心概念、适用场景和例子。"),
            }
        )

    average_score = round(total_score / max(1, len(questions)), 2)
    return {
        "total_score": round(total_score, 1),
        "average_score": average_score,
        "max_total_score": len(questions) * 5,
        "overall_feedback": "整体掌握不错，可以继续通过追问和复习巩固细节。" if average_score >= 3 else "还有不少细节没有答完整，建议先回看摘要和知识点。",
        "item_feedback": item_feedback,
    }


def grade_quiz_attempt(questions: list[dict], answers: list[str], llm_generator=None) -> dict:
    normalized_answers = [(item or "").strip() for item in answers]
    fallback = _fallback_grade_quiz(questions, normalized_answers)
    if llm_generator is None:
        return fallback

    question_blocks = []
    for question, answer in zip(questions, normalized_answers):
        question_blocks.append(
            {
                "question_index": question.get("question_index"),
                "question_text": question.get("question_text", ""),
                "reference_answer": question.get("reference_answer", ""),
                "scoring_rubric": question.get("scoring_rubric", ""),
                "learner_answer": answer,
            }
        )

    prompt = f"""
请作为中文学习测验评分助手，对下面的自测题回答进行评分。

要求：
1. 输出必须是合法 JSON。
2. 每题满分 5 分。
3. 反馈必须简洁、具体、中文。

JSON 结构：
{{
  "overall_feedback": "总体反馈",
  "item_feedback": [
    {{
      "question_index": 1,
      "score": 4,
      "max_score": 5,
      "feedback": "评分反馈",
      "suggestion": "补充建议"
    }}
  ]
}}

题目与回答：
{json.dumps(question_blocks, ensure_ascii=False)}
"""

    try:
        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个中文学习测验评分助手，擅长根据参考答案和评分标准给出简洁可靠的评分结果。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1400,
        )
        parsed = _extract_json_object(raw)
        if not parsed:
            return fallback

        item_feedback = []
        total_score = 0.0
        for index, item in enumerate(parsed.get("item_feedback", [])[: len(questions)], start=1):
            score = float(item.get("score", 0))
            total_score += score
            item_feedback.append(
                {
                    "question_index": item.get("question_index", index),
                    "score": score,
                    "max_score": float(item.get("max_score", 5)),
                    "feedback": item.get("feedback", ""),
                    "suggestion": item.get("suggestion", ""),
                }
            )

        if not item_feedback:
            return fallback

        return {
            "total_score": round(total_score, 1),
            "average_score": round(total_score / max(1, len(item_feedback)), 2),
            "max_total_score": len(questions) * 5,
            "overall_feedback": parsed.get("overall_feedback", fallback["overall_feedback"]),
            "item_feedback": item_feedback,
        }
    except Exception:
        return fallback
