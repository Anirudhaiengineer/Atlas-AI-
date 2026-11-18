import hmac
import uuid
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import instrument_module
from app.core.security import (
    create_access_token,
    create_refresh_token,
    ensure_utc,
    hash_refresh_token,
    refresh_token_session_id,
    utc_now,
)
from app.models import User, UserSession


class InvalidRefreshToken(Exception):
    pass


def create_session(
    db: Session,
    user: User,
    user_agent: str | None,
) -> tuple[UserSession, str, str]:
    now = utc_now()
    session_id = uuid.uuid4()
    refresh_token = create_refresh_token(session_id)
    session = UserSession(
        id=session_id,
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        created_at=now,
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        last_used_at=now,
        user_agent=user_agent[:512] if user_agent else None,
    )
    access_token = create_access_token(user.id, session.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, access_token, refresh_token


def rotate_refresh_token(
    db: Session,
    token: str,
) -> tuple[User, UserSession, str, str]:
    try:
        session_id = refresh_token_session_id(token)
    except (ValueError, AttributeError) as exc:
        raise InvalidRefreshToken from exc

    session = db.scalar(
        select(UserSession)
        .where(UserSession.id == session_id)
        .with_for_update()
    )
    if session is None:
        raise InvalidRefreshToken

    now = utc_now()
    if session.revoked_at is not None or ensure_utc(session.expires_at) <= now:
        raise InvalidRefreshToken

    presented_hash = hash_refresh_token(token)
    if not hmac.compare_digest(session.refresh_token_hash, presented_hash):
        raise InvalidRefreshToken

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        session.revoked_at = now
        db.commit()
        raise InvalidRefreshToken

    new_refresh_token = create_refresh_token(session.id)
    access_token = create_access_token(user.id, session.id)
    session.refresh_token_hash = hash_refresh_token(new_refresh_token)
    session.last_used_at = now
    db.commit()
    db.refresh(session)
    return user, session, access_token, new_refresh_token


def revoke_session(db: Session, session: UserSession) -> None:
    if session.revoked_at is None:
        session.revoked_at = utc_now()
        db.commit()


def revoke_all_sessions(db: Session, user_id: UUID) -> int:
    result = db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now())
    )
    db.commit()
    return result.rowcount or 0


def list_active_sessions(db: Session, user_id: UUID) -> list[UserSession]:
    now = utc_now()
    sessions = db.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .order_by(UserSession.created_at.desc())
    )
    return [
        session
        for session in sessions
        if ensure_utc(session.expires_at) > now
    ]


instrument_module(globals())
