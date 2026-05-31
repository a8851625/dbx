from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DBX Enterprise API"
    app_version: str = "0.5.19-enterprise-web"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_static_dir: str = "/app/static"
    dbx_data_dir: str = "/app/data"
    database_url: str = Field(
        default="postgresql+psycopg://dbx:dbx@postgres:5432/dbx_enterprise"
    )
    database_echo: bool = False
    api_v1_prefix: str = "/api/v1"
    session_cookie_name: str = "dbx_enterprise_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_max_age_seconds: int = 60 * 60 * 8
    oidc_enabled: bool = False
    oidc_provider_name: str = "Enterprise SSO"
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_authorize_url: str = ""
    oidc_token_url: str = ""
    oidc_userinfo_url: str = ""
    oidc_issuer: str = ""
    oidc_jwks_url: str = ""
    oidc_discovery_url: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/callback"
    oidc_post_logout_redirect_uri: str = "http://localhost:1420/login"
    oidc_logout_url: str = ""
    oidc_allowed_email_domains: str = ""
    oidc_frontend_login_path: str = "/login"
    oidc_frontend_post_login_path: str = "/"
    oidc_default_role: str = "viewer"
    oidc_role_claim: str = "dbx_role"
    oidc_groups_claim: str = "groups"
    oidc_admin_roles: str = "dbx-admin,admin"
    oidc_editor_roles: str = "dbx-editor,editor"
    oidc_viewer_roles: str = "dbx-viewer,viewer"
    oidc_mock_mode: bool = False
    oidc_mock_subject: str = "mock-admin"
    oidc_mock_email: str = "admin@example.com"
    oidc_mock_name: str = "DBX Administrator"
    oidc_mock_username: str = "admin"
    dbx_web_base_url: str = "http://dbx:4224"
    dbx_web_internal_token: str = "dbx-enterprise-internal"
    enterprise_internal_token: str = "dbx-enterprise-internal"
    config_migration_auto_enabled: bool = True
    config_migration_auto_source: str = ""
    config_migration_auto_overwrite_existing: bool = False
    approval_scheduler_interval_seconds: int = 15
    approval_execution_lock_seconds: int = 60 * 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
