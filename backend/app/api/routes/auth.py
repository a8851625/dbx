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
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


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
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthRedirectResponse:
    if not auth_service.is_auth_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC is not configured")

    provider = auth_service.sync_default_provider(db)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Identity provider is unavailable")

    context = auth_service.create_authorization_request(db, provider)
    return AuthRedirectResponse(authorization_url=context.authorization_url)


@router.get("/callback")
async def callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> Response:
    provider = auth_service.sync_default_provider(db)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Identity provider is unavailable")

    auth_request = auth_service.consume_authorization_request(db, state)
    claims = await auth_service.exchange_code_for_claims(code)
    auth_service.validate_claims(claims)
    user = auth_service.upsert_user(db, provider, claims)
    session = auth_service.create_session(
        db,
        user=user,
        provider_id=provider.id,
        request=request,
        state=state,
        nonce=auth_request.nonce,
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
) -> Response:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    auth_service.revoke_session(db, token)
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
