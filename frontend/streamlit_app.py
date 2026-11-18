import os

import requests
import streamlit as st

from app.core.config import settings
from app.core.logging import configure_logging, instrument_module


configure_logging(settings.DEBUG)

API_BASE_URL = os.getenv("ATLASAI_API_URL", "http://localhost:8000").rstrip("/")
ACCESS_TOKEN_KEY = "atlasai_access_token"


def _request(
    method: str,
    path: str,
    *,
    access_token: str | None = None,
    json: dict | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    timeout: float = 10.0,
) -> requests.Response | None:
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    try:
        return requests.request(
            method,
            f"{API_BASE_URL}{path}",
            headers=headers,
            json=json,
            data=data,
            files=files,
            timeout=timeout,
        )
    except requests.RequestException:
        st.error("Could not reach the AtlasAI API.")
        return None


def _api_error_message(
    status_code: int,
    detail: object = None,
    *,
    login: bool = False,
) -> str:
    if status_code == 401:
        if login:
            return "Invalid email or password."
        if detail == "Invalid refresh token.":
            return "Your session has expired. Please log in again."
        return "Your login has expired. Please sign in again."
    if status_code == 409:
        if detail == "No indexed document chunks are available.":
            return (
                "No indexed content is ready yet. "
                "Wait for document ingestion to finish."
            )
        return "An account with that email already exists."
    if status_code == 400 and detail == "The uploaded document is empty.":
        return "The selected document is empty."
    if status_code == 413:
        return "The document is larger than the configured upload limit."
    if status_code == 415:
        return "Upload a CSV, DOCX, Markdown, PDF, or TXT document."
    if status_code == 422:
        return "Check the submitted fields and try again."
    if status_code == 429:
        return "Too many attempts. Please wait before trying again."
    if status_code == 503 and detail == "Authentication is not configured.":
        return (
            "Authentication is not configured. Set AUTH_JWT_SECRET in .env "
            "and restart FastAPI."
        )
    if (
        status_code == 503
        and isinstance(detail, str)
        and "event could not be published" in detail
    ):
        return (
            "The document was saved, but Kafka could not queue ingestion. "
            "Check the Kafka broker and retry."
        )
    if status_code == 503 and detail == "The configured Ollama model is unavailable.":
        return (
            "The Ollama model is unavailable. Check that Ollama is running "
            "and the configured models are installed."
        )
    if status_code == 503 and detail == "The query coreference model is unavailable.":
        return (
            "The query coreference model is unavailable. Check the FastCoref "
            "installation and model access."
        )
    if status_code == 503 and detail == "The query keyword model is unavailable.":
        return (
            "The English keyword model is unavailable. Check the spaCy model "
            "installation."
        )
    if status_code == 503 and detail == "The query reranking model is unavailable.":
        return (
            "The chunk reranking model is unavailable. Check the model download "
            "and FastAPI logs."
        )
    if status_code >= 500:
        return f"AtlasAI API error (HTTP {status_code}). Check the FastAPI terminal for details."
    if status_code == 404 and detail == "Session not found.":
        return "That session was not found or does not belong to your account."
    if status_code == 404 and detail == "Conversation not found.":
        return "That conversation was not found or does not belong to your account."
    return f"The API request failed (HTTP {status_code})."


def _show_api_error(
    response: requests.Response,
    *,
    login: bool = False,
) -> None:
    try:
        body = response.json()
    except requests.exceptions.JSONDecodeError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    st.error(_api_error_message(response.status_code, detail, login=login))


def _login_screen() -> None:
    st.title("AtlasAI")
    mode = st.radio("Choose an option", ["Log in", "Register"], horizontal=True)
    with st.form("auth_form"):
        email = st.text_input("Email", autocomplete="email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button(mode)

    if not submitted:
        return

    endpoint = "/auth/login" if mode == "Log in" else "/auth/register"
    response = _request(
        "POST",
        endpoint,
        json={"email": email, "password": password},
    )
    if response is None:
        return
    if response.status_code >= 400:
        _show_api_error(response, login=(mode == "Log in"))
        return

    if mode == "Register":
        st.success("Account created. You can now log in.")
        return

    token_data = response.json()
    st.session_state[ACCESS_TOKEN_KEY] = token_data["access_token"]
    del token_data
    del response
    st.rerun()


def _dashboard(access_token: str, user: dict) -> None:
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background: #ffffff;
        }
        [data-testid="stSidebar"] {
            background: #f7f7f8;
            border-right: 1px solid #ececef;
        }
        [data-testid="stMainBlockContainer"] {
            max-width: 900px;
            padding-top: 2.5rem;
        }
        [data-testid="stChatMessage"] {
            border-radius: 14px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.sidebar:
        st.markdown("## AtlasAI")
        st.caption(user["email"])
        if st.button("Log out", use_container_width=True):
            response = _request("POST", "/auth/logout", access_token=access_token)
            if response is None:
                return
            if response.status_code >= 400:
                _show_api_error(response)
                return
            st.session_state.pop(ACCESS_TOKEN_KEY, None)
            st.session_state.pop("atlasai_chat_messages", None)
            st.session_state.pop("atlasai_chat_conversation_id", None)
            st.rerun()

    _document_workspace(access_token)


def _document_workspace(access_token: str) -> None:
    response = _request("GET", "/documents", access_token=access_token)
    if response is None:
        documents = []
    elif response.status_code >= 400:
        _show_api_error(response)
        documents = []
    else:
        documents = response.json()

    with st.sidebar:
        upload_col, title_col = st.columns([0.75, 0.25], vertical_alignment="center")
        with upload_col:
            st.subheader("Documents")
        with title_col:
            if st.button(
                " ",
                icon=":material/attach_file:",
                help="Upload a document",
                key="atlasai_toggle_upload",
            ):
                st.session_state["atlasai_show_upload"] = not st.session_state.get(
                    "atlasai_show_upload",
                    False,
                )

        if st.session_state.get("atlasai_show_upload", False):
            with st.form("document_upload_form"):
                uploaded_file = st.file_uploader(
                    "Choose a document",
                    type=["csv", "docx", "md", "pdf", "txt"],
                )
                title = st.text_input("Document title (optional)")
                upload_submitted = st.form_submit_button("Upload")

            if upload_submitted:
                if uploaded_file is None:
                    st.warning("Choose a document to upload.")
                else:
                    uploaded = _request(
                        "POST",
                        "/documents",
                        access_token=access_token,
                        data={"title": title} if title.strip() else None,
                        files={
                            "upload": (
                                uploaded_file.name,
                                uploaded_file.getvalue(),
                                uploaded_file.type or "application/octet-stream",
                            )
                        },
                        timeout=60.0,
                    )
                    if uploaded is not None and uploaded.status_code < 400:
                        documents.insert(0, uploaded.json())
                        st.success("Uploaded; indexing is queued.")
                    elif uploaded is not None:
                        _show_api_error(uploaded)
                        try:
                            detail = uploaded.json().get("detail")
                        except requests.exceptions.JSONDecodeError:
                            detail = None
                        if (
                            uploaded.status_code == 503
                            and isinstance(detail, str)
                            and "event could not be published" in detail
                        ):
                            refreshed = _request(
                                "GET",
                                "/documents",
                                access_token=access_token,
                            )
                            if refreshed is not None and refreshed.status_code < 400:
                                documents = refreshed.json()

        document_options = {"All documents": None}
        document_options.update(
            {
                f"{document['title']} ({document['original_filename']})":
                document["id"]
                for document in documents
            }
        )
        selected_document = st.selectbox("Search in", list(document_options))

    conversations_response = _request(
        "GET",
        "/chat/conversations",
        access_token=access_token,
    )
    if conversations_response is None:
        conversations = []
        conversation_id = None
    elif conversations_response.status_code >= 400:
        _show_api_error(conversations_response)
        conversations = []
        conversation_id = None
    else:
        conversations = conversations_response.json()

        conversation_id_is_set = (
            "atlasai_chat_conversation_id" in st.session_state
        )
        conversation_id = st.session_state.get("atlasai_chat_conversation_id")
        conversation_ids = [conversation["id"] for conversation in conversations]
        if not conversation_id_is_set:
            conversation_id = conversation_ids[0] if conversation_ids else None
            st.session_state["atlasai_chat_conversation_id"] = conversation_id
        elif conversation_id is not None and conversation_id not in conversation_ids:
            conversation_id = conversation_ids[0] if conversation_ids else None
            st.session_state["atlasai_chat_conversation_id"] = conversation_id

    with st.sidebar:
        st.divider()
        if st.button(
            "New chat",
            icon=":material/edit_square:",
            use_container_width=True,
            key="atlasai_new_conversation",
        ):
            st.session_state["atlasai_chat_conversation_id"] = None
            st.rerun()
        st.caption("RECENTS")
        if conversations:
            for conversation in conversations:
                conversation_id_option = conversation["id"]
                is_selected = conversation_id_option == conversation_id
                if st.button(
                    conversation["title"],
                    key=f"atlasai_conversation_{conversation_id_option}",
                    type="primary" if is_selected else "tertiary",
                    use_container_width=True,
                ):
                    st.session_state["atlasai_chat_conversation_id"] = (
                        conversation_id_option
                    )
                    st.rerun()
        else:
            st.caption("Your conversations will appear here.")

    st.title("Ask your documents")
    if conversation_id is None and not st.session_state.get(
        "atlasai_show_upload",
        False,
    ):
        st.markdown("Start a conversation")
        st.caption("Ask a question and AtlasAI will find answers in your documents.")

    messages = []
    if conversation_id is not None:
        conversation_response = _request(
            "GET",
            f"/chat/conversations/{conversation_id}",
            access_token=access_token,
        )
        if conversation_response is None:
            conversation_id = None
        elif conversation_response.status_code >= 400:
            _show_api_error(conversation_response)
            conversation_id = None
        else:
            messages = conversation_response.json()["messages"]

    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("sources"):
                sources = message["sources"]
                with st.expander(
                    f"Retrieved chunks ({len(sources)})",
                    expanded=True,
                ):
                    for index, source in enumerate(sources, start=1):
                        heading = (
                            f" · {source['heading_path']}"
                            if source.get("heading_path")
                            else ""
                        )
                        st.caption(
                            f"Chunk {index} · {source['document_title']}{heading}"
                        )
                        st.text(source["excerpt"])

    if conversation_id is not None:
        if st.button(
            "Delete conversation",
            icon=":material/delete:",
            type="tertiary",
        ):
            deleted = _request(
                "DELETE",
                f"/chat/conversations/{conversation_id}",
                access_token=access_token,
            )
            if deleted is not None and deleted.status_code < 400:
                st.session_state["atlasai_chat_conversation_id"] = None
                st.rerun()
            elif deleted is not None:
                _show_api_error(deleted)

    question = st.chat_input("Ask a question about your documents")
    if question:
        with st.spinner("Resolving the query and searching your documents..."):
            response = _request(
                "POST",
                "/chat",
                access_token=access_token,
                json={
                    "question": question,
                    "document_id": document_options[selected_document],
                    "conversation_id": conversation_id,
                },
                timeout=180.0,
            )
        if response is None:
            st.error("Could not reach the AtlasAI API.")
        elif response.status_code >= 400:
            _show_api_error(response)
        else:
            st.session_state["atlasai_chat_conversation_id"] = response.json()[
                "conversation_id"
            ]
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="AtlasAI")
    access_token = st.session_state.get(ACCESS_TOKEN_KEY)
    if not access_token:
        _login_screen()
        return

    response = _request("GET", "/auth/me", access_token=access_token)
    if response is None:
        return
    if response.status_code == 401:
        st.session_state.pop(ACCESS_TOKEN_KEY, None)
        st.session_state.pop("atlasai_chat_messages", None)
        st.session_state.pop("atlasai_chat_conversation_id", None)
        st.info("Your access token expired. Please log in again.")
        _login_screen()
        return
    if response.status_code >= 400:
        _show_api_error(response)
        return
    _dashboard(access_token, response.json())


instrument_module(globals())


if __name__ == "__main__":
    main()
