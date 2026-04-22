from langchain_core.tools import tool


@tool
def create_study_plan(goal: str, available_time: str, current_level: str = "unknown") -> str:
    """Create a short study plan oriented around a concrete learning goal."""

    return (
        f"Study goal: {goal}\n"
        f"Available time: {available_time}\n"
        f"Current level: {current_level}\n"
        "Plan:\n"
        "1. Clarify the target outcome and expected deliverable.\n"
        "2. Split the work into reading, implementation, and review.\n"
        "3. Reserve time for one recall exercise and one summary pass."
    )


@tool
def reprioritize_tasks(today_focus: str, blockers: str = "") -> str:
    """Reprioritize a study day when the learner hits blockers or loses focus."""

    lines = [
        f"Today's focus: {today_focus}",
        f"Known blockers: {blockers or 'none provided'}",
        "Reprioritization rules:",
        "1. Keep the most interview-relevant task first.",
        "2. Push low-signal polishing work after understanding work.",
        "3. End with one artifact that can be shown in a portfolio or interview.",
    ]
    return "\n".join(lines)

