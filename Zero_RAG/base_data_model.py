from pydantic import BaseModel, Field


class CompleteOrEscalate(BaseModel):
    """Mark the current delegated task as completed or return control to the primary assistant."""

    cancel: bool = Field(
        default=True,
        description="Whether the delegated task should stop and hand control back to the primary assistant.",
    )
    reason: str = Field(
        description="A short explanation describing why the current task is complete or should be escalated.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "cancel": True,
                "reason": "The current study subtask is complete and the main assistant can continue.",
            }
        }


class ToLearningIngestAssistant(BaseModel):
    """Delegate document intake, study material organization, and key-point extraction."""

    request: str = Field(description="What should be prepared from the current learning materials.")
    material_title: str = Field(
        default="today_materials",
        description="A short title for the learning materials being processed.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "request": "Organize today's documents and extract the core knowledge points.",
                "material_title": "agent_interview_notes",
            }
        }


class ToReviewAssistant(BaseModel):
    """Delegate spaced review, recap, and review-note generation."""

    topic: str = Field(description="The study topic that needs review support.")
    request: str = Field(description="What kind of review help is needed.")

    class Config:
        json_schema_extra = {
            "example": {
                "topic": "RAG retrieval pipeline",
                "request": "Summarize what should be reviewed first and generate a short recap.",
            }
        }


class ToQuizAssistant(BaseModel):
    """Delegate quiz generation or self-check grading."""

    topic: str = Field(description="The topic to quiz the learner on.")
    request: str = Field(description="Quiz or grading request details.")
    question_count: int = Field(
        default=3,
        description="Suggested number of questions to generate when creating a quiz.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "topic": "multi-agent orchestration",
                "request": "Create a short interview-style self-check quiz.",
                "question_count": 3,
            }
        }


class ToPlannerAssistant(BaseModel):
    """Delegate study planning, reprioritization, and milestone design."""

    goal: str = Field(description="The learner's current study goal.")
    request: str = Field(description="What planning task should be completed.")
    horizon: str = Field(
        default="today",
        description="The planning horizon, for example today, this_week, or interview_prep.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "goal": "Prepare an agent application portfolio project for interviews",
                "request": "Break the work into today's actionable tasks.",
                "horizon": "today",
            }
        }

