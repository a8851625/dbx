from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AuthStatusResponse(BaseModel):
    required: bool
    authenticated: bool
    setup_required: bool = False
    provider_name: str | None = None
    login_url: str | None = None
    mock_mode: bool = False


class AuthRedirectResponse(BaseModel):
    authorization_url: str


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider_id: str
    subject: str
    email: str
    display_name: str | None
    username: str | None
    role: str


class LogoutResponse(BaseModel):
    logout_url: str | None = None
