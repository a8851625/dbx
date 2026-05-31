from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConnectionProfile(Base):
    __tablename__ = "connection_profile"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="uq_connection_profile_owner_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ConnectionSecret(Base):
    __tablename__ = "connection_secret"

    connection_id: Mapped[str] = mapped_column(
        ForeignKey("connection_profile.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_scheme: Mapped[str] = mapped_column(String(32), nullable=False, default="fernet", server_default="fernet")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserPreference(Base):
    __tablename__ = "user_preference"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "preference_key", name="uq_user_preference_owner_key"),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="CASCADE"), nullable=False)
    preference_key: Mapped[str] = mapped_column(String(96), nullable=False)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SidebarLayoutState(Base):
    __tablename__ = "sidebar_layout_state"

    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("user_identity.id", ondelete="CASCADE"), primary_key=True
    )
    layout: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SavedSqlFolderState(Base):
    __tablename__ = "saved_sql_folder_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SavedSqlFileState(Base):
    __tablename__ = "saved_sql_file_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("saved_sql_folder_state.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QueryHistoryEntry(Base):
    __tablename__ = "query_history_entry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    activity_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    affected_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rollback_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AiConversationState(Base):
    __tablename__ = "ai_conversation_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("user_identity.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    connection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    messages: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
