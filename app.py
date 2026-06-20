from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from _project_bootstrap import activate_project_environment

# Support `python -m streamlit run app.py` directly from a source checkout,
# even before the project has been installed with `pip install -e .`.
PROJECT_ROOT = Path(__file__).resolve().parent
BOOTSTRAP = activate_project_environment(PROJECT_ROOT)
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import streamlit as st  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from offline_rag.config import AppConfig  # noqa: E402
from offline_rag.preflight import check_local_model, check_ollama  # noqa: E402
from offline_rag.runtime import RuntimeController  # noqa: E402

st.set_page_config(
    page_title="Air-Gapped RAG",
    page_icon="🔒",
    layout="wide",
)


@st.cache_resource
def get_controller() -> RuntimeController:
    return RuntimeController(AppConfig.from_env())


@st.cache_data(ttl=5, show_spinner=False)
def get_preflight_checks(
    embedding_model: str,
    reranker_model: str,
    ollama_base_url: str,
    ollama_model: str,
) -> list[dict[str, object]]:
    return [
        check_ollama(ollama_base_url, ollama_model).as_dict(),
        check_local_model(embedding_model, "Embedding model").as_dict(),
        check_local_model(reranker_model, "Reranker model").as_dict(),
    ]


def render_citations(citations: list[dict[str, object]]) -> None:
    if not citations:
        return
    st.markdown("**Sources**")
    for citation in citations:
        file_name = str(citation["source_file_name"])
        page_number = int(citation["page_number"])
        st.markdown(f"- `{file_name}` — page {page_number}")


def to_langchain_history(messages: list[dict[str, object]], max_turns: int):
    history = []
    for message in messages[-(max_turns * 2) :]:
        content = str(message["content"])
        if message["role"] == "user":
            history.append(HumanMessage(content=content))
        elif message["role"] == "assistant":
            history.append(AIMessage(content=content))
    return history


controller = get_controller()
preflight_checks = get_preflight_checks(
    controller.config.embedding_model,
    controller.config.reranker_model,
    controller.config.ollama_base_url,
    controller.config.ollama_model,
)
st.title("🔒 AI Document Assistant")
st.caption("Ask questions about your local documents without worrying for data privacy.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "active_index_key" not in st.session_state:
    st.session_state.active_index_key = None
if "directory_path" not in st.session_state:
    st.session_state.directory_path = ""

with st.sidebar:
    st.header("Document index")
    with st.expander("Runtime checks", expanded=not all(check["ok"] for check in preflight_checks)):
        if not BOOTSTRAP.running_in_project_venv and BOOTSTRAP.injected_site_packages:
            st.warning("Using packages from the project venv. Prefer `python run_app.py`.")
        for check in preflight_checks:
            icon = "✅" if check["ok"] else "❌"
            st.markdown(f"{icon} **{check['name']}**")
            st.caption(str(check["detail"]))

    directory_path = st.text_input(
        "Absolute document directory",
        key="directory_path",
        placeholder="/absolute/path/to/documents",
    )
    user_path = os.path.expanduser(directory_path.strip()) if directory_path else ""
    path_is_absolute = bool(user_path and os.path.isabs(user_path))
    expanded_path = os.path.abspath(user_path) if user_path else ""
    path_exists = bool(path_is_absolute and os.path.exists(expanded_path))
    path_is_directory = bool(path_exists and os.path.isdir(expanded_path))

    if directory_path and not path_is_absolute:
        st.error("Enter an absolute local directory path.")
    elif directory_path and not path_exists:
        st.error("The path does not exist.")
    elif path_exists and not path_is_directory:
        st.error("The path is not a directory.")

    start_clicked = st.button(
        "Start / switch index",
        type="primary",
        use_container_width=True,
        disabled=not path_is_directory,
    )
    if start_clicked:
        index_key = controller.activate(expanded_path)
        if st.session_state.active_index_key != index_key:
            st.session_state.messages = []
            st.session_state.active_index_key = index_key
        st.rerun()

    col1, col2 = st.columns(2)
    if col1.button("Re-scan", use_container_width=True, disabled=controller.active is None):
        controller.request_full_scan()
    if col2.button("Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


def render_index_status(snapshot) -> None:
    st.subheader("Indexing status")
    st.progress(snapshot.progress, text=snapshot.phase.replace("_", " ").title())
    if snapshot.error:
        st.error(snapshot.error)
    else:
        st.caption(snapshot.message)
    metric1, metric2 = st.columns(2)
    metric1.metric("Stage progress", f"{snapshot.completed}/{snapshot.total}")
    metric2.metric("Indexed chunks", snapshot.indexed_chunks)
    if snapshot.stats:
        with st.expander("Latest run details"):
            st.json(snapshot.stats)


with st.sidebar:
    render_index_status(controller.snapshot())


@st.fragment(run_every=1.0)
def refresh_runtime_state() -> None:
    snapshot = controller.snapshot()
    previous_ready = st.session_state.get("_last_runtime_ready")
    st.session_state["_last_runtime_ready"] = snapshot.ready
    if previous_ready is not None and previous_ready != snapshot.ready:
        st.rerun()
    if snapshot.phase not in {"idle", "ready"}:
        st.progress(snapshot.progress, text=snapshot.message)


refresh_runtime_state()

for message in st.session_state.messages:
    with st.chat_message(str(message["role"])):
        st.markdown(str(message["content"]))
        render_citations(list(message.get("citations", [])))

snapshot = controller.snapshot()
prompt = st.chat_input(
    "Ask a question about the indexed documents",
    disabled=not snapshot.ready,
)

if not snapshot.ready:
    if controller.active is None:
        st.info("Choose a local document directory to begin.")
    elif not snapshot.error:
        st.info("The local index is being prepared. Chat will unlock when it is ready.")

if prompt:
    chat_history = to_langchain_history(
        st.session_state.messages,
        controller.config.history_turns,
    )
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"), st.spinner("Searching and generating locally..."):
        try:
            result = controller.answer(prompt, chat_history)
            answer = result.answer
            citations = result.citations
        except Exception as exc:  # Streamlit must surface local runtime failures cleanly.
            answer = (
                "The local RAG pipeline could not complete the request. "
                f"Details: {type(exc).__name__}: {exc}"
            )
            citations = []
        st.markdown(answer)
        render_citations(citations)

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
            "citations": json.loads(json.dumps(citations)),
        }
    )
