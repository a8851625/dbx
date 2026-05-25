from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from app.config import Settings
from app.models.auth import IdentityProvider, OidcAuthRequest
from app.services.auth import AuthService


class FakeDB:
    def __init__(self) -> None:
        self.auth_requests: dict[str, OidcAuthRequest] = {}

    def add(self, obj: object) -> None:
        if isinstance(obj, OidcAuthRequest):
            self.auth_requests[obj.state] = obj

    def commit(self) -> None:
        return None

    def refresh(self, _: object) -> None:
        return None

    def get(self, model: type[object], key: str) -> OidcAuthRequest | None:
        if model is OidcAuthRequest:
            return self.auth_requests.get(key)
        return None


def make_provider() -> IdentityProvider:
    return IdentityProvider(
        id="default",
        name="Enterprise SSO",
        authorize_url="https://idp.example.com/authorize",
        token_url="https://idp.example.com/token",
        userinfo_url="https://idp.example.com/userinfo",
        client_id="dbx-client",
        scopes="openid profile email",
    )


class AuthServiceTests(unittest.TestCase):
    def test_create_authorization_request_persists_state_for_mock_mode(self) -> None:
        service = AuthService(Settings(oidc_mock_mode=True, api_v1_prefix="/api/v1"))
        db = FakeDB()

        context = service.create_authorization_request(db, make_provider())

        self.assertIn(context.state, db.auth_requests)
        stored = db.auth_requests[context.state]
        self.assertEqual(stored.nonce, context.nonce)
        self.assertTrue(context.authorization_url.startswith("/api/v1/auth/callback?"))

        query = parse_qs(urlparse(context.authorization_url).query)
        self.assertEqual(query["code"], ["mock-login"])
        self.assertEqual(query["state"], [context.state])

    def test_consume_authorization_request_marks_state_consumed(self) -> None:
        service = AuthService(Settings())
        db = FakeDB()
        auth_request = OidcAuthRequest(
            state="state-1",
            provider_id="default",
            nonce="nonce-1",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        db.add(auth_request)

        consumed = service.consume_authorization_request(db, "state-1")

        self.assertIs(consumed, auth_request)
        self.assertIsNotNone(consumed.consumed_at)

    def test_consume_authorization_request_rejects_expired_state(self) -> None:
        service = AuthService(Settings())
        db = FakeDB()
        db.add(
            OidcAuthRequest(
                state="expired-state",
                provider_id="default",
                nonce="nonce-1",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )

        with self.assertRaises(HTTPException) as ctx:
            service.consume_authorization_request(db, "expired-state")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_validate_claims_rejects_disallowed_email_domain(self) -> None:
        service = AuthService(Settings(oidc_allowed_email_domains="example.com"))

        with self.assertRaises(HTTPException) as ctx:
            service.validate_claims({"sub": "user-1", "email": "admin@other.com"})

        self.assertEqual(ctx.exception.status_code, 403)


class AuthServiceAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_exchange_code_for_claims_returns_mock_claims(self) -> None:
        service = AuthService(
            Settings(
                oidc_mock_mode=True,
                oidc_mock_subject="mock-user",
                oidc_mock_email="mock@example.com",
                oidc_mock_name="Mock User",
                oidc_mock_username="mock-user",
            )
        )

        claims = await service.exchange_code_for_claims("mock-code")

        self.assertEqual(claims["sub"], "mock-user")
        self.assertEqual(claims["email"], "mock@example.com")
        self.assertEqual(claims["code"], "mock-code")


if __name__ == "__main__":
    unittest.main()
