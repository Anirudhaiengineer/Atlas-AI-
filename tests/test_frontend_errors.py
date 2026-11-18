from frontend.streamlit_app import _api_error_message


def test_login_unauthorized_message_is_about_credentials():
    message = _api_error_message(
        401,
        "Invalid email or password.",
        login=True,
    )

    assert message == "Invalid email or password."


def test_authenticated_unauthorized_message_is_about_expired_login():
    message = _api_error_message(401, "Authentication required.")

    assert message == "Your login has expired. Please sign in again."


def test_auth_configuration_error_is_actionable():
    message = _api_error_message(
        503,
        "Authentication is not configured.",
    )

    assert "AUTH_JWT_SECRET" in message


def test_server_error_instructs_user_to_check_api_logs():
    message = _api_error_message(500)

    assert "HTTP 500" in message
    assert "FastAPI terminal" in message


def test_chat_not_ready_message_explains_ingestion_delay():
    message = _api_error_message(
        409,
        "No indexed document chunks are available.",
    )

    assert "Wait for document ingestion" in message


def test_coreference_model_error_is_actionable():
    message = _api_error_message(
        503,
        "The query coreference model is unavailable.",
    )

    assert "FastCoref" in message
    assert "model access" in message


def test_keyword_model_error_is_actionable():
    message = _api_error_message(
        503,
        "The query keyword model is unavailable.",
    )

    assert "spaCy model installation" in message


def test_reranker_model_error_is_actionable():
    message = _api_error_message(
        503,
        "The query reranking model is unavailable.",
    )

    assert "reranking model is unavailable" in message
    assert "FastAPI logs" in message
