from streamlit.testing.v1 import AppTest

from frontend import streamlit_app


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


def test_dashboard_keeps_chat_input_visible_without_conversation_history(
    monkeypatch,
):
    requested_paths = []

    def request(_method, path, **_kwargs):
        requested_paths.append(path)
        if path == "/documents":
            return FakeResponse(200, [])
        if path == "/chat/conversations":
            return FakeResponse(500, {"detail": "Internal server error"})
        raise AssertionError(f"Unexpected API request: {path}")

    monkeypatch.setattr(streamlit_app, "_request", request)
    app = AppTest.from_string(
        "from frontend import streamlit_app\n"
        "streamlit_app._dashboard('test-token', "
        "{'email': 'reader@example.com'})"
    ).run()

    assert not app.exception
    assert len(app.chat_input) == 1
    assert app.error
    assert "/auth/sessions" not in requested_paths


def test_conversations_are_in_sidebar_and_upload_opens_from_icon(monkeypatch):
    conversation_id = "conversation-1"

    def request(_method, path, **_kwargs):
        if path == "/documents":
            return FakeResponse(200, [])
        if path == "/chat/conversations":
            return FakeResponse(
                200,
                [{"id": conversation_id, "title": "Project questions"}],
            )
        if path == f"/chat/conversations/{conversation_id}":
            return FakeResponse(200, {"messages": []})
        raise AssertionError(f"Unexpected API request: {path}")

    monkeypatch.setattr(streamlit_app, "_request", request)
    app = AppTest.from_string(
        "from frontend import streamlit_app\n"
        "streamlit_app._dashboard('test-token', "
        "{'email': 'reader@example.com'})"
    ).run()

    assert not app.exception
    assert app.button(key=f"atlasai_conversation_{conversation_id}").label == (
        "Project questions"
    )
    assert not app.file_uploader

    app.button(key="atlasai_toggle_upload").click().run()

    assert not app.exception
    assert len(app.file_uploader) == 1
