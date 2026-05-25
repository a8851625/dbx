from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(Base):
    __tablename__ = "role"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    built_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Permission(Base):
    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(String(96), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = (
        UniqueConstraint("role_code", "permission_code", name="uq_role_permission_binding"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    role_code: Mapped[str] = mapped_column(ForeignKey("role.code", ondelete="CASCADE"), nullable=False)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permission.code", ondelete="CASCADE"), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False, default="allow", server_default="allow")
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserRoleBinding(Base):
    __tablename__ = "user_role_binding"
    __table_args__ = (
        UniqueConstraint("user_id", "role_code", name="uq_user_role_binding_user_role"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="CASCADE"), nullable=False)
    role_code: Mapped[str] = mapped_column(ForeignKey("role.code", ondelete="CASCADE"), nullable=False)
    granted_by: Mapped[str | None] = mapped_column(ForeignKey("user_identity.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResourcePolicy(Base):
    __tablename__ = "resource_policy"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(255), nullable=False)
    permission_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    effect: Mapped[str] = mapped_column(String(16), nullable=False, default="allow", server_default="allow")
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
