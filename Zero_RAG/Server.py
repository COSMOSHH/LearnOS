# Server.py
import json
import os
import shutil
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

import config
from RAG.document_loader import DocumentLoader
from RAG.hybrid_retriever import HybridRetriever
from RAG.text_splitter import SemanticTextSplitter
from RAG.vector_store import ChromaDBStore
from agent_engine import run_agent_cycle
from chat_history_service import delete_session_history, get_user_history, init_db, load_thread_state, save_chat_history
from llm_generator import LLMGenerator
from services.document_service import (
    calculate_content_hash,
    create_document,
    get_session_documents,
    get_session_knowledge_points,
    get_session_summaries,
    mark_document_ingested,
    save_document_chunks,
    save_document_summary,
    save_knowledge_points,
)
from services.evaluation_service import evaluate_answer, list_answer_evaluations, save_answer_evaluation, summarize_evaluations
from services.interview_service import (
    create_interview_session,
    generate_interview_blueprint,
    get_interview_session,
    get_interview_turns,
    get_latest_interview_session,
    submit_interview_answer,
    summarize_interview_session,
)
from services.observability_service import add_run_step, create_run, finish_run, get_run_steps, list_recent_events, list_recent_runs, record_event
from services.plan_service import (
    generate_learning_plan,
    get_latest_learning_plan,
    save_learning_plan,
    update_plan_item_completion,
)
from services.quiz_service import (
    create_quiz_set,
    generate_quiz_bundle,
    get_latest_quiz_attempt_for_session,
    get_latest_quiz_for_session,
    get_quiz_set_with_questions,
    grade_quiz_attempt,
    save_quiz_attempt,
    save_quiz_questions,
)
from services.report_service import generate_session_report
from services.review_service import (
    build_review_context,
    create_review_items_from_knowledge_points,
    create_review_items_from_quiz_feedback,
    get_quiz_feedback_items,
    get_review_items_for_session,
    list_review_queue,
    retry_wrong_question,
    update_review_item_progress,
)
from services.study_session_service import create_study_session, delete_study_session, get_study_session, list_study_sessions, update_study_session
from services.summary_service import infer_session_metadata, summarize_text
from services.webpage_service import fetch_webpage_batch, fetch_webpage_content
from tools.init_db import init_study_db


UPLOAD_DIR = Path(config.upload_directory)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_study_db()
    yield


app = FastAPI(title="LearnOS API", lifespan=lifespan)
vector_store = ChromaDBStore()
llm_generator = LLMGenerator(
    model_name=config.chat_model,
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
)


class SessionCreateRequest(BaseModel):
    user_id: str
    session_name: str
    topic: str = ""
    goal: str = ""
    tags: list[str] = []


class ChatRequest(BaseModel):
    user_id: str = "default_user"
    session_id: int | None = None
    query: str


class WebpageImportRequest(BaseModel):
    user_id: str
    url: str


class BatchWebpageImportRequest(BaseModel):
    user_id: str
    url: str
    max_pages: int = 5


class SessionDeleteRequest(BaseModel):
    user_id: str


class QuizGenerateRequest(BaseModel):
    user_id: str
    question_count: int = 3
    difficulty: str = "medium"


class QuizSubmitRequest(BaseModel):
    user_id: str
    quiz_set_id: int
    answers: list[object]


class PlanGenerateRequest(BaseModel):
    user_id: str


class PlanItemUpdateRequest(BaseModel):
    is_completed: bool


class ReviewProgressRequest(BaseModel):
    outcome: str
    notes: str = ""


class WrongQuestionRetryRequest(BaseModel):
    user_id: str
    answer: object


class InterviewStartRequest(BaseModel):
    user_id: str
    total_rounds: int = 3
    difficulty: str = "medium"


class InterviewAnswerRequest(BaseModel):
    user_id: str
    answer: str


def _ingest_text_resource(
    session_id: int,
    user_id: str,
    *,
    title: str,
    file_name: str,
    file_path: str,
    file_type: str,
    file_size: int,
    text: str,
    source_type: str,
    metadata: dict | None = None,
):
    normalized_text = text.strip()
    if not normalized_text:
        raise HTTPException(status_code=400, detail=f"Resource {title} does not contain readable text.")

    content_hash = calculate_content_hash(normalized_text)
    document_id = create_document(
        session_id=session_id,
        title=title,
        file_name=file_name,
        file_path=file_path,
        file_type=file_type,
        file_size=file_size,
        content_hash=content_hash,
        source_type=source_type,
        metadata=metadata,
    )

    splitter = SemanticTextSplitter(chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap)
    chunks = splitter.split_text(normalized_text)
    if not chunks:
        chunks = [normalized_text]

    source_reference = (metadata or {}).get("source_url") or (metadata or {}).get("source") or file_path
    metadatas = []
    ids = []
    for chunk_index, _ in enumerate(chunks):
        metadatas.append(
            {
                "source": source_reference,
                "document_id": str(document_id),
                "document_title": title,
                "session_id": str(session_id),
                "chunk_index": chunk_index,
                "source_type": source_type,
            }
        )
        ids.append(f"session_{session_id}_doc_{document_id}_chunk_{chunk_index}")

    vector_store.add_documents(documents=chunks, metadatas=metadatas, ids=ids)
    save_document_chunks(
        document_id=document_id,
        chunks=chunks,
        chroma_ids=ids,
        base_metadata={
            "session_id": session_id,
            "source": source_reference,
            "source_type": source_type,
        },
    )

    summary_bundle = summarize_text(normalized_text, llm_generator=llm_generator)
    save_document_summary(document_id, "short_summary", summary_bundle["short_summary"])
    save_document_summary(document_id, "keywords", ", ".join(summary_bundle["keywords"]))
    save_document_summary(
        document_id,
        "interview_takeaways",
        "\n".join(summary_bundle["interview_takeaways"]),
    )

    knowledge_point_ids = save_knowledge_points(session_id, document_id, summary_bundle["knowledge_points"])
    create_review_items_from_knowledge_points(
        user_id=user_id,
        session_id=session_id,
        knowledge_points=summary_bundle["knowledge_points"],
        knowledge_point_ids=knowledge_point_ids,
    )

    mark_document_ingested(document_id=document_id, status="completed")
    return (
        {
            "document_id": document_id,
            "title": title,
            "file_name": file_name,
            "source_type": source_type,
            "summary": summary_bundle["short_summary"],
            "knowledge_points": summary_bundle["knowledge_points"],
        },
        file_name,
        normalized_text[:5000],
    )


def _refresh_session_metadata(session_id: int, file_names: list[str], merged_text_parts: list[str]):
    all_summaries = get_session_summaries(session_id)
    overall_summary = {
        "short_summary": "\n".join(
            [item["summary_text"] for item in all_summaries if item["summary_type"] == "short_summary"][:3]
        ),
        "keywords": [],
    }
    for item in all_summaries:
        if item["summary_type"] == "keywords":
            overall_summary["keywords"].extend([part.strip() for part in item["summary_text"].split(",") if part.strip()])

    metadata = infer_session_metadata(
        file_names=file_names,
        merged_text="\n\n".join(merged_text_parts),
        summary_bundle=overall_summary,
        llm_generator=llm_generator,
    )
    return update_study_session(
        session_id=session_id,
        session_name=metadata["session_name"],
        topic=metadata["topic"],
        goal=metadata["goal"],
        tags=metadata.get("tags", []),
    )


async def _ingest_documents_for_session(session_id: int, user_id: str, files: list[UploadFile]):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    session_dir = UPLOAD_DIR / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    created_documents = []
    file_names = []
    merged_text_parts = []

    for upload in files:
        file_path = session_dir / upload.filename
        file_bytes = await upload.read()
        file_path.write_bytes(file_bytes)

        try:
            loader = DocumentLoader(str(file_path))
            text = loader.load().strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read {upload.filename}: {exc}")

        created_document, display_name, preview_text = _ingest_text_resource(
            session_id=session_id,
            user_id=user_id,
            title=Path(upload.filename).stem,
            file_name=upload.filename,
            file_path=str(file_path),
            file_type=file_path.suffix.lower(),
            file_size=len(file_bytes),
            text=text,
            source_type="upload",
            metadata={"source": str(file_path)},
        )
        created_documents.append(created_document)
        file_names.append(display_name)
        merged_text_parts.append(preview_text)

    updated_session = _refresh_session_metadata(session_id, file_names, merged_text_parts)
    return {"session": updated_session, "documents": created_documents}


def _ingest_webpage_for_session(session_id: int, user_id: str, url: str):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    try:
        webpage = fetch_webpage_content(url)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(status_code=status_code, detail=f"Failed to fetch webpage: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to import webpage: {exc}")

    created_document, display_name, preview_text = _ingest_text_resource(
        session_id=session_id,
        user_id=user_id,
        title=webpage["title"],
        file_name=webpage["source_url"],
        file_path=webpage["source_url"],
        file_type="url",
        file_size=len(webpage["text"].encode("utf-8")),
        text=webpage["text"],
        source_type="webpage",
        metadata={
            "source": webpage["source_url"],
            "source_url": webpage["source_url"],
            "site_name": webpage["site_name"],
        },
    )
    updated_session = _refresh_session_metadata(session_id, [display_name], [preview_text])
    return {"session": updated_session, "documents": [created_document], "webpage": webpage}


def _ingest_webpage_batch_for_session(session_id: int, user_id: str, url: str, max_pages: int):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    try:
        batch = fetch_webpage_batch(url, max_pages=max_pages)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(status_code=status_code, detail=f"Failed to fetch webpage batch: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to import webpage batch: {exc}")

    created_documents = []
    file_names = []
    merged_text_parts = []

    for page in batch["pages"]:
        created_document, display_name, preview_text = _ingest_text_resource(
            session_id=session_id,
            user_id=user_id,
            title=page["title"],
            file_name=page["source_url"],
            file_path=page["source_url"],
            file_type="url",
            file_size=len(page["text"].encode("utf-8")),
            text=page["text"],
            source_type="webpage",
            metadata={
                "source": page["source_url"],
                "source_url": page["source_url"],
                "site_name": page["site_name"],
                "import_mode": "batch_webpage",
                "batch_source_url": batch["source_url"],
            },
        )
        created_documents.append(created_document)
        file_names.append(display_name)
        merged_text_parts.append(preview_text)

    updated_session = _refresh_session_metadata(session_id, file_names, merged_text_parts)
    return {"session": updated_session, "documents": created_documents, "batch": batch}


def _build_session_retriever(session_id: int):
    where = {"session_id": str(session_id)}
    doc_chunks = vector_store.get_all_documents(where=where)
    if not doc_chunks:
        return None, []
    retriever = HybridRetriever(
        vector_store=vector_store,
        doc_chunks=doc_chunks,
        vector_top_k=5,
        bm25_top_k=5,
        final_top_k=3,
        vector_where=where,
    )
    return retriever, doc_chunks


def _build_quiz_bundle_for_session(session_id: int, question_count: int, difficulty: str):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    knowledge_points = get_session_knowledge_points(session_id)
    summaries = get_session_summaries(session_id)
    generated = generate_quiz_bundle(
        session=session,
        knowledge_points=knowledge_points,
        summaries=summaries,
        llm_generator=llm_generator,
        question_count=question_count,
        difficulty=difficulty,
    )
    quiz_set_id = create_quiz_set(
        session_id=session_id,
        title=generated["title"],
        question_count=len(generated["questions"]),
        difficulty=generated["difficulty"],
        metadata={"instructions": generated.get("instructions", "")},
    )
    save_quiz_questions(quiz_set_id, generated["questions"])
    stored = get_quiz_set_with_questions(quiz_set_id)
    return {
        "quiz_set_id": quiz_set_id,
        "title": generated["title"],
        "difficulty": generated["difficulty"],
        "instructions": generated.get("instructions", ""),
        "questions": stored["questions"] if stored else generated["questions"],
    }


def _build_learning_report(session_id: int):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    report = generate_session_report(
        session=session,
        documents=get_session_documents(session_id),
        summaries=get_session_summaries(session_id),
        knowledge_points=get_session_knowledge_points(session_id),
        review_items=get_review_items_for_session(session_id),
        history=get_user_history(session["user_id"], session_id=session_id),
        latest_quiz_attempt=get_latest_quiz_attempt_for_session(session_id, session["user_id"]),
        llm_generator=llm_generator,
    )
    return {"session_id": session_id, "report": report}


def _build_interview_session_payload(session_id: int, user_id: str):
    session = get_latest_interview_session(session_id, user_id)
    if session is None:
        return {"interview_session": None, "turns": [], "summary": None}
    turns = get_interview_turns(session["id"])
    summary = summarize_interview_session(session["id"]) if session.get("status") == "completed" else None
    return {"interview_session": session, "turns": turns, "summary": summary}


def _build_learning_plan(session_id: int):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    stored = get_latest_learning_plan(session_id)
    return {"session_id": session_id, "plan": stored}


def _record_event_safe(
    event_type: str,
    *,
    status: str = "success",
    session_id: int | None = None,
    user_id: str | None = None,
    duration_ms: int | None = None,
    message: str = "",
    metadata: dict | None = None,
):
    try:
        record_event(
            event_type=event_type,
            status=status,
            session_id=session_id,
            user_id=user_id,
            duration_ms=duration_ms,
            message=message,
            metadata=metadata,
        )
    except Exception:
        pass


def _record_run_step_safe(run_id: int | None, step_name: str, **kwargs):
    if not run_id:
        return
    try:
        add_run_step(run_id=run_id, step_name=step_name, **kwargs)
    except Exception:
        pass


def _finish_run_safe(run_id: int | None, **kwargs):
    if not run_id:
        return
    try:
        finish_run(run_id=run_id, **kwargs)
    except Exception:
        pass


def _generate_and_save_learning_plan(
    session_id: int,
    user_id: str,
    *,
    preserve_completion: bool = False,
    source_type: str = "generated",
):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")
    if session["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You do not have access to this session.")

    plan = generate_learning_plan(
        session=session,
        knowledge_points=get_session_knowledge_points(session_id),
        review_items=get_review_items_for_session(session_id),
        history=get_user_history(session["user_id"], session_id=session_id),
        latest_quiz_attempt=get_latest_quiz_attempt_for_session(session_id, session["user_id"]),
        llm_generator=llm_generator,
    )
    plan_id = save_learning_plan(
        session_id=session_id,
        user_id=user_id,
        plan=plan,
        source_type=source_type,
        preserve_completion=preserve_completion,
    )
    return {"session_id": session_id, "plan": get_latest_learning_plan(session_id), "plan_id": plan_id}


def _delete_session_resources(session_id: int, user_id: str):
    if not get_study_session(session_id):
        raise HTTPException(status_code=404, detail="Study session not found.")

    try:
        vector_store.delete_documents(where={"session_id": str(session_id)})
    except Exception:
        pass

    session_dir = UPLOAD_DIR / f"session_{session_id}"
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)

    deleted = delete_study_session(session_id=session_id, user_id=user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Study session not found for this user.")

    delete_session_history(user_id=user_id, session_id=session_id)
    return {"deleted": True, "session_id": session_id}


def _format_sources(retrieved_results: list[dict]) -> list[dict]:
    sources = []
    for item in retrieved_results:
        metadata = item.get("metadata") or {}
        sources.append(
            {
                "score": item.get("score", 0.0),
                "source": metadata.get("source", "unknown"),
                "document_title": metadata.get("document_title", ""),
                "chunk_index": metadata.get("chunk_index"),
            }
        )
    return sources


def _build_extra_system_context(review_text: str) -> str:
    if not review_text:
        return ""
    return (
        "After answering the learner's main question, add a short review reminder only if it helps. "
        "Keep the review section brief and clearly separated from the main answer.\n"
        f"{review_text}"
    )


@app.post("/study_sessions")
async def create_session_endpoint(request: SessionCreateRequest):
    session = create_study_session(
        user_id=request.user_id,
        session_name=request.session_name,
        topic=request.topic,
        goal=request.goal,
        tags=request.tags,
    )
    return {"session": session}


@app.get("/study_sessions")
async def list_sessions_endpoint(user_id: str = Query(...)):
    return {"sessions": list_study_sessions(user_id)}


@app.get("/study_sessions/{session_id}")
async def get_session_detail_endpoint(session_id: int):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    return {
        "session": session,
        "documents": get_session_documents(session_id),
        "summaries": get_session_summaries(session_id),
        "knowledge_points": get_session_knowledge_points(session_id),
        "review_items": get_review_items_for_session(session_id),
    }


@app.delete("/study_sessions/{session_id}")
async def delete_session_endpoint(session_id: int, request: SessionDeleteRequest):
    return _delete_session_resources(session_id=session_id, user_id=request.user_id)


@app.get("/study_sessions/{session_id}/report")
async def get_session_report_endpoint(session_id: int):
    started = time.perf_counter()
    try:
        payload = _build_learning_report(session_id)
        _record_event_safe(
            "report.generate",
            session_id=session_id,
            user_id=payload["report"].get("user_id"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"has_report": payload["report"] is not None},
        )
        return payload
    except Exception as exc:
        _record_event_safe(
            "report.generate",
            status="error",
            session_id=session_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=str(exc),
        )
        raise


@app.get("/study_sessions/{session_id}/plan")
async def get_session_plan_endpoint(session_id: int, only_incomplete: bool = Query(False)):
    payload = _build_learning_plan(session_id)
    if payload["plan"] is None:
        return payload
    return {"session_id": session_id, "plan": get_latest_learning_plan(session_id, only_incomplete=only_incomplete)}


@app.post("/study_sessions/{session_id}/plan")
async def generate_session_plan_endpoint(session_id: int, request: PlanGenerateRequest):
    started = time.perf_counter()
    try:
        payload = _generate_and_save_learning_plan(session_id=session_id, user_id=request.user_id)
        _record_event_safe(
            "plan.generate",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"plan_id": payload.get("plan_id")},
        )
        return payload
    except Exception as exc:
        _record_event_safe(
            "plan.generate",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=str(exc),
        )
        raise


@app.post("/study_sessions/{session_id}/plan/reprioritize")
async def reprioritize_session_plan_endpoint(session_id: int, request: PlanGenerateRequest):
    started = time.perf_counter()
    try:
        payload = _generate_and_save_learning_plan(
            session_id=session_id,
            user_id=request.user_id,
            preserve_completion=True,
            source_type="reprioritized",
        )
        _record_event_safe(
            "plan.reprioritize",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"plan_id": payload.get("plan_id")},
        )
        return payload
    except Exception as exc:
        _record_event_safe(
            "plan.reprioritize",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=str(exc),
        )
        raise


@app.patch("/study_plans/items/{item_id}")
async def update_plan_item_endpoint(item_id: int, request: PlanItemUpdateRequest):
    item = update_plan_item_completion(item_id=item_id, is_completed=request.is_completed)
    if item is None:
        raise HTTPException(status_code=404, detail="Plan item not found.")
    return {"item": item}


@app.get("/review_queue")
async def get_review_queue_endpoint(
    user_id: str = Query(...),
    session_id: int | None = Query(None),
    limit: int = Query(8),
    due_only: bool = Query(False),
):
    return {
        "items": list_review_queue(
            user_id=user_id,
            session_id=session_id,
            limit=limit,
            due_only=due_only,
        )
    }


@app.patch("/review_items/{item_id}/progress")
async def update_review_item_progress_endpoint(item_id: int, request: ReviewProgressRequest):
    started = time.perf_counter()
    try:
        item = update_review_item_progress(item_id=item_id, outcome=request.outcome, notes=request.notes)
        if item is None:
            raise HTTPException(status_code=404, detail="Review item not found.")
        if item.get("session_id"):
            try:
                _generate_and_save_learning_plan(
                    session_id=item["session_id"],
                    user_id=item["user_id"],
                    preserve_completion=True,
                    source_type="reprioritized",
                )
            except Exception:
                pass
        _record_event_safe(
            "review.progress",
            session_id=item.get("session_id"),
            user_id=item.get("user_id"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"item_id": item_id, "outcome": request.outcome},
        )
        return {"item": item}
    except Exception as exc:
        _record_event_safe(
            "review.progress",
            status="error",
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"item_id": item_id, "outcome": request.outcome},
            message=str(exc),
        )
        raise


@app.get("/wrong_questions")
async def get_wrong_questions_endpoint(
    user_id: str = Query(...),
    session_id: int | None = Query(None),
    max_score: float | None = Query(None),
    recent_days: int | None = Query(None),
):
    items = get_quiz_feedback_items(
        user_id=user_id,
        session_id=session_id,
        max_score=max_score,
        recent_days=recent_days,
    )
    session_name_map = {item["id"]: item["session_name"] for item in list_study_sessions(user_id)}
    for item in items:
        item["session_name"] = session_name_map.get(item.get("session_id"), "")
    return {"items": items}


@app.post("/wrong_questions/{item_id}/retry")
async def retry_wrong_question_endpoint(item_id: int, request: WrongQuestionRetryRequest):
    started = time.perf_counter()
    try:
        payload = retry_wrong_question(item_id, request.user_id, request.answer, llm_generator=llm_generator)
        if payload is None:
            raise HTTPException(status_code=404, detail="Wrong question item not found.")
        review_item = payload.get("review_item") or {}
        if review_item.get("session_id"):
            try:
                _generate_and_save_learning_plan(
                    session_id=review_item["session_id"],
                    user_id=request.user_id,
                    preserve_completion=True,
                    source_type="reprioritized",
                )
            except Exception:
                pass
        _record_event_safe(
            "wrong_question.retry",
            session_id=review_item.get("session_id"),
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"item_id": item_id, "status": payload.get("status")},
        )
        return payload
    except Exception as exc:
        _record_event_safe(
            "wrong_question.retry",
            status="error",
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"item_id": item_id},
            message=str(exc),
        )
        raise


@app.get("/system/events")
async def get_system_events_endpoint(
    session_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(30),
):
    return {"events": list_recent_events(limit=limit, session_id=session_id, status=status)}


@app.get("/study_sessions/{session_id}/evaluations")
async def get_session_evaluations_endpoint(
    session_id: int,
    source_type: str | None = Query(None),
    limit: int = Query(20),
):
    return {
        "session_id": session_id,
        "summary": summarize_evaluations(session_id, source_type=source_type),
        "items": list_answer_evaluations(session_id, source_type=source_type, limit=limit),
    }


@app.get("/study_sessions/{session_id}/agent_runs")
async def get_session_agent_runs_endpoint(session_id: int, run_type: str | None = Query(None), limit: int = Query(20)):
    runs = list_recent_runs(session_id=session_id, run_type=run_type, limit=limit)
    for run in runs:
        run["steps"] = get_run_steps(run["id"])
    return {"session_id": session_id, "runs": runs}


@app.post("/study_sessions/{session_id}/interview_sessions")
async def start_interview_session_endpoint(session_id: int, request: InterviewStartRequest):
    started = time.perf_counter()
    run_id = create_run(
        run_type="interview.start",
        session_id=session_id,
        user_id=request.user_id,
        title="模拟面试启动",
        input_summary=f"rounds={request.total_rounds}, difficulty={request.difficulty}",
    )
    try:
        session = get_study_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Study session not found.")
        if session["user_id"] != request.user_id:
            raise HTTPException(status_code=403, detail="You do not have access to this session.")

        blueprint = generate_interview_blueprint(
            session=session,
            knowledge_points=get_session_knowledge_points(session_id),
            summaries=get_session_summaries(session_id),
            total_rounds=request.total_rounds,
            difficulty=request.difficulty,
            llm_generator=llm_generator,
        )
        _record_run_step_safe(run_id, "blueprint.generate", duration_ms=0, metadata={"questions": len(blueprint.get("questions", []))})
        interview_session_id = create_interview_session(
            session_id=session_id,
            user_id=request.user_id,
            title=blueprint["title"],
            difficulty=request.difficulty,
            total_rounds=request.total_rounds,
            intro_text=blueprint["intro_text"],
            questions=blueprint["questions"],
        )
        payload = _build_interview_session_payload(session_id, request.user_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        _finish_run_safe(run_id, status="success", output_summary=f"interview_session_id={interview_session_id}", duration_ms=duration_ms)
        _record_event_safe(
            "interview.start",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            metadata={"interview_session_id": interview_session_id, "rounds": request.total_rounds},
        )
        return {"session_id": session_id, **payload}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_run_step_safe(run_id, "interview.start.error", step_status="error", duration_ms=duration_ms, message=str(exc))
        _finish_run_safe(run_id, status="error", output_summary=str(exc), duration_ms=duration_ms)
        _record_event_safe(
            "interview.start",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            message=str(exc),
        )
        raise


@app.get("/study_sessions/{session_id}/interview_sessions/latest")
async def get_latest_interview_session_endpoint(session_id: int, user_id: str = Query(...)):
    return {"session_id": session_id, **_build_interview_session_payload(session_id, user_id)}


@app.post("/interview_sessions/{interview_session_id}/answer")
async def submit_interview_answer_endpoint(interview_session_id: int, request: InterviewAnswerRequest):
    started = time.perf_counter()
    interview_session = get_interview_session(interview_session_id)
    if interview_session is None:
        raise HTTPException(status_code=404, detail="Interview session not found.")
    session_id = interview_session["session_id"]
    run_id = create_run(
        run_type="interview.answer",
        session_id=session_id,
        user_id=request.user_id,
        title="模拟面试答题",
        input_summary=request.answer[:120],
        metadata={"interview_session_id": interview_session_id},
    )
    try:
        payload = submit_interview_answer(interview_session_id, request.user_id, request.answer, llm_generator=llm_generator)
        if payload is None:
            raise HTTPException(status_code=404, detail="Interview session not found.")
        _record_run_step_safe(run_id, "interview.evaluate", duration_ms=0, metadata={"score": payload.get("score", 0), "status": payload.get("status")})
        evaluation = payload.get("evaluation", {})
        save_answer_evaluation(
            session_id=session_id,
            user_id=request.user_id,
            query_text=payload.get("question_text", "模拟面试问题"),
            answer_text=request.answer,
            evaluation=evaluation,
            source_type="interview",
            metadata={"interview_session_id": interview_session_id},
        )
        response_payload = _build_interview_session_payload(session_id, request.user_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        _finish_run_safe(run_id, status="success", output_summary=f"score={payload.get('score', 0)}", duration_ms=duration_ms)
        _record_event_safe(
            "interview.answer",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            metadata={"interview_session_id": interview_session_id, "score": payload.get("score", 0), "status": payload.get("status")},
        )
        return {"session_id": session_id, "result": payload, **response_payload}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_run_step_safe(run_id, "interview.answer.error", step_status="error", duration_ms=duration_ms, message=str(exc))
        _finish_run_safe(run_id, status="error", output_summary=str(exc), duration_ms=duration_ms)
        _record_event_safe(
            "interview.answer",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            metadata={"interview_session_id": interview_session_id},
            message=str(exc),
        )
        raise


@app.post("/study_sessions/{session_id}/documents")
async def upload_documents_endpoint(
    session_id: int,
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    result = await _ingest_documents_for_session(session_id=session_id, user_id=user_id, files=files)
    return {"session_id": session_id, "session": result["session"], "documents": result["documents"]}


@app.post("/study_sessions/auto_from_documents")
async def auto_create_session_from_documents_endpoint(
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    placeholder_session = create_study_session(
        user_id=user_id,
        session_name="资料整理中",
        topic="待分析",
        goal="等待系统根据上传资料自动生成学习信息。",
        tags=[],
    )
    result = await _ingest_documents_for_session(
        session_id=placeholder_session["id"],
        user_id=user_id,
        files=files,
    )
    return {
        "session_id": placeholder_session["id"],
        "session": result["session"],
        "documents": result["documents"],
    }


@app.post("/study_sessions/{session_id}/webpages")
async def import_webpage_endpoint(session_id: int, request: WebpageImportRequest):
    started = time.perf_counter()
    try:
        result = _ingest_webpage_for_session(session_id=session_id, user_id=request.user_id, url=request.url)
        _record_event_safe(
            "webpage.import",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"url": request.url},
        )
        return {"session_id": session_id, "session": result["session"], "documents": result["documents"]}
    except Exception as exc:
        _record_event_safe(
            "webpage.import",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"url": request.url},
            message=str(exc),
        )
        raise


@app.post("/study_sessions/{session_id}/webpages/batch")
async def import_webpage_batch_endpoint(session_id: int, request: BatchWebpageImportRequest):
    started = time.perf_counter()
    try:
        result = _ingest_webpage_batch_for_session(
            session_id=session_id,
            user_id=request.user_id,
            url=request.url,
            max_pages=request.max_pages,
        )
        _record_event_safe(
            "webpage.batch_import",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"url": request.url, "imported_count": len(result["documents"])},
        )
        return {
            "session_id": session_id,
            "session": result["session"],
            "documents": result["documents"],
            "imported_count": len(result["documents"]),
            "source_url": result["batch"]["source_url"],
        }
    except Exception as exc:
        _record_event_safe(
            "webpage.batch_import",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"url": request.url},
            message=str(exc),
        )
        raise


@app.post("/study_sessions/auto_from_webpage")
async def auto_create_session_from_webpage_endpoint(request: WebpageImportRequest):
    placeholder_session = create_study_session(
        user_id=request.user_id,
        session_name="网页资料整理中",
        topic="待分析",
        goal="等待系统根据网页内容自动生成学习信息。",
        tags=[],
    )
    result = _ingest_webpage_for_session(
        session_id=placeholder_session["id"],
        user_id=request.user_id,
        url=request.url,
    )
    return {
        "session_id": placeholder_session["id"],
        "session": result["session"],
        "documents": result["documents"],
    }


@app.post("/study_sessions/auto_from_webpage_batch")
async def auto_create_session_from_webpage_batch_endpoint(request: BatchWebpageImportRequest):
    placeholder_session = create_study_session(
        user_id=request.user_id,
        session_name="批量网页资料整理中",
        topic="待分析",
        goal="等待系统根据同站网页资料自动生成学习信息。",
        tags=[],
    )
    result = _ingest_webpage_batch_for_session(
        session_id=placeholder_session["id"],
        user_id=request.user_id,
        url=request.url,
        max_pages=request.max_pages,
    )
    return {
        "session_id": placeholder_session["id"],
        "session": result["session"],
        "documents": result["documents"],
        "imported_count": len(result["documents"]),
        "source_url": result["batch"]["source_url"],
    }


@app.post("/study_sessions/{session_id}/quiz_sets")
async def generate_quiz_endpoint(session_id: int, request: QuizGenerateRequest):
    started = time.perf_counter()
    try:
        payload = _build_quiz_bundle_for_session(
            session_id=session_id,
            question_count=request.question_count,
            difficulty=request.difficulty,
        )
        _record_event_safe(
            "quiz.generate",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={"quiz_set_id": payload.get("quiz_set_id"), "question_count": len(payload.get("questions", []))},
        )
        return payload
    except Exception as exc:
        _record_event_safe(
            "quiz.generate",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message=str(exc),
        )
        raise


@app.get("/study_sessions/{session_id}/quiz_sets/latest")
async def get_latest_quiz_endpoint(session_id: int):
    latest = get_latest_quiz_for_session(session_id)
    if latest is None:
        return {"quiz": None}

    metadata = json.loads(latest["quiz_set"].get("metadata_json") or "{}")
    return {
        "quiz": {
            "quiz_set_id": latest["quiz_set"]["id"],
            "title": latest["quiz_set"]["title"],
            "difficulty": latest["quiz_set"]["difficulty"],
            "instructions": metadata.get("instructions", ""),
            "questions": latest["questions"],
        }
    }


@app.post("/study_sessions/{session_id}/quiz_attempts")
async def submit_quiz_endpoint(session_id: int, request: QuizSubmitRequest):
    started = time.perf_counter()
    stored = get_quiz_set_with_questions(request.quiz_set_id)
    if stored is None or stored["quiz_set"]["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Quiz not found for this session.")

    result = grade_quiz_attempt(
        questions=stored["questions"],
        answers=request.answers,
        llm_generator=llm_generator,
    )
    attempt_id = save_quiz_attempt(
        quiz_set_id=request.quiz_set_id,
        session_id=session_id,
        user_id=request.user_id,
        answers=request.answers,
        result=result,
    )
    created_review_topics = create_review_items_from_quiz_feedback(
        user_id=request.user_id,
        session_id=session_id,
        questions=stored["questions"],
        result=result,
    )
    if get_latest_learning_plan(session_id) is not None:
        try:
            _generate_and_save_learning_plan(
                session_id=session_id,
                user_id=request.user_id,
                preserve_completion=True,
                source_type="reprioritized",
            )
        except Exception:
            pass
    _record_event_safe(
        "quiz.submit",
        session_id=session_id,
        user_id=request.user_id,
        duration_ms=int((time.perf_counter() - started) * 1000),
        metadata={
            "quiz_set_id": request.quiz_set_id,
            "total_score": result.get("total_score", 0),
            "review_items_created": len(created_review_topics),
        },
    )
    return {
        "attempt_id": attempt_id,
        "result": result,
        "review_items_created": len(created_review_topics),
        "review_topics": created_review_topics,
    }


@app.get("/study_sessions/{session_id}/quiz_attempts/latest")
async def get_latest_quiz_attempt_endpoint(session_id: int, user_id: str = Query(...)):
    attempt = get_latest_quiz_attempt_for_session(session_id, user_id)
    return {"attempt": attempt}


@app.post("/agent_chat")
async def agent_chat_endpoint(request: ChatRequest):
    try:
        thread_id = f"thread_{request.user_id}_{request.session_id or 'general'}"
        state = load_thread_state(thread_id)

        if request.session_id is not None:
            retriever, _ = _build_session_retriever(request.session_id)
            if retriever:
                retrieved_results = retriever.retrieve(request.query)
                state.user_info["rag_context"] = "\n".join(
                    [f"- {item['metadata'].get('source', 'unknown')}: {item['document']}" for item in retrieved_results]
                )
            review_context = build_review_context(request.user_id, request.query, current_session_id=request.session_id, limit=2)
            state.user_info["review_context"] = review_context["text"]

        def generate_sse():
            for chunk in run_agent_cycle(request.query, state, llm_generator):
                yield json.dumps({"chunk": chunk}, ensure_ascii=False) + "\n"

        return StreamingResponse(generate_sse(), media_type="application/x-ndjson")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    if request.session_id is None:
        raise HTTPException(status_code=400, detail="session_id is required for study chat.")

    retriever, doc_chunks = _build_session_retriever(request.session_id)
    if not retriever or not doc_chunks:
        raise HTTPException(status_code=400, detail="No indexed study materials were found for this session.")

    try:
        run_started = time.perf_counter()
        retrieved_results = retriever.retrieve(request.query)
        sources = _format_sources(retrieved_results)
        run_id = create_run(
            run_type="study_chat",
            session_id=request.session_id,
            user_id=request.user_id,
            title="学习问答",
            input_summary=request.query[:160],
            metadata={"source_count": len(sources)},
        )
        _record_run_step_safe(run_id, "retrieve", duration_ms=0, metadata={"source_count": len(sources)})

        raw_history = get_user_history(request.user_id, session_id=request.session_id)[-6:]
        chat_history = []
        for item in raw_history:
            chat_history.append({"role": "user", "content": item["query"]})
            chat_history.append({"role": "assistant", "content": item["response"]})

        review_context = build_review_context(
            user_id=request.user_id,
            query=request.query,
            current_session_id=request.session_id,
            limit=2,
        )
        extra_system_context = _build_extra_system_context(review_context["text"])

        def generate_stream():
            full_response = ""
            try:
                generation_started = time.perf_counter()
                for chunk in llm_generator.generate_answer_stream(
                    request.query,
                    retrieved_results,
                    history=chat_history,
                    extra_system_context=extra_system_context,
                ):
                    full_response += chunk
                    yield json.dumps({"chunk": chunk}, ensure_ascii=False) + "\n"

                _record_run_step_safe(
                    run_id,
                    "generate_answer",
                    duration_ms=int((time.perf_counter() - generation_started) * 1000),
                    metadata={"response_length": len(full_response)},
                )
                yield json.dumps({"sources": sources, "review_items": review_context["items"]}, ensure_ascii=False) + "\n"
                save_chat_history(
                    request.user_id,
                    request.query,
                    full_response,
                    session_id=request.session_id,
                    sources=sources,
                )
                evaluation = evaluate_answer(
                    query_text=request.query,
                    answer_text=full_response,
                    sources=sources,
                    llm_generator=llm_generator,
                    source_type="chat",
                )
                evaluation_id = save_answer_evaluation(
                    session_id=request.session_id,
                    user_id=request.user_id,
                    query_text=request.query,
                    answer_text=full_response,
                    evaluation=evaluation,
                    source_type="chat",
                    metadata={"source_count": len(sources)},
                )
                _record_run_step_safe(
                    run_id,
                    "evaluate_answer",
                    duration_ms=0,
                    metadata={"evaluation_id": evaluation_id, "overall_score": evaluation.get("overall_score", 0)},
                )
                _finish_run_safe(
                    run_id,
                    status="success",
                    output_summary=full_response[:180],
                    duration_ms=int((time.perf_counter() - run_started) * 1000),
                    metadata={"evaluation_score": evaluation.get("overall_score", 0)},
                )
            except Exception as inner_exc:
                error_msg = f"\n\n[backend streaming error: {inner_exc}]"
                _record_run_step_safe(run_id, "study_chat.error", step_status="error", message=str(inner_exc))
                _finish_run_safe(
                    run_id,
                    status="error",
                    output_summary=str(inner_exc),
                    duration_ms=int((time.perf_counter() - run_started) * 1000),
                )
                yield json.dumps({"chunk": error_msg}, ensure_ascii=False) + "\n"

        return StreamingResponse(generate_stream(), media_type="application/x-ndjson")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history/{user_id}")
async def get_history_endpoint(user_id: str, session_id: int | None = None):
    history_data = get_user_history(user_id, session_id=session_id)
    return {"user_id": user_id, "session_id": session_id, "history": history_data}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
