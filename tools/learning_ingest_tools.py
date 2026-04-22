from langchain_core.tools import tool


@tool
def prepare_study_materials(material_title: str, learner_goal: str = "", notes: str = "") -> str:
    """Prepare a concise intake summary for the current learning materials."""

    lines = [
        f"Study material title: {material_title}",
        f"Learner goal: {learner_goal or 'not provided'}",
    ]
    if notes:
        lines.append(f"Notes from the learner: {notes}")
    lines.extend(
        [
            "Recommended intake checklist:",
            "1. Identify the document type and its role in today's study session.",
            "2. Extract definitions, processes, and interview-worthy takeaways.",
            "3. Mark unclear sections for follow-up questions.",
        ]
    )
    return "\n".join(lines)


@tool
def extract_key_points(material_title: str, focus: str = "", max_points: int = 5) -> str:
    """Generate a key-point extraction template for the active learning materials."""

    max_points = max(1, min(max_points, 10))
    lines = [
        f"Key-point extraction plan for: {material_title}",
        f"Focus area: {focus or 'overall understanding'}",
        f"Expected point count: {max_points}",
        "Capture each point in this format:",
        "- concept",
        "- why it matters",
        "- likely interview angle",
        "- one question worth asking next",
    ]
    return "\n".join(lines)

