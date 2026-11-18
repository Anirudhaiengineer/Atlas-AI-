import hmac
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AuthenticatedContext, get_current_auth
from app.core.config import settings
from app.core.logging import instrument_module, log_execution
from app.core.rate_limit import limiter
from app.database.connection import get_db
from app.models import UserSession
from app.schemas.auth import (
    LoginRequest,
    LogoutAllResponse,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import (
    DuplicateEmail,
    InvalidCredentials,
    authenticate_user,
    register_user,
)
from app.services.session_service import (
    InvalidRefreshToken,
    create_session,
    list_active_sessions,
    revoke_all_sessions,
    revoke_session,
    rotate_refresh_token,
)


router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite="lax",
        path="/auth",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite="lax",
        path="/auth",
    )


def _token_response(access_token: str, refresh_token: str) -> TokenResponse:
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
@log_execution
def register(
    request: Request,
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> UserResponse:
    try:
        user = register_user(db, payload)
    except DuplicateEmail:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from None
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
@log_execution
def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    try:
        user = authenticate_user(db, str(payload.email), payload.password)
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    try:
        _, access_token, refresh_token = create_session(
            db,
            user,
            request.headers.get("user-agent"),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from exc

    _set_refresh_cookie(response, refresh_token)
    return _token_response(access_token, refresh_token)


@router.get("/me", response_model=UserResponse)
@log_execution
def get_me(
    auth: AuthenticatedContext = Depends(get_current_auth),
) -> UserResponse:
    return UserResponse.model_validate(auth.user)


@router.post("/refresh", response_model=TokenResponse)
@log_execution
def refresh(
    response: Response,
    payload: RefreshRequest | None = None,
    refresh_cookie: str | None = Cookie(
        default=None, alias=settings.REFRESH_COOKIE_NAME
    ),
    db: Session = Depends(get_db),
) -> TokenResponse:
    body_token = payload.refresh_token if payload is not None else None
    if refresh_cookie and body_token and not hmac.compare_digest(
        refresh_cookie, body_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )
    token = refresh_cookie or body_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    try:
        _, _, access_token, rotated_token = rotate_refresh_token(db, token)
    except InvalidRefreshToken:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        ) from None
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from exc

    _set_refresh_cookie(response, rotated_token)
    return _token_response(access_token, rotated_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@log_execution
def logout(
    response: Response,
    auth: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    session = db.get(UserSession, auth.session_id)
    if session is not None:
        revoke_session(db, session)
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", response_model=LogoutAllResponse)
@log_execution
def logout_all(
    response: Response,
    auth: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> LogoutAllResponse:
    revoked = revoke_all_sessions(db, auth.user.id)
    _clear_refresh_cookie(response)
    return LogoutAllResponse(revoked_sessions=revoked)


@router.get("/sessions", response_model=list[SessionResponse])
@log_execution
def get_sessions(
    auth: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> list[SessionResponse]:
    sessions = list_active_sessions(db, auth.user.id)
    return [SessionResponse.model_validate(session) for session in sessions]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@log_execution
def delete_session(
    session_id: UUID,
    response: Response,
    auth: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    session = db.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == auth.user.id,
        )
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )
    revoke_session(db, session)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


instrument_module(globals())
