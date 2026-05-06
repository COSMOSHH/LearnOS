# Server.py
import json
import os
import shutil
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
from services.context_service import build_generation_context
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
from services.query_service import expand_query_to_multi_queries, plan_retrieval_route, rewrite_query
from services.rag_eval_service import build_eval_dataset_template, build_session_eval_cases, evaluate_retrieval_cases, resolve_retrieval_config
from services.rag_quality_service import build_rag_quality_dashboard, save_low_quality_samples
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

IMPORT_JOBS: dict[str, dict] = {}
IMPORT_JOBS_LOCK = threading.Lock()
RAG_EVAL_JOBS: dict[str, dict] = {}
RAG_EVAL_JOBS_LOCK = threading.Lock()
RAG_EVAL_LOG_DIR = ROOT_DIR / "logs" / "rag_eval"
RAG_EVAL_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _create_import_job(*, session_id: int, user_id: str, auto_create: bool, total_files: int) -> dict:
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "pending",
        "stage": "pending",
        "message": "等待导入任务开始。",
        "progress": 0,
        "session_id": session_id,
        "user_id": user_id,
        "auto_create": auto_create,
        "total_files": total_files,
        "processed_files": 0,
        "current_file": "",
        "total_chunks": 0,
        "processed_chunks": 0,
        "result": None,
        "error": None,
        "created_at": _now_ms(),
        "updated_at": _now_ms(),
    }
    with IMPORT_JOBS_LOCK:
        IMPORT_JOBS[job_id] = job
    return job


def _get_import_job(job_id: str) -> dict | None:
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        return dict(job) if job else None


def _update_import_job(job_id: str, **updates):
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = _now_ms()


def _chunk_progress(processed_chunks: int, total_chunks: int) -> int:
    if total_chunks <= 0:
        return 35
    return min(88, 35 + int((processed_chunks / total_chunks) * 53))


def _create_rag_eval_job(*, session_id: int, user_id: str, total_cases: int) -> dict:
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "pending",
        "stage": "pending",
        "message": "等待 RAG 评测任务开始。",
        "progress": 0,
        "session_id": session_id,
        "user_id": user_id,
        "total_cases": total_cases,
        "processed_cases": 0,
        "current_query": "",
        "result": None,
        "error": None,
        "log_path": "",
        "created_at": _now_ms(),
        "updated_at": _now_ms(),
    }
    with RAG_EVAL_JOBS_LOCK:
        RAG_EVAL_JOBS[job_id] = job
    return job


def _get_rag_eval_job(job_id: str) -> dict | None:
    with RAG_EVAL_JOBS_LOCK:
        job = RAG_EVAL_JOBS.get(job_id)
        return dict(job) if job else None


def _update_rag_eval_job(job_id: str, **updates):
    with RAG_EVAL_JOBS_LOCK:
        job = RAG_EVAL_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = _now_ms()


def _rag_eval_progress(processed_cases: int, total_cases: int) -> int:
    if total_cases <= 0:
        return 10
    return min(95, 10 + int((processed_cases / total_cases) * 85))


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


class RetrievalEvalCase(BaseModel):
    query: str
    relevant_sources: list[str] = []
    relevant_titles: list[str] = []
    relevant_keywords: list[str] = []


class RetrievalEvalRequest(BaseModel):
    user_id: str = "default_user"
    cases: list[RetrievalEvalCase] = Field(default_factory=list)
    top_k: int = 5
    low_quality_mrr_threshold: float = 0.5
    retrieval_config: dict = Field(default_factory=dict)
    compare_to_original: bool = False
    run_ablation: bool = False


class BenchmarkImportRequest(BaseModel):
    user_id: str


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
    import_job_id: str | None = None,
    skip_learning_artifacts: bool = False,
):
    normalized_text = text.strip()
    if not normalized_text:
        raise HTTPException(status_code=400, detail=f"Resource {title} does not contain readable text.")

    if import_job_id:
        _update_import_job(
            import_job_id,
            status="running",
            stage="splitting",
            message=f"正在切分 {file_name}。",
            current_file=file_name,
            progress=25,
        )

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
    chunk_records = splitter.split_text_with_metadata(
        normalized_text,
        document_title=title,
        section_items=(metadata or {}).get("sections"),
    )
    if not chunk_records:
        chunk_records = [
            {
                "text": normalized_text,
                "section_title": title,
                "heading_path": title,
                "chunk_type": "fallback",
            }
        ]

    if import_job_id:
        _update_import_job(
            import_job_id,
            stage="indexing",
            message=f"正在向量化并写入 {len(chunk_records)} 个 chunk。",
            total_chunks=len(chunk_records),
            processed_chunks=0,
            progress=35,
        )

    source_reference = (metadata or {}).get("source_url") or (metadata or {}).get("source") or file_path
    metadatas = []
    ids = []
    for chunk_index, chunk_record in enumerate(chunk_records):
        metadatas.append(
            {
                "source": source_reference,
                "document_id": str(document_id),
                "document_title": title,
                "session_id": str(session_id),
                "chunk_index": chunk_index,
                "source_type": source_type,
                "section_title": chunk_record.get("section_title", title),
                "heading_path": chunk_record.get("heading_path", title),
                "chunk_type": chunk_record.get("chunk_type", "section"),
            }
        )
        ids.append(f"session_{session_id}_doc_{document_id}_chunk_{chunk_index}")

    chunks = [item["text"] for item in chunk_records]

    def update_vector_progress(processed_chunks: int, total_chunks: int):
        if import_job_id:
            _update_import_job(
                import_job_id,
                stage="indexing",
                message=f"已入库 {processed_chunks}/{total_chunks} 个 chunk。",
                processed_chunks=processed_chunks,
                total_chunks=total_chunks,
                progress=_chunk_progress(processed_chunks, total_chunks),
            )

    vector_store.add_documents(documents=chunks, metadatas=metadatas, ids=ids, progress_callback=update_vector_progress)
    if import_job_id:
        _update_import_job(
            import_job_id,
            stage="saving_chunks",
            message="正在保存 chunk 元数据。",
            progress=90,
        )
    save_document_chunks(
        document_id=document_id,
        chunks=chunks,
        chroma_ids=ids,
        base_metadata={
            "session_id": session_id,
            "source": source_reference,
            "source_type": source_type,
            "document_title": title,
        },
    )

    if import_job_id and not skip_learning_artifacts:
        _update_import_job(
            import_job_id,
            stage="summarizing",
            message="正在生成摘要和知识点。",
            progress=93,
        )
    if skip_learning_artifacts:
        summary_bundle = {
            "short_summary": "Benchmark corpus imported without learning summary generation.",
            "keywords": [],
            "interview_takeaways": [],
            "knowledge_points": [],
        }
    else:
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

    if import_job_id:
        _update_import_job(
            import_job_id,
            stage="finalizing_document",
            message=f"{file_name} 已完成入库。",
            progress=96,
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


async def _ingest_documents_for_session(session_id: int, user_id: str, files: list[UploadFile], import_job_id: str | None = None):
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
        if import_job_id:
            _update_import_job(
                import_job_id,
                status="running",
                stage="reading",
                message=f"正在读取 {upload.filename}。",
                current_file=upload.filename,
                progress=10,
            )
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
            import_job_id=import_job_id,
        )
        created_documents.append(created_document)
        file_names.append(display_name)
        merged_text_parts.append(preview_text)

    if import_job_id:
        _update_import_job(
            import_job_id,
            stage="session_metadata",
            message="正在刷新会话名称、主题和目标。",
            progress=97,
        )
    updated_session = _refresh_session_metadata(session_id, file_names, merged_text_parts)
    return {"session": updated_session, "documents": created_documents}


def _ingest_saved_documents_for_session(session_id: int, user_id: str, file_records: list[dict], import_job_id: str):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    created_documents = []
    file_names = []
    merged_text_parts = []

    for file_index, record in enumerate(file_records, start=1):
        file_path = Path(record["path"])
        file_name = record["filename"]
        _update_import_job(
            import_job_id,
            status="running",
            stage="reading",
            message=f"正在读取 {file_name}。",
            current_file=file_name,
            processed_files=file_index - 1,
            progress=10,
        )

        try:
            loader = DocumentLoader(str(file_path))
            text = loader.load().strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read {file_name}: {exc}")

        created_document, display_name, preview_text = _ingest_text_resource(
            session_id=session_id,
            user_id=user_id,
            title=Path(file_name).stem,
            file_name=file_name,
            file_path=str(file_path),
            file_type=file_path.suffix.lower(),
            file_size=int(record.get("size") or file_path.stat().st_size),
            text=text,
            source_type="upload",
            metadata={"source": str(file_path)},
            import_job_id=import_job_id,
        )
        created_documents.append(created_document)
        file_names.append(display_name)
        merged_text_parts.append(preview_text)
        _update_import_job(import_job_id, processed_files=file_index)

    _update_import_job(
        import_job_id,
        stage="session_metadata",
        message="正在刷新会话名称、主题和目标。",
        progress=97,
    )
    updated_session = _refresh_session_metadata(session_id, file_names, merged_text_parts)
    return {"session": updated_session, "documents": created_documents}


def _run_import_job(job_id: str, *, session_id: int, user_id: str, file_records: list[dict]):
    started = time.perf_counter()
    try:
        _update_import_job(job_id, status="running", stage="starting", message="导入任务已启动。", progress=5)
        result = _ingest_saved_documents_for_session(
            session_id=session_id,
            user_id=user_id,
            file_records=file_records,
            import_job_id=job_id,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_import_job(
            job_id,
            status="completed",
            stage="completed",
            message="导入完成。",
            progress=100,
            result={"session_id": session_id, "session": result["session"], "documents": result["documents"]},
        )
        _record_event_safe(
            "documents.import_job",
            session_id=session_id,
            user_id=user_id,
            duration_ms=duration_ms,
            metadata={"job_id": job_id, "document_count": len(result["documents"])},
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_import_job(
            job_id,
            status="failed",
            stage="failed",
            message="导入失败。",
            error=str(exc.detail if isinstance(exc, HTTPException) else exc),
        )
        _record_event_safe(
            "documents.import_job",
            status="error",
            session_id=session_id,
            user_id=user_id,
            duration_ms=duration_ms,
            metadata={"job_id": job_id},
            message=str(exc),
        )


def _run_scifact_import_job(job_id: str, *, session_id: int, user_id: str, corpus_path: Path):
    started = time.perf_counter()
    try:
        _update_import_job(
            job_id,
            status="running",
            stage="reading",
            message="正在读取 SciFact benchmark corpus。",
            current_file=corpus_path.name,
            progress=10,
        )
        text = corpus_path.read_text(encoding="utf-8").strip()
        created_document, _, _ = _ingest_text_resource(
            session_id=session_id,
            user_id=user_id,
            title="SciFact Benchmark Corpus",
            file_name=corpus_path.name,
            file_path=str(corpus_path),
            file_type=corpus_path.suffix.lower(),
            file_size=corpus_path.stat().st_size,
            text=text,
            source_type="benchmark",
            metadata={
                "source": "scifact_benchmark",
                "dataset": "scifact",
                "benchmark_path": str(corpus_path),
            },
            import_job_id=job_id,
            skip_learning_artifacts=True,
        )
        session = get_study_session(session_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_import_job(
            job_id,
            status="completed",
            stage="completed",
            message="SciFact benchmark 语料导入完成。",
            processed_files=1,
            progress=100,
            result={"session_id": session_id, "session": session, "documents": [created_document]},
        )
        _record_event_safe(
            "benchmark.scifact.import_job",
            session_id=session_id,
            user_id=user_id,
            duration_ms=duration_ms,
            metadata={"job_id": job_id, "document_id": created_document["document_id"]},
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_import_job(
            job_id,
            status="failed",
            stage="failed",
            message="SciFact benchmark 导入失败。",
            error=str(exc.detail if isinstance(exc, HTTPException) else exc),
        )
        _record_event_safe(
            "benchmark.scifact.import_job",
            status="error",
            session_id=session_id,
            user_id=user_id,
            duration_ms=duration_ms,
            metadata={"job_id": job_id},
            message=str(exc),
        )


def _run_t2retrieval_import_job(job_id: str, *, session_id: int, user_id: str, corpus_path: Path):
    started = time.perf_counter()
    try:
        _update_import_job(
            job_id,
            status="running",
            stage="reading",
            message="正在读取 T2Retrieval 中文 benchmark corpus。",
            current_file=corpus_path.name,
            progress=10,
        )
        text = corpus_path.read_text(encoding="utf-8").strip()
        created_document, _, _ = _ingest_text_resource(
            session_id=session_id,
            user_id=user_id,
            title="T2Retrieval Chinese Benchmark Corpus",
            file_name=corpus_path.name,
            file_path=str(corpus_path),
            file_type=corpus_path.suffix.lower(),
            file_size=corpus_path.stat().st_size,
            text=text,
            source_type="benchmark",
            metadata={
                "source": "t2retrieval_benchmark",
                "dataset": "t2retrieval",
                "benchmark_path": str(corpus_path),
            },
            import_job_id=job_id,
            skip_learning_artifacts=True,
        )
        session = get_study_session(session_id)
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_import_job(
            job_id,
            status="completed",
            stage="completed",
            message="T2Retrieval 中文 benchmark 语料导入完成。",
            processed_files=1,
            progress=100,
            result={"session_id": session_id, "session": session, "documents": [created_document]},
        )
        _record_event_safe(
            "benchmark.t2retrieval.import_job",
            session_id=session_id,
            user_id=user_id,
            duration_ms=duration_ms,
            metadata={"job_id": job_id, "document_id": created_document["document_id"]},
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _update_import_job(
            job_id,
            status="failed",
            stage="failed",
            message="T2Retrieval 中文 benchmark 导入失败。",
            error=str(exc.detail if isinstance(exc, HTTPException) else exc),
        )
        _record_event_safe(
            "benchmark.t2retrieval.import_job",
            status="error",
            session_id=session_id,
            user_id=user_id,
            duration_ms=duration_ms,
            metadata={"job_id": job_id},
            message=str(exc),
        )


def _build_rag_eval_cases_for_request(session_id: int, request: RetrievalEvalRequest) -> tuple[dict, list[dict], list[dict], list[dict]]:
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")
    documents = get_session_documents(session_id)
    knowledge_points = get_session_knowledge_points(session_id)
    eval_cases = [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in request.cases]
    if not eval_cases:
        eval_cases = build_session_eval_cases(
            session,
            documents,
            knowledge_points,
            limit=8,
            include_template_cases=True,
        )
    if not eval_cases:
        raise HTTPException(status_code=400, detail="No RAG evaluation cases are available for this session.")
    return session, documents, knowledge_points, eval_cases


def _write_rag_eval_log(
    *,
    job_id: str | None,
    session_id: int,
    user_id: str,
    request: RetrievalEvalRequest,
    payload: dict,
    run_id: int | None,
    duration_ms: int,
) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_job_id = job_id or "sync"
    log_path = RAG_EVAL_LOG_DIR / f"session_{session_id}_{timestamp}_{safe_job_id}.json"
    metrics = payload.get("metrics", {})
    low_quality_cases = payload.get("low_quality_cases", [])
    low_quality_summary = payload.get("low_quality_summary", {})
    log_payload = {
        "log_type": "rag_retrieval_evaluation",
        "generated_at": timestamp,
        "job_id": job_id,
        "run_id": run_id,
        "session_id": session_id,
        "user_id": user_id,
        "request": {
            "top_k": request.top_k,
            "low_quality_mrr_threshold": request.low_quality_mrr_threshold,
            "case_count": payload.get("case_count", 0),
            "retrieval_config": request.retrieval_config,
            "compare_to_original": request.compare_to_original,
            "run_ablation": request.run_ablation,
        },
        "metrics": metrics,
        "low_quality_cases": low_quality_cases,
        "low_quality_summary": low_quality_summary,
        "cases": payload.get("cases", []),
        "comparison": payload.get("comparison"),
        "ablation": payload.get("ablation"),
        "duration_ms": duration_ms,
        "llm_analysis_prompt": (
            "请分析这次 RAG 检索评测结果。重点判断：1. 召回质量是否达标；"
            "2. 低质量 query 的主要失败原因；3. Query Rewrite、Multi-Query、Rerank、Parent 回填中哪一环最可能需要优化；"
            "4. 给出下一轮可执行的 RAG 调优建议。"
        ),
    }
    log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(log_path)


def _build_rag_eval_comparison(current_payload: dict, baseline_payload: dict) -> dict:
    current_metrics = current_payload.get("metrics", {}) or {}
    baseline_metrics = baseline_payload.get("metrics", {}) or {}
    current_recall = current_metrics.get("recall_at", {}) or {}
    baseline_recall = baseline_metrics.get("recall_at", {}) or {}
    current_ndcg = current_metrics.get("ndcg_at", {}) or {}
    baseline_ndcg = baseline_metrics.get("ndcg_at", {}) or {}
    return {
        "baseline_name": "original_rag",
        "baseline_metrics": baseline_metrics,
        "baseline_low_quality_count": len(baseline_payload.get("low_quality_cases", [])),
        "delta": {
            "mrr": round(current_metrics.get("mrr", 0) - baseline_metrics.get("mrr", 0), 4),
            "recall_at_1": round(current_recall.get("1", 0) - baseline_recall.get("1", 0), 4),
            "recall_at_5": round(current_recall.get("5", 0) - baseline_recall.get("5", 0), 4),
            "ndcg_at_5": round(current_ndcg.get("5", 0) - baseline_ndcg.get("5", 0), 4),
        },
        "baseline_cases": baseline_payload.get("cases", []),
    }


def _rag_eval_metric_delta(current_metrics: dict, baseline_metrics: dict) -> dict:
    current_recall = current_metrics.get("recall_at", {}) or {}
    baseline_recall = baseline_metrics.get("recall_at", {}) or {}
    current_ndcg = current_metrics.get("ndcg_at", {}) or {}
    baseline_ndcg = baseline_metrics.get("ndcg_at", {}) or {}
    return {
        "mrr": round(current_metrics.get("mrr", 0) - baseline_metrics.get("mrr", 0), 4),
        "recall_at_1": round(current_recall.get("1", 0) - baseline_recall.get("1", 0), 4),
        "recall_at_5": round(current_recall.get("5", 0) - baseline_recall.get("5", 0), 4),
        "ndcg_at_5": round(current_ndcg.get("5", 0) - baseline_ndcg.get("5", 0), 4),
    }


def _build_rag_ablation_configs(active_config: dict | None) -> list[dict]:
    latest = resolve_retrieval_config({"mode": "latest"})
    active = resolve_retrieval_config(active_config or {"mode": "latest"})
    variants = [
        {"name": "original", "label": "原始RAG", "config": {"mode": "original"}},
        {"name": "latest", "label": "最新RAG", "config": {"mode": "latest"}},
        {
            "name": "latest_without_rewrite",
            "label": "最新RAG - Query Rewrite",
            "config": {**latest, "mode": "custom", "use_query_rewrite": False},
        },
        {
            "name": "latest_without_bm25",
            "label": "最新RAG - BM25",
            "config": {**latest, "mode": "custom", "use_bm25": False},
        },
        {
            "name": "latest_without_rerank",
            "label": "最新RAG - Rerank",
            "config": {**latest, "mode": "custom", "use_rerank": False},
        },
        {
            "name": "latest_without_multi_query",
            "label": "最新RAG - Multi-Query",
            "config": {**latest, "mode": "custom", "use_multi_query": False},
        },
        {
            "name": "latest_without_parent",
            "label": "最新RAG - Parent回填",
            "config": {**latest, "mode": "custom", "use_parent": False},
        },
    ]
    if active.get("mode") == "custom":
        variants.insert(1, {"name": "active_custom", "label": "当前自定义配置", "config": active})

    deduped = []
    seen = set()
    for variant in variants:
        resolved = resolve_retrieval_config(variant["config"])
        key = json.dumps(resolved, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({**variant, "config": resolved})
    return deduped


def _run_rag_eval_ablation(
    *,
    retriever,
    eval_cases: list[dict],
    request: RetrievalEvalRequest,
    session_context: str,
    progress_callback=None,
) -> dict:
    results = []
    variants = _build_rag_ablation_configs(request.retrieval_config)
    variant_count = max(1, len(variants))
    total_cases = max(1, len(eval_cases))
    for variant_index, variant in enumerate(variants, start=1):
        if progress_callback:
            progress_callback(variant_index, variant_count, 0, total_cases, "", variant)

        def update_variant_progress(done: int, total: int, query: str):
            if progress_callback:
                progress_callback(variant_index, variant_count, done, total, query, variant)

        variant_payload = evaluate_retrieval_cases(
            retriever,
            eval_cases,
            rewrite_query=rewrite_query,
            expand_query_to_multi_queries=expand_query_to_multi_queries,
            plan_retrieval_route=plan_retrieval_route,
            session_context=session_context,
            llm_generator=llm_generator,
            top_k=request.top_k,
            low_quality_mrr_threshold=request.low_quality_mrr_threshold,
            retrieval_config=variant["config"],
            progress_callback=update_variant_progress if progress_callback else None,
        )
        results.append(
            {
                "name": variant["name"],
                "label": variant["label"],
                "config": variant_payload.get("metrics", {}).get("retrieval_config", variant["config"]),
                "metrics": variant_payload.get("metrics", {}),
                "low_quality_count": len(variant_payload.get("low_quality_cases", [])),
                "low_quality_summary": variant_payload.get("low_quality_summary", {}),
            }
        )

    baseline = next((item for item in results if item["name"] == "original"), results[0] if results else {})
    baseline_metrics = baseline.get("metrics", {}) if baseline else {}
    for item in results:
        item["delta_vs_original"] = _rag_eval_metric_delta(item.get("metrics", {}), baseline_metrics)
    return {"baseline": "original", "variants": results}


def _run_rag_eval_job(job_id: str, *, session_id: int, request_payload: dict):
    started = time.perf_counter()
    run_id = None
    request = RetrievalEvalRequest(**request_payload)
    try:
        retriever, doc_chunks = _build_session_retriever(session_id)
        if not retriever or not doc_chunks:
            raise HTTPException(status_code=400, detail="No indexed study materials were found for this session.")

        session, documents, knowledge_points, eval_cases = _build_rag_eval_cases_for_request(session_id, request)
        _update_rag_eval_job(
            job_id,
            status="running",
            stage="starting",
            message=f"准备评测 {len(eval_cases)} 条 query。",
            total_cases=len(eval_cases),
            progress=5,
        )

        run_id = create_run(
            run_type="rag.evaluate",
            session_id=session_id,
            user_id=request.user_id,
            title="RAG 检索评测",
            input_summary=f"case_count={len(eval_cases)}",
            metadata={
                "top_k": request.top_k,
                "threshold": request.low_quality_mrr_threshold,
                "job_id": job_id,
                "retrieval_config": request.retrieval_config,
                "compare_to_original": request.compare_to_original,
                "run_ablation": request.run_ablation,
            },
        )

        def update_eval_progress(done: int, total: int, query: str):
            _update_rag_eval_job(
                job_id,
                status="running",
                stage="retrieving",
                message=f"正在评测 {done}/{total} 条 query。",
                processed_cases=done,
                total_cases=total,
                current_query=query[:180],
                progress=_rag_eval_progress(done, total),
            )

        payload = evaluate_retrieval_cases(
            retriever,
            eval_cases,
            rewrite_query=rewrite_query,
            expand_query_to_multi_queries=expand_query_to_multi_queries,
            plan_retrieval_route=plan_retrieval_route,
            session_context=_build_session_context_text(session_id),
            llm_generator=llm_generator,
            top_k=request.top_k,
            low_quality_mrr_threshold=request.low_quality_mrr_threshold,
            progress_callback=update_eval_progress,
            retrieval_config=request.retrieval_config,
        )
        if request.compare_to_original:
            _update_rag_eval_job(
                job_id,
                status="running",
                stage="baseline",
                message="正在运行原始 RAG 对比评测。",
                progress=96,
            )
            baseline_payload = evaluate_retrieval_cases(
                retriever,
                eval_cases,
                rewrite_query=rewrite_query,
                expand_query_to_multi_queries=expand_query_to_multi_queries,
                plan_retrieval_route=plan_retrieval_route,
                session_context=_build_session_context_text(session_id),
                llm_generator=llm_generator,
                top_k=request.top_k,
                low_quality_mrr_threshold=request.low_quality_mrr_threshold,
                retrieval_config={"mode": "original"},
            )
            payload["comparison"] = _build_rag_eval_comparison(payload, baseline_payload)
        if request.run_ablation:
            _update_rag_eval_job(
                job_id,
                status="running",
                stage="ablation",
                message="正在运行 RAG ablation 对比评测。",
                progress=97,
            )

            def update_ablation_progress(
                variant_index: int,
                variant_count: int,
                done: int,
                total: int,
                query: str,
                variant: dict,
            ):
                total = max(1, int(total or 1))
                variant_count = max(1, int(variant_count or 1))
                variant_fraction = ((max(1, variant_index) - 1) + (max(0, done) / total)) / variant_count
                progress = min(99, 96 + int(variant_fraction * 3))
                label = variant.get("label") or variant.get("name") or "ablation"
                _update_rag_eval_job(
                    job_id,
                    status="running",
                    stage="ablation",
                    message=f"正在运行 RAG ablation {variant_index}/{variant_count}：{label}。",
                    processed_cases=done,
                    total_cases=total,
                    current_query=(query or "")[:180],
                    progress=progress,
                    ablation_variant_index=variant_index,
                    ablation_variant_count=variant_count,
                    ablation_variant_name=variant.get("name", ""),
                    ablation_variant_label=label,
                )

            payload["ablation"] = _run_rag_eval_ablation(
                retriever=retriever,
                eval_cases=eval_cases,
                request=request,
                session_context=_build_session_context_text(session_id),
                progress_callback=update_ablation_progress,
            )
        low_quality_sample_count = save_low_quality_samples(
            session_id=session_id,
            user_id=request.user_id,
            low_quality_cases=payload.get("low_quality_cases", []),
            metrics=payload.get("metrics", {}),
            source_run_id=run_id,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_path = _write_rag_eval_log(
            job_id=job_id,
            session_id=session_id,
            user_id=request.user_id,
            request=request,
            payload=payload,
            run_id=run_id,
            duration_ms=duration_ms,
        )
        payload["log_path"] = log_path
        _record_run_step_safe(
            run_id,
            "rag.evaluate.retrieval",
            duration_ms=duration_ms,
            metadata={
                "case_count": payload.get("case_count", 0),
                "mrr": payload.get("metrics", {}).get("mrr", 0),
                "recall_at": payload.get("metrics", {}).get("recall_at", {}),
                "ndcg_at": payload.get("metrics", {}).get("ndcg_at", {}),
                "buckets": payload.get("metrics", {}).get("buckets", {}),
                "low_quality_count": len(payload.get("low_quality_cases", [])),
                "low_quality_sample_count": low_quality_sample_count,
                "log_path": log_path,
            },
        )
        _finish_run_safe(
            run_id,
            status="success",
            output_summary=f"mrr={payload.get('metrics', {}).get('mrr', 0)}",
            duration_ms=duration_ms,
            metadata={
                "mrr": payload.get("metrics", {}).get("mrr", 0),
                "recall_at": payload.get("metrics", {}).get("recall_at", {}),
                "ndcg_at": payload.get("metrics", {}).get("ndcg_at", {}),
                "buckets": payload.get("metrics", {}).get("buckets", {}),
                "low_quality_count": len(payload.get("low_quality_cases", [])),
                "low_quality_sample_count": low_quality_sample_count,
                "log_path": log_path,
            },
        )
        _record_event_safe(
            "rag.evaluate",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            metadata={
                "case_count": payload.get("case_count", 0),
                "mrr": payload.get("metrics", {}).get("mrr", 0),
                "ndcg_at": payload.get("metrics", {}).get("ndcg_at", {}),
                "low_quality_count": len(payload.get("low_quality_cases", [])),
                "low_quality_sample_count": low_quality_sample_count,
                "log_path": log_path,
                "job_id": job_id,
            },
        )
        _update_rag_eval_job(
            job_id,
            status="completed",
            stage="completed",
            message="RAG 评测完成。",
            progress=100,
            result={"session_id": session_id, **payload},
            log_path=log_path,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        if run_id is not None:
            _record_run_step_safe(
                run_id,
                "rag.evaluate.error",
                step_status="error",
                duration_ms=duration_ms,
                message=str(exc),
            )
            _finish_run_safe(run_id, status="error", output_summary=str(exc), duration_ms=duration_ms)
        _record_event_safe(
            "rag.evaluate",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            metadata={"job_id": job_id},
            message=str(exc),
        )
        _update_rag_eval_job(
            job_id,
            status="failed",
            stage="failed",
            message="RAG 评测失败。",
            error=str(exc.detail if isinstance(exc, HTTPException) else exc),
        )


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
            "sections": webpage.get("sections", []),
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
                "sections": page.get("sections", []),
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
                "section_title": metadata.get("section_title", ""),
                "heading_path": metadata.get("heading_path", ""),
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


def _build_session_context_text(session_id: int) -> str:
    session = get_study_session(session_id)
    if not session:
        return ""
    parts = [session.get("session_name", ""), session.get("topic", ""), session.get("goal", "")]
    return " | ".join([part for part in parts if part])


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


@app.get("/study_sessions/{session_id}/rag/eval_dataset_template")
async def get_rag_eval_dataset_template_endpoint(session_id: int):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")
    documents = get_session_documents(session_id)
    return {
        "session_id": session_id,
        "case_count": len(documents),
        "cases": build_eval_dataset_template(documents),
    }


@app.get("/study_sessions/{session_id}/rag/eval_cases")
async def get_rag_eval_cases_endpoint(session_id: int, limit: int = Query(8)):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")

    documents = get_session_documents(session_id)
    knowledge_points = get_session_knowledge_points(session_id)
    cases = build_session_eval_cases(
        session,
        documents,
        knowledge_points,
        limit=limit,
        include_template_cases=True,
    )
    return {"session_id": session_id, "case_count": len(cases), "cases": cases}


# @app.get("/rag/benchmarks/scifact")
# async def get_scifact_benchmark_cases_endpoint(limit: int = Query(300)):
#     benchmark_path = ROOT_DIR / "benchmarks" / "scifact" / "scifact_test_eval_cases.json"
#     if not benchmark_path.exists():
#         raise HTTPException(
#             status_code=404,
#             detail="SciFact benchmark cases were not found. Run tools/prepare_scifact_benchmark.py first.",
#         )
#     try:
#         cases = json.loads(benchmark_path.read_text(encoding="utf-8"))
#     except Exception as exc:
#         raise HTTPException(status_code=500, detail=f"Failed to load SciFact benchmark cases: {exc}")
#     cases = [item for item in cases if isinstance(item, dict) and item.get("query")]
#     cases = cases[: max(1, int(limit or 300))]
#     return {
#         "dataset": "scifact",
#         "case_count": len(cases),
#         "cases": cases,
#         "source_path": str(benchmark_path),
#     }


# 强制切片为前 10 条，忽略传入的 limit 参数，以确保评测的一致性和可控性
@app.get("/rag/benchmarks/scifact")
async def get_scifact_benchmark_cases_endpoint(limit: int = Query(10)):
    benchmark_path = ROOT_DIR / "benchmarks" / "scifact" / "scifact_test_eval_cases.json"
    if not benchmark_path.exists():
        raise HTTPException(
            status_code=404,
            detail="SciFact benchmark cases were not found. Run tools/prepare_scifact_benchmark.py first.",
        )
    try:
        cases = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load SciFact benchmark cases: {exc}")
    cases = [item for item in cases if isinstance(item, dict) and item.get("query")]

    # 强制切片为前 10 条，忽略传入的 limit
    cases = cases[:10]

    return {
        "dataset": "scifact",
        "case_count": len(cases),
        "cases": cases,
        "source_path": str(benchmark_path),
    }


@app.post("/benchmarks/scifact/import_jobs")
async def start_scifact_benchmark_import_job_endpoint(request: BenchmarkImportRequest):
    corpus_path = ROOT_DIR / "benchmarks" / "scifact" / "scifact_corpus.md"
    if not corpus_path.exists():
        raise HTTPException(
            status_code=404,
            detail="SciFact benchmark corpus was not found. Run tools/prepare_scifact_benchmark.py first.",
        )

    session = create_study_session(
        user_id=request.user_id,
        session_name="SciFact Benchmark",
        topic="Biomedical claim verification",
        goal="Run fixed RAG retrieval benchmark with BEIR SciFact.",
        tags=["benchmark", "scifact", "rag_eval"],
    )

    job = _create_import_job(
        session_id=session["id"],
        user_id=request.user_id,
        auto_create=True,
        total_files=1,
    )
    _update_import_job(
        job["job_id"],
        stage="starting",
        message="正在启动 SciFact benchmark 专用导入任务。",
        current_file=corpus_path.name,
        progress=3,
    )
    thread = threading.Thread(
        target=_run_scifact_import_job,
        kwargs={
            "job_id": job["job_id"],
            "session_id": session["id"],
            "user_id": request.user_id,
            "corpus_path": corpus_path,
        },
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"], "session_id": session["id"], "session": session}


@app.get("/rag/benchmarks/t2retrieval")
async def get_t2retrieval_benchmark_cases_endpoint(limit: int = Query(50)):
    benchmark_path = ROOT_DIR / "benchmarks" / "t2retrieval" / "t2retrieval_dev_eval_cases.json"
    if not benchmark_path.exists():
        raise HTTPException(
            status_code=404,
            detail="T2Retrieval benchmark cases were not found. Run tools/prepare_t2retrieval_benchmark.py first.",
        )
    try:
        cases = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load T2Retrieval benchmark cases: {exc}")
    cases = [item for item in cases if isinstance(item, dict) and item.get("query")]
    cases = cases[: max(1, int(limit or 50))]
    return {
        "dataset": "t2retrieval",
        "case_count": len(cases),
        "cases": cases,
        "source_path": str(benchmark_path),
    }


@app.post("/benchmarks/t2retrieval/import_jobs")
async def start_t2retrieval_benchmark_import_job_endpoint(request: BenchmarkImportRequest):
    corpus_path = ROOT_DIR / "benchmarks" / "t2retrieval" / "t2retrieval_corpus.md"
    if not corpus_path.exists():
        raise HTTPException(
            status_code=404,
            detail="T2Retrieval benchmark corpus was not found. Run tools/prepare_t2retrieval_benchmark.py first.",
        )

    session = create_study_session(
        user_id=request.user_id,
        session_name="T2Retrieval 中文 Benchmark",
        topic="中文文本检索评测",
        goal="使用 mteb/T2Retrieval 运行中文 RAG 检索 benchmark。",
        tags=["benchmark", "t2retrieval", "chinese", "rag_eval"],
    )

    job = _create_import_job(
        session_id=session["id"],
        user_id=request.user_id,
        auto_create=True,
        total_files=1,
    )
    _update_import_job(
        job["job_id"],
        stage="starting",
        message="正在启动 T2Retrieval 中文 benchmark 专用导入任务。",
        current_file=corpus_path.name,
        progress=3,
    )
    thread = threading.Thread(
        target=_run_t2retrieval_import_job,
        kwargs={
            "job_id": job["job_id"],
            "session_id": session["id"],
            "user_id": request.user_id,
            "corpus_path": corpus_path,
        },
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"], "session_id": session["id"], "session": session}


@app.get("/rag/eval_jobs/{job_id}")
async def get_rag_eval_job_endpoint(job_id: str):
    job = _get_rag_eval_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="RAG evaluation job not found.")
    return job


@app.post("/study_sessions/{session_id}/rag/evaluate_jobs")
async def start_rag_evaluation_job_endpoint(session_id: int, request: RetrievalEvalRequest):
    if not get_study_session(session_id):
        raise HTTPException(status_code=404, detail="Study session not found.")
    request_cases = [item.model_dump() for item in request.cases]
    total_cases = len(request_cases) if request_cases else 0
    job = _create_rag_eval_job(session_id=session_id, user_id=request.user_id, total_cases=total_cases)
    thread = threading.Thread(
        target=_run_rag_eval_job,
        kwargs={"job_id": job["job_id"], "session_id": session_id, "request_payload": request.model_dump()},
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"], "session_id": session_id}


@app.post("/study_sessions/{session_id}/rag/evaluate")
async def evaluate_rag_retrieval_endpoint(session_id: int, request: RetrievalEvalRequest):
    started = time.perf_counter()
    retriever, doc_chunks = _build_session_retriever(session_id)
    if not retriever or not doc_chunks:
        raise HTTPException(status_code=400, detail="No indexed study materials were found for this session.")

    session = get_study_session(session_id)
    documents = get_session_documents(session_id)
    knowledge_points = get_session_knowledge_points(session_id)
    eval_cases = [item.model_dump() for item in request.cases]
    if not eval_cases:
        eval_cases = build_session_eval_cases(
            session,
            documents,
            knowledge_points,
            limit=8,
            include_template_cases=True,
        )
    if not eval_cases:
        raise HTTPException(status_code=400, detail="No RAG evaluation cases are available for this session.")

    run_id = create_run(
        run_type="rag.evaluate",
        session_id=session_id,
        user_id=request.user_id,
        title="RAG 检索评测",
        input_summary=f"case_count={len(request.cases)}",
        metadata={
            "top_k": request.top_k,
            "threshold": request.low_quality_mrr_threshold,
            "retrieval_config": request.retrieval_config,
            "compare_to_original": request.compare_to_original,
            "run_ablation": request.run_ablation,
        },
    )

    try:
        payload = evaluate_retrieval_cases(
            retriever,
            eval_cases,
            rewrite_query=rewrite_query,
            expand_query_to_multi_queries=expand_query_to_multi_queries,
            plan_retrieval_route=plan_retrieval_route,
            session_context=_build_session_context_text(session_id),
            llm_generator=llm_generator,
            top_k=request.top_k,
            low_quality_mrr_threshold=request.low_quality_mrr_threshold,
            retrieval_config=request.retrieval_config,
        )
        if request.compare_to_original:
            baseline_payload = evaluate_retrieval_cases(
                retriever,
                eval_cases,
                rewrite_query=rewrite_query,
                expand_query_to_multi_queries=expand_query_to_multi_queries,
                plan_retrieval_route=plan_retrieval_route,
                session_context=_build_session_context_text(session_id),
                llm_generator=llm_generator,
                top_k=request.top_k,
                low_quality_mrr_threshold=request.low_quality_mrr_threshold,
                retrieval_config={"mode": "original"},
            )
            payload["comparison"] = _build_rag_eval_comparison(payload, baseline_payload)
        if request.run_ablation:
            payload["ablation"] = _run_rag_eval_ablation(
                retriever=retriever,
                eval_cases=eval_cases,
                request=request,
                session_context=_build_session_context_text(session_id),
            )
        low_quality_sample_count = save_low_quality_samples(
            session_id=session_id,
            user_id=request.user_id,
            low_quality_cases=payload.get("low_quality_cases", []),
            metrics=payload.get("metrics", {}),
            source_run_id=run_id,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_path = _write_rag_eval_log(
            job_id=None,
            session_id=session_id,
            user_id=request.user_id,
            request=request,
            payload=payload,
            run_id=run_id,
            duration_ms=duration_ms,
        )
        payload["log_path"] = log_path
        _record_run_step_safe(
            run_id,
            "rag.evaluate.retrieval",
            duration_ms=duration_ms,
            metadata={
                "case_count": payload.get("case_count", 0),
                "mrr": payload.get("metrics", {}).get("mrr", 0),
                "recall_at": payload.get("metrics", {}).get("recall_at", {}),
                "ndcg_at": payload.get("metrics", {}).get("ndcg_at", {}),
                "buckets": payload.get("metrics", {}).get("buckets", {}),
                "low_quality_count": len(payload.get("low_quality_cases", [])),
                "low_quality_sample_count": low_quality_sample_count,
                "log_path": log_path,
            },
        )
        _finish_run_safe(
            run_id,
            status="success",
            output_summary=f"mrr={payload.get('metrics', {}).get('mrr', 0)}",
            duration_ms=duration_ms,
            metadata={
                "mrr": payload.get("metrics", {}).get("mrr", 0),
                "recall_at": payload.get("metrics", {}).get("recall_at", {}),
                "ndcg_at": payload.get("metrics", {}).get("ndcg_at", {}),
                "buckets": payload.get("metrics", {}).get("buckets", {}),
                "low_quality_count": len(payload.get("low_quality_cases", [])),
                "low_quality_sample_count": low_quality_sample_count,
                "log_path": log_path,
            },
        )
        _record_event_safe(
            "rag.evaluate",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            metadata={
                "case_count": payload.get("case_count", 0),
                "mrr": payload.get("metrics", {}).get("mrr", 0),
                "ndcg_at": payload.get("metrics", {}).get("ndcg_at", {}),
                "buckets": payload.get("metrics", {}).get("buckets", {}),
                "low_quality_count": len(payload.get("low_quality_cases", [])),
                "low_quality_sample_count": low_quality_sample_count,
                "log_path": log_path,
            },
        )
        return {"session_id": session_id, **payload}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_run_step_safe(
            run_id,
            "rag.evaluate.error",
            step_status="error",
            duration_ms=duration_ms,
            message=str(exc),
        )
        _finish_run_safe(run_id, status="error", output_summary=str(exc), duration_ms=duration_ms)
        _record_event_safe(
            "rag.evaluate",
            status="error",
            session_id=session_id,
            user_id=request.user_id,
            duration_ms=duration_ms,
            message=str(exc),
        )
        raise


@app.get("/study_sessions/{session_id}/rag/quality")
async def get_rag_quality_dashboard_endpoint(session_id: int, limit: int = Query(50)):
    session = get_study_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found.")
    return build_rag_quality_dashboard(session_id=session_id, limit=limit)


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


async def _save_import_job_uploads(job_id: str, files: list[UploadFile]) -> list[dict]:
    job_dir = UPLOAD_DIR / "import_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    file_records = []
    for upload in files:
        safe_name = Path(upload.filename).name
        file_path = job_dir / safe_name
        file_bytes = await upload.read()
        file_path.write_bytes(file_bytes)
        file_records.append({"filename": safe_name, "path": str(file_path), "size": len(file_bytes)})
    return file_records


def _start_import_job_thread(job_id: str, *, session_id: int, user_id: str, file_records: list[dict]):
    thread = threading.Thread(
        target=_run_import_job,
        kwargs={
            "job_id": job_id,
            "session_id": session_id,
            "user_id": user_id,
            "file_records": file_records,
        },
        daemon=True,
    )
    thread.start()


@app.get("/import_jobs/{job_id}")
async def get_import_job_endpoint(job_id: str):
    job = _get_import_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found.")
    return job


@app.post("/study_sessions/{session_id}/documents/import_jobs")
async def start_upload_documents_job_endpoint(
    session_id: int,
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    if not get_study_session(session_id):
        raise HTTPException(status_code=404, detail="Study session not found.")
    job = _create_import_job(session_id=session_id, user_id=user_id, auto_create=False, total_files=len(files))
    _update_import_job(job["job_id"], stage="saving_uploads", message="正在保存上传文件。", progress=2)
    file_records = await _save_import_job_uploads(job["job_id"], files)
    _start_import_job_thread(job["job_id"], session_id=session_id, user_id=user_id, file_records=file_records)
    return {"job_id": job["job_id"], "session_id": session_id}


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


@app.post("/study_sessions/auto_from_documents/import_jobs")
async def start_auto_create_documents_job_endpoint(
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
    job = _create_import_job(
        session_id=placeholder_session["id"],
        user_id=user_id,
        auto_create=True,
        total_files=len(files),
    )
    _update_import_job(job["job_id"], stage="saving_uploads", message="正在保存上传文件。", progress=2)
    file_records = await _save_import_job_uploads(job["job_id"], files)
    _start_import_job_thread(
        job["job_id"],
        session_id=placeholder_session["id"],
        user_id=user_id,
        file_records=file_records,
    )
    return {"job_id": job["job_id"], "session_id": placeholder_session["id"], "session": placeholder_session}


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
                session_context = _build_session_context_text(request.session_id)
                rewrite_payload = rewrite_query(
                    request.query,
                    history=[],
                    session_context=session_context,
                    llm_generator=llm_generator,
                )
                route_payload = plan_retrieval_route(
                    request.query,
                    rewritten_query=rewrite_payload["rewritten_query"],
                    session_context=session_context,
                    mode="chat",
                )
                route_strategy = route_payload["route_strategy"]
                if route_strategy.get("use_multi_query"):
                    expanded_query_payload = expand_query_to_multi_queries(
                        original_query=request.query,
                        rewritten_query=rewrite_payload["rewritten_query"],
                        session_context=session_context,
                        llm_generator=llm_generator,
                    )
                else:
                    expanded_query_payload = {"strategy": "routed_single_query", "queries": [rewrite_payload["rewritten_query"]]}
                retrieved_results, _ = retriever.retrieve_with_debug(
                    rewrite_payload["rewritten_query"],
                    queries=expanded_query_payload["queries"],
                    vector_top_k=route_strategy.get("vector_top_k"),
                    bm25_top_k=route_strategy.get("bm25_top_k"),
                    final_top_k=route_strategy.get("final_top_k"),
                    parent_window=route_strategy.get("parent_window"),
                    parent_max_chars=route_strategy.get("parent_max_chars"),
                )
                retrieved_results, _ = build_generation_context(
                    retrieved_results,
                    max_context_chars=route_strategy.get("max_context_chars", 1800),
                    per_chunk_max_chars=route_strategy.get("per_chunk_max_chars", 420),
                )
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
        raw_history = get_user_history(request.user_id, session_id=request.session_id)[-6:]
        chat_history = []
        for item in raw_history:
            chat_history.append({"role": "user", "content": item["query"]})
            chat_history.append({"role": "assistant", "content": item["response"]})

        session_context = _build_session_context_text(request.session_id)
        rewrite_started = time.perf_counter()
        rewrite_payload = rewrite_query(
            request.query,
            history=chat_history,
            session_context=session_context,
            llm_generator=llm_generator,
        )
        route_payload = plan_retrieval_route(
            request.query,
            rewritten_query=rewrite_payload["rewritten_query"],
            session_context=session_context,
            mode="chat",
        )
        route_strategy = route_payload["route_strategy"]
        classification = route_payload["classification"]
        if route_strategy.get("use_multi_query"):
            expanded_query_payload = expand_query_to_multi_queries(
                original_query=request.query,
                rewritten_query=rewrite_payload["rewritten_query"],
                session_context=session_context,
                llm_generator=llm_generator,
            )
        else:
            expanded_query_payload = {"strategy": "routed_single_query", "queries": [rewrite_payload["rewritten_query"]]}

        run_id = create_run(
            run_type="study_chat",
            session_id=request.session_id,
            user_id=request.user_id,
            title="学习问答",
            input_summary=request.query[:160],
            metadata={
                "original_query": rewrite_payload["original_query"],
                "rewritten_query": rewrite_payload["rewritten_query"],
                "rewrite_reason": rewrite_payload["rewrite_reason"],
                "question_type": classification["question_type"],
                "question_type_confidence": classification["confidence"],
                "route_strategy": route_strategy,
                "query_strategy": expanded_query_payload["strategy"],
                "expanded_queries": expanded_query_payload["queries"],
            },
        )
        _record_run_step_safe(
            run_id,
            "query_rewrite_and_route",
            duration_ms=int((time.perf_counter() - rewrite_started) * 1000),
            metadata={
                "rewrite": rewrite_payload,
                "classification": classification,
                "route_strategy": route_strategy,
                "expanded_queries": expanded_query_payload["queries"],
            },
        )

        retrieve_started = time.perf_counter()
        retrieved_results, retrieval_debug = retriever.retrieve_with_debug(
            rewrite_payload["rewritten_query"],
            queries=expanded_query_payload["queries"],
            vector_top_k=route_strategy.get("vector_top_k"),
            bm25_top_k=route_strategy.get("bm25_top_k"),
            final_top_k=route_strategy.get("final_top_k"),
            parent_window=route_strategy.get("parent_window"),
            parent_max_chars=route_strategy.get("parent_max_chars"),
        )
        generation_results, context_debug = build_generation_context(
            retrieved_results,
            max_context_chars=route_strategy.get("max_context_chars", 1800),
            per_chunk_max_chars=route_strategy.get("per_chunk_max_chars", 420),
        )
        retrieval_debug.update(
            {
                "original_query": rewrite_payload["original_query"],
                "rewritten_query": rewrite_payload["rewritten_query"],
                "rewrite_reason": rewrite_payload["rewrite_reason"],
                "query_strategy": expanded_query_payload["strategy"],
                "expanded_queries": expanded_query_payload["queries"],
                "question_type": classification["question_type"],
                "question_type_confidence": classification["confidence"],
                "question_type_signals": classification.get("signals", []),
                "route_strategy": route_strategy,
                "context_debug": context_debug,
            }
        )
        sources = _format_sources(generation_results)
        _record_run_step_safe(
            run_id,
            "retrieve",
            duration_ms=int((time.perf_counter() - retrieve_started) * 1000),
            metadata={
                "source_count": len(sources),
                "original_query": rewrite_payload["original_query"],
                "rewritten_query": rewrite_payload["rewritten_query"],
                "query_strategy": expanded_query_payload["strategy"],
                "expanded_queries": expanded_query_payload["queries"],
                "question_type": classification["question_type"],
                "route_strategy": route_strategy,
                "context_debug": context_debug,
                "parent_debug": retrieval_debug.get("parent_debug", []),
            },
        )

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
                    generation_results,
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
                yield json.dumps(
                    {"sources": sources, "review_items": review_context["items"], "retrieval_debug": retrieval_debug},
                    ensure_ascii=False,
                ) + "\n"
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
                    metadata={
                        "evaluation_score": evaluation.get("overall_score", 0),
                        "source_count": len(sources),
                        "retrieval_debug": retrieval_debug,
                    },
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
    uvicorn.run(app, host="127.0.0.1", port=8888)
