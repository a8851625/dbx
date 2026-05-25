from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import CurrentUserResponse


class ResourcePolicyResponse(BaseModel):
    principal_type: str
    principal_ref: str
    resource_type: str
    resource_key: str
    permission_code: str | None
    effect: str
    conditions: dict[str, Any]


class ResourcePolicyDetailResponse(ResourcePolicyResponse):
    model_config = ConfigDict(from_attributes=True)

    id: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


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


class RoleBindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    role_code: str
    granted_by: str | None
    expires_at: datetime | None
    created_at: datetime


class RoleBindingUpsertRequest(BaseModel):
    user_id: str
    role_code: str
    expires_at: datetime | None = None


class ResourcePolicyUpsertRequest(BaseModel):
    principal_type: str
    principal_ref: str
    resource_type: str
    resource_key: str
    permission_code: str | None = None
    effect: str = "allow"
    conditions: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
