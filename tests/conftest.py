import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.api.routes import documents as document_routes
from app.core.config import settings
from app.core.rate_limit import limiter
from app.database.connection import Base, get_db
from app.main import app


@pytest.fixture
def test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def register_full_text_search_stub(dbapi_connection, _connection_record):
        dbapi_connection.create_function(
            "to_tsvector",
            2,
            lambda _language, text: text,
            deterministic=True,
        )

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(test_engine, monkeypatch):
    session_factory = sessionmaker(bind=test_engine, autoflush=False)

    async def idle_consumer():
        await asyncio.Event().wait()

    def override_get_db():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(main_module, "consume_document_events", idle_consumer)
    monkeypatch.setattr(settings, "AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(
        document_routes,
        "publish_document_inserted",
        lambda _document: None,
    )
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def register_user(client: TestClient, email: str = "reader@example.com") -> dict:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "SecurePassword1!"},
    )
    assert response.status_code == 201
    return response.json()


def login_user(
    client: TestClient,
    email: str = "reader@example.com",
) -> dict:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "SecurePassword1!"},
    )
    assert response.status_code == 200
    return response.json()


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
