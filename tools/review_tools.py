from langchain_core.tools import tool


@tool
def record_review_note(topic: str, summary: str, confidence: int = 3) -> str:
    """Create a structured review note that can later be persisted into memory."""

    confidence = max(1, min(confidence, 5))
    return (
        f"Review note created.\n"
        f"Topic: {topic}\n"
        f"Summary: {summary}\n"
        f"Confidence (1-5): {confidence}\n"
        "Suggested next step: revisit this topic with one active recall question."
    )


@tool
def build_review_prompt(topic: str = "", max_items: int = 3) -> str:
    """Generate a short spaced-review prompt pack for the learner."""

    max_items = max(1, min(max_items, 5))
    topic_label = topic or "recent study topics"
    prompts = [f"Review prompts for {topic_label}:"]
    for index in range(1, max_items + 1):
        prompts.append(f"{index}. Explain the concept in your own words without looking at notes.")
    prompts.append("Finish by writing one confusion point and one confidence point.")
    return "\n".join(prompts)

