from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.auth import CurrentUserResponse


class ResourcePolicyResponse(BaseModel):
    principal_type: str
    principal_ref: str
    resource_type: str
    resource_key: str
    permission_code: str | None
    effect: str
    conditions: dict[str, Any]


class AccessContextResponse(BaseModel):
    user: CurrentUserResponse
    roles: list[str]
    permissions: list[str]
    policies: list[ResourcePolicyResponse]


class AccessCheckRequest(BaseModel):
    permission: str
    datasource_id: str | None = None
    database: str | None = None
    schema: str | None = None
    table: str | None = None


class AccessCheckResponse(BaseModel):
    allowed: bool
    reason: str | None = None
