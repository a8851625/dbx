from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_current_user
from app.config import get_settings
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.schemas.auth import AuthRedirectResponse, AuthStatusResponse, CurrentUserResponse, LogoutResponse
from app.services.audit import AuditService
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_audit_service() -> AuditService:
    return AuditService()


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthStatusResponse:
    settings = get_settings()
    provider = auth_service.sync_default_provider(db)
    token = request.cookies.get(settings.session_cookie_name)
    context = auth_service.read_session(db, token)
    login_url = None
    if auth_service.is_auth_enabled() and provider is not None:
        login_url = f"{settings.api_v1_prefix}/auth/login"
    return AuthStatusResponse(
        required=auth_service.is_auth_enabled(),
        authenticated=context is not None,
        setup_required=False,
        provider_name=provider.name if provider else None,
        login_url=login_url,
        mock_mode=settings.oidc_mock_mode,
    )


@router.post("/login", response_model=AuthRedirectResponse)
def login(
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> AuthRedirectResponse:
    if not auth_service.is_auth_enabled():
        audit_service.record_event(
            db,
            event_type="auth.login.redirect_failed",
            category="auth",
            action="login",
            outcome="failure",
            actor=audit_service.build_actor(request=request),
            resource_type="identity_provider",
            payload={"reason": "OIDC is not configured"},
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC is not configured")

    provider = auth_service.sync_default_provider(db)
    if provider is None:
        audit_service.record_event(
            db,
            event_type="auth.login.redirect_failed",
            category="auth",
            action="login",
            outcome="failure",
            actor=audit_service.build_actor(request=request),
            resource_type="identity_provider",
            payload={"reason": "Identity provider is unavailable"},
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Identity provider is unavailable")

    context = auth_service.create_authorization_request(db, provider)
    audit_service.record_event(
        db,
        event_type="auth.login.redirect",
        category="auth",
        action="login",
        actor=audit_service.build_actor(request=request),
        resource_type="identity_provider",
        resource_id=provider.id,
        resource_name=provider.name,
        payload={"provider_id": provider.id, "provider_name": provider.name},
        commit=True,
    )
    return AuthRedirectResponse(authorization_url=context.authorization_url)


@router.get("/callback")
async def callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    provider = auth_service.sync_default_provider(db)
    if provider is None:
        audit_service.record_event(
            db,
            event_type="auth.login.failed",
            category="auth",
            action="login",
            outcome="failure",
            actor=audit_service.build_actor(request=request),
            resource_type="identity_provider",
            payload={"state": state, "reason": "Identity provider is unavailable"},
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Identity provider is unavailable")

    try:
        auth_request = auth_service.consume_authorization_request(db, state)
        claims = await auth_service.exchange_code_for_claims(code)
        auth_service.validate_auth_request_claims(claims, auth_request)
    except Exception as exc:
        audit_service.record_event(
            db,
            event_type="auth.login.failed",
            category="auth",
            action="login",
            outcome="failure",
            actor=audit_service.build_actor(request=request),
            resource_type="identity_provider",
            resource_id=provider.id,
            resource_name=provider.name,
            payload={"state": state, "error": str(exc)},
            commit=True,
        )
        raise
    user = auth_service.upsert_user(db, provider, claims)
    session = auth_service.create_session(
        db,
        user=user,
        provider_id=provider.id,
        request=request,
        state=state,
        nonce=auth_request.nonce,
    )
    audit_service.record_event(
        db,
        event_type="auth.login.succeeded",
        category="auth",
        action="login",
        actor=audit_service.build_actor(user=user, request=request),
        resource_type="user_session",
        resource_id=session.id,
        resource_name=user.email,
        payload={
            "provider_id": provider.id,
            "subject": user.subject,
            "email": user.email,
            "session_id": session.id,
        },
        commit=True,
    )

    settings = get_settings()
    redirect = RedirectResponse(url=auth_service.frontend_redirect(authenticated=True), status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=settings.session_cookie_name,
        value=session.session_token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite=settings.session_cookie_samesite,
        secure=settings.session_cookie_secure,
        path="/",
    )
    return redirect


@router.post("/logout", response_model=LogoutResponse)
def logout(
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    session_context = auth_service.read_session(db, token)
    auth_service.revoke_session(db, token)
    audit_service.record_event(
        db,
        event_type="auth.logout",
        category="auth",
        action="logout",
        actor=audit_service.build_actor(user=session_context.user if session_context is not None else None, request=request),
        resource_type="user_session",
        resource_id=session_context.session.id if session_context is not None else None,
        resource_name=session_context.user.email if session_context is not None else None,
        payload={
            "provider_id": session_context.session.provider_id if session_context is not None else None,
            "session_id": session_context.session.id if session_context is not None else None,
        },
        commit=True,
    )
    payload = LogoutResponse(logout_url=None)
    if settings.oidc_logout_url:
        query = urlencode({"post_logout_redirect_uri": settings.oidc_post_logout_redirect_uri})
        payload.logout_url = f"{settings.oidc_logout_url}?{query}"

    response = Response(content=payload.model_dump_json(), media_type="application/json")
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return response


@router.get("/me", response_model=CurrentUserResponse)
def me(current_user: UserIdentity = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse.model_validate(current_user)
