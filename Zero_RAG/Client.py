import json
import mimetypes
import uuid

import requests
import streamlit as st


API_BASE_URL = "http://127.0.0.1:8000"

SUMMARY_TYPE_LABELS = {
    "short_summary": "简要摘要",
    "keywords": "关键词",
    "interview_takeaways": "面试要点",
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
    existing_user_id = get_query_param("user_id")
    if existing_user_id:
        return existing_user_id
    new_user_id = str(uuid.uuid4())
    set_query_param("user_id", new_user_id)
    return new_user_id


st.set_page_config(page_title="LearnOS", layout="wide")
st.title("LearnOS 工作台")
st.caption("上传资料后，系统会自动生成会话名称、学习主题和学习目标。")


if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "user_id" not in st.session_state:
    st.session_state["user_id"] = ensure_user_id()
if "selected_session_id" not in st.session_state:
    session_id_from_query = get_query_param("session_id")
    st.session_state["selected_session_id"] = int(session_id_from_query) if session_id_from_query else None
if "session_detail" not in st.session_state:
    st.session_state["session_detail"] = None


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


def select_session(session_id: int):
    st.session_state["selected_session_id"] = session_id
    st.session_state["messages"] = []
    set_query_param("session_id", session_id)
    load_session_detail(session_id)


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
    else:
        st.caption("当前还没有学习会话，上传资料后会自动创建。")
        set_query_param("session_id", None)

    uploaded_files = st.file_uploader(
        "上传学习资料",
        type=["pdf", "docx", "txt", "md"],
        accept_multiple_files=True,
    )

    if st.session_state["selected_session_id"] is None:
        if st.button("根据上传资料自动创建会话", disabled=not uploaded_files, use_container_width=True):
            files_payload = []
            for file in uploaded_files:
                mime_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                files_payload.append(("files", (file.name, file.getvalue(), mime_type)))

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
                st.error(response.text)
    else:
        if st.button("向当前会话继续导入资料", disabled=not uploaded_files, use_container_width=True):
            files_payload = []
            for file in uploaded_files:
                mime_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                files_payload.append(("files", (file.name, file.getvalue(), mime_type)))

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
                st.error(response.text)

    st.caption(f"当前用户标识：{st.session_state['user_id']}")


detail = st.session_state.get("session_detail")

left_col, right_col = st.columns([1.1, 1.4])

with left_col:
    st.subheader("会话概览")
    if detail:
        session = detail["session"]
        st.markdown(f"**会话名称：** {session['session_name']}")
        if session.get("topic"):
            st.markdown(f"**学习主题：** {session['topic']}")
        if session.get("goal"):
            st.markdown(f"**学习目标：** {session['goal']}")

        documents = detail.get("documents", [])
        summaries = detail.get("summaries", [])
        knowledge_points = detail.get("knowledge_points", [])
        review_items = detail.get("review_items", [])

        with st.expander("文档列表", expanded=True):
            if documents:
                for document in documents:
                    st.write(f"- {document['title']} ({document.get('file_type') or '未知类型'})")
            else:
                st.caption("当前还没有导入文档。")

        with st.expander("摘要信息", expanded=True):
            if summaries:
                for item in summaries:
                    st.markdown(f"**{summary_type_label(item['summary_type'])}**")
                    st.write(item["summary_text"])
            else:
                st.caption("当前还没有摘要。")

        with st.expander("知识点", expanded=True):
            if knowledge_points:
                for point in knowledge_points:
                    st.markdown(f"**{point['title']}**")
                    st.caption(point["description"])
            else:
                st.caption("当前还没有知识点。")

        with st.expander("复习项", expanded=False):
            if review_items:
                for item in review_items:
                    st.markdown(f"**{item['topic']}**")
                    st.caption(item["summary"])
            else:
                st.caption("当前还没有复习项。")
    else:
        st.info("请先上传学习资料，系统会自动创建会话。")

with right_col:
    st.subheader("学习问答")

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message.get("sources"):
                with st.expander("参考来源"):
                    for source in message["sources"]:
                        st.caption(
                            f"{source.get('document_title') or source['source']} | "
                            f"分数={source.get('score', 0):.4f} | 分片={source.get('chunk_index')}"
                        )
            if message.get("review_items"):
                with st.expander("复习提醒"):
                    for item in message["review_items"]:
                        st.caption(f"{item['topic']}: {item['summary']}")

    user_query = st.chat_input(
        "围绕今天的学习资料提问...",
        disabled=st.session_state["selected_session_id"] is None,
    )

    if user_query and st.session_state["selected_session_id"] is not None:
        st.session_state["messages"].append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.write(user_query)

        with st.chat_message("assistant"):
            try:
                payload = {
                    "user_id": st.session_state["user_id"],
                    "session_id": st.session_state["selected_session_id"],
                    "query": user_query,
                }
                response = requests.post(f"{API_BASE_URL}/chat", json=payload, stream=True, timeout=120)
                response.raise_for_status()

                answer_placeholder = st.empty()
                full_answer = ""
                sources = []
                review_items = []

                for line in response.iter_lines():
                    if line:
                        data = json.loads(line.decode("utf-8"))
                        if "chunk" in data:
                            full_answer += data["chunk"]
                            answer_placeholder.markdown(full_answer + "▌")
                        if "sources" in data:
                            sources = data["sources"]
                        if "review_items" in data:
                            review_items = data["review_items"]

                answer_placeholder.markdown(full_answer)

                if sources:
                    with st.expander("参考来源"):
                        for source in sources:
                            st.caption(
                                f"{source.get('document_title') or source['source']} | "
                                f"分数={source.get('score', 0):.4f} | 分片={source.get('chunk_index')}"
                            )
                if review_items:
                    with st.expander("复习提醒"):
                        for item in review_items:
                            st.caption(f"{item['topic']}: {item['summary']}")

                st.session_state["messages"].append(
                    {
                        "role": "assistant",
                        "content": full_answer,
                        "sources": sources,
                        "review_items": review_items,
                    }
                )
            except Exception as exc:
                st.error(f"请求失败：{exc}")
