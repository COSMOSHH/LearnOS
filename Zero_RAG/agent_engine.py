import json
import os
import sys
from typing import Callable

import requests
from langchain_core.utils.function_calling import convert_to_openai_tool

from chat_history_service import ThreadState, save_thread_state

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_data_model import (  # noqa: E402
    CompleteOrEscalate,
    ToLearningIngestAssistant,
    ToPlannerAssistant,
    ToQuizAssistant,
    ToReviewAssistant,
)
from tools.learning_ingest_tools import extract_key_points, prepare_study_materials  # noqa: E402
from tools.planner_tools import create_study_plan, reprioritize_tasks  # noqa: E402
from tools.quiz_tools import create_quiz, grade_self_check  # noqa: E402
from tools.retriever_vector import lookup_study_context  # noqa: E402
from tools.review_tools import build_review_prompt, record_review_note  # noqa: E402


class BaseAgent:
    def __init__(self, name: str, system_prompt: str, tools: list):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools

    def build_prompt(self, state: ThreadState) -> list:
        messages = [{"role": "system", "content": self.system_prompt}]

        rag_context = state.user_info.get("rag_context")
        if rag_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Current retrieved study context is available below. "
                        "Prioritize it when answering questions about today's learning materials.\n"
                        f"{rag_context}"
                    ),
                }
            )

        review_context = state.user_info.get("review_context")
        if review_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Relevant review memory is available below. "
                        "Use it only when it genuinely helps reinforce learning.\n"
                        f"{review_context}"
                    ),
                }
            )

        messages.extend(state.messages)
        return messages


def delegate_to_learning_ingest_assistant(**kwargs):
    return "ToLearningIngestAssistant"


def delegate_to_review_assistant(**kwargs):
    return "ToReviewAssistant"


def delegate_to_quiz_assistant(**kwargs):
    return "ToQuizAssistant"


def delegate_to_planner_assistant(**kwargs):
    return "ToPlannerAssistant"


def complete_or_escalate(**kwargs):
    return "CompleteOrEscalate"


def search_tavily(query: str, **kwargs):
    """Search the web for up-to-date public information that is not contained in local study materials."""

    url = "https://api.tavily.com/search"
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "Tavily API key is not configured."

    payload = {"api_key": api_key, "query": query, "max_results": 3}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return "No relevant web results were found."
        return "\n\n".join(
            [f"Source: {item.get('url')}\nContent: {item.get('content')}" for item in results]
        )
    except Exception as exc:
        return f"Web search failed: {exc}"


search_tavily_tool_schema = {
    "type": "function",
    "function": {
        "name": "search_tavily",
        "description": (
            "Search the web for current public information when the answer depends on up-to-date facts "
            "outside the uploaded study materials."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query describing what information is needed.",
                }
            },
            "required": ["query"],
        },
    },
}


TOOLS_REGISTRY: dict[str, Callable] = {
    "ToLearningIngestAssistant": delegate_to_learning_ingest_assistant,
    "ToReviewAssistant": delegate_to_review_assistant,
    "ToQuizAssistant": delegate_to_quiz_assistant,
    "ToPlannerAssistant": delegate_to_planner_assistant,
    "CompleteOrEscalate": complete_or_escalate,
    "search_tavily": search_tavily,
    "lookup_study_context": lookup_study_context.func,
    "prepare_study_materials": prepare_study_materials.func,
    "extract_key_points": extract_key_points.func,
    "record_review_note": record_review_note.func,
    "build_review_prompt": build_review_prompt.func,
    "create_quiz": create_quiz.func,
    "grade_self_check": grade_self_check.func,
    "create_study_plan": create_study_plan.func,
    "reprioritize_tasks": reprioritize_tasks.func,
}

SENSITIVE_TOOLS = []

PRIMARY_TOOLS = [
    search_tavily_tool_schema,
    convert_to_openai_tool(lookup_study_context),
    convert_to_openai_tool(ToLearningIngestAssistant),
    convert_to_openai_tool(ToReviewAssistant),
    convert_to_openai_tool(ToQuizAssistant),
    convert_to_openai_tool(ToPlannerAssistant),
]

LEARNING_INGEST_TOOLS = [
    convert_to_openai_tool(prepare_study_materials),
    convert_to_openai_tool(extract_key_points),
    convert_to_openai_tool(CompleteOrEscalate),
]

REVIEW_TOOLS = [
    convert_to_openai_tool(build_review_prompt),
    convert_to_openai_tool(record_review_note),
    convert_to_openai_tool(CompleteOrEscalate),
]

QUIZ_TOOLS = [
    convert_to_openai_tool(create_quiz),
    convert_to_openai_tool(grade_self_check),
    convert_to_openai_tool(CompleteOrEscalate),
]

PLANNER_TOOLS = [
    convert_to_openai_tool(create_study_plan),
    convert_to_openai_tool(reprioritize_tasks),
    convert_to_openai_tool(CompleteOrEscalate),
]


agent_registry = {
    "primary_assistant": BaseAgent(
        name="primary_assistant",
        system_prompt=(
            "You are a study copilot focused on helping the learner understand today's materials, "
            "connect them with prior knowledge, and make steady progress. "
            "Answer directly when you can. "
            "Delegate document preparation to the learning ingest assistant, review tasks to the review assistant, "
            "quiz generation or grading to the quiz assistant, and planning work to the planner assistant. "
            "Do not mention internal assistant switching to the learner."
        ),
        tools=PRIMARY_TOOLS,
    ),
    "learning_ingest_assistant": BaseAgent(
        name="learning_ingest_assistant",
        system_prompt=(
            "You specialize in organizing study materials, extracting key points, and preparing concise learning notes. "
            "When the task is done, use CompleteOrEscalate to return control."
        ),
        tools=LEARNING_INGEST_TOOLS,
    ),
    "review_assistant": BaseAgent(
        name="review_assistant",
        system_prompt=(
            "You specialize in review support. Focus on recap, spaced review prompts, and concise memory anchors. "
            "When the task is done, use CompleteOrEscalate to return control."
        ),
        tools=REVIEW_TOOLS,
    ),
    "quiz_assistant": BaseAgent(
        name="quiz_assistant",
        system_prompt=(
            "You specialize in active recall, quiz generation, and self-check grading. "
            "Create practical questions and concise evaluation criteria. "
            "When the task is done, use CompleteOrEscalate to return control."
        ),
        tools=QUIZ_TOOLS,
    ),
    "planner_assistant": BaseAgent(
        name="planner_assistant",
        system_prompt=(
            "You specialize in planning study work. Break goals into prioritized tasks with concrete next steps. "
            "When the task is done, use CompleteOrEscalate to return control."
        ),
        tools=PLANNER_TOOLS,
    ),
}


def run_agent_cycle(user_input: str, state: ThreadState, llm_generator):
    """
    Run the stack-based multi-agent loop and stream text chunks back to the caller.
    """
    if user_input:
        state.messages.append({"role": "user", "content": user_input})

    while True:
        current_agent_name = state.agent_stack[-1]
        active_agent = agent_registry.get(current_agent_name)
        if not active_agent:
            yield f"\n\n[system error: unknown agent {current_agent_name}]"
            return

        messages_to_llm = active_agent.build_prompt(state)
        final_content = ""
        final_tool_calls = None

        try:
            stream_gen = llm_generator.chat_with_tools_stream(messages_to_llm, tools=active_agent.tools)
            for item in stream_gen:
                if item["type"] == "content":
                    yield item["data"]
                elif item["type"] == "done":
                    final_content = item["content"]
                    final_tool_calls = item["tool_calls"]
        except Exception as exc:
            yield f"\n\n[llm scheduling failed: {exc}]"
            save_thread_state(state)
            return

        message_record = {"role": "assistant"}
        if final_content:
            message_record["content"] = final_content
        if final_tool_calls:
            message_record["tool_calls"] = final_tool_calls
        state.messages.append(message_record)

        if not final_tool_calls:
            save_thread_state(state)
            return

        for tool_call in final_tool_calls:
            tool_name = tool_call["function"]["name"]

            try:
                args = json.loads(tool_call["function"]["arguments"]) if tool_call["function"]["arguments"] else {}
            except json.JSONDecodeError:
                args = {}

            yield f"\n\n> calling tool: `{tool_name}` ...\n\n"

            if tool_name == "ToLearningIngestAssistant":
                state.agent_stack.append("learning_ingest_assistant")
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"Delegated to learning ingest assistant with args: {args}",
                    }
                )
                continue
            if tool_name == "ToReviewAssistant":
                state.agent_stack.append("review_assistant")
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"Delegated to review assistant with args: {args}",
                    }
                )
                continue
            if tool_name == "ToQuizAssistant":
                state.agent_stack.append("quiz_assistant")
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"Delegated to quiz assistant with args: {args}",
                    }
                )
                continue
            if tool_name == "ToPlannerAssistant":
                state.agent_stack.append("planner_assistant")
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"Delegated to planner assistant with args: {args}",
                    }
                )
                continue
            if tool_name == "CompleteOrEscalate":
                if len(state.agent_stack) > 1:
                    state.agent_stack.pop()
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": "Returned control to the primary assistant.",
                    }
                )
                continue

            if tool_name in SENSITIVE_TOOLS:
                if "confirm" not in user_input.lower() and "确认" not in user_input:
                    save_thread_state(state)
                    yield (
                        f"\n\n[confirmation required: tool `{tool_name}` needs explicit approval. "
                        f"Arguments: {args}]"
                    )
                    return

            if tool_name in TOOLS_REGISTRY:
                try:
                    result = TOOLS_REGISTRY[tool_name](**args)
                    tool_result_content = str(result)
                except Exception as exc:
                    tool_result_content = f"tool execution failed: {exc}"
            else:
                tool_result_content = f"unknown tool: {tool_name}"

            state.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": tool_result_content,
                }
            )

