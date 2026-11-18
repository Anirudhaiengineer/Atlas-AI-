from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User
from tests.conftest import auth_headers, login_user, register_user


def test_registration_returns_safe_user_profile(client):
    response = client.post(
        "/auth/register",
        json={"email": "Reader@Example.com", "password": "SecurePassword1!"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "reader@example.com"
    assert UUID(body["id"])
    assert "password_hash" not in body
    assert "password" not in body


def test_duplicate_email_is_rejected_after_normalization(client):
    register_user(client, "Reader@Example.com")

    response = client.post(
        "/auth/register",
        json={"email": "reader@example.com", "password": "SecurePassword1!"},
    )

    assert response.status_code == 409


def test_invalid_email_is_rejected(client):
    response = client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "SecurePassword1!"},
    )

    assert response.status_code == 422


def test_weak_password_is_rejected(client):
    response = client.post(
        "/auth/register",
        json={"email": "reader@example.com", "password": "weakpassword"},
    )

    assert response.status_code == 422


def test_successful_login_returns_tokens(client):
    register_user(client)

    response = client.post(
        "/auth/login",
        json={"email": "READER@example.com", "password": "SecurePassword1!"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["refresh_token"]
    assert response.json()["token_type"] == "bearer"
    assert response.headers["set-cookie"].startswith("atlasai_refresh=")


def test_incorrect_password_returns_generic_error(client):
    register_user(client)

    response = client.post(
        "/auth/login",
        json={"email": "reader@example.com", "password": "WrongPassword1!"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_me_rejects_missing_token(client):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_accepts_valid_token(client):
    register_user(client)
    tokens = login_user(client)

    response = client.get(
        "/auth/me",
        headers=auth_headers(tokens["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["email"] == "reader@example.com"


def test_me_rejects_expired_access_token(client):
    register_user(client)
    tokens = login_user(client)
    claims = jwt.decode(
        tokens["access_token"],
        settings.AUTH_JWT_SECRET,
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_aud": False},
    )
    claims["iat"] = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp())
    claims["exp"] = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())
    expired_token = jwt.encode(
        claims,
        settings.AUTH_JWT_SECRET,
        algorithm="HS256",
    )

    response = client.get("/auth/me", headers=auth_headers(expired_token))
    assert response.status_code == 401


def test_inactive_user_cannot_authenticate(client, test_engine):
    profile = register_user(client)
    tokens = login_user(client)
    with Session(test_engine) as db:
        user = db.get(User, UUID(profile["id"]))
        user.is_active = False
        db.commit()

    protected = client.get(
        "/auth/me",
        headers=auth_headers(tokens["access_token"]),
    )
    login = client.post(
        "/auth/login",
        json={"email": "reader@example.com", "password": "SecurePassword1!"},
    )

    assert protected.status_code == 401
    assert login.status_code == 401
