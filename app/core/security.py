import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings
from app.core.logging import instrument_module


password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))


class InvalidAccessToken(Exception):
    pass


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def verify_unknown_user_password(password: str) -> None:
    verify_password(password, _dummy_password_hash)


def _jwt_secret() -> str:
    secret = settings.AUTH_JWT_SECRET
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("AUTH_JWT_SECRET must contain at least 32 bytes.")
    return secret


def create_access_token(user_id: UUID, session_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(16),
        "iss": settings.AUTH_JWT_ISSUER,
        "aud": settings.AUTH_JWT_AUDIENCE,
    }
    return jwt.encode(claims, _jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_JWT_AUDIENCE,
            options={
                "require": ["sub", "sid", "type", "iat", "exp", "jti", "iss", "aud"]
            },
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidAccessToken from exc

    if not isinstance(claims, dict) or claims.get("type") != "access":
        raise InvalidAccessToken
    return claims


def create_refresh_token(session_id: UUID) -> str:
    return f"{session_id}.{secrets.token_urlsafe(32)}"


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_session_id(token: str) -> UUID:
    session_id, separator, secret = token.partition(".")
    if not separator or not secret:
        raise ValueError("Malformed refresh token.")
    return UUID(session_id)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


instrument_module(globals())
