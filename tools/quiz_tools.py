from langchain_core.tools import tool


@tool
def create_quiz(topic: str, question_count: int = 3, difficulty: str = "medium") -> str:
    """Create a lightweight self-check quiz outline for the given topic."""

    question_count = max(1, min(question_count, 10))
    difficulty = difficulty or "medium"
    lines = [f"Quiz topic: {topic}", f"Difficulty: {difficulty}", "Questions:"]
    for index in range(1, question_count + 1):
        lines.append(f"{index}. Explain one important idea about {topic} and why it matters.")
    lines.append("Answering tip: use examples and compare similar concepts where possible.")
    return "\n".join(lines)


@tool
def grade_self_check(topic: str, learner_answer: str) -> str:
    """Provide a simple rubric for grading a learner's free-form answer."""

    answer_length = len((learner_answer or "").strip())
    completeness = "strong" if answer_length > 180 else "partial" if answer_length > 60 else "thin"
    return (
        f"Self-check review for topic: {topic}\n"
        f"Observed answer depth: {completeness}\n"
        "Rubric:\n"
        "- correctness: check whether the main idea is factually aligned with the study material\n"
        "- structure: check whether the answer has definition, explanation, and example\n"
        "- transfer: check whether the answer connects the topic to a broader agent-system context"
    )

