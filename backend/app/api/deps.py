from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.services.auth import AuthService


def get_auth_service() -> AuthService:
    return AuthService(get_settings())


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserIdentity:
    token = request.cookies.get(get_settings().session_cookie_name)
    context = auth_service.read_session(db, token)
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    auth_service.touch_session(db, context.session)
    return context.user
