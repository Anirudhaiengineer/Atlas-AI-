from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.api.routes import documents as document_routes
from app.models import Document
from tests.conftest import auth_headers, login_user, register_user


def test_document_upload_persists_file_and_user_owned_metadata(
    client,
    test_engine,
    tmp_path,
    monkeypatch,
):
    profile = register_user(client)
    tokens = login_user(client)
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    file_content = b"AtlasAI test document"

    response = client.post(
        "/documents",
        headers=auth_headers(tokens["access_token"]),
        data={"title": "Project notes", "description": "First upload"},
        files={"upload": ("notes.txt", file_content, "text/plain")},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["title"] == "Project notes"
    assert result["original_filename"] == "notes.txt"
    assert result["size_bytes"] == len(file_content)
    assert "storage_key" not in result

    with Session(test_engine) as db:
        document = db.scalar(
            select(Document).where(Document.id == UUID(result["id"]))
        )
        assert document is not None
        assert str(document.owner_id) == profile["id"]
        stored_file = Path(tmp_path, document.storage_key)
        assert stored_file.read_bytes() == file_content


def test_document_upload_publishes_saved_document_event(
    client,
    test_engine,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    events = []

    def publish_saved_document(document):
        with Session(test_engine) as db:
            assert db.get(Document, document.id) is not None
        assert Path(tmp_path, document.storage_key).is_file()
        events.append(document)

    monkeypatch.setattr(
        document_routes,
        "publish_document_inserted",
        publish_saved_document,
    )
    register_user(client)
    tokens = login_user(client)

    response = client.post(
        "/documents",
        headers=auth_headers(tokens["access_token"]),
        files={"upload": ("notes.txt", b"document contents", "text/plain")},
    )

    assert response.status_code == 201
    assert len(events) == 1
    document = events[0]
    assert str(document.id) == response.json()["id"]
    assert document.storage_key.endswith(".txt")
    assert Path(tmp_path, document.storage_key).is_file()


def test_document_upload_requires_authentication(client):
    response = client.post(
        "/documents",
        files={"upload": ("notes.txt", b"private", "text/plain")},
    )

    assert response.status_code == 401


def test_document_upload_rejects_unsupported_extension(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    register_user(client)
    tokens = login_user(client)

    response = client.post(
        "/documents",
        headers=auth_headers(tokens["access_token"]),
        files={"upload": ("payload.exe", b"content", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert list(tmp_path.rglob("*")) == []


def test_document_upload_rejects_empty_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    register_user(client)
    tokens = login_user(client)

    response = client.post(
        "/documents",
        headers=auth_headers(tokens["access_token"]),
        files={"upload": ("empty.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert list(tmp_path.rglob("*")) == []


def test_document_upload_enforces_size_limit_and_cleans_temporary_file(
    client,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "MAX_DOCUMENT_UPLOAD_BYTES", 4)
    register_user(client)
    tokens = login_user(client)

    response = client.post(
        "/documents",
        headers=auth_headers(tokens["access_token"]),
        files={"upload": ("too-large.txt", b"12345", "text/plain")},
    )

    assert response.status_code == 413
    assert list(tmp_path.rglob("*")) == []


def test_document_upload_sanitizes_client_filename(
    client,
    test_engine,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    register_user(client)
    tokens = login_user(client)

    response = client.post(
        "/documents",
        headers=auth_headers(tokens["access_token"]),
        files={"upload": ("..\\..\\private.txt", b"content", "text/plain")},
    )

    assert response.status_code == 201
    assert response.json()["original_filename"] == "private.txt"
    with Session(test_engine) as db:
        document = db.scalar(select(Document))
        assert document is not None
        assert Path(tmp_path, document.storage_key).is_file()


def test_document_metadata_can_be_read_updated_and_deleted(
    client,
    test_engine,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    register_user(client)
    tokens = login_user(client)
    headers = auth_headers(tokens["access_token"])
    uploaded = client.post(
        "/documents",
        headers=headers,
        data={"title": "Original title", "description": "Original description"},
        files={"upload": ("notes.txt", b"document contents", "text/plain")},
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]

    listing = client.get("/documents", headers=headers)
    assert listing.status_code == 200
    assert [document["id"] for document in listing.json()] == [document_id]

    detail = client.get(f"/documents/{document_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["description"] == "Original description"

    updated = client.patch(
        f"/documents/{document_id}",
        headers=headers,
        json={"title": "Updated title", "description": None},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated title"
    assert updated.json()["description"] is None

    with Session(test_engine) as db:
        document = db.get(Document, UUID(document_id))
        assert document is not None
        stored_file = Path(tmp_path, document.storage_key)

    deleted = client.delete(f"/documents/{document_id}", headers=headers)
    assert deleted.status_code == 204
    assert not stored_file.exists()
    assert client.get(f"/documents/{document_id}", headers=headers).status_code == 404
    with Session(test_engine) as db:
        assert db.get(Document, UUID(document_id)) is None


def test_document_metadata_operations_are_owner_scoped(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_STORAGE_DIR", str(tmp_path))
    register_user(client)
    owner_tokens = login_user(client)
    uploaded = client.post(
        "/documents",
        headers=auth_headers(owner_tokens["access_token"]),
        files={"upload": ("notes.txt", b"private", "text/plain")},
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]

    register_user(client, "other@example.com")
    other_tokens = login_user(client, "other@example.com")
    other_headers = auth_headers(other_tokens["access_token"])
    assert client.get(f"/documents/{document_id}", headers=other_headers).status_code == 404
    assert client.patch(
        f"/documents/{document_id}",
        headers=other_headers,
        json={"title": "Changed"},
    ).status_code == 404
    assert client.delete(
        f"/documents/{document_id}",
        headers=other_headers,
    ).status_code == 404
