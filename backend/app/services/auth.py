from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.auth import IdentityProvider, OidcAuthRequest, UserIdentity, UserSession


@dataclass
class OidcAuthorizationContext:
    authorization_url: str
    state: str
    nonce: str


@dataclass
class SessionUserContext:
    user: UserIdentity
    session: UserSession


class AuthService:
    AUTH_REQUEST_TTL_SECONDS = 10 * 60

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._jwks_client: PyJWKClient | None = None

    def is_auth_enabled(self) -> bool:
        return self.settings.oidc_enabled or self.settings.oidc_mock_mode

    def get_provider(self, db: Session) -> IdentityProvider | None:
        return db.get(IdentityProvider, "default")

    def sync_default_provider(self, db: Session) -> IdentityProvider | None:
        if not self.is_auth_enabled():
            return None

        provider = db.get(IdentityProvider, "default")
        if provider is None:
            provider = IdentityProvider(id="default", name=self.settings.oidc_provider_name)
            db.add(provider)

        provider.name = self.settings.oidc_provider_name
        provider.issuer = self.settings.oidc_issuer or None
        provider.authorize_url = self.settings.oidc_authorize_url or "mock://authorize"
        provider.token_url = self.settings.oidc_token_url or "mock://token"
        provider.userinfo_url = self.settings.oidc_userinfo_url or "mock://userinfo"
        provider.logout_url = self.settings.oidc_logout_url or None
        provider.client_id = self.settings.oidc_client_id or "mock-client"
        provider.scopes = self.settings.oidc_scopes
        provider.enabled = True
        db.commit()
        db.refresh(provider)
        return provider

    def create_authorization_request(self, db: Session, provider: IdentityProvider) -> OidcAuthorizationContext:
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        db.add(
            OidcAuthRequest(
                state=state,
                provider_id=provider.id,
                nonce=nonce,
                expires_at=datetime.now(UTC) + timedelta(seconds=self.AUTH_REQUEST_TTL_SECONDS),
            )
        )
        db.commit()

        params = {
            "client_id": provider.client_id,
            "response_type": "code",
            "scope": provider.scopes,
            "redirect_uri": self.settings.oidc_redirect_uri,
            "state": state,
            "nonce": nonce,
        }
        if self.settings.oidc_mock_mode:
            authorization_url = (
                f"{self.settings.api_v1_prefix}/auth/callback?{urlencode({'code': 'mock-login', 'state': state})}"
            )
        else:
            authorization_url = f"{provider.authorize_url}?{urlencode(params)}"
        return OidcAuthorizationContext(
            authorization_url=authorization_url,
            state=state,
            nonce=nonce,
        )

    async def exchange_code_for_claims(self, code: str) -> dict[str, Any]:
        if self.settings.oidc_mock_mode:
            return {
                "sub": self.settings.oidc_mock_subject,
                "email": self.settings.oidc_mock_email,
                "name": self.settings.oidc_mock_name,
                "preferred_username": self.settings.oidc_mock_username,
                "code": code,
            }

        if not self.settings.oidc_enabled:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC is not enabled")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token_response = await client.post(
                    self.settings.oidc_token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": self.settings.oidc_redirect_uri,
                        "client_id": self.settings.oidc_client_id,
                        "client_secret": self.settings.oidc_client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
                token_response.raise_for_status()
                token_payload = token_response.json()

                access_token = token_payload.get("access_token")
                if not access_token:
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC token missing access_token")
                id_token = str(token_payload.get("id_token") or "")
                if not id_token:
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC token missing id_token")

                id_claims = self.verify_id_token(id_token)

                userinfo_response = await client.get(
                    self.settings.oidc_userinfo_url,
                    headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                )
                userinfo_response.raise_for_status()
                userinfo_claims = userinfo_response.json()
                claims = {**id_claims, **userinfo_claims}
                claims["access_token"] = access_token
                claims["id_token"] = id_token
                return claims
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="OIDC upstream request failed",
            ) from exc

    def consume_authorization_request(self, db: Session, state: str) -> OidcAuthRequest:
        auth_request = db.get(OidcAuthRequest, state)
        if auth_request is None or auth_request.consumed_at is not None or auth_request.expires_at <= datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OIDC authorization state is invalid or expired",
            )

        auth_request.consumed_at = datetime.now(UTC)
        db.commit()
        db.refresh(auth_request)
        return auth_request

    def validate_claims(self, claims: dict[str, Any]) -> None:
        email = str(claims.get("email") or "").strip().lower()
        subject = str(claims.get("sub") or "").strip()
        if not email or not subject:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC userinfo missing email or sub")

        allowed_domains = [
            domain.strip().lower() for domain in self.settings.oidc_allowed_email_domains.split(",") if domain.strip()
        ]
        if allowed_domains and email.split("@")[-1] not in allowed_domains:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email domain is not allowed")

    def validate_auth_request_claims(self, claims: dict[str, Any], auth_request: OidcAuthRequest) -> None:
        self.validate_claims(claims)
        token_nonce = str(claims.get("nonce") or "").strip()
        if not token_nonce or token_nonce != auth_request.nonce:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC nonce is invalid")

    def upsert_user(self, db: Session, provider: IdentityProvider, claims: dict[str, Any]) -> UserIdentity:
        subject = str(claims["sub"])
        email = str(claims["email"]).strip().lower()

        stmt = select(UserIdentity).where(UserIdentity.provider_id == provider.id, UserIdentity.subject == subject)
        user = db.execute(stmt).scalar_one_or_none()
        if user is None:
            user = db.execute(select(UserIdentity).where(UserIdentity.email == email)).scalar_one_or_none()

        if user is None:
            user = UserIdentity(provider_id=provider.id, subject=subject, email=email)
            db.add(user)

        user.provider_id = provider.id
        user.subject = subject
        user.email = email
        user.display_name = str(claims.get("name") or claims.get("preferred_username") or email)
        user.username = str(claims.get("preferred_username") or "") or None
        user.role = self.resolve_user_role(claims, existing_role=user.role)
        user.is_active = True
        user.claims = claims
        user.last_login_at = datetime.now(UTC)
        db.commit()
        db.refresh(user)
        return user

    def create_session(
        self,
        db: Session,
        *,
        user: UserIdentity,
        provider_id: str,
        request: Request,
        state: str | None,
        nonce: str | None,
    ) -> UserSession:
        raw_token = secrets.token_urlsafe(48)
        session = UserSession(
            user_id=user.id,
            provider_id=provider_id,
            session_token=self._hash_token(raw_token),
            state=state,
            nonce=nonce,
            source_ip=self._client_ip(request),
            user_agent=request.headers.get("user-agent"),
            expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.session_max_age_seconds),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session.session_token = raw_token
        return session

    def read_session(self, db: Session, token: str | None) -> SessionUserContext | None:
        if not token:
            return None
        hashed_token = self._hash_token(token)
        stmt = (
            select(UserSession)
            .where(UserSession.session_token == hashed_token, UserSession.revoked_at.is_(None))
        )
        session = db.execute(stmt).scalar_one_or_none()
        if session is None or session.expires_at <= datetime.now(UTC):
            return None
        user = db.get(UserIdentity, session.user_id)
        if user is None or not user.is_active:
            return None
        return SessionUserContext(user=user, session=session)

    def touch_session(self, db: Session, session: UserSession) -> None:
        session.last_seen_at = datetime.now(UTC)
        db.commit()

    def revoke_session(self, db: Session, token: str | None) -> None:
        if not token:
            return
        hashed_token = self._hash_token(token)
        stmt = select(UserSession).where(UserSession.session_token == hashed_token, UserSession.revoked_at.is_(None))
        session = db.execute(stmt).scalar_one_or_none()
        if session is None:
            return
        session.revoked_at = datetime.now(UTC)
        db.commit()

    def frontend_redirect(self, authenticated: bool) -> str:
        path = self.settings.oidc_frontend_post_login_path if authenticated else self.settings.oidc_frontend_login_path
        return path or "/"

    def verify_id_token(self, id_token: str) -> dict[str, Any]:
        if self.settings.oidc_mock_mode:
            return jwt.decode(id_token, options={"verify_signature": False, "verify_aud": False})
        issuer = self._oidc_issuer()
        jwks_url = self._oidc_jwks_url()
        if not issuer or not jwks_url:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC issuer and JWKS URL are required")
        try:
            signing_key = self._jwks_client_for(jwks_url).get_signing_key_from_jwt(id_token).key
            return jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self.settings.oidc_client_id,
                issuer=issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except InvalidTokenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC id_token is invalid") from exc

    def resolve_user_role(self, claims: dict[str, Any], *, existing_role: str | None = None) -> str:
        claim_values = self._claim_values(claims.get(self.settings.oidc_role_claim))
        claim_values.extend(self._claim_values(claims.get(self.settings.oidc_groups_claim)))
        normalized = {value.strip().lower() for value in claim_values if value.strip()}
        role_map = (
            ("admin", self.settings.oidc_admin_roles),
            ("editor", self.settings.oidc_editor_roles),
            ("viewer", self.settings.oidc_viewer_roles),
        )
        for role, configured_values in role_map:
            allowed = {value.strip().lower() for value in configured_values.split(",") if value.strip()}
            if normalized & allowed:
                return role
        return existing_role or self.settings.oidc_default_role or "viewer"

    def _oidc_issuer(self) -> str:
        if self.settings.oidc_issuer:
            return self.settings.oidc_issuer.rstrip("/")
        return self._issuer_from_discovery_url()

    def _oidc_jwks_url(self) -> str:
        if self.settings.oidc_jwks_url:
            return self.settings.oidc_jwks_url
        return ""

    def _issuer_from_discovery_url(self) -> str:
        marker = "/.well-known/openid-configuration"
        if marker in self.settings.oidc_discovery_url:
            return self.settings.oidc_discovery_url.split(marker, 1)[0].rstrip("/")
        return ""

    def _jwks_client_for(self, jwks_url: str) -> PyJWKClient:
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(jwks_url)
        return self._jwks_client

    def _claim_values(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list | tuple | set):
            return [str(part).strip() for part in value if str(part).strip()]
        return [str(value).strip()]

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _client_ip(self, request: Request) -> str | None:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else None
