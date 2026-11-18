from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import hash_refresh_token
from app.models import UserSession
from tests.conftest import auth_headers, login_user, register_user


def test_refresh_rotates_token_and_updates_session(client, test_engine):
    register_user(client)
    original = login_user(client)

    response = client.post(
        "/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )

    assert response.status_code == 200
    rotated = response.json()
    assert rotated["refresh_token"] != original["refresh_token"]
    with Session(test_engine) as db:
        session_id = UUID(original["refresh_token"].split(".", 1)[0])
        session = db.get(UserSession, session_id)
        assert session.refresh_token_hash == hash_refresh_token(rotated["refresh_token"])
        assert session.last_used_at is not None


def test_rotated_refresh_token_cannot_be_reused(client):
    register_user(client)
    original = login_user(client)
    rotated = client.post(
        "/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )
    assert rotated.status_code == 200
    client.cookies.clear()

    replay = client.post(
        "/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )

    assert replay.status_code == 401
    assert client.post(
        "/auth/refresh",
        json={"refresh_token": rotated.json()["refresh_token"]},
    ).status_code == 200


def test_refresh_after_logout_is_rejected(client):
    register_user(client)
    tokens = login_user(client)

    logout = client.post(
        "/auth/logout",
        headers=auth_headers(tokens["access_token"]),
    )
    refresh = client.post(
        "/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert logout.status_code == 204
    assert refresh.status_code == 401


def test_logout_all_revokes_every_session(client):
    register_user(client)
    first = login_user(client)
    second = login_user(client)

    response = client.post(
        "/auth/logout-all",
        headers=auth_headers(second["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 2
    assert client.get(
        "/auth/me",
        headers=auth_headers(first["access_token"]),
    ).status_code == 401


def test_session_listing_only_returns_active_sessions(client):
    register_user(client)
    first = login_user(client)
    second = login_user(client)
    client.post(
        "/auth/logout",
        headers=auth_headers(first["access_token"]),
    )

    response = client.get(
        "/auth/sessions",
        headers=auth_headers(second["access_token"]),
    )

    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 1
    assert {"id", "created_at", "last_used_at", "expires_at"} <= sessions[0].keys()
    assert "refresh_token_hash" not in sessions[0]


def test_user_can_revoke_own_session(client):
    register_user(client)
    tokens = login_user(client)
    session_id = tokens["refresh_token"].split(".", 1)[0]

    response = client.delete(
        f"/auth/sessions/{session_id}",
        headers=auth_headers(tokens["access_token"]),
    )

    assert response.status_code == 204
    assert client.post(
        "/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    ).status_code == 401


def test_user_cannot_revoke_another_users_session(client):
    register_user(client, "first@example.com")
    first = login_user(client, "first@example.com")
    register_user(client, "second@example.com")
    second = login_user(client, "second@example.com")
    second_session_id = second["refresh_token"].split(".", 1)[0]

    response = client.delete(
        f"/auth/sessions/{second_session_id}",
        headers=auth_headers(first["access_token"]),
    )

    assert response.status_code == 404


def test_expired_session_is_not_listed_or_refreshed(client, test_engine):
    register_user(client)
    tokens = login_user(client)
    session_id = UUID(tokens["refresh_token"].split(".", 1)[0])
    with Session(test_engine) as db:
        session = db.get(UserSession, session_id)
        session.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.commit()

    listed = client.get(
        "/auth/sessions",
        headers=auth_headers(tokens["access_token"]),
    )
    refreshed = client.post(
        "/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert listed.status_code == 401
    assert refreshed.status_code == 401
