import json
import mimetypes

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
    if session_id is None:
        st.session_state["session_detail"] = None
        set_query_param("session_id", None)
        return
    set_query_param("session_id", session_id)
    load_session_detail(session_id)


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
            st.success("已根据上传资料自动创建学习会话。")
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
        st.success("学习资料已导入，系统已同步更新会话信息。")
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
            st.success("已根据网页内容自动创建学习会话。")
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


with st.sidebar:
    st.subheader("学习会话")
    try:
        sessions = refresh_sessions()
    except Exception as exc:
        sessions = []
        st.error(f"加载学习会话失败：{exc}")

    if sessions:
        options = {f"{item['session_name']} (#{item['id']})": item["id"] for item in sessions}
        default_label = next(
            (label for label, value in options.items() if value == st.session_state["selected_session_id"]),
            None,
        )
        labels = list(options.keys())
        selected_label = st.selectbox(
            "选择学习会话",
            options=labels,
            index=labels.index(default_label) if default_label in labels else 0,
        )
        selected_session_id = options[selected_label]
        if selected_session_id != st.session_state["selected_session_id"]:
            select_session(selected_session_id)
        elif st.session_state["session_detail"] is None:
            set_query_param("session_id", selected_session_id)
            load_session_detail(selected_session_id)

        if st.button("删除当前会话", use_container_width=True):
            delete_selected_session()
            st.rerun()
    else:
        st.caption("当前还没有学习会话，导入资料后会自动创建。")
        set_query_param("session_id", None)

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
        st.info("请先上传学习资料或导入网页，系统会自动创建会话。")


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
    st.caption("先导入资料后再开始提问。")

user_query = submitted_query.strip()
if submitted and st.session_state["selected_session_id"] is not None and user_query:
    try:
        with st.spinner("正在整理回答..."):
            stream_study_question(user_query, streaming_container)
    except Exception as exc:
        st.error(f"请求失败：{exc}")
