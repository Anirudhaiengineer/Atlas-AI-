from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    hash_password,
    verify_password,
    verify_unknown_user_password,
)
from app.core.logging import instrument_module
from app.models import User
from app.schemas.auth import RegisterRequest


class DuplicateEmail(Exception):
    pass


class InvalidCredentials(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def register_user(db: Session, payload: RegisterRequest) -> User:
    email = normalize_email(str(payload.email))
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise DuplicateEmail

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateEmail from exc
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_unknown_user_password(password)
        raise InvalidCredentials

    verified = verify_password(password, user.password_hash)
    if not verified or not user.is_active:
        raise InvalidCredentials
    return user


instrument_module(globals())
