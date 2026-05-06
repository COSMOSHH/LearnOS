import json
import mimetypes
import time
from datetime import datetime

import requests
import streamlit as st


API_BASE_URL = "http://127.0.0.1:8888"
DEFAULT_USER_ID = "learnos_local_user"
DEFAULT_IMPORT_TIMEOUT_SECONDS = 180
LARGE_IMPORT_TIMEOUT_SECONDS = 1800
LARGE_IMPORT_THRESHOLD_BYTES = 5 * 1024 * 1024

SUMMARY_TYPE_LABELS = {
    "short_summary": "简要摘要",
    "keywords": "关键词",
    "interview_takeaways": "面试要点",
}

SOURCE_TYPE_LABELS = {
    "upload": "文件",
    "webpage": "网页",
}

QUIZ_DIFFICULTY_OPTIONS = {
    "简单": "easy",
    "中等": "medium",
    "困难": "hard",
}


def get_query_param(name: str, default=None):
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def set_query_param(name: str, value):
    if value is None:
        try:
            del st.query_params[name]
        except Exception:
            pass
        return
    st.query_params[name] = str(value)


def ensure_user_id() -> str:
    return DEFAULT_USER_ID


def extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text or "请求失败。"
    return payload.get("detail") or payload.get("message") or response.text or "请求失败。"


def reset_cached_views():
    st.session_state["quiz_bundle"] = None
    st.session_state["quiz_session_id"] = None
    st.session_state["quiz_attempt"] = None
    st.session_state["quiz_attempt_session_id"] = None
    st.session_state["review_queue"] = None
    st.session_state["review_queue_session_id"] = None
    st.session_state["wrong_questions"] = None
    st.session_state["wrong_questions_filters"] = None
    st.session_state["plan_data"] = None
    st.session_state["plan_session_id"] = None
    st.session_state["report_data"] = None
    st.session_state["report_session_id"] = None
    st.session_state["evaluation_data"] = None
    st.session_state["evaluation_session_id"] = None
    st.session_state["interview_data"] = None
    st.session_state["interview_session_id"] = None
    st.session_state["agent_runs_data"] = None
    st.session_state["agent_runs_session_id"] = None
    st.session_state["event_logs"] = None
    st.session_state["event_logs_session_id"] = None
    st.session_state["rag_eval_data"] = None
    st.session_state["rag_eval_session_id"] = None
    st.session_state["rag_eval_cases"] = None
    st.session_state["rag_eval_cases_session_id"] = None
    st.session_state["rag_quality_data"] = None
    st.session_state["rag_quality_session_id"] = None


def refresh_sessions():
    response = requests.get(
        f"{API_BASE_URL}/study_sessions",
        params={"user_id": st.session_state["user_id"]},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("sessions", [])


def load_session_detail(session_id: int):
    response = requests.get(f"{API_BASE_URL}/study_sessions/{session_id}", timeout=20)
    response.raise_for_status()
    st.session_state["session_detail"] = response.json()


def summary_type_label(summary_type: str) -> str:
    return SUMMARY_TYPE_LABELS.get(summary_type, summary_type)


def parse_metadata(metadata_json: str | None) -> dict:
    if not metadata_json:
        return {}
    try:
        return json.loads(metadata_json)
    except Exception:
        return {}


def select_session(session_id: int | None):
    st.session_state["selected_session_id"] = session_id
    st.session_state["messages"] = []
    reset_cached_views()
    if session_id is None:
        st.session_state["session_detail"] = None
        set_query_param("session_id", None)
        return
    set_query_param("session_id", session_id)
    load_session_detail(session_id)


def create_blank_session():
    session_name = st.session_state.get("new_session_name_input", "").strip()
    if not session_name:
        session_name = f"空白学习会话 {datetime.now().strftime('%m-%d %H:%M')}"

    payload = {
        "user_id": st.session_state["user_id"],
        "session_name": session_name,
        "topic": "",
        "goal": "",
        "tags": [],
    }
    response = requests.post(f"{API_BASE_URL}/study_sessions", json=payload, timeout=30)
    if response.ok:
        created = response.json()["session"]
        st.session_state["new_session_name_input"] = ""
        select_session(created["id"])
        st.success("已创建空白学习会话。")
    else:
        st.error(extract_error_message(response))


def build_file_payload(uploaded_files):
    files_payload = []
    for file in uploaded_files:
        mime_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        files_payload.append(("files", (file.name, file.getvalue(), mime_type)))
    return files_payload


def get_upload_total_size(uploaded_files) -> int:
    total_size = 0
    for file in uploaded_files:
        if getattr(file, "size", None) is not None:
            total_size += int(file.size)
        else:
            total_size += len(file.getvalue())
    return total_size


def get_import_timeout(uploaded_files) -> int:
    if get_upload_total_size(uploaded_files) >= LARGE_IMPORT_THRESHOLD_BYTES:
        return LARGE_IMPORT_TIMEOUT_SECONDS
    return DEFAULT_IMPORT_TIMEOUT_SECONDS


def show_import_timeout_error(timeout_seconds: int):
    st.error(
        "导入仍在后端处理中，但前端等待超时了。"
        f"本次等待上限为 {timeout_seconds} 秒；SciFact 这类大语料切分和向量化会比较慢。"
        "可以稍等后刷新会话列表，或先用 prepare_scifact_benchmark.py 的 --max-documents 生成小样本做快速验证。"
    )


def _render_import_job_progress(job: dict, progress_bar, status_placeholder, detail_placeholder):
    progress_bar.progress(max(0, min(100, int(job.get("progress") or 0))))
    status_placeholder.caption(job.get("message") or "导入任务处理中...")

    details = [
        f"阶段：{job.get('stage', '-')}",
        f"文件：{job.get('processed_files', 0)}/{job.get('total_files', 0)}",
    ]
    if job.get("current_file"):
        details.append(f"当前文件：{job.get('current_file')}")
    if int(job.get("total_chunks") or 0) > 0:
        details.append(f"chunk：{job.get('processed_chunks', 0)}/{job.get('total_chunks', 0)}")
    detail_placeholder.caption(" | ".join(details))


def poll_import_job(job_id: str, timeout_seconds: int) -> dict | None:
    status_placeholder = st.empty()
    progress_bar = st.progress(0)
    detail_placeholder = st.empty()
    started = time.monotonic()

    while True:
        if time.monotonic() - started > timeout_seconds:
            show_import_timeout_error(timeout_seconds)
            return None

        response = requests.get(f"{API_BASE_URL}/import_jobs/{job_id}", timeout=20)
        if not response.ok:
            st.error(extract_error_message(response))
            return None

        job = response.json()
        _render_import_job_progress(job, progress_bar, status_placeholder, detail_placeholder)

        if job.get("status") == "completed":
            progress_bar.progress(100)
            return job
        if job.get("status") == "failed":
            st.error(job.get("error") or "导入失败。")
            return None

        time.sleep(1)


def start_import_job(auto_create: bool, files_payload: list, timeout_seconds: int) -> dict | None:
    if auto_create:
        url = f"{API_BASE_URL}/study_sessions/auto_from_documents/import_jobs"
    else:
        url = f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/documents/import_jobs"

    try:
        response = requests.post(
            url,
            data={"user_id": st.session_state["user_id"]},
            files=files_payload,
            timeout=DEFAULT_IMPORT_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        st.error(f"导入任务启动失败：{exc}")
        return None

    if not response.ok:
        st.error(extract_error_message(response))
        return None

    return poll_import_job(response.json()["job_id"], timeout_seconds)


def import_scifact_benchmark():
    payload = {"user_id": st.session_state["user_id"]}

    try:
        response = requests.post(
            f"{API_BASE_URL}/benchmarks/scifact/import_jobs",
            json=payload,
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        st.error(f"SciFact 导入任务启动失败：{exc}")
        return

    if not response.ok:
        st.error(extract_error_message(response))
        return

    job_payload = response.json()
    job = poll_import_job(job_payload["job_id"], LARGE_IMPORT_TIMEOUT_SECONDS)
    if not job:
        return

    session_id = (job.get("result") or {}).get("session_id") or job_payload.get("session_id")
    if session_id:
        select_session(session_id)
    st.success("SciFact benchmark 语料已完成专用导入。")


def import_t2retrieval_benchmark():
    payload = {"user_id": st.session_state["user_id"]}

    try:
        response = requests.post(
            f"{API_BASE_URL}/benchmarks/t2retrieval/import_jobs",
            json=payload,
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        st.error(f"T2Retrieval 中文导入任务启动失败：{exc}")
        return

    if not response.ok:
        st.error(extract_error_message(response))
        return

    job_payload = response.json()
    job = poll_import_job(job_payload["job_id"], LARGE_IMPORT_TIMEOUT_SECONDS)
    if not job:
        return

    session_id = (job.get("result") or {}).get("session_id") or job_payload.get("session_id")
    if session_id:
        select_session(session_id)
    st.success("T2Retrieval 中文 benchmark 语料已完成专用导入。")


def import_uploaded_files(auto_create: bool, uploaded_files):
    files_payload = build_file_payload(uploaded_files)
    timeout_seconds = get_import_timeout(uploaded_files)
    job = start_import_job(auto_create, files_payload, timeout_seconds)
    if not job:
        return

    if auto_create:
        result = job.get("result") or {}
        created = result.get("session") or {"id": job.get("session_id")}
        select_session(created["id"])
        st.success("已根据上传资料新建学习会话。")
        return

    load_session_detail(st.session_state["selected_session_id"])
    st.success("学习资料已导入当前会话。")


def import_webpage(url: str, auto_create: bool):
    payload = {
        "user_id": st.session_state["user_id"],
        "url": url.strip(),
    }
    if auto_create:
        response = requests.post(
            f"{API_BASE_URL}/study_sessions/auto_from_webpage",
            json=payload,
            timeout=180,
        )
        if response.ok:
            created = response.json()["session"]
            select_session(created["id"])
            st.success("已根据网页内容新建学习会话。")
        else:
            st.error(extract_error_message(response))
        return

    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/webpages",
        json=payload,
        timeout=180,
    )
    if response.ok:
        load_session_detail(st.session_state["selected_session_id"])
        st.success("网页资料已导入当前会话。")
    else:
        st.error(extract_error_message(response))


def import_webpage_batch(url: str, max_pages: int, auto_create: bool):
    payload = {
        "user_id": st.session_state["user_id"],
        "url": url.strip(),
        "max_pages": int(max_pages),
    }
    if auto_create:
        response = requests.post(
            f"{API_BASE_URL}/study_sessions/auto_from_webpage_batch",
            json=payload,
            timeout=240,
        )
        if response.ok:
            created = response.json()["session"]
            imported_count = int(response.json().get("imported_count", 0) or 0)
            select_session(created["id"])
            st.success(f"已根据目录页批量新建学习会话，共导入 {imported_count} 篇网页。")
        else:
            st.error(extract_error_message(response))
        return

    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/webpages/batch",
        json=payload,
        timeout=240,
    )
    if response.ok:
        imported_count = int(response.json().get("imported_count", 0) or 0)
        load_session_detail(st.session_state["selected_session_id"])
        st.success(f"已批量导入 {imported_count} 篇网页到当前会话。")
    else:
        st.error(extract_error_message(response))


def delete_selected_session():
    session_id = st.session_state.get("selected_session_id")
    if session_id is None:
        return

    response = requests.delete(
        f"{API_BASE_URL}/study_sessions/{session_id}",
        json={"user_id": st.session_state["user_id"]},
        timeout=60,
    )
    if not response.ok:
        st.error(extract_error_message(response))
        return

    remaining_sessions = refresh_sessions()
    if remaining_sessions:
        select_session(remaining_sessions[0]["id"])
    else:
        select_session(None)
    st.success("学习会话已删除。")


def stream_study_question(user_query: str, container):
    payload = {
        "user_id": st.session_state["user_id"],
        "session_id": st.session_state["selected_session_id"],
        "query": user_query,
    }
    response = requests.post(f"{API_BASE_URL}/chat", json=payload, stream=True, timeout=120)
    response.raise_for_status()

    full_answer = ""
    sources = []
    review_items = []
    retrieval_debug = {}

    with container:
        with st.chat_message("user"):
            st.write(user_query)

        with st.chat_message("assistant"):
            answer_placeholder = st.empty()
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line.decode("utf-8"))
                if "chunk" in data:
                    full_answer += data["chunk"]
                    answer_placeholder.markdown(full_answer + "▌")
                if "sources" in data:
                    sources = data["sources"]
                if "review_items" in data:
                    review_items = data["review_items"]
                if "retrieval_debug" in data:
                    retrieval_debug = data["retrieval_debug"]

            answer_placeholder.markdown(full_answer)
            render_sources(sources)
            render_review_items(review_items)
            render_retrieval_debug(retrieval_debug)

    st.session_state["messages"].append({"role": "user", "content": user_query})
    st.session_state["messages"].append(
        {
            "role": "assistant",
            "content": full_answer,
            "sources": sources,
            "review_items": review_items,
            "retrieval_debug": retrieval_debug,
        }
    )


def generate_quiz(question_count: int, difficulty_label: str):
    payload = {
        "user_id": st.session_state["user_id"],
        "question_count": question_count,
        "difficulty": QUIZ_DIFFICULTY_OPTIONS[difficulty_label],
    }
    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/quiz_sets",
        json=payload,
        timeout=120,
    )
    if response.ok:
        st.session_state["quiz_bundle"] = response.json()
        st.session_state["quiz_session_id"] = st.session_state["selected_session_id"]
        st.session_state["quiz_attempt"] = None
        st.session_state["quiz_attempt_session_id"] = st.session_state["selected_session_id"]
        st.success("已生成一套新的自测题。")
    else:
        st.error(extract_error_message(response))


def load_latest_quiz():
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/quiz_sets/latest",
        timeout=30,
    )
    if response.ok:
        payload = response.json().get("quiz")
        st.session_state["quiz_bundle"] = payload
        st.session_state["quiz_session_id"] = st.session_state["selected_session_id"]
        if payload:
            st.success("已加载最近一次测验。")
        else:
            st.info("当前会话还没有测验，先生成一套吧。")
    else:
        st.error(extract_error_message(response))


def submit_quiz_answers(quiz_set_id: int, answers: list[str]):
    payload = {
        "user_id": st.session_state["user_id"],
        "quiz_set_id": quiz_set_id,
        "answers": answers,
    }
    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/quiz_attempts",
        json=payload,
        timeout=120,
    )
    if response.ok:
        payload = response.json()
        st.session_state["quiz_attempt"] = payload
        st.session_state["quiz_attempt_session_id"] = st.session_state["selected_session_id"]
        load_session_detail(st.session_state["selected_session_id"])
        load_learning_plan(only_incomplete=st.session_state.get("plan_only_incomplete", False))
        fetch_review_queue(
            limit=st.session_state.get("review_queue_limit", 8),
            due_only=st.session_state.get("review_queue_due_only", True),
        )
        filters = st.session_state.get("wrong_questions_filters") or {}
        if filters:
            fetch_wrong_questions(
                session_id=filters.get("session_id"),
                max_score=filters.get("max_score"),
                recent_days=filters.get("recent_days"),
            )
        created_count = int(payload.get("review_items_created", 0) or 0)
        if created_count > 0:
            st.success(f"测验已提交，评分结果如下。系统已新增 {created_count} 条高优先级复习项。")
        else:
            st.success("测验已提交，评分结果如下。")
    else:
        st.error(extract_error_message(response))


def refresh_learning_report():
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/report",
        timeout=120,
    )
    if response.ok:
        st.session_state["report_data"] = response.json()["report"]
        st.session_state["report_session_id"] = st.session_state["selected_session_id"]
        st.success("学习报告已刷新。")
    else:
        st.error(extract_error_message(response))


def load_answer_evaluations(source_type: str | None = None, limit: int = 20):
    params = {"limit": limit}
    if source_type:
        params["source_type"] = source_type
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/evaluations",
        params=params,
        timeout=30,
    )
    if response.ok:
        st.session_state["evaluation_data"] = response.json()
        st.session_state["evaluation_session_id"] = st.session_state["selected_session_id"]
    else:
        st.error(extract_error_message(response))


def load_learning_plan(only_incomplete: bool = False):
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/plan",
        params={"only_incomplete": str(bool(only_incomplete)).lower()},
        timeout=120,
    )
    if response.ok:
        st.session_state["plan_data"] = response.json()["plan"]
        st.session_state["plan_session_id"] = st.session_state["selected_session_id"]
    else:
        st.error(extract_error_message(response))


def generate_learning_plan():
    payload = {"user_id": st.session_state["user_id"]}
    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/plan",
        json=payload,
        timeout=120,
    )
    if response.ok:
        st.session_state["plan_data"] = response.json()["plan"]
        st.session_state["plan_session_id"] = st.session_state["selected_session_id"]
        st.success("学习计划已生成并保存。")
    else:
        st.error(extract_error_message(response))


def update_plan_item(item_id: int, is_completed: bool):
    response = requests.patch(
        f"{API_BASE_URL}/study_plans/items/{item_id}",
        json={"is_completed": bool(is_completed)},
        timeout=30,
    )
    if response.ok:
        load_learning_plan(only_incomplete=st.session_state.get("plan_only_incomplete", False))
    else:
        st.error(extract_error_message(response))


def reprioritize_learning_plan():
    payload = {"user_id": st.session_state["user_id"]}
    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/plan/reprioritize",
        json=payload,
        timeout=120,
    )
    if response.ok:
        st.session_state["plan_data"] = response.json()["plan"]
        st.session_state["plan_session_id"] = st.session_state["selected_session_id"]
        st.success("学习计划已按当前进展重排优先级。")
    else:
        st.error(extract_error_message(response))


def fetch_review_queue(limit: int = 8, due_only: bool = True):
    params = {
        "user_id": st.session_state["user_id"],
        "limit": limit,
        "due_only": str(bool(due_only)).lower(),
    }
    if st.session_state.get("selected_session_id") is not None:
        params["session_id"] = st.session_state["selected_session_id"]
    response = requests.get(f"{API_BASE_URL}/review_queue", params=params, timeout=30)
    if response.ok:
        st.session_state["review_queue"] = response.json()["items"]
        st.session_state["review_queue_session_id"] = st.session_state.get("selected_session_id")
    else:
        st.error(extract_error_message(response))


def update_review_progress_action(item_id: int, outcome: str, notes: str = ""):
    response = requests.patch(
        f"{API_BASE_URL}/review_items/{item_id}/progress",
        json={"outcome": outcome, "notes": notes},
        timeout=30,
    )
    if response.ok:
        fetch_review_queue(
            limit=st.session_state.get("review_queue_limit", 8),
            due_only=st.session_state.get("review_queue_due_only", True),
        )
        load_learning_plan(only_incomplete=st.session_state.get("plan_only_incomplete", False))
        load_session_detail(st.session_state["selected_session_id"])
    else:
        st.error(extract_error_message(response))


def fetch_wrong_questions(session_id: int | None, max_score: float | None, recent_days: int | None):
    params = {"user_id": st.session_state["user_id"]}
    if session_id is not None:
        params["session_id"] = session_id
    if max_score is not None:
        params["max_score"] = max_score
    if recent_days is not None:
        params["recent_days"] = recent_days
    response = requests.get(f"{API_BASE_URL}/wrong_questions", params=params, timeout=30)
    if response.ok:
        st.session_state["wrong_questions"] = response.json()["items"]
        st.session_state["wrong_questions_filters"] = {
            "session_id": session_id,
            "max_score": max_score,
            "recent_days": recent_days,
        }
    else:
        st.error(extract_error_message(response))


def retry_wrong_question_action(item_id: int, answer):
    response = requests.post(
        f"{API_BASE_URL}/wrong_questions/{item_id}/retry",
        json={"user_id": st.session_state["user_id"], "answer": answer},
        timeout=60,
    )
    if response.ok:
        filters = st.session_state.get("wrong_questions_filters") or {}
        fetch_wrong_questions(
            session_id=filters.get("session_id"),
            max_score=filters.get("max_score"),
            recent_days=filters.get("recent_days"),
        )
        fetch_review_queue(
            limit=st.session_state.get("review_queue_limit", 8),
            due_only=st.session_state.get("review_queue_due_only", True),
        )
        load_learning_plan(only_incomplete=st.session_state.get("plan_only_incomplete", False))
        load_session_detail(st.session_state["selected_session_id"])
        st.success("错题已重新评分，状态已更新。")
    else:
        st.error(extract_error_message(response))


def load_recent_events(limit: int = 20, status: str | None = None):
    params = {"limit": limit}
    if st.session_state.get("selected_session_id") is not None:
        params["session_id"] = st.session_state["selected_session_id"]
    if status:
        params["status"] = status
    response = requests.get(f"{API_BASE_URL}/system/events", params=params, timeout=30)
    if response.ok:
        st.session_state["event_logs"] = response.json()["events"]
        st.session_state["event_logs_session_id"] = st.session_state.get("selected_session_id")
    else:
        st.error(extract_error_message(response))


def load_agent_runs(limit: int = 20, run_type: str | None = None):
    params = {"limit": limit}
    if run_type:
        params["run_type"] = run_type
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/agent_runs",
        params=params,
        timeout=30,
    )
    if response.ok:
        st.session_state["agent_runs_data"] = response.json()["runs"]
        st.session_state["agent_runs_session_id"] = st.session_state["selected_session_id"]
    else:
        st.error(extract_error_message(response))


def load_rag_eval_cases(limit: int = 8):
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/rag/eval_cases",
        params={"limit": limit},
        timeout=30,
    )
    if response.ok:
        payload = response.json()
        st.session_state["rag_eval_cases"] = payload.get("cases", [])
        st.session_state["rag_eval_cases_session_id"] = st.session_state["selected_session_id"]
    else:
        st.error(extract_error_message(response))


def load_scifact_eval_cases(limit: int = 300):
    response = requests.get(
        f"{API_BASE_URL}/rag/benchmarks/scifact",
        params={"limit": int(limit)},
        timeout=30,
    )
    if response.ok:
        payload = response.json()
        st.session_state["rag_eval_cases"] = payload.get("cases", [])
        st.session_state["rag_eval_cases_session_id"] = st.session_state["selected_session_id"]
        st.success(f"已加载 SciFact benchmark：{payload.get('case_count', 0)} 条。")
    else:
        st.error(extract_error_message(response))


def load_t2retrieval_eval_cases(limit: int = 50):
    response = requests.get(
        f"{API_BASE_URL}/rag/benchmarks/t2retrieval",
        params={"limit": int(limit)},
        timeout=30,
    )
    if response.ok:
        payload = response.json()
        st.session_state["rag_eval_cases"] = payload.get("cases", [])
        st.session_state["rag_eval_cases_session_id"] = st.session_state["selected_session_id"]
        st.success(f"已加载 T2Retrieval 中文 benchmark：{payload.get('case_count', 0)} 条。")
    else:
        st.error(extract_error_message(response))


def _render_rag_eval_job_progress(job: dict, progress_bar, status_placeholder, detail_placeholder):
    progress_bar.progress(max(0, min(100, int(job.get("progress") or 0))))
    status_placeholder.caption(job.get("message") or "RAG 评测处理中...")
    details = [
        f"阶段：{job.get('stage', '-')}",
        f"case：{job.get('processed_cases', 0)}/{job.get('total_cases', 0)}",
    ]
    if job.get("current_query"):
        details.append(f"当前 query：{job.get('current_query')}")
    if job.get("log_path"):
        details.append(f"日志：{job.get('log_path')}")
    detail_placeholder.caption(" | ".join(details))


def poll_rag_eval_job(job_id: str, timeout_seconds: int = 3600) -> dict | None:
    status_placeholder = st.empty()
    progress_bar = st.progress(0)
    detail_placeholder = st.empty()
    started = time.monotonic()

    while True:
        if time.monotonic() - started > timeout_seconds:
            st.error(f"RAG 评测仍在后端处理中，但前端等待超过 {timeout_seconds} 秒。")
            return None

        response = requests.get(f"{API_BASE_URL}/rag/eval_jobs/{job_id}", timeout=20)
        if not response.ok:
            st.error(extract_error_message(response))
            return None

        job = response.json()
        _render_rag_eval_job_progress(job, progress_bar, status_placeholder, detail_placeholder)

        if job.get("status") == "completed":
            progress_bar.progress(100)
            return job
        if job.get("status") == "failed":
            st.error(job.get("error") or "RAG 评测失败。")
            return None

        time.sleep(1)


def run_rag_evaluation(
    top_k: int = 5,
    threshold: float = 0.5,
    retrieval_config: dict | None = None,
    compare_to_original: bool = False,
):
    payload = {
        "user_id": st.session_state["user_id"],
        "cases": st.session_state.get("rag_eval_cases") or [],
        "top_k": int(top_k),
        "low_quality_mrr_threshold": float(threshold),
        "retrieval_config": retrieval_config or {"mode": "latest"},
        "compare_to_original": bool(compare_to_original),
    }
    try:
        response = requests.post(
            f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/rag/evaluate_jobs",
            json=payload,
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        st.error(f"RAG 评测任务启动失败：{exc}")
        return
    if not response.ok:
        st.error(extract_error_message(response))
        return

    job = poll_rag_eval_job(response.json()["job_id"])
    if not job:
        return
    st.session_state["rag_eval_data"] = job.get("result") or {}
    st.session_state["rag_eval_session_id"] = st.session_state["selected_session_id"]
    load_agent_runs(limit=20, run_type="rag.evaluate")
    load_recent_events(limit=20)
    load_rag_quality_dashboard(limit=50)
    log_path = st.session_state["rag_eval_data"].get("log_path") or job.get("log_path")
    if log_path:
        st.success(f"RAG 评测已完成。日志：{log_path}")
    else:
        st.success("RAG 评测已完成。")


def load_rag_quality_dashboard(limit: int = 50):
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/rag/quality",
        params={"limit": limit},
        timeout=30,
    )
    if response.ok:
        st.session_state["rag_quality_data"] = response.json()
        st.session_state["rag_quality_session_id"] = st.session_state["selected_session_id"]
    else:
        st.error(extract_error_message(response))


def start_interview_session(total_rounds: int, difficulty_label: str):
    payload = {
        "user_id": st.session_state["user_id"],
        "total_rounds": total_rounds,
        "difficulty": QUIZ_DIFFICULTY_OPTIONS[difficulty_label],
    }
    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/interview_sessions",
        json=payload,
        timeout=120,
    )
    if response.ok:
        st.session_state["interview_data"] = response.json()
        st.session_state["interview_session_id"] = st.session_state["selected_session_id"]
        st.success("模拟面试已开始。")
    else:
        st.error(extract_error_message(response))


def load_latest_interview_session():
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/interview_sessions/latest",
        params={"user_id": st.session_state["user_id"]},
        timeout=30,
    )
    if response.ok:
        st.session_state["interview_data"] = response.json()
        st.session_state["interview_session_id"] = st.session_state["selected_session_id"]
    else:
        st.error(extract_error_message(response))


def submit_interview_answer(answer: str):
    interview_data = get_current_interview_data()
    interview_session = (interview_data or {}).get("interview_session")
    if not interview_session:
        return
    response = requests.post(
        f"{API_BASE_URL}/interview_sessions/{interview_session['id']}/answer",
        json={"user_id": st.session_state["user_id"], "answer": answer},
        timeout=120,
    )
    if response.ok:
        st.session_state["interview_data"] = response.json()
        st.session_state["interview_session_id"] = st.session_state["selected_session_id"]
        load_answer_evaluations(limit=20)
        load_agent_runs(limit=20)
        st.success("本轮面试回答已评分。")
    else:
        st.error(extract_error_message(response))


def render_sources(sources: list[dict]):
    if not sources:
        return
    with st.expander("参考来源"):
        for source in sources:
            label = source.get("document_title") or source["source"]
            if source.get("section_title"):
                label = f"{label} / {source.get('section_title')}"
            st.caption(f"{label} | 分数={source.get('score', 0):.4f} | 分片={source.get('chunk_index')}")


def render_review_items(review_items: list[dict]):
    if not review_items:
        return
    with st.expander("复习提醒"):
        for item in review_items:
            st.caption(f"{item['topic']}: {item['summary']}")


def render_retrieval_debug(retrieval_debug: dict | None, eval_case: dict | None = None):
    if not retrieval_debug:
        return
    eval_case = eval_case or {}
    original_query = retrieval_debug.get("original_query") or eval_case.get("query") or retrieval_debug.get("query", "")
    rewritten_query = retrieval_debug.get("rewritten_query") or eval_case.get("rewritten_query") or retrieval_debug.get("query", "")
    rewrite_reason = retrieval_debug.get("rewrite_reason") or eval_case.get("rewrite_reason", "")
    with st.expander("检索调试", expanded=False):
        st.caption(
            f"原始问题：{original_query}\n"
            f"\n改写问题：{rewritten_query}\n"
            f"\n改写原因：{rewrite_reason}"
        )
        route_strategy = retrieval_debug.get("route_strategy") or {}
        if retrieval_debug.get("question_type") or route_strategy:
            st.caption(
                f"问题类型：{retrieval_debug.get('question_type', 'unknown')} | "
                f"路由：{route_strategy.get('strategy_name', 'default')} | "
                f"top-k={route_strategy.get('final_top_k', retrieval_debug.get('final_top_k', ''))}"
            )

        expanded_queries = retrieval_debug.get("expanded_queries", [])
        if expanded_queries:
            st.markdown(f"**Multi-Query 策略**: `{retrieval_debug.get('query_strategy', 'single_query')}`")
            for index, item in enumerate(expanded_queries, start=1):
                st.caption(f"Q{index}: {item}")

        vector_candidates = retrieval_debug.get("vector_candidates", [])
        if vector_candidates:
            st.markdown("**向量召回 Top-K**")
            for item in vector_candidates:
                st.caption(
                    f"[{item.get('query', '')}] {item.get('document_title', '')} / {item.get('section_title', '')} | "
                    f"分片={item.get('chunk_index')} | distance={item.get('distance')}"
                )

        bm25_candidates = retrieval_debug.get("bm25_candidates", [])
        if bm25_candidates:
            st.markdown("**BM25 Top-K**")
            for item in bm25_candidates:
                st.caption(
                    f"[{item.get('query', '')}] {item.get('document_title', '')} / {item.get('section_title', '')} | "
                    f"分片={item.get('chunk_index')} | bm25={item.get('bm25_score', 0):.4f}"
                )

        reranked_results = retrieval_debug.get("reranked_results", [])
        if reranked_results:
            st.markdown("**Rerank 结果**")
            for item in reranked_results:
                st.caption(
                    f"{item.get('document_title', '')} / {item.get('section_title', '')} | "
                    f"分片={item.get('chunk_index')} | rerank={item.get('rerank_score', 0):.4f}"
                )

        context_debug = retrieval_debug.get("context_debug") or {}
        if context_debug:
            st.markdown("**Context Compression**")
            st.caption(
                f"context before={context_debug.get('before_count', 0)} | "
                f"after={context_debug.get('after_count', 0)} | "
                f"deduped={context_debug.get('deduped', 0)} | "
                f"truncated={context_debug.get('truncated', 0)} | "
                f"context chars={context_debug.get('final_context_chars', 0)}"
            )

        parent_enriched = retrieval_debug.get("parent_enriched", 0)
        if parent_enriched:
            st.caption(f"Parent-Child 回填次数: {parent_enriched}")
        parent_debug = retrieval_debug.get("parent_debug") or []
        if parent_debug:
            st.markdown("**Parent 回填细节**")
            for item in parent_debug[:5]:
                st.caption(
                    f"{item.get('heading_path', '')} | hit={item.get('hit_chunk_index')} | "
                    f"window={item.get('window_start')}-{item.get('window_end')} | "
                    f"chars={item.get('parent_chars')} | truncated={item.get('truncated')}"
                )


def get_current_quiz_bundle():
    if st.session_state.get("quiz_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("quiz_bundle")
    return None


def get_current_quiz_attempt():
    if st.session_state.get("quiz_attempt_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("quiz_attempt")
    return None


def get_current_report():
    if st.session_state.get("report_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("report_data")
    return None


def get_current_evaluation_data():
    if st.session_state.get("evaluation_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("evaluation_data")
    return None


def get_current_plan():
    if st.session_state.get("plan_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("plan_data")
    return None


def get_current_wrong_questions():
    return st.session_state.get("wrong_questions") or []


def get_current_review_queue():
    if st.session_state.get("review_queue_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("review_queue") or []
    return []


def get_current_event_logs():
    if st.session_state.get("event_logs_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("event_logs") or []
    return []


def get_current_interview_data():
    if st.session_state.get("interview_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("interview_data")
    return None


def get_current_agent_runs():
    if st.session_state.get("agent_runs_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("agent_runs_data") or []
    return []


def get_current_rag_eval_data():
    if st.session_state.get("rag_eval_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("rag_eval_data")
    return None


def get_current_rag_eval_cases():
    if st.session_state.get("rag_eval_cases_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("rag_eval_cases") or []
    return []


def get_current_rag_quality_data():
    if st.session_state.get("rag_quality_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("rag_quality_data")
    return None


def render_quiz_answer_input(question: dict, quiz_set_id: int, index: int):
    question_type = question.get("question_type", "short_answer")
    base_key = f"quiz_answer_{quiz_set_id}_{index}"
    options = (question.get("metadata") or {}).get("options", [])
    if question_type == "single_choice":
        return st.radio(
            f"single_choice_{quiz_set_id}_{index}",
            options=options,
            key=base_key,
            index=None,
            horizontal=False,
            label_visibility="collapsed",
        )
    if question_type == "multiple_choice":
        return st.multiselect(
            f"multiple_choice_{quiz_set_id}_{index}",
            options=options,
            key=base_key,
            label_visibility="collapsed",
        )
    if question_type == "fill_blank":
        return st.text_input(
            f"fill_blank_{quiz_set_id}_{index}",
            key=base_key,
            label_visibility="collapsed",
            placeholder="在这里填写答案...",
        )
    return st.text_area(
        f"short_answer_{quiz_set_id}_{index}",
        key=base_key,
        label_visibility="collapsed",
        placeholder="在这里写下你的回答...",
        height=120,
    )


st.set_page_config(page_title="LearnOS", layout="wide")
st.markdown(
    """
    <style>
    div[data-testid="InputInstructions"] {
        display: none;
    }
    div[data-testid="stForm"] {
        border: 1px solid rgba(49, 51, 63, 0.14);
        border-radius: 16px;
        padding: 0.75rem 0.9rem 0.5rem;
        background: rgba(255, 255, 255, 0.72);
    }
    div[data-testid="stForm"] button {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("LearnOS 工作台")
st.caption("上传文件或导入网页后，系统会自动生成会话名称、学习主题和学习目标。")


if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "user_id" not in st.session_state:
    st.session_state["user_id"] = ensure_user_id()
if "selected_session_id" not in st.session_state:
    session_id_from_query = get_query_param("session_id")
    st.session_state["selected_session_id"] = int(session_id_from_query) if session_id_from_query else None
if "session_detail" not in st.session_state:
    st.session_state["session_detail"] = None
if "quiz_bundle" not in st.session_state:
    st.session_state["quiz_bundle"] = None
if "quiz_session_id" not in st.session_state:
    st.session_state["quiz_session_id"] = None
if "quiz_attempt" not in st.session_state:
    st.session_state["quiz_attempt"] = None
if "quiz_attempt_session_id" not in st.session_state:
    st.session_state["quiz_attempt_session_id"] = None
if "review_queue" not in st.session_state:
    st.session_state["review_queue"] = None
if "review_queue_session_id" not in st.session_state:
    st.session_state["review_queue_session_id"] = None
if "report_data" not in st.session_state:
    st.session_state["report_data"] = None
if "report_session_id" not in st.session_state:
    st.session_state["report_session_id"] = None
if "evaluation_data" not in st.session_state:
    st.session_state["evaluation_data"] = None
if "evaluation_session_id" not in st.session_state:
    st.session_state["evaluation_session_id"] = None
if "interview_data" not in st.session_state:
    st.session_state["interview_data"] = None
if "interview_session_id" not in st.session_state:
    st.session_state["interview_session_id"] = None
if "agent_runs_data" not in st.session_state:
    st.session_state["agent_runs_data"] = None
if "agent_runs_session_id" not in st.session_state:
    st.session_state["agent_runs_session_id"] = None
if "plan_data" not in st.session_state:
    st.session_state["plan_data"] = None
if "plan_session_id" not in st.session_state:
    st.session_state["plan_session_id"] = None
if "wrong_questions" not in st.session_state:
    st.session_state["wrong_questions"] = None
if "wrong_questions_filters" not in st.session_state:
    st.session_state["wrong_questions_filters"] = None
if "event_logs" not in st.session_state:
    st.session_state["event_logs"] = None
if "event_logs_session_id" not in st.session_state:
    st.session_state["event_logs_session_id"] = None
if "rag_eval_data" not in st.session_state:
    st.session_state["rag_eval_data"] = None
if "rag_eval_session_id" not in st.session_state:
    st.session_state["rag_eval_session_id"] = None
if "rag_eval_cases" not in st.session_state:
    st.session_state["rag_eval_cases"] = None
if "rag_eval_cases_session_id" not in st.session_state:
    st.session_state["rag_eval_cases_session_id"] = None
if "rag_quality_data" not in st.session_state:
    st.session_state["rag_quality_data"] = None
if "rag_quality_session_id" not in st.session_state:
    st.session_state["rag_quality_session_id"] = None
if "plan_only_incomplete" not in st.session_state:
    st.session_state["plan_only_incomplete"] = False
if "review_queue_limit" not in st.session_state:
    st.session_state["review_queue_limit"] = 8
if "review_queue_due_only" not in st.session_state:
    st.session_state["review_queue_due_only"] = True
if "new_session_name_input" not in st.session_state:
    st.session_state["new_session_name_input"] = ""


with st.sidebar:
    st.subheader("学习会话")
    try:
        sessions = refresh_sessions()
    except Exception as exc:
        sessions = []
        st.error(f"加载学习会话失败：{exc}")

    if sessions:
        options = {f"{item['session_name']} (#{item['id']})": item["id"] for item in sessions}
        labels = list(options.keys())
        default_label = next(
            (label for label, value in options.items() if value == st.session_state["selected_session_id"]),
            labels[0] if labels else None,
        )
        selected_label = st.selectbox(
            "选择学习会话",
            options=labels,
            index=labels.index(default_label) if default_label in labels else 0,
        )
        selected_session_id = options[selected_label]
        if selected_session_id != st.session_state["selected_session_id"]:
            select_session(selected_session_id)
        elif st.session_state["session_detail"] is None:
            select_session(selected_session_id)

        if st.button("删除当前会话", use_container_width=True):
            delete_selected_session()
            st.rerun()
    else:
        st.caption("当前还没有学习会话。")
        set_query_param("session_id", None)
        st.session_state["selected_session_id"] = None
        st.session_state["session_detail"] = None

    st.markdown("### 新建空白会话")
    st.text_input("空白会话名称（可选）", key="new_session_name_input", placeholder="例如：MySQL 锁机制速记")
    if st.button("新建空白学习会话", use_container_width=True):
        create_blank_session()

    st.markdown("### 文件导入")
    uploaded_files = st.file_uploader(
        "上传学习资料",
        type=["pdf", "docx", "txt", "md"],
        accept_multiple_files=True,
    )
    if st.button("根据上传资料新建学习会话", disabled=not uploaded_files, use_container_width=True):
        import_uploaded_files(auto_create=True, uploaded_files=uploaded_files)
    if st.button(
        "导入资料到当前会话",
        disabled=not uploaded_files or st.session_state["selected_session_id"] is None,
        use_container_width=True,
    ):
        import_uploaded_files(auto_create=False, uploaded_files=uploaded_files)

    st.markdown("### 网页导入")
    webpage_url = st.text_input(
        "学习网页链接",
        placeholder="例如：https://xiaolincoding.com/mysql/lock/how_to_lock.html",
    )
    if st.button("根据网页内容新建学习会话", disabled=not webpage_url.strip(), use_container_width=True):
        import_webpage(webpage_url, auto_create=True)
    if st.button(
        "导入网页到当前会话",
        disabled=not webpage_url.strip() or st.session_state["selected_session_id"] is None,
        use_container_width=True,
    ):
        import_webpage(webpage_url, auto_create=False)

    st.markdown("### 批量网页导入")
    batch_webpage_url = st.text_input(
        "目录页链接",
        placeholder="例如：https://xiaolincoding.com/mysql/",
    )
    batch_max_pages = st.slider("最多导入篇数", min_value=2, max_value=10, value=5)
    if st.button("根据目录页批量新建学习会话", disabled=not batch_webpage_url.strip(), use_container_width=True):
        import_webpage_batch(batch_webpage_url, max_pages=batch_max_pages, auto_create=True)
    if st.button(
        "批量导入网页到当前会话",
        disabled=not batch_webpage_url.strip() or st.session_state["selected_session_id"] is None,
        use_container_width=True,
    ):
        import_webpage_batch(batch_webpage_url, max_pages=batch_max_pages, auto_create=False)

    st.caption(f"当前用户标识：{st.session_state['user_id']}")


detail = st.session_state.get("session_detail")

with st.expander("会话概览", expanded=False):
    if detail:
        session = detail["session"]
        documents = detail.get("documents", [])
        summaries = detail.get("summaries", [])
        knowledge_points = detail.get("knowledge_points", [])
        review_items = detail.get("review_items", [])

        st.markdown(f"**会话名称： {session['session_name']}")
        if session.get("topic"):
            st.markdown(f"**学习主题： {session['topic']}")
        if session.get("goal"):
            st.markdown(f"**学习目标： {session['goal']}")

        st.divider()
        st.markdown("**资料列表**")
        if documents:
            for document in documents:
                source_type = SOURCE_TYPE_LABELS.get(document.get("source_type"), document.get("source_type") or "未知")
                st.write(f"- {document['title']}（{source_type}）")
                metadata = parse_metadata(document.get("metadata_json"))
                if document.get("source_type") == "webpage":
                    source_url = metadata.get("source_url") or document.get("file_path")
                    if source_url:
                        st.caption(source_url)
        else:
            st.caption("当前还没有导入资料。")

        st.divider()
        st.markdown("**摘要信息**")
        if summaries:
            for item in summaries:
                st.markdown(f"**{summary_type_label(item['summary_type'])}**")
                st.write(item["summary_text"])
        else:
            st.caption("当前还没有摘要。")

        st.divider()
        st.markdown("**知识点")
        if knowledge_points:
            for point in knowledge_points:
                st.markdown(f"**{point['title']}**")
                st.caption(point["description"])
        else:
            st.caption("当前还没有知识点。")

        st.divider()
        st.markdown("**复习项")
        if review_items:
            for item in review_items:
                st.markdown(f"**{item['topic']}**")
                st.caption(item["summary"])
        else:
            st.caption("当前还没有复习项。")
    else:
        st.info("请先新建空白会话，或者导入资料开始学习。")


chat_tab, eval_tab, interview_tab, quiz_tab, wrong_tab, review_tab, plan_tab, report_tab, rag_eval_tab, rag_quality_tab, logs_tab = st.tabs(
    ["学习问答", "回答评测", "模拟面试", "测验模式", "错题本", "复习调度", "学习计划", "学习报告", "RAG评测", "RAG质量", "运行观测"]
)

with chat_tab:
    st.subheader("学习问答")
    messages_container = st.container()
    with messages_container:
        for message in st.session_state["messages"]:
            with st.chat_message(message["role"]):
                st.write(message["content"])
                render_sources(message.get("sources", []))
                render_review_items(message.get("review_items", []))
                render_retrieval_debug(message.get("retrieval_debug", {}))

    streaming_container = st.container()

    with st.form("chat_input_form", clear_on_submit=True):
        submitted_query = st.text_input(
            "学习提问",
            placeholder="围绕今天的学习资料提问...",
            label_visibility="collapsed",
            disabled=st.session_state["selected_session_id"] is None,
        )
        submitted = st.form_submit_button(
            "发送",
            disabled=st.session_state["selected_session_id"] is None,
        )

    if st.session_state["selected_session_id"] is None:
        st.caption("先新建会话或导入资料后再开始提问。")

    user_query = submitted_query.strip()
    if submitted and st.session_state["selected_session_id"] is not None and user_query:
        try:
            with st.spinner("正在整理回答..."):
                stream_study_question(user_query, streaming_container)
                load_answer_evaluations(limit=20)
                load_agent_runs(limit=20, run_type="study_chat")
        except Exception as exc:
            st.error(f"请求失败：{exc}")

with eval_tab:
    st.subheader("回答评测")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再查看回答评测。")
    else:
        eval_col1, eval_col2 = st.columns([1, 1])
        with eval_col1:
            if st.button("刷新问答评测", use_container_width=True):
                load_answer_evaluations(source_type="chat", limit=20)
        with eval_col2:
            if st.button("刷新面试评测", use_container_width=True):
                load_answer_evaluations(source_type="interview", limit=20)

        evaluation_data = get_current_evaluation_data()
        if evaluation_data:
            summary = evaluation_data.get("summary", {})
            metric_col1, metric_col2, metric_col3, metric_col4, metric_col5 = st.columns(5)
            with metric_col1:
                st.metric("评测条数", summary.get("count", 0))
            with metric_col2:
                st.metric("总分", summary.get("overall_score", 0))
            with metric_col3:
                st.metric("准确性", summary.get("accuracy_score", 0))
            with metric_col4:
                st.metric("资料依据", summary.get("grounding_score", 0))
            with metric_col5:
                st.metric("表达清晰度", summary.get("clarity_score", 0))

            for item in evaluation_data.get("items", []):
                with st.expander(f"{item.get('source_type', 'chat')} | 总分 {item.get('overall_score', 0)} | {item.get('created_at', '')}", expanded=False):
                    st.markdown(f"**问题**：{item.get('query_text', '')}")
                    st.markdown(f"**回答**：{item.get('answer_text', '')}")
                    st.caption(
                        f"准确性={item.get('accuracy_score', 0)} | "
                        f"资料依据={item.get('grounding_score', 0)} | "
                        f"完整性={item.get('completeness_score', 0)} | "
                        f"清晰度={item.get('clarity_score', 0)}"
                    )
                    st.write(item.get("feedback_text", ""))
                    metadata = item.get("metadata", {})
                    if metadata.get("strengths"):
                        st.markdown("**优点**")
                        for text in metadata.get("strengths", []):
                            st.caption(text)
                    if metadata.get("risks"):
                        st.markdown("**风险**")
                        for text in metadata.get("risks", []):
                            st.caption(text)
                    if metadata.get("next_actions"):
                        st.markdown("**下一步建议")
                        for text in metadata.get("next_actions", []):
                            st.caption(text)
        else:
            st.caption("先进行学习问答或模拟面试，再刷新这里查看回答质量评测。")

with interview_tab:
    st.subheader("模拟面试")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再开始模拟面试。")
    else:
        interview_col1, interview_col2 = st.columns([1, 1])
        with interview_col1:
            interview_rounds = st.slider("面试轮数", min_value=2, max_value=5, value=3)
        with interview_col2:
            interview_difficulty = st.selectbox("面试难度", options=list(QUIZ_DIFFICULTY_OPTIONS.keys()), index=1, key="interview_difficulty")

        interview_action_col1, interview_action_col2 = st.columns([1, 1])
        with interview_action_col1:
            if st.button("开始新一轮模拟面试", use_container_width=True):
                start_interview_session(interview_rounds, interview_difficulty)
        with interview_action_col2:
            if st.button("加载最近一次模拟面试", use_container_width=True):
                load_latest_interview_session()

        interview_data = get_current_interview_data()
        if interview_data and interview_data.get("interview_session"):
            interview_session = interview_data["interview_session"]
            st.markdown(f"**{interview_session.get('title', '模拟面试')}**")
            if interview_session.get("intro_text"):
                st.write(interview_session["intro_text"])
            st.caption(
                f"状态：{interview_session.get('status', 'active')} | "
                f"轮数：{interview_session.get('current_round', 1)} / {interview_session.get('total_rounds', 0)} | "
                f"难度：{interview_session.get('difficulty', 'medium')}"
            )

            turns = interview_data.get("turns", [])
            current_turn = next((turn for turn in turns if not turn.get("answer_text")), None)
            if current_turn is not None:
                st.markdown(f"**褰撳墠闂锛堢 {current_turn.get('round_index', 1)} 杞級**")
                st.write(current_turn.get("question_text", ""))
                interview_answer = st.text_area(
                    "面试回答",
                    key=f"interview_answer_{interview_session['id']}_{current_turn.get('round_index', 1)}",
                    placeholder="像技术面试一样回答：先结论，再解释原理、场景和取舍。",
                    height=150,
                )
                if st.button("提交本轮回答", use_container_width=True):
                    submit_interview_answer(interview_answer)
                    st.rerun()

            st.divider()
            st.markdown("**面试过程**")
            for turn in turns:
                with st.expander(f"第 {turn.get('round_index')} 轮 | 得分 {turn.get('score', 0)}", expanded=False):
                    st.markdown(f"**问题**：{turn.get('question_text', '')}")
                    if turn.get("answer_text"):
                        st.markdown(f"**回答**：{turn.get('answer_text', '')}")
                        st.caption(turn.get("feedback_text", ""))
                        metadata = turn.get("metadata", {})
                        if metadata.get("ideal_answer"):
                            st.markdown("**理想回答要点**")
                            st.write(metadata.get("ideal_answer"))
                        if turn.get("follow_up_question"):
                            st.markdown("**建议追问**")
                            st.write(turn.get("follow_up_question"))

            if interview_data.get("summary"):
                summary = interview_data["summary"]
                st.divider()
                st.markdown("**面试总结**")
                st.write(summary.get("overview", ""))
                st.metric("平均分", summary.get("average_score", 0))
                st.markdown("**优势**")
                for text in summary.get("strengths", []):
                    st.caption(text)
                st.markdown("**风险**")
                for text in summary.get("risks", []):
                    st.caption(text)
                st.markdown("**下一步建议")
                for text in summary.get("next_actions", []):
                    st.caption(text)
        else:
            st.caption("点击上方按钮开始一轮模拟面试，系统会基于当前会话资料自动生成问题。")

with quiz_tab:
    st.subheader("测验模式")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再开始自测。")
    else:
        quiz_col1, quiz_col2 = st.columns([1, 1])
        with quiz_col1:
            question_count = st.slider("题目数量", min_value=2, max_value=6, value=3)
        with quiz_col2:
            difficulty_label = st.selectbox("难度", options=list(QUIZ_DIFFICULTY_OPTIONS.keys()), index=1)

        action_col1, action_col2 = st.columns([1, 1])
        with action_col1:
            if st.button("生成新测验", use_container_width=True):
                generate_quiz(question_count=question_count, difficulty_label=difficulty_label)
        with action_col2:
            if st.button("加载最近一次测验", use_container_width=True):
                load_latest_quiz()

        quiz_bundle = get_current_quiz_bundle()
        if quiz_bundle:
            st.markdown(f"**{quiz_bundle['title']}**")
            st.caption(f"难度：{quiz_bundle.get('difficulty', 'medium')} | 题目数：{len(quiz_bundle.get('questions', []))}")
            if quiz_bundle.get("instructions"):
                st.write(quiz_bundle["instructions"])

            answers = []
            for index, question in enumerate(quiz_bundle.get("questions", []), start=1):
                st.markdown(f"**第 {index} 题**")
                st.caption(
                    {
                        "single_choice": "题型：单选题",
                        "multiple_choice": "题型：多选题",
                        "fill_blank": "题型：填空题",
                    }.get(question.get("question_type", "short_answer"), "题型：简答题")
                )
                st.write(question.get("question_text", ""))
                metadata = question.get("metadata") or {}
                if metadata.get("options"):
                    for option_index, option in enumerate(metadata.get("options", []), start=1):
                        st.caption(f"{chr(64 + option_index)}. {option}")
                answers.append(render_quiz_answer_input(question, quiz_bundle["quiz_set_id"], index))

            if st.button("提交测验并评分", key=f"submit_quiz_{quiz_bundle['quiz_set_id']}", use_container_width=True):
                submit_quiz_answers(quiz_bundle["quiz_set_id"], answers)

        quiz_attempt = get_current_quiz_attempt()
        if quiz_attempt:
            result = quiz_attempt.get("result", quiz_attempt.get("result", {})) or quiz_attempt.get("result", {})
            if not result and "result" not in quiz_attempt:
                result = quiz_attempt
            st.divider()
            st.markdown("**最近一次评分结果**")
            st.write(f"总分：{result.get('total_score', 0)} / {result.get('max_total_score', 0)}")
            st.caption(result.get("overall_feedback", ""))
            for item in result.get("item_feedback", []):
                st.markdown(f"**第 {item.get('question_index')} 题：{item.get('score')} / {item.get('max_score')}**")
                st.write(item.get("feedback", ""))
                if item.get("suggestion"):
                    st.caption(f"建议：{item['suggestion']}")

with wrong_tab:
    st.subheader("错题本")
    session_filter_options = {"全部会话": None}
    for item in sessions:
        session_filter_options[f"{item['session_name']} (#{item['id']})"] = item["id"]

    filter_col1, filter_col2, filter_col3 = st.columns([1.2, 1, 1])
    with filter_col1:
        selected_wrong_session_label = st.selectbox("会话筛选", options=list(session_filter_options.keys()))
    with filter_col2:
        selected_max_score = st.selectbox("分数筛选", options=["3 分及以下", "2 分及以下", "1 分及以下", "全部"], index=0)
    with filter_col3:
        selected_recent_label = st.selectbox("时间筛选", options=["最近 7 天", "最近 30 天", "全部"], index=0)

    max_score_mapping = {"3 分及以下": 3.0, "2 分及以下": 2.0, "1 分及以下": 1.0, "全部": None}
    recent_days_mapping = {"最近 7 天": 7, "最近 30 天": 30, "全部": None}
    selected_wrong_session_id = session_filter_options[selected_wrong_session_label]
    selected_max_score_value = max_score_mapping[selected_max_score]
    selected_recent_days = recent_days_mapping[selected_recent_label]

    if st.button("刷新错题本", use_container_width=True):
        fetch_wrong_questions(
            session_id=selected_wrong_session_id,
            max_score=selected_max_score_value,
            recent_days=selected_recent_days,
        )

    wrong_questions = get_current_wrong_questions()
    if wrong_questions:
        st.caption(f"当前共筛出 {len(wrong_questions)} 条错题记录。")
        for item in wrong_questions:
            session_text = item.get("session_name") or f"会话 #{item.get('session_id')}"
            score_text = f"{item.get('score', 0)} / {item.get('max_score', 5)}"
            status_text = item.get("review_status", "new")
            title = item.get("question_text") or item.get("topic") or "未命名错题"
            with st.expander(f"{title} | {score_text} | {status_text} | {session_text}", expanded=False):
                st.markdown(f"**题目**：{item.get('question_text') or item.get('topic')}")
                st.caption(
                    f"记录时间：{item.get('created_at', '')} | "
                    f"重练次数：{item.get('retry_count', 0)} | "
                    f"最佳分数：{item.get('best_score', 0)} | "
                    f"当前优先级：{item.get('queue_score', item.get('priority_score', 0))}"
                )
                st.markdown(f"**评分反馈**：{item.get('feedback', item.get('summary', ''))}")
                if item.get("suggestion"):
                    st.markdown(f"**改进建议**：{item['suggestion']}")
                if item.get("reference_answer"):
                    st.markdown("**参考答案要点**")
                    st.write(item["reference_answer"])
                options = item.get("question_options") or []
                question_type = item.get("question_type", "short_answer")
                retry_key = f"wrong_retry_{item['id']}"
                if question_type == "single_choice":
                    retry_answer = st.radio(
                        "重新作答",
                        options=options,
                        key=retry_key,
                        index=None,
                        label_visibility="collapsed",
                    )
                elif question_type == "multiple_choice":
                    retry_answer = st.multiselect(
                        "重新作答",
                        options=options,
                        key=retry_key,
                        label_visibility="collapsed",
                    )
                elif question_type == "fill_blank":
                    retry_answer = st.text_input(
                        "重新作答",
                        key=retry_key,
                        label_visibility="collapsed",
                        placeholder="重新填写答案...",
                    )
                else:
                    retry_answer = st.text_area(
                        "重新作答",
                        key=retry_key,
                        label_visibility="collapsed",
                        placeholder="重新组织并写下你的回答...",
                        height=100,
                    )
                if st.button("重新练习并评分", key=f"retry_submit_{item['id']}", use_container_width=True):
                    retry_wrong_question_action(item["id"], retry_answer)
                    st.rerun()
                if item.get("retry_history"):
                    st.markdown("**最近状态演进")
                    for history in item.get("retry_history", []):
                        st.caption(
                            f"{history.get('created_at') or history.get('retried_at', '')} | "
                            f"状态：{history.get('status', '')} | "
                            f"得分={history.get('total_score', history.get('score', 0))}"
                        )
    else:
        st.caption("点击上方按钮，按会话、分数和时间筛选当前错题。")

with review_tab:
    st.subheader("复习调度")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再开始复习调度。")
    else:
        review_col1, review_col2 = st.columns([1, 1])
        with review_col1:
            st.session_state["review_queue_limit"] = st.slider(
                "队列数量",
                min_value=3,
                max_value=12,
                value=st.session_state.get("review_queue_limit", 8),
            )
        with review_col2:
            st.session_state["review_queue_due_only"] = st.checkbox(
                "只看到期或即将到期",
                value=st.session_state.get("review_queue_due_only", True),
            )
        if st.button("刷新复习队列", use_container_width=True):
            fetch_review_queue(
                limit=st.session_state["review_queue_limit"],
                due_only=st.session_state["review_queue_due_only"],
            )

        review_queue = get_current_review_queue()
        if review_queue:
            st.caption(f"当前共拉取 {len(review_queue)} 条复习项，已按调度优先级排序。")
            for item in review_queue:
                with st.container(border=True):
                    st.markdown(f"**{item.get('topic', '未命名复习项')}**")
                    st.caption(
                        f"队列分数：{item.get('queue_score', 0)} | "
                        f"状态：{item.get('review_status', 'new')} | "
                        f"到期状态：{item.get('due_state', 'unknown')} | "
                        f"掌握度：{item.get('mastery_level', 0)}"
                    )
                    st.write(item.get("summary", ""))
                    action_cols = st.columns(4)
                    action_specs = [("Again", "again"), ("Hard", "hard"), ("Good", "good"), ("Easy", "easy")]
                    for action_col, (label, outcome) in zip(action_cols, action_specs):
                        with action_col:
                            if st.button(label, key=f"review_{item['id']}_{outcome}", use_container_width=True):
                                update_review_progress_action(item["id"], outcome)
                                st.rerun()
        else:
            st.caption("点击上方按钮刷新复习队列，系统会按复习优先级自动排序。")

with plan_tab:
    st.subheader("学习计划")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再生成学习计划。")
    else:
        plan_action_col1, plan_action_col2, plan_action_col3 = st.columns([1, 1, 1])
        with plan_action_col1:
            if st.button("生成 / 刷新学习计划", use_container_width=True):
                generate_learning_plan()
        with plan_action_col2:
            only_incomplete = st.checkbox("今天只看未完成项", key="plan_only_incomplete")
            if st.button("加载已保存学习计划", use_container_width=True):
                load_learning_plan(only_incomplete=only_incomplete)
        with plan_action_col3:
            if st.button("按当前进展重排优先级", use_container_width=True):
                reprioritize_learning_plan()

        plan = get_current_plan()
        if plan:
            st.markdown(f"**{plan.get('title', '学习计划')}**")
            st.write(plan.get("overview", ""))
            progress = plan.get("progress", {})
            metric_col1, metric_col2, metric_col3 = st.columns(3)
            with metric_col1:
                st.metric("计划项总数", progress.get("total_items", 0))
            with metric_col2:
                st.metric("已完成", progress.get("completed_items", 0))
            with metric_col3:
                st.metric("完成率", f"{progress.get('completion_rate', 0)}%")

            plan_col1, plan_col2 = st.columns(2)
            with plan_col1:
                st.markdown("**今天学什么")
                for item in plan.get("today_focus", []):
                    value = st.checkbox(
                        item["text"],
                        value=bool(item.get("is_completed", False)),
                        key=f"plan_item_{item['id']}",
                    )
                    if value != bool(item.get("is_completed", False)):
                        update_plan_item(item["id"], value)
                        st.rerun()
                    st.caption(f"优先级：{item.get('priority_score', 0)} | {item.get('metadata', {}).get('reason', '')}")

                st.markdown("**先复习什么")
                for item in plan.get("priority_review", []):
                    value = st.checkbox(
                        item["text"],
                        value=bool(item.get("is_completed", False)),
                        key=f"plan_item_{item['id']}",
                    )
                    if value != bool(item.get("is_completed", False)):
                        update_plan_item(item["id"], value)
                        st.rerun()
                    st.caption(f"优先级：{item.get('priority_score', 0)} | {item.get('metadata', {}).get('reason', '')}")

            with plan_col2:
                st.markdown("**下一步问什么")
                for item in plan.get("next_questions", []):
                    value = st.checkbox(
                        item["text"],
                        value=bool(item.get("is_completed", False)),
                        key=f"plan_item_{item['id']}",
                    )
                    if value != bool(item.get("is_completed", False)):
                        update_plan_item(item["id"], value)
                        st.rerun()
                    st.caption(f"优先级：{item.get('priority_score', 0)} | {item.get('metadata', {}).get('reason', '')}")

                st.markdown("**行动步骤**")
                for item in plan.get("action_steps", []):
                    value = st.checkbox(
                        item["text"],
                        value=bool(item.get("is_completed", False)),
                        key=f"plan_item_{item['id']}",
                    )
                    if value != bool(item.get("is_completed", False)):
                        update_plan_item(item["id"], value)
                        st.rerun()
                    st.caption(f"优先级：{item.get('priority_score', 0)} | {item.get('metadata', {}).get('reason', '')}")
        else:
            st.caption("点击上方按钮生成并保存计划，或加载当前会话最近一次已保存计划。")

with report_tab:
    st.subheader("学习报告")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再生成学习报告。")
    else:
        if st.button("生成 / 刷新学习报告", use_container_width=True):
            refresh_learning_report()

        report = get_current_report()
        if report:
            st.markdown(f"**{report.get('title', '学习报告')}**")
            st.write(report.get("overview", ""))

            left_col, right_col = st.columns(2)
            with left_col:
                st.markdown("**进展快照**")
                for item in report.get("progress_snapshot", []):
                    st.write(f"- {item}")

                st.markdown("**褰撳墠优势**")
                for item in report.get("strengths", []):
                    st.write(f"- {item}")

                st.markdown("**面试表达重点**")
                for item in report.get("interview_focus", []):
                    st.write(f"- {item}")

            with right_col:
                st.markdown("**当前风险**")
                for item in report.get("risks", []):
                    st.write(f"- {item}")

                st.markdown("**下一步建议")
                for item in report.get("next_actions", []):
                    st.write(f"- {item}")
        else:
            st.caption("点击上方按钮生成当前学习会话的复盘报告。")

with rag_eval_tab:
    st.subheader("RAG评测")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再运行 RAG 评测。")
        no_session_col1, no_session_col2 = st.columns([1, 1])
        with no_session_col1:
            if st.button("新建并导入SciFact Benchmark", use_container_width=True):
                import_scifact_benchmark()
        with no_session_col2:
            if st.button("新建并导入中文T2 Benchmark", use_container_width=True):
                import_t2retrieval_benchmark()
    else:
        rag_eval_col1, rag_eval_col2 = st.columns([1, 1])
        with rag_eval_col1:
            rag_eval_top_k = st.slider("评测 top-k", min_value=1, max_value=8, value=5)
        with rag_eval_col2:
            rag_eval_threshold = st.slider("低质量阈值(MRR)", min_value=0.1, max_value=1.0, value=0.5, step=0.1)

        st.markdown("**RAG 对比配置**")
        rag_mode_label = st.radio(
            "评测模式",
            ["最新RAG", "原始RAG", "自定义开关"],
            horizontal=True,
            index=0,
        )
        if rag_mode_label == "原始RAG":
            rag_eval_retrieval_config = {"mode": "original"}
            rag_eval_compare_original = False
            st.caption("原始RAG：仅向量召回，不启用 Query Rewrite、动态路由、Multi-Query、BM25、Rerank 和 Parent 回填。")
        elif rag_mode_label == "自定义开关":
            custom_col1, custom_col2, custom_col3 = st.columns(3)
            with custom_col1:
                use_query_rewrite = st.checkbox("Query Rewrite", value=True)
                use_dynamic_route = st.checkbox("动态路由", value=True)
            with custom_col2:
                use_multi_query = st.checkbox("Multi-Query", value=True)
                use_bm25 = st.checkbox("BM25", value=True)
            with custom_col3:
                use_rerank = st.checkbox("Rerank", value=True)
                use_parent = st.checkbox("Parent 回填", value=True)
            rag_eval_retrieval_config = {
                "mode": "custom",
                "use_query_rewrite": use_query_rewrite,
                "use_dynamic_route": use_dynamic_route,
                "use_multi_query": use_multi_query,
                "use_bm25": use_bm25,
                "use_rerank": use_rerank,
                "use_parent": use_parent,
            }
            rag_eval_compare_original = st.checkbox("同时跑原始RAG作为 baseline", value=True)
        else:
            rag_eval_retrieval_config = {"mode": "latest"}
            rag_eval_compare_original = st.checkbox("同时跑原始RAG作为 baseline", value=True)

        rag_action_col1, rag_action_col2, rag_action_col3, rag_action_col4 = st.columns([1, 1, 1, 1])
        with rag_action_col1:
            if st.button("预览默认评测集", use_container_width=True):
                load_rag_eval_cases(limit=8)
        with rag_action_col2:
            if st.button("加载SciFact评测集", use_container_width=True):
                load_scifact_eval_cases(limit=300)
        with rag_action_col3:
            if st.button("新建并导入SciFact", use_container_width=True):
                import_scifact_benchmark()
        with rag_action_col4:
            if st.button("运行RAG评测", use_container_width=True):
                run_rag_evaluation(
                    top_k=rag_eval_top_k,
                    threshold=rag_eval_threshold,
                    retrieval_config=rag_eval_retrieval_config,
                    compare_to_original=rag_eval_compare_original,
                )
        zh_action_col1, zh_action_col2 = st.columns([1, 1])
        with zh_action_col1:
            t2_case_limit = st.number_input(
                "中文T2评测条数",
                min_value=1,
                max_value=200,
                value=50,
                step=10,
            )
            if st.button("加载中文T2评测集", use_container_width=True):
                load_t2retrieval_eval_cases(limit=t2_case_limit)
        with zh_action_col2:
            if st.button("新建并导入中文T2", use_container_width=True):
                import_t2retrieval_benchmark()

        rag_eval_cases = get_current_rag_eval_cases()
        if rag_eval_cases:
            st.markdown("**当前评测集**")
            st.caption(f"已为当前会话准备 {len(rag_eval_cases)} 条评测样例。")
            for index, case in enumerate(rag_eval_cases, start=1):
                with st.expander(f"Case {index}: {case.get('query', '')}", expanded=False):
                    st.caption(json.dumps(case, ensure_ascii=False))

        rag_eval_data = get_current_rag_eval_data()
        if rag_eval_data:
            metrics = rag_eval_data.get("metrics", {})
            if rag_eval_data.get("log_path"):
                st.caption(f"评测日志：{rag_eval_data.get('log_path')}")
            active_config = metrics.get("retrieval_config") or {}
            if active_config:
                enabled_items = [
                    name
                    for name, enabled in [
                        ("Query Rewrite", active_config.get("use_query_rewrite")),
                        ("动态路由", active_config.get("use_dynamic_route")),
                        ("Multi-Query", active_config.get("use_multi_query")),
                        ("BM25", active_config.get("use_bm25")),
                        ("Rerank", active_config.get("use_rerank")),
                        ("Parent 回填", active_config.get("use_parent")),
                    ]
                    if enabled
                ]
                st.caption(
                    f"当前模式：{active_config.get('mode', 'latest')} | "
                    f"启用：{', '.join(enabled_items) if enabled_items else '仅向量召回'}"
                )
            metric_col1, metric_col2, metric_col3, metric_col4, metric_col5 = st.columns(5)
            with metric_col1:
                st.metric("Case 数", rag_eval_data.get("case_count", 0))
            with metric_col2:
                st.metric("MRR", metrics.get("mrr", 0))
            with metric_col3:
                st.metric("Recall@1", (metrics.get("recall_at", {}) or {}).get("1", 0))
            with metric_col4:
                st.metric("Recall@5", (metrics.get("recall_at", {}) or {}).get("5", 0))
            with metric_col5:
                st.metric("NDCG@5", (metrics.get("ndcg_at", {}) or {}).get("5", 0))

            comparison = rag_eval_data.get("comparison") or {}
            if comparison:
                st.markdown("**与原始RAG对比**")
                baseline_metrics = comparison.get("baseline_metrics") or {}
                baseline_recall = baseline_metrics.get("recall_at") or {}
                baseline_ndcg = baseline_metrics.get("ndcg_at") or {}
                delta = comparison.get("delta") or {}
                cmp_col1, cmp_col2, cmp_col3, cmp_col4 = st.columns(4)
                with cmp_col1:
                    st.metric("MRR", metrics.get("mrr", 0), delta.get("mrr", 0))
                    st.caption(f"原始：{baseline_metrics.get('mrr', 0)}")
                with cmp_col2:
                    st.metric("Recall@1", (metrics.get("recall_at", {}) or {}).get("1", 0), delta.get("recall_at_1", 0))
                    st.caption(f"原始：{baseline_recall.get('1', 0)}")
                with cmp_col3:
                    st.metric("Recall@5", (metrics.get("recall_at", {}) or {}).get("5", 0), delta.get("recall_at_5", 0))
                    st.caption(f"原始：{baseline_recall.get('5', 0)}")
                with cmp_col4:
                    st.metric("NDCG@5", (metrics.get("ndcg_at", {}) or {}).get("5", 0), delta.get("ndcg_at_5", 0))
                    st.caption(f"原始：{baseline_ndcg.get('5', 0)}")

            low_quality_cases = rag_eval_data.get("low_quality_cases", [])
            buckets = metrics.get("buckets") or {}
            if buckets:
                st.markdown("**问题类型分桶指标**")
                st.json(buckets)

            st.markdown("**低质量 Query 分析**")
            if low_quality_cases:
                for item in low_quality_cases:
                    with st.expander(f"{item.get('query', '')} | {item.get('reason', '')}", expanded=False):
                        st.caption(
                            f"rewritten={item.get('rewritten_query', '')} | "
                            f"reciprocal_rank={item.get('reciprocal_rank', 0)}"
                        )
                        st.caption(json.dumps(item.get("top1", {}), ensure_ascii=False))
            else:
                st.caption("当前评测结果里没有低质量 query。")

            st.markdown("**评测明细**")
            for item in rag_eval_data.get("cases", []):
                with st.expander(
                    f"{item.get('query', '')} | rank={item.get('first_hit_rank')} | rr={item.get('reciprocal_rank', 0)}",
                    expanded=False,
                ):
                    st.caption(
                        f"rewritten={item.get('rewritten_query', '')} | "
                        f"strategy={item.get('query_strategy', 'single_query')}"
                    )
                    render_retrieval_debug(item.get("retrieval_debug", {}), eval_case=item)
        else:
            st.caption("先预览默认评测集，再运行 RAG 评测查看 Recall@k、MRR 和低质量 query。")

with rag_quality_tab:
    st.subheader("RAG质量")
    if st.session_state["selected_session_id"] is None:
        st.info("先选择学习会话后再查看 RAG 质量。")
    else:
        if st.button("刷新RAG质量看板", use_container_width=True):
            load_rag_quality_dashboard(limit=50)

        quality_data = get_current_rag_quality_data()
        if quality_data:
            summary = quality_data.get("summary", {})
            q_col1, q_col2, q_col3, q_col4, q_col5 = st.columns(5)
            with q_col1:
                st.metric("评测次数", summary.get("eval_run_count", 0))
            with q_col2:
                st.metric("平均MRR", summary.get("avg_mrr", 0))
            with q_col3:
                st.metric("平均Recall@1", summary.get("avg_recall_at_1", 0))
            with q_col4:
                st.metric("平均NDCG@5", summary.get("avg_ndcg_at_5", 0))
            with q_col5:
                st.metric("低质样本", summary.get("low_quality_sample_count", 0))

            distributions = quality_data.get("distributions", {})
            dist_left, dist_right = st.columns([1, 1])
            with dist_left:
                st.markdown("**问题类型分布**")
                question_type_dist = distributions.get("question_type") or distributions.get("low_quality_question_type") or {}
                if question_type_dist:
                    st.bar_chart(question_type_dist)
                else:
                    st.caption("暂无问题类型数据。")
            with dist_right:
                st.markdown("**路由策略分布**")
                route_dist = distributions.get("route_strategy") or {}
                if route_dist:
                    st.bar_chart(route_dist)
                else:
                    st.caption("暂无路由策略数据。")

            reason_dist = distributions.get("low_quality_reason") or {}
            if reason_dist:
                st.markdown("**低质原因分布**")
                st.bar_chart(reason_dist)

            low_quality_samples = quality_data.get("low_quality_samples", [])
            st.markdown("**低质 Query 样本库**")
            if low_quality_samples:
                for item in low_quality_samples:
                    with st.expander(f"{item.get('query_text', '')} | {item.get('reason', '')}", expanded=False):
                        st.caption(
                            f"type={item.get('question_type', 'unknown')} | "
                            f"rr={item.get('reciprocal_rank', 0)} | "
                            f"created={item.get('created_at', '')}"
                        )
                        if item.get("rewritten_query"):
                            st.write(item.get("rewritten_query"))
                        if item.get("top1"):
                            st.caption(json.dumps(item.get("top1", {}), ensure_ascii=False))
            else:
                st.caption("暂无沉淀的低质 query。")

            trend = quality_data.get("low_quality_trend", [])
            if trend:
                st.markdown("**近期评测趋势**")
                st.json(trend)
        else:
            st.caption("点击上方按钮加载当前会话的 RAG 质量看板。")

with logs_tab:
    st.subheader("运行观测")
    logs_col1, logs_col2 = st.columns([1, 1])
    with logs_col1:
        if st.button("刷新 agent runs", use_container_width=True):
            load_agent_runs(limit=20)
    with logs_col2:
        if st.button("刷新事件日志", use_container_width=True):
            load_recent_events(limit=20)

    agent_runs = get_current_agent_runs()
    if agent_runs:
        st.markdown("**Agent Runs**")
        for run in agent_runs:
            with st.expander(f"{run.get('run_type', 'unknown')} | {run.get('status', 'unknown')} | {run.get('started_at', '')}", expanded=False):
                st.caption(
                    f"标题：{run.get('title', '')} | "
                    f"耗时：{run.get('duration_ms', 0)} ms | "
                    f"输入：{run.get('input_summary', '')}"
                )
                if run.get("output_summary"):
                    st.write(run.get("output_summary"))
                if run.get("metadata"):
                    metadata = run.get("metadata", {})
                    retrieval_debug = metadata.get("retrieval_debug")
                    if retrieval_debug:
                        render_retrieval_debug(retrieval_debug)
                    else:
                        st.caption(json.dumps(metadata, ensure_ascii=False))
                if run.get("steps"):
                    st.markdown("**步骤**")
                    for step in run.get("steps", []):
                        st.caption(
                            f"{step.get('step_name', '')} | "
                            f"状态：{step.get('step_status', '')} | "
                            f"耗时={step.get('duration_ms', 0)} ms | "
                            f"{step.get('message', '')}"
                        )

    event_logs = get_current_event_logs()
    if not agent_runs and not event_logs:
        st.caption("点击上方按钮加载当前会话的运行观测信息。")

    if event_logs:
        st.markdown("**事件日志**")
        for event in event_logs:
            with st.container(border=True):
                st.markdown(f"**{event.get('event_type', 'unknown')}**")
                st.caption(
                    f"状态：{event.get('status', 'unknown')} | "
                    f"耗时：{event.get('duration_ms', 0)} ms | "
                    f"时间：{event.get('created_at', '')}"
                )
                if event.get("message"):
                    st.write(event["message"])
                if event.get("metadata"):
                    st.caption(json.dumps(event.get("metadata", {}), ensure_ascii=False))


