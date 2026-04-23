import json
import re
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"
OBJECTIVE_TYPES = {"single_choice", "multiple_choice", "fill_blank"}


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


def _parse_question_metadata(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _normalize_answer_payload(answer):
    if isinstance(answer, list):
        return [str(item).strip() for item in answer if str(item).strip()]
    if answer is None:
        return ""
    return str(answer).strip()


def _stringify_answer(answer) -> str:
    normalized = _normalize_answer_payload(answer)
    if isinstance(normalized, list):
        return ", ".join(normalized)
    return normalized


def _normalize_questions(questions: list[dict], fallback_source: list[dict], question_count: int) -> list[dict]:
    normalized = []
    for index, item in enumerate(questions[:question_count], start=1):
        question_type = str(item.get("question_type", "short_answer") or "short_answer").strip().lower()
        if question_type not in {"short_answer", "single_choice", "multiple_choice", "fill_blank"}:
            question_type = "short_answer"
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if not metadata:
            metadata = {
                "options": item.get("options", []),
                "correct_answer": item.get("correct_answer"),
                "correct_answers": item.get("correct_answers", []),
                "blank_answers": item.get("blank_answers", []),
            }
        normalized.append(
            {
                "question_index": index,
                "question_type": question_type,
                "question_text": item.get("question_text") or item.get("question") or f"请解释第 {index} 个核心知识点。",
                "reference_answer": item.get("reference_answer", ""),
                "scoring_rubric": item.get("scoring_rubric", "回答应包含定义、原理、影响与例子。"),
                "metadata": metadata,
            }
        )

    while len(normalized) < question_count:
        source = fallback_source[len(normalized) % len(fallback_source)] if fallback_source else {}
        source = dict(source)
        source["question_index"] = len(normalized) + 1
        normalized.append(source)
    return normalized


def _build_choice_question(point: dict, knowledge_points: list[dict], index: int) -> dict:
    titles = []
    for item in knowledge_points:
        title = item.get("title", "").strip()
        if title and title not in titles:
            titles.append(title)
    correct_title = point.get("title", "核心知识点")
    if correct_title not in titles:
        titles.insert(0, correct_title)
    options = titles[:4]
    while len(options) < 4:
        options.append(f"干扰项{len(options) + 1}")
    return {
        "question_index": index,
        "question_type": "single_choice",
        "question_text": f"单选题：下面哪个概念最符合这段描述？{point.get('description', '')}",
        "reference_answer": correct_title,
        "scoring_rubric": "选出与描述最匹配的概念即可。",
        "metadata": {
            "options": options,
            "correct_answer": correct_title,
        },
    }


def _build_fill_blank_question(point: dict, index: int) -> dict:
    title = point.get("title", "核心知识点")
    description = point.get("description", "")
    hint = description[:36] if description else f"{title} 的核心作用"
    return {
        "question_index": index,
        "question_type": "fill_blank",
        "question_text": f"填空题：请写出最匹配这段提示的知识点名称。提示：{hint}",
        "reference_answer": title,
        "scoring_rubric": "填出准确知识点名称即可。",
        "metadata": {
            "blank_answers": [title],
        },
    }


def _build_short_answer_question(point: dict, index: int) -> dict:
    return {
        "question_index": index,
        "question_type": "short_answer",
        "question_text": f"请解释“{point.get('title', '核心知识点')}”，并说明它为什么重要。",
        "reference_answer": point.get("description", ""),
        "scoring_rubric": "回答应包含定义、原理、作用和一个具体例子。",
        "metadata": {},
    }


def _fallback_generate_quiz(session_name: str, knowledge_points: list[dict], question_count: int, difficulty: str) -> dict:
    base_points = knowledge_points[: max(1, question_count)]
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
        question_index = index + 1
        if question_index == 1:
            questions.append(_build_choice_question(point, knowledge_points, question_index))
        elif question_index == 2:
            questions.append(_build_fill_blank_question(point, question_index))
        else:
            questions.append(_build_short_answer_question(point, question_index))

    return {
        "title": f"{session_name} 自测题",
        "difficulty": difficulty,
        "instructions": "先完成选择题和填空题，再用自己的语言完成简答题。",
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
3. 至少包含一道单选题和一道填空题，其余可以是简答题。
4. 所有内容必须是中文。
5. 每道题都提供参考答案和评分要点。

JSON 结构：
{{
  "title": "测验标题",
  "difficulty": "{difficulty}",
  "instructions": "作答说明",
  "questions": [
    {{
      "question_type": "single_choice",
      "question_text": "题目内容",
      "options": ["选项A", "选项B", "选项C", "选项D"],
      "correct_answer": "正确选项文本",
      "reference_answer": "参考答案",
      "scoring_rubric": "评分要点"
    }},
    {{
      "question_type": "fill_blank",
      "question_text": "填空题内容",
      "blank_answers": ["答案1"],
      "reference_answer": "参考答案",
      "scoring_rubric": "评分要点"
    }},
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
            max_tokens=1600,
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
        SELECT *
        FROM quiz_questions
        WHERE quiz_set_id = ?
        ORDER BY question_index ASC, id ASC
        """,
        (quiz_set_id,),
    )
    questions = []
    for row in cursor.fetchall():
        item = dict(row)
        item["metadata"] = _parse_question_metadata(item.get("metadata_json"))
        questions.append(item)
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
    answers: list,
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
        SELECT *
        FROM quiz_attempts
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


def _grade_single_choice(question: dict, answer) -> dict:
    metadata = question.get("metadata") or {}
    expected = str(metadata.get("correct_answer", "")).strip()
    received = _stringify_answer(answer).strip()
    is_correct = expected and received == expected
    return {
        "question_index": question.get("question_index"),
        "score": 5.0 if is_correct else 1.0 if received else 0.0,
        "max_score": 5.0,
        "feedback": "回答正确。" if is_correct else "选择不正确。" if received else "未作答。",
        "suggestion": f"正确答案是：{expected}" if expected and not is_correct else "",
    }


def _grade_multiple_choice(question: dict, answer) -> dict:
    metadata = question.get("metadata") or {}
    expected = {str(item).strip() for item in metadata.get("correct_answers", []) if str(item).strip()}
    received = set(_normalize_answer_payload(answer)) if isinstance(_normalize_answer_payload(answer), list) else {str(_normalize_answer_payload(answer)).strip()} if _normalize_answer_payload(answer) else set()
    if not expected:
        return _grade_single_choice(question, answer)
    overlap = len(expected & received)
    penalty = len(received - expected)
    raw_score = max(0.0, overlap - penalty * 0.5)
    score = round(min(5.0, 5.0 * raw_score / max(1, len(expected))), 1)
    is_correct = received == expected
    return {
        "question_index": question.get("question_index"),
        "score": 5.0 if is_correct else score,
        "max_score": 5.0,
        "feedback": "回答正确。" if is_correct else "部分正确，仍有遗漏或误选。" if received else "未作答。",
        "suggestion": f"正确答案是：{', '.join(expected)}" if expected and not is_correct else "",
    }


def _grade_fill_blank(question: dict, answer) -> dict:
    metadata = question.get("metadata") or {}
    expected_answers = [str(item).strip().lower() for item in metadata.get("blank_answers", []) if str(item).strip()]
    received = _stringify_answer(answer).strip().lower()
    if not received:
        return {
            "question_index": question.get("question_index"),
            "score": 0.0,
            "max_score": 5.0,
            "feedback": "未作答。",
            "suggestion": f"参考答案：{question.get('reference_answer', '')}",
        }

    matched = any(expected in received or received in expected for expected in expected_answers) if expected_answers else False
    score = 5.0 if matched else 2.5 if expected_answers and set(_tokenize(received)) & set(_tokenize(" ".join(expected_answers))) else 1.0
    return {
        "question_index": question.get("question_index"),
        "score": score,
        "max_score": 5.0,
        "feedback": "填空正确。" if score >= 5 else "答案部分接近，但不够准确。" if score >= 2.5 else "填空不正确。",
        "suggestion": f"参考答案：{question.get('reference_answer', '')}" if score < 5 else "",
    }


def _fallback_grade_short_answer(question: dict, answer) -> dict:
    reference = question.get("reference_answer", "")
    answer_text = _stringify_answer(answer)
    overlap = len(set(_tokenize(reference)) & set(_tokenize(answer_text)))
    answer_length = len(answer_text.strip())
    base_score = 1.5 if answer_length > 20 else 0.5 if answer_length > 0 else 0.0
    overlap_score = min(2.5, overlap * 0.5)
    score = round(min(5.0, base_score + overlap_score), 1)
    return {
        "question_index": question.get("question_index"),
        "score": score,
        "max_score": 5.0,
        "feedback": "回答覆盖了部分关键点。" if score >= 2.5 else "回答偏简略，建议补充定义、原理和例子。",
        "suggestion": question.get("scoring_rubric", "补充核心概念、适用场景和例子。"),
    }


def grade_single_question(question: dict, answer, llm_generator=None) -> dict:
    question_type = str(question.get("question_type", "short_answer") or "short_answer").lower()
    if question_type == "single_choice":
        return _grade_single_choice(question, answer)
    if question_type == "multiple_choice":
        return _grade_multiple_choice(question, answer)
    if question_type == "fill_blank":
        return _grade_fill_blank(question, answer)
    return _fallback_grade_short_answer(question, answer)


def _grade_short_answers_with_llm(short_blocks: list[dict], llm_generator) -> dict[int, dict] | None:
    prompt = f"""
请作为中文学习测验评分助手，对下面的简答题回答进行评分。

要求：
1. 输出必须是合法 JSON。
2. 每题满分 5 分。
3. 反馈必须简洁、具体、中文。

JSON 结构：
{{
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
{json.dumps(short_blocks, ensure_ascii=False)}
"""
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
        return None
    feedback_map = {}
    for item in parsed.get("item_feedback", []):
        question_index = int(item.get("question_index", 0) or 0)
        if question_index <= 0:
            continue
        feedback_map[question_index] = {
            "question_index": question_index,
            "score": float(item.get("score", 0) or 0),
            "max_score": float(item.get("max_score", 5) or 5),
            "feedback": item.get("feedback", ""),
            "suggestion": item.get("suggestion", ""),
        }
    return feedback_map or None


def grade_quiz_attempt(questions: list[dict], answers: list, llm_generator=None) -> dict:
    normalized_answers = [_normalize_answer_payload(item) for item in answers]
    item_feedback = []
    short_blocks = []
    for question, answer in zip(questions, normalized_answers):
        metadata = question.get("metadata")
        if metadata is None:
            question["metadata"] = _parse_question_metadata(question.get("metadata_json"))
        question_type = str(question.get("question_type", "short_answer") or "short_answer").lower()
        if question_type in OBJECTIVE_TYPES:
            item_feedback.append(grade_single_question(question, answer, llm_generator=None))
        else:
            short_blocks.append(
                {
                    "question_index": question.get("question_index"),
                    "question_text": question.get("question_text", ""),
                    "reference_answer": question.get("reference_answer", ""),
                    "scoring_rubric": question.get("scoring_rubric", ""),
                    "learner_answer": _stringify_answer(answer),
                }
            )

    llm_short_feedback = None
    if llm_generator is not None and short_blocks:
        try:
            llm_short_feedback = _grade_short_answers_with_llm(short_blocks, llm_generator)
        except Exception:
            llm_short_feedback = None

    for question, answer in zip(questions, normalized_answers):
        question_type = str(question.get("question_type", "short_answer") or "short_answer").lower()
        if question_type in OBJECTIVE_TYPES:
            continue
        feedback = None
        if llm_short_feedback is not None:
            feedback = llm_short_feedback.get(int(question.get("question_index", 0) or 0))
        item_feedback.append(feedback or _fallback_grade_short_answer(question, answer))

    item_feedback.sort(key=lambda item: int(item.get("question_index", 0) or 0))
    total_score = round(sum(float(item.get("score", 0) or 0) for item in item_feedback), 1)
    average_score = round(total_score / max(1, len(questions)), 2)
    return {
        "total_score": total_score,
        "average_score": average_score,
        "max_total_score": len(questions) * 5,
        "overall_feedback": "整体掌握不错，可以继续通过追问和复习巩固细节。" if average_score >= 3 else "还有不少细节没有答完整，建议先回看摘要和知识点。",
        "item_feedback": item_feedback,
    }
