from pathlib import Path
import re

from langchain_core.tools import tool


ROOT_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_FILES = [
    ROOT_DIR / "学习辅助Agent项目改造计划.md",
    ROOT_DIR / "纯手搓多Agent改造说明文档.md",
]


def _load_documents() -> list[dict]:
    documents = []
    for path in KNOWLEDGE_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for chunk in re.split(r"\n(?=#|\*\*)", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            documents.append({"source": path.name, "page_content": chunk})
    return documents


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", text)}


def _score(query: str, content: str) -> int:
    query_tokens = _tokenize(query)
    content_tokens = _tokenize(content)
    if not query_tokens or not content_tokens:
        return 0
    return len(query_tokens & content_tokens)


@tool
def lookup_study_context(query: str) -> str:
    """Search local study-planning notes and project guidance for relevant context."""

    documents = _load_documents()
    if not documents:
        return "No local study guidance documents are available yet."

    ranked = sorted(documents, key=lambda item: _score(query, item["page_content"]), reverse=True)
    top_hits = [item for item in ranked[:3] if _score(query, item["page_content"]) > 0]

    if not top_hits:
        top_hits = ranked[:2]

    return "\n\n".join(
        [f"Source: {item['source']}\nContent: {item['page_content']}" for item in top_hits]
    )


if __name__ == "__main__":
    print(lookup_study_context.invoke({"query": "如何设计学习型 agent 的自动复习机制"}))

