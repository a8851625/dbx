from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.audit import AuditService
from app.services.auth import AuthService


AUDITED_API_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/access",
    "/api/v1/approval",
    "/api/v1/audit",
    "/api/connection",
    "/api/app-settings",
    "/api/desktop-settings",
    "/api/editor-settings",
    "/api/layout",
    "/api/saved-sql",
    "/api/history",
    "/api/ai",
    "/api/query",
    "/api/internal/query",
)
SKIPPED_API_SUFFIXES = ("/status", "/me")
BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_PAYLOAD_TEXT = 2_000


class AuditContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, auth_service_factory: Callable[[], AuthService]) -> None:
        super().__init__(app)
        self.auth_service_factory = auth_service_factory
        self.audit_service = AuditService()

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _header_or_uuid(request, "x-request-id")
        trace_id = _header_or_uuid(request, "x-trace-id")
        request.state.request_id = request_id
        request.state.trace_id = trace_id

        start = time.perf_counter()
        response: Response | None = None
        error: Exception | None = None
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            error = exc
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            if response is not None:
                response.headers["X-Request-Id"] = request_id
                response.headers["X-Trace-Id"] = trace_id
            self._record_request_event(request, response, error, duration_ms)

    def _record_request_event(
        self,
        request: Request,
        response: Response | None,
        error: Exception | None,
        duration_ms: int,
    ) -> None:
        if not _should_audit_request(request):
            return

        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            auth_service = self.auth_service_factory()
            token = request.cookies.get(auth_service.settings.session_cookie_name)
            context = auth_service.read_session(db, token) if token else None
            status_code = response.status_code if response is not None else 500
            actor = self.audit_service.build_actor(user=context.user if context else None, request=request)
            self.audit_service.record_event(
                db,
                event_type=f"api.{_api_event_name(request)}",
                category=_api_category(request.url.path),
                action=_api_action(request),
                outcome="success" if status_code < 400 and error is None else "failure",
                actor=actor,
                resource_type=_api_resource_type(request.url.path),
                resource_id=_api_resource_id(request.url.path),
                resource_name=request.url.path,
                payload={
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "query": dict(request.query_params),
                    "request_body": _safe_request_body(request),
                    "error": None if error is None else str(error),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def _header_or_uuid(request: Request, name: str) -> str:
    value = request.headers.get(name)
    return value.strip() if value and value.strip() else str(uuid4())


def _should_audit_request(request: Request) -> bool:
    path = request.url.path
    if path.endswith(SKIPPED_API_SUFFIXES):
        return False
    if path.startswith("/api/v1/audit") and request.method == "GET":
        return False
    if path.startswith("/api/query/execute") or path.startswith("/api/internal/query/execute"):
        return False
    return any(path.startswith(prefix) for prefix in AUDITED_API_PREFIXES)


def _api_event_name(request: Request) -> str:
    path = request.url.path.strip("/").replace("api/v1/", "").replace("api/", "")
    return f"{path.replace('/', '.')}.{request.method.lower()}"


def _api_category(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if parts[:2] == ["api", "v1"] and len(parts) > 2:
        return parts[2]
    if len(parts) > 1:
        return "connection" if parts[1] == "connection" else "config" if parts[1] in {"app-settings", "desktop-settings", "editor-settings", "layout", "saved-sql", "history", "ai"} else parts[1]
    return "api"


def _api_action(request: Request) -> str:
    if request.method == "GET":
        return "view"
    if request.method == "POST":
        return "execute" if "/query/" in request.url.path else "create"
    if request.method == "PUT":
        return "update"
    if request.method == "DELETE":
        return "delete"
    return request.method.lower()


def _api_resource_type(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if parts[:2] == ["api", "v1"] and len(parts) > 2:
        return parts[2]
    if len(parts) > 1:
        return parts[1]
    return "api"


def _api_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    for part in reversed(parts):
        if part not in {"api", "v1", "admin", "tickets", "flows", "role-bindings", "resource-policies"}:
            return part
    return None


def _safe_request_body(request: Request) -> dict[str, Any] | None:
    if request.method not in BODY_METHODS:
        return None
    cached_body = getattr(request, "_body", None)
    if not cached_body:
        return None
    text = cached_body.decode("utf-8", errors="replace")
    if len(text) > MAX_PAYLOAD_TEXT:
        text = text[: MAX_PAYLOAD_TEXT - 15] + "...<truncated>"
    return _redact_payload_text(text)


def _redact_payload_text(text: str) -> dict[str, Any]:
    lowered = text.lower()
    if any(secret in lowered for secret in ("password", "token", "secret", "client_secret")):
        return {"redacted": True, "length": len(text)}
    return {"text": text}
