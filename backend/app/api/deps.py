from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.authorization import AuthorizationService


def get_auth_service() -> AuthService:
    return AuthService(get_settings())


def get_authorization_service() -> AuthorizationService:
    return AuthorizationService()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserIdentity:
    token = request.cookies.get(get_settings().session_cookie_name)
    context = auth_service.read_session(db, token)
    if context is None:
        audit_service = AuditService()
        audit_service.record_event(
            db,
            event_type="auth.session.required",
            category="auth",
            action="authenticate",
            outcome="failure",
            actor=audit_service.build_actor(request=request),
            resource_type="api",
            resource_name=str(request.url.path),
            payload={"reason": "Authentication required"},
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    auth_service.touch_session(db, context.session)
    return context.user


def require_permission(permission_code: str):
    def dependency(
        request: Request,
        current_user: UserIdentity = Depends(get_current_user),
        db: Session = Depends(get_db),
        authorization_service: AuthorizationService = Depends(get_authorization_service),
    ) -> UserIdentity:
        context = authorization_service.build_access_context(db, current_user)
        decision = authorization_service.check_permission(context, permission_code)
        if not decision.allowed:
            audit_service = AuditService()
            audit_service.record_event(
                db,
                event_type="access.permission.denied",
                category="access",
                action="authorize",
                outcome="failure",
                actor=audit_service.build_actor(user=current_user, request=request),
                resource_type="permission",
                resource_id=permission_code,
                resource_name=str(request.url.path),
                payload={"permission": permission_code, "reason": decision.reason or "Forbidden"},
                commit=True,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason or "Forbidden")
        return current_user

    return dependency
