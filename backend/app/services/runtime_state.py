from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.runtime_state import (
    AiConversationState,
    ConnectionProfile,
    QueryHistoryEntry,
    SavedSqlFileState,
    SavedSqlFolderState,
    SidebarLayoutState,
    UserPreference,
)


class RuntimeStateService:
    PINNED_TREE_NODE_IDS_KEY = "pinned_tree_node_ids"
    AI_CONFIG_KEY = "ai_config"
    DESKTOP_SETTINGS_KEY = "desktop_settings"
    EDITOR_SETTINGS_KEY = "editor_settings"

    def load_connections(self, db: Session, user_id: str) -> list[dict[str, Any]]:
        profiles = db.execute(
            select(ConnectionProfile)
            .where(ConnectionProfile.owner_user_id == user_id)
            .order_by(ConnectionProfile.name.asc(), ConnectionProfile.created_at.asc())
        ).scalars().all()
        return [self._connection_payload(profile) for profile in profiles]

    def load_connection_profile(self, db: Session, connection_id: str) -> dict[str, Any] | None:
        profile = db.get(ConnectionProfile, connection_id)
        if profile is None:
            return None
        return self._connection_payload(profile)

    def save_connections(self, db: Session, user_id: str, configs: list[dict[str, Any]]) -> None:
        incoming_ids = {str(config["id"]) for config in configs if config.get("id")}
        existing = db.execute(
            select(ConnectionProfile).where(ConnectionProfile.owner_user_id == user_id)
        ).scalars().all()
        existing_by_id = {profile.id: profile for profile in existing}

        for profile in existing:
            if profile.id not in incoming_ids:
                db.delete(profile)

        for config in configs:
            connection_id = str(config["id"])
            profile = existing_by_id.get(connection_id)
            if profile is None:
                profile = ConnectionProfile(
                    id=connection_id,
                    owner_user_id=user_id,
                    name=str(config.get("name") or connection_id),
                    config={},
                )
                db.add(profile)
            profile.name = str(config.get("name") or connection_id)
            profile.config = config

        db.commit()

    def load_sidebar_layout(self, db: Session, user_id: str) -> dict[str, Any] | None:
        state = db.get(SidebarLayoutState, user_id)
        return None if state is None else (state.layout or {})

    def save_sidebar_layout(self, db: Session, user_id: str, layout: dict[str, Any], *, commit: bool = True) -> None:
        state = db.get(SidebarLayoutState, user_id)
        if state is None:
            state = SidebarLayoutState(owner_user_id=user_id, layout=layout or {})
            db.add(state)
        else:
            state.layout = layout or {}
        self._commit_or_flush(db, commit=commit)

    def load_pinned_tree_node_ids(self, db: Session, user_id: str) -> list[str]:
        value = self._get_preference(db, user_id, self.PINNED_TREE_NODE_IDS_KEY)
        return value if isinstance(value, list) else []

    def save_pinned_tree_node_ids(self, db: Session, user_id: str, ids: list[str], *, commit: bool = True) -> None:
        self._set_preference(db, user_id, self.PINNED_TREE_NODE_IDS_KEY, ids, commit=commit)

    def load_ai_config(self, db: Session, user_id: str) -> dict[str, Any] | None:
        value = self._get_preference(db, user_id, self.AI_CONFIG_KEY)
        return value if isinstance(value, dict) else None

    def save_ai_config(self, db: Session, user_id: str, config: dict[str, Any], *, commit: bool = True) -> None:
        self._set_preference(db, user_id, self.AI_CONFIG_KEY, config, commit=commit)

    def load_desktop_settings(self, db: Session, user_id: str) -> dict[str, Any]:
        value = self._get_preference(db, user_id, self.DESKTOP_SETTINGS_KEY)
        return value if isinstance(value, dict) else {"show_tray_icon": False}

    def save_desktop_settings(self, db: Session, user_id: str, settings: dict[str, Any], *, commit: bool = True) -> None:
        self._set_preference(db, user_id, self.DESKTOP_SETTINGS_KEY, settings, commit=commit)

    def load_editor_settings(self, db: Session, user_id: str) -> dict[str, Any] | None:
        value = self._get_preference(db, user_id, self.EDITOR_SETTINGS_KEY)
        return value if isinstance(value, dict) else None

    def save_editor_settings(self, db: Session, user_id: str, settings: dict[str, Any], *, commit: bool = True) -> None:
        self._set_preference(db, user_id, self.EDITOR_SETTINGS_KEY, settings, commit=commit)

    def load_saved_sql_library(self, db: Session, user_id: str) -> dict[str, list[dict[str, Any]]]:
        folders = db.execute(
            select(SavedSqlFolderState)
            .where(SavedSqlFolderState.owner_user_id == user_id)
            .order_by(SavedSqlFolderState.updated_at.desc(), SavedSqlFolderState.created_at.asc())
        ).scalars().all()
        files = db.execute(
            select(SavedSqlFileState)
            .where(SavedSqlFileState.owner_user_id == user_id)
            .order_by(SavedSqlFileState.updated_at.desc(), SavedSqlFileState.created_at.asc())
        ).scalars().all()
        return {
            "folders": [self._saved_sql_folder_payload(item) for item in folders],
            "files": [self._saved_sql_file_payload(item) for item in files],
        }

    def save_saved_sql_folder(
        self,
        db: Session,
        user_id: str,
        folder: dict[str, Any],
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        folder_id = str(folder["id"])
        state = db.get(SavedSqlFolderState, folder_id)
        if state is None:
            state = SavedSqlFolderState(
                id=folder_id,
                owner_user_id=user_id,
                connection_id=str(folder["connectionId"]),
                name=str(folder["name"]),
                created_at=self._parse_datetime(folder.get("createdAt")) or datetime.utcnow(),
                updated_at=self._parse_datetime(folder.get("updatedAt")) or datetime.utcnow(),
            )
            db.add(state)
        else:
            state.connection_id = str(folder["connectionId"])
            state.name = str(folder["name"])
            state.updated_at = self._parse_datetime(folder.get("updatedAt")) or datetime.utcnow()
        self._commit_or_flush(db, commit=commit)
        if commit:
            db.refresh(state)
        return self._saved_sql_folder_payload(state)

    def delete_saved_sql_folder(self, db: Session, user_id: str, folder_id: str) -> None:
        folder = db.get(SavedSqlFolderState, folder_id)
        if folder is None or folder.owner_user_id != user_id:
            return
        db.execute(
            delete(SavedSqlFileState).where(
                SavedSqlFileState.owner_user_id == user_id,
                SavedSqlFileState.folder_id == folder_id,
            )
        )
        db.delete(folder)
        db.commit()

    def save_saved_sql_file(
        self,
        db: Session,
        user_id: str,
        file: dict[str, Any],
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        file_id = str(file["id"])
        state = db.get(SavedSqlFileState, file_id)
        if state is None:
            state = SavedSqlFileState(
                id=file_id,
                owner_user_id=user_id,
                connection_id=str(file["connectionId"]),
                folder_id=file.get("folderId"),
                name=str(file["name"]),
                database_name=str(file["database"]),
                schema_name=file.get("schema"),
                sql_text=str(file["sql"]),
                created_at=self._parse_datetime(file.get("createdAt")) or datetime.utcnow(),
                updated_at=self._parse_datetime(file.get("updatedAt")) or datetime.utcnow(),
            )
            db.add(state)
        else:
            state.connection_id = str(file["connectionId"])
            state.folder_id = file.get("folderId")
            state.name = str(file["name"])
            state.database_name = str(file["database"])
            state.schema_name = file.get("schema")
            state.sql_text = str(file["sql"])
            state.updated_at = self._parse_datetime(file.get("updatedAt")) or datetime.utcnow()
        self._commit_or_flush(db, commit=commit)
        if commit:
            db.refresh(state)
        return self._saved_sql_file_payload(state)

    def delete_saved_sql_file(self, db: Session, user_id: str, file_id: str) -> None:
        file = db.get(SavedSqlFileState, file_id)
        if file is None or file.owner_user_id != user_id:
            return
        db.delete(file)
        db.commit()

    def save_history_entry(self, db: Session, user_id: str, entry: dict[str, Any], *, commit: bool = True) -> None:
        state = QueryHistoryEntry(
            id=str(entry["id"]),
            owner_user_id=user_id,
            connection_id=entry.get("connection_id"),
            connection_name=str(entry.get("connection_name") or ""),
            database_name=str(entry.get("database") or ""),
            sql_text=str(entry.get("sql") or ""),
            executed_at=self._parse_datetime(entry.get("executed_at")) or datetime.utcnow(),
            execution_time_ms=int(entry.get("execution_time_ms") or 0),
            success=bool(entry.get("success", True)),
            error_message=entry.get("error"),
            activity_kind=entry.get("activity_kind"),
            operation=entry.get("operation"),
            target=entry.get("target"),
            affected_rows=entry.get("affected_rows"),
            rollback_sql=entry.get("rollback_sql"),
            details=self._parse_details(entry.get("details_json")),
        )
        existing = db.get(QueryHistoryEntry, state.id)
        if existing is not None and existing.owner_user_id == user_id:
            db.delete(existing)
            db.flush()
        db.add(state)
        self._commit_or_flush(db, commit=commit)

    def load_history_entries(self, db: Session, user_id: str, limit: int, offset: int) -> list[dict[str, Any]]:
        items = db.execute(
            select(QueryHistoryEntry)
            .where(QueryHistoryEntry.owner_user_id == user_id)
            .order_by(QueryHistoryEntry.executed_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        ).scalars().all()
        return [self._history_payload(item) for item in items]

    def clear_history_entries(self, db: Session, user_id: str) -> None:
        db.execute(delete(QueryHistoryEntry).where(QueryHistoryEntry.owner_user_id == user_id))
        db.commit()

    def delete_history_entry(self, db: Session, user_id: str, entry_id: str) -> None:
        item = db.get(QueryHistoryEntry, entry_id)
        if item is None or item.owner_user_id != user_id:
            return
        db.delete(item)
        db.commit()

    def save_ai_conversation(
        self,
        db: Session,
        user_id: str,
        conversation: dict[str, Any],
        *,
        commit: bool = True,
    ) -> None:
        conversation_id = str(conversation["id"])
        state = db.get(AiConversationState, conversation_id)
        if state is None:
            state = AiConversationState(
                id=conversation_id,
                owner_user_id=user_id,
                title=str(conversation.get("title") or "Untitled"),
                connection_name=str(conversation.get("connectionName") or ""),
                database_name=str(conversation.get("database") or ""),
                messages=list(conversation.get("messages") or []),
                created_at=self._parse_datetime(conversation.get("createdAt")) or datetime.utcnow(),
                updated_at=self._parse_datetime(conversation.get("updatedAt")) or datetime.utcnow(),
            )
            db.add(state)
        else:
            state.title = str(conversation.get("title") or state.title)
            state.connection_name = str(conversation.get("connectionName") or "")
            state.database_name = str(conversation.get("database") or "")
            state.messages = list(conversation.get("messages") or [])
            state.updated_at = self._parse_datetime(conversation.get("updatedAt")) or datetime.utcnow()
        self._commit_or_flush(db, commit=commit)

    def load_ai_conversations(self, db: Session, user_id: str) -> list[dict[str, Any]]:
        items = db.execute(
            select(AiConversationState)
            .where(AiConversationState.owner_user_id == user_id)
            .order_by(AiConversationState.updated_at.desc(), AiConversationState.created_at.desc())
        ).scalars().all()
        return [self._ai_conversation_payload(item) for item in items]

    def delete_ai_conversation(self, db: Session, user_id: str, conversation_id: str) -> None:
        item = db.get(AiConversationState, conversation_id)
        if item is None or item.owner_user_id != user_id:
            return
        db.delete(item)
        db.commit()

    def _get_preference(self, db: Session, user_id: str, key: str) -> Any:
        item = db.execute(
            select(UserPreference)
            .where(UserPreference.owner_user_id == user_id, UserPreference.preference_key == key)
        ).scalar_one_or_none()
        return None if item is None else item.value

    def _set_preference(self, db: Session, user_id: str, key: str, value: Any, *, commit: bool = True) -> None:
        item = db.execute(
            select(UserPreference)
            .where(UserPreference.owner_user_id == user_id, UserPreference.preference_key == key)
        ).scalar_one_or_none()
        if item is None:
            item = UserPreference(
                id=self._preference_id(user_id, key),
                owner_user_id=user_id,
                preference_key=key,
                value=value,
            )
            db.add(item)
        else:
            item.value = value
        self._commit_or_flush(db, commit=commit)

    def _preference_id(self, user_id: str, key: str) -> str:
        return hashlib.sha256(f"{user_id}:{key}".encode("utf-8")).hexdigest()[:32]

    def _connection_payload(self, profile: ConnectionProfile) -> dict[str, Any]:
        payload = dict(profile.config or {})
        payload["id"] = profile.id
        payload["name"] = payload.get("name") or profile.name
        return payload

    def _saved_sql_folder_payload(self, item: SavedSqlFolderState) -> dict[str, Any]:
        return {
            "id": item.id,
            "connectionId": item.connection_id,
            "name": item.name,
            "createdAt": item.created_at.isoformat(),
            "updatedAt": item.updated_at.isoformat(),
        }

    def _saved_sql_file_payload(self, item: SavedSqlFileState) -> dict[str, Any]:
        return {
            "id": item.id,
            "connectionId": item.connection_id,
            "folderId": item.folder_id,
            "name": item.name,
            "database": item.database_name,
            "schema": item.schema_name,
            "sql": item.sql_text,
            "createdAt": item.created_at.isoformat(),
            "updatedAt": item.updated_at.isoformat(),
        }

    def _history_payload(self, item: QueryHistoryEntry) -> dict[str, Any]:
        return {
            "id": item.id,
            "connection_id": item.connection_id,
            "connection_name": item.connection_name,
            "database": item.database_name,
            "sql": item.sql_text,
            "executed_at": item.executed_at.isoformat(),
            "execution_time_ms": item.execution_time_ms,
            "success": item.success,
            "error": item.error_message,
            "activity_kind": item.activity_kind,
            "operation": item.operation,
            "target": item.target,
            "affected_rows": item.affected_rows,
            "rollback_sql": item.rollback_sql,
            "details_json": None if item.details is None else str(item.details),
        }

    def _ai_conversation_payload(self, item: AiConversationState) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title,
            "connectionName": item.connection_name,
            "database": item.database_name,
            "messages": item.messages,
            "createdAt": item.created_at.isoformat(),
            "updatedAt": item.updated_at.isoformat(),
        }

    def _parse_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _parse_details(self, value: Any) -> dict[str, Any] | None:
        if value in (None, "", {}):
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return {"raw": value}
        return {"value": value}

    def _commit_or_flush(self, db: Session, *, commit: bool) -> None:
        if commit:
            db.commit()
            return
        db.flush()
