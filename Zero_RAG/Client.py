import json
import mimetypes
from datetime import datetime

import requests
import streamlit as st


API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_USER_ID = "learnos_local_user"

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
    st.session_state["plan_data"] = None
    st.session_state["plan_session_id"] = None
    st.session_state["report_data"] = None
    st.session_state["report_session_id"] = None


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


def import_uploaded_files(auto_create: bool, uploaded_files):
    files_payload = build_file_payload(uploaded_files)
    if auto_create:
        response = requests.post(
            f"{API_BASE_URL}/study_sessions/auto_from_documents",
            data={"user_id": st.session_state["user_id"]},
            files=files_payload,
            timeout=180,
        )
        if response.ok:
            created = response.json()["session"]
            select_session(created["id"])
            st.success("已根据上传资料新建学习会话。")
        else:
            st.error(extract_error_message(response))
        return

    response = requests.post(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/documents",
        data={"user_id": st.session_state["user_id"]},
        files=files_payload,
        timeout=180,
    )
    if response.ok:
        load_session_detail(st.session_state["selected_session_id"])
        st.success("学习资料已导入当前会话。")
    else:
        st.error(extract_error_message(response))


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

            answer_placeholder.markdown(full_answer)
            render_sources(sources)
            render_review_items(review_items)

    st.session_state["messages"].append({"role": "user", "content": user_query})
    st.session_state["messages"].append(
        {
            "role": "assistant",
            "content": full_answer,
            "sources": sources,
            "review_items": review_items,
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


def refresh_learning_plan():
    response = requests.get(
        f"{API_BASE_URL}/study_sessions/{st.session_state['selected_session_id']}/plan",
        timeout=120,
    )
    if response.ok:
        st.session_state["plan_data"] = response.json()["plan"]
        st.session_state["plan_session_id"] = st.session_state["selected_session_id"]
        st.success("学习计划已刷新。")
    else:
        st.error(extract_error_message(response))


def render_sources(sources: list[dict]):
    if not sources:
        return
    with st.expander("参考来源"):
        for source in sources:
            st.caption(
                f"{source.get('document_title') or source['source']} | "
                f"分数={source.get('score', 0):.4f} | 分片={source.get('chunk_index')}"
            )


def render_review_items(review_items: list[dict]):
    if not review_items:
        return
    with st.expander("复习提醒"):
        for item in review_items:
            st.caption(f"{item['topic']}: {item['summary']}")


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


def get_current_plan():
    if st.session_state.get("plan_session_id") == st.session_state.get("selected_session_id"):
        return st.session_state.get("plan_data")
    return None


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
if "report_data" not in st.session_state:
    st.session_state["report_data"] = None
if "report_session_id" not in st.session_state:
    st.session_state["report_session_id"] = None
if "plan_data" not in st.session_state:
    st.session_state["plan_data"] = None
if "plan_session_id" not in st.session_state:
    st.session_state["plan_session_id"] = None
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

        st.markdown(f"**会话名称：** {session['session_name']}")
        if session.get("topic"):
            st.markdown(f"**学习主题：** {session['topic']}")
        if session.get("goal"):
            st.markdown(f"**学习目标：** {session['goal']}")

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
        st.markdown("**知识点**")
        if knowledge_points:
            for point in knowledge_points:
                st.markdown(f"**{point['title']}**")
                st.caption(point["description"])
        else:
            st.caption("当前还没有知识点。")

        st.divider()
        st.markdown("**复习项**")
        if review_items:
            for item in review_items:
                st.markdown(f"**{item['topic']}**")
                st.caption(item["summary"])
        else:
            st.caption("当前还没有复习项。")
    else:
        st.info("请先新建空白会话，或者导入资料开始学习。")


chat_tab, quiz_tab, plan_tab, report_tab = st.tabs(["学习问答", "测验模式", "学习计划", "学习报告"])

with chat_tab:
    st.subheader("学习问答")
    messages_container = st.container()
    with messages_container:
        for message in st.session_state["messages"]:
            with st.chat_message(message["role"]):
                st.write(message["content"])
                render_sources(message.get("sources", []))
                render_review_items(message.get("review_items", []))

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
        except Exception as exc:
            st.error(f"请求失败：{exc}")

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
                st.write(question.get("question_text", ""))
                answers.append(
                    st.text_area(
                        f"answer_{quiz_bundle['quiz_set_id']}_{index}",
                        key=f"quiz_answer_{quiz_bundle['quiz_set_id']}_{index}",
                        label_visibility="collapsed",
                        placeholder="在这里写下你的回答...",
                        height=120,
                    )
                )

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

                st.markdown("**当前优势**")
                for item in report.get("strengths", []):
                    st.write(f"- {item}")

                st.markdown("**面试表达重点**")
                for item in report.get("interview_focus", []):
                    st.write(f"- {item}")

            with right_col:
                st.markdown("**当前风险**")
                for item in report.get("risks", []):
                    st.write(f"- {item}")

                st.markdown("**下一步建议**")
                for item in report.get("next_actions", []):
                    st.write(f"- {item}")
        else:
            st.caption("点击上方按钮生成当前学习会话的复盘报告。")

with plan_tab:
    st.subheader("学习计划")
    if st.session_state["selected_session_id"] is None:
        st.info("先新建会话或导入资料后再生成学习计划。")
    else:
        if st.button("生成 / 刷新学习计划", use_container_width=True):
            refresh_learning_plan()

        plan = get_current_plan()
        if plan:
            st.markdown(f"**{plan.get('title', '学习计划')}**")
            st.write(plan.get("overview", ""))

            plan_col1, plan_col2 = st.columns(2)
            with plan_col1:
                st.markdown("**今天学什么**")
                for item in plan.get("today_focus", []):
                    st.write(f"- {item}")

                st.markdown("**先复习什么**")
                for item in plan.get("priority_review", []):
                    st.write(f"- {item}")

            with plan_col2:
                st.markdown("**下一步问什么**")
                for item in plan.get("next_questions", []):
                    st.write(f"- {item}")

                st.markdown("**行动步骤**")
                for item in plan.get("action_steps", []):
                    st.write(f"- {item}")
        else:
            st.caption("点击上方按钮，基于当前会话的知识点、复习项和测验结果生成计划。")
