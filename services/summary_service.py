import json
import re
from collections import Counter


STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "with",
    "this",
    "from",
    "have",
    "your",
    "you",
    "are",
    "was",
    "will",
    "into",
    "一个",
    "这个",
    "我们",
    "你们",
    "以及",
    "进行",
    "需要",
    "可以",
    "如果",
    "因为",
    "的是",
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> list[str]:
    normalized = text.replace("\r", "\n")
    raw = re.split(r"(?<=[。！？.!?])\s+|\n+", normalized)
    return [item.strip() for item in raw if item and len(item.strip()) > 10]


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text)]


def _top_keywords(text: str, top_k: int = 8) -> list[str]:
    counter = Counter(token for token in _tokenize(text) if len(token) > 1 and token not in STOPWORDS)
    return [token for token, _ in counter.most_common(top_k)]


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


def _fallback_summary(text: str) -> dict:
    normalized = _normalize_text(text)
    sentences = _split_sentences(normalized)
    summary_sentences = sentences[:3] if sentences else [normalized[:240]]
    short_summary = " ".join(summary_sentences).strip()[:500]
    keywords = _top_keywords(normalized, top_k=8)

    knowledge_points = []
    for index, sentence in enumerate(sentences[:5]):
        title = sentence[:28] + ("..." if len(sentence) > 28 else "")
        knowledge_points.append(
            {
                "title": title,
                "description": sentence[:220],
                "category": "core_concept" if index < 3 else "supporting_detail",
                "importance": max(1, 5 - index),
                "difficulty": 3,
                "metadata": {"origin": "heuristic_summary"},
            }
        )

    if not knowledge_points:
        knowledge_points.append(
            {
                "title": (normalized[:28] + "...") if len(normalized) > 28 else normalized,
                "description": normalized[:220],
                "category": "core_concept",
                "importance": 3,
                "difficulty": 3,
                "metadata": {"origin": "heuristic_summary"},
            }
        )

    interview_takeaways = []
    for keyword in keywords[:3]:
        interview_takeaways.append(f"准备说明 `{keyword}` 在整篇材料中的作用，以及它为什么重要。")

    return {
        "short_summary": short_summary,
        "keywords": keywords,
        "knowledge_points": knowledge_points,
        "interview_takeaways": interview_takeaways,
    }


def _llm_summary(text: str, llm_generator) -> dict | None:
    if llm_generator is None:
        return None

    prompt = f"""
请阅读下面的学习材料内容，并输出中文总结结果。

要求：
1. 输出必须是合法 JSON。
2. 所有字段内容都必须使用中文。
3. 不要输出 JSON 之外的任何解释。
4. 如果原文是英文，也要翻译和概括为中文。
5. knowledge_points 保持 3 到 5 条。
6. keywords 尽量给出中文关键词，必要时可保留英文术语。

JSON 结构必须为：
{{
  "short_summary": "中文摘要",
  "keywords": ["关键词1", "关键词2"],
  "knowledge_points": [
    {{
      "title": "中文知识点标题",
      "description": "中文知识点说明",
      "category": "核心概念",
      "importance": 5,
      "difficulty": 3
    }}
  ],
  "interview_takeaways": ["中文面试要点1", "中文面试要点2"]
}}

学习材料内容：
\"\"\"
{text[:12000]}
\"\"\"
"""

    try:
        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个中文学习总结助手，擅长把英文技术文档转换成简洁、准确的中文学习笔记。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1400,
        )
        parsed = _extract_json_object(raw)
        if not parsed:
            return None

        knowledge_points = []
        for item in parsed.get("knowledge_points", [])[:5]:
            knowledge_points.append(
                {
                    "title": item.get("title", "未命名知识点"),
                    "description": item.get("description", ""),
                    "category": item.get("category", "核心概念"),
                    "importance": int(item.get("importance", 3)),
                    "difficulty": int(item.get("difficulty", 3)),
                    "metadata": {"origin": "llm_summary"},
                }
            )

        return {
            "short_summary": parsed.get("short_summary", ""),
            "keywords": parsed.get("keywords", []),
            "knowledge_points": knowledge_points,
            "interview_takeaways": parsed.get("interview_takeaways", []),
        }
    except Exception:
        return None


def _fallback_session_metadata(file_names: list[str], summary_bundle: dict) -> dict:
    base_name = file_names[0].rsplit(".", 1)[0] if file_names else "学习资料"
    topic = "文献学习"
    goal = f"围绕《{base_name}》相关资料完成阅读、摘要整理和问答学习。"
    return {
        "session_name": f"今日学习：{base_name[:24]}",
        "topic": topic,
        "goal": goal,
        "tags": summary_bundle.get("keywords", [])[:5],
    }


def infer_session_metadata(file_names: list[str], merged_text: str, summary_bundle: dict, llm_generator=None) -> dict:
    if llm_generator is None:
        return _fallback_session_metadata(file_names, summary_bundle)

    joined_names = "、".join(file_names[:5]) if file_names else "学习资料"
    prompt = f"""
请根据以下上传资料，自动生成一个中文学习会话信息。

要求：
1. 输出必须是合法 JSON。
2. 所有内容都必须使用中文。
3. session_name 要简洁，像真实用户会看到的会话标题。
4. topic 是一句短语，表示学习主题。
5. goal 是一句中文目标描述。
6. tags 最多 5 个。

JSON 结构：
{{
  "session_name": "中文会话名称",
  "topic": "中文学习主题",
  "goal": "中文学习目标",
  "tags": ["标签1", "标签2"]
}}

文件名：
{joined_names}

资料摘要：
{summary_bundle.get("short_summary", "")}

资料关键词：
{", ".join(summary_bundle.get("keywords", []))}

资料正文片段：
\"\"\"
{merged_text[:6000]}
\"\"\"
"""

    try:
        raw = llm_generator.chat_once(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个中文学习规划助手，擅长把上传的英文或中文资料整理成适合学习系统展示的中文会话信息。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        parsed = _extract_json_object(raw)
        if parsed and parsed.get("session_name"):
            return {
                "session_name": parsed.get("session_name", "今日学习"),
                "topic": parsed.get("topic", "文献学习"),
                "goal": parsed.get("goal", "完成资料阅读与学习理解。"),
                "tags": parsed.get("tags", [])[:5],
            }
    except Exception:
        pass

    return _fallback_session_metadata(file_names, summary_bundle)


def summarize_text(text: str, llm_generator=None) -> dict:
    llm_result = _llm_summary(text, llm_generator=llm_generator)
    if llm_result and llm_result.get("short_summary"):
        return llm_result
    return _fallback_summary(text)
