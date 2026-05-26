from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.auth import UserIdentity
from app.models.runtime_state import (
    AiConversationState,
    ConnectionProfile,
    QueryHistoryEntry,
    SavedSqlFileState,
    SavedSqlFolderState,
)
from app.models.system_state import LegacyImportJob
from app.services.audit import AuditActor, AuditService
from app.services.runtime_state import RuntimeStateService
from app.services.system_settings import SystemSettingService

UNSUPPORTED_WEB_DATABASES = {"sqlite", "duckdb", "access"}


@dataclass(slots=True)
class LegacyImportSnapshot:
    source_kind: str
    source_label: str
    connections: list[dict[str, Any]] = field(default_factory=list)
    sidebar_layout: dict[str, Any] | None = None
    pinned_tree_node_ids: list[str] | None = None
    desktop_settings: dict[str, Any] | None = None
    editor_settings: dict[str, Any] | None = None
    ai_config: dict[str, Any] | None = None
    ai_conversations: list[dict[str, Any]] = field(default_factory=list)
    history_entries: list[dict[str, Any]] = field(default_factory=list)
    saved_sql_folders: list[dict[str, Any]] = field(default_factory=list)
    saved_sql_files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class LegacyImportService:
    def __init__(
        self,
        *,
        runtime_state_service: RuntimeStateService | None = None,
        system_setting_service: SystemSettingService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.runtime_state_service = runtime_state_service or RuntimeStateService()
        self.system_setting_service = system_setting_service or SystemSettingService()
        self.audit_service = audit_service or AuditService()

    def load_source(self, source_path: str | Path) -> LegacyImportSnapshot:
        path = Path(source_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Legacy source file does not exist: {path}")

        suffix = path.suffix.lower()
        if suffix in {".db", ".sqlite", ".sqlite3", ".db3"}:
            return self._load_sqlite_snapshot(path)
        if suffix == ".json":
            return self._load_json_snapshot(path)
        raise ValueError(f"Unsupported legacy source format: {path.name}")

    def import_source(
        self,
        db: Session,
        *,
        source_path: str | Path,
        owner_user_id: str,
        created_by_user_id: str | None = None,
        overwrite_existing: bool = False,
    ) -> LegacyImportJob:
        owner = db.get(UserIdentity, owner_user_id)
        if owner is None:
            raise ValueError(f"Target user not found: {owner_user_id}")
        if created_by_user_id and db.get(UserIdentity, created_by_user_id) is None:
            raise ValueError(f"Creator user not found: {created_by_user_id}")
        snapshot = self.load_source(source_path)
        checksum = self._sha256(source_path)
        started_at = datetime.now(UTC)
        job_id = str(uuid4())
        options = {
            "overwriteExisting": overwrite_existing,
            "sourcePath": str(Path(source_path).expanduser().resolve()),
            "metadata": snapshot.metadata,
        }

        try:
            job = LegacyImportJob(
                id=job_id,
                source_kind=snapshot.source_kind,
                source_label=snapshot.source_label,
                source_checksum=checksum,
                target_user_id=owner_user_id,
                created_by_user_id=created_by_user_id,
                status="running",
                overwrite_existing=overwrite_existing,
                options=options,
                result={},
                started_at=started_at,
            )
            db.add(job)

            summary = self._apply_snapshot(
                db,
                owner_user_id=owner_user_id,
                snapshot=snapshot,
                overwrite_existing=overwrite_existing,
            )
            completed_at = datetime.now(UTC)
            job.status = "succeeded"
            job.completed_at = completed_at
            job.result = summary

            self.system_setting_service.set(
                db,
                key="config_migration.last_import",
                value={
                    "jobId": job.id,
                    "targetUserId": owner_user_id,
                    "sourceKind": snapshot.source_kind,
                    "sourceLabel": snapshot.source_label,
                    "sourceChecksum": checksum,
                    "completedAt": completed_at.isoformat(),
                    "summary": summary,
                },
                description="Most recent legacy configuration import summary.",
                updated_by_user_id=created_by_user_id,
                commit=False,
            )
            self.audit_service.record_event(
                db,
                event_type="legacy_import",
                category="configuration",
                action="import",
                actor=self._actor_for_import(owner),
                resource_type="legacy_import_job",
                resource_id=job.id,
                resource_name=snapshot.source_label,
                payload=summary,
                commit=False,
            )
            db.commit()
            db.refresh(job)
            return job
        except Exception as exc:
            db.rollback()
            failed_job = LegacyImportJob(
                id=job_id,
                source_kind=snapshot.source_kind,
                source_label=snapshot.source_label,
                source_checksum=checksum,
                target_user_id=owner_user_id,
                created_by_user_id=created_by_user_id,
                status="failed",
                overwrite_existing=overwrite_existing,
                options=options,
                result={"error": str(exc)},
                error_message=str(exc),
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )
            db.add(failed_job)
            self.audit_service.record_event(
                db,
                event_type="legacy_import",
                category="configuration",
                action="import",
                outcome="failure",
                actor=self._actor_for_import(owner),
                resource_type="legacy_import_job",
                resource_id=failed_job.id,
                resource_name=snapshot.source_label,
                payload={"error": str(exc), "sourceKind": snapshot.source_kind, "sourceLabel": snapshot.source_label},
                commit=False,
            )
            db.commit()
            db.refresh(failed_job)
            raise

    def _apply_snapshot(
        self,
        db: Session,
        *,
        owner_user_id: str,
        snapshot: LegacyImportSnapshot,
        overwrite_existing: bool,
    ) -> dict[str, Any]:
        connection_result = self._import_connections(
            db,
            owner_user_id=owner_user_id,
            connections=snapshot.connections,
            overwrite_existing=overwrite_existing,
        )
        connection_id_map = connection_result.pop("id_map")

        if snapshot.sidebar_layout is not None:
            self.runtime_state_service.save_sidebar_layout(
                db,
                owner_user_id,
                self._remap_sidebar_layout(snapshot.sidebar_layout, connection_id_map),
                commit=False,
            )
        if snapshot.pinned_tree_node_ids is not None:
            self.runtime_state_service.save_pinned_tree_node_ids(
                db,
                owner_user_id,
                [self._remap_tree_node_id(item, connection_id_map) for item in snapshot.pinned_tree_node_ids],
                commit=False,
            )
        if snapshot.desktop_settings is not None:
            self.runtime_state_service.save_desktop_settings(db, owner_user_id, snapshot.desktop_settings, commit=False)
        if snapshot.editor_settings is not None:
            self.runtime_state_service.save_editor_settings(db, owner_user_id, snapshot.editor_settings, commit=False)
        if snapshot.ai_config is not None:
            self.runtime_state_service.save_ai_config(db, owner_user_id, snapshot.ai_config, commit=False)

        history_result = self._import_history(
            db,
            owner_user_id=owner_user_id,
            entries=snapshot.history_entries,
            connection_id_map=connection_id_map,
        )
        saved_sql_result = self._import_saved_sql(
            db,
            owner_user_id=owner_user_id,
            folders=snapshot.saved_sql_folders,
            files=snapshot.saved_sql_files,
            connection_id_map=connection_id_map,
        )
        conversation_result = self._import_ai_conversations(
            db,
            owner_user_id=owner_user_id,
            conversations=snapshot.ai_conversations,
        )

        return {
            "sourceKind": snapshot.source_kind,
            "sourceLabel": snapshot.source_label,
            "connections": connection_result,
            "history": history_result,
            "savedSql": saved_sql_result,
            "aiConversations": conversation_result,
            "preferences": {
                "sidebarLayoutImported": snapshot.sidebar_layout is not None,
                "pinnedTreeNodeIdsImported": snapshot.pinned_tree_node_ids is not None,
                "desktopSettingsImported": snapshot.desktop_settings is not None,
                "editorSettingsImported": snapshot.editor_settings is not None,
                "aiConfigImported": snapshot.ai_config is not None,
            },
            "metadata": snapshot.metadata,
        }

    def _import_connections(
        self,
        db: Session,
        *,
        owner_user_id: str,
        connections: list[dict[str, Any]],
        overwrite_existing: bool,
    ) -> dict[str, Any]:
        existing = {
            profile.id: profile
            for profile in db.query(ConnectionProfile)
            .filter(ConnectionProfile.owner_user_id == owner_user_id)
            .all()
        }
        existing_by_identity = {
            self._connection_identity(profile.config): profile
            for profile in existing.values()
            if self._connection_identity(profile.config) is not None
        }

        created = 0
        updated = 0
        skipped = 0
        unsupported: list[str] = []
        id_map: dict[str, str] = {}

        for raw_config in connections:
            config = self._canonicalize_connection(dict(raw_config))
            original_id = str(config.get("id") or "")
            name = str(config.get("name") or original_id or "unnamed")
            database_type = str(config.get("db_type") or "").lower()
            if not database_type or database_type in UNSUPPORTED_WEB_DATABASES:
                unsupported.append(name)
                continue

            identity = self._connection_identity(config)
            profile = existing.get(original_id) if original_id else None
            if profile is None and identity is not None:
                profile = existing_by_identity.get(identity)

            if profile is None:
                if original_id:
                    profile_id = self._resolve_connection_profile_id(db, owner_user_id, original_id)
                else:
                    profile_source = json.dumps(identity or {"name": name}, sort_keys=True)
                    profile_id = self._stable_scoped_id("connection", owner_user_id, profile_source)
                config["id"] = profile_id
                profile = ConnectionProfile(
                    id=profile_id,
                    owner_user_id=owner_user_id,
                    name=name,
                    config={},
                )
                db.add(profile)
                existing[profile.id] = profile
                if identity is not None:
                    existing_by_identity[identity] = profile
                created += 1
            else:
                config["id"] = profile.id
                if profile.id != original_id and not overwrite_existing:
                    if original_id:
                        id_map[original_id] = profile.id
                    skipped += 1
                    continue
                updated += 1

            if original_id:
                id_map[original_id] = profile.id
            profile.name = str(config.get("name") or profile.id)
            profile.config = config

        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "unsupported": unsupported,
            "id_map": id_map,
        }

    def _import_history(
        self,
        db: Session,
        *,
        owner_user_id: str,
        entries: list[dict[str, Any]],
        connection_id_map: dict[str, str],
    ) -> dict[str, int]:
        created = 0
        updated = 0
        for entry in entries:
            payload = dict(entry)
            payload["id"] = self._resolve_scoped_id(
                db,
                QueryHistoryEntry,
                str(payload.get("id") or uuid4()),
                owner_attr="owner_user_id",
                owner_user_id=owner_user_id,
                prefix="history",
            )
            connection_id = str(payload.get("connection_id") or "")
            if connection_id:
                payload["connection_id"] = connection_id_map.get(connection_id, connection_id)
            existing = db.get(QueryHistoryEntry, payload["id"])
            if existing is None:
                created += 1
            else:
                updated += 1
            self.runtime_state_service.save_history_entry(db, owner_user_id, payload, commit=False)
        return {"created": created, "updated": updated}

    def _import_saved_sql(
        self,
        db: Session,
        *,
        owner_user_id: str,
        folders: list[dict[str, Any]],
        files: list[dict[str, Any]],
        connection_id_map: dict[str, str],
    ) -> dict[str, int]:
        folder_id_map: dict[str, str] = {}
        folder_created = 0
        folder_updated = 0
        for folder in folders:
            payload = dict(folder)
            original_id = str(payload.get("id") or uuid4())
            payload["id"] = self._resolve_scoped_id(
                db,
                SavedSqlFolderState,
                original_id,
                owner_attr="owner_user_id",
                owner_user_id=owner_user_id,
                prefix="saved_sql_folder",
            )
            folder_id_map[original_id] = payload["id"]
            payload["connectionId"] = connection_id_map.get(str(payload.get("connectionId") or ""), str(payload.get("connectionId") or ""))
            existing = db.get(SavedSqlFolderState, payload["id"])
            if existing is None:
                folder_created += 1
            else:
                folder_updated += 1
            self.runtime_state_service.save_saved_sql_folder(db, owner_user_id, payload, commit=False)

        file_created = 0
        file_updated = 0
        for file in files:
            payload = dict(file)
            payload["id"] = self._resolve_scoped_id(
                db,
                SavedSqlFileState,
                str(payload.get("id") or uuid4()),
                owner_attr="owner_user_id",
                owner_user_id=owner_user_id,
                prefix="saved_sql_file",
            )
            payload["connectionId"] = connection_id_map.get(str(payload.get("connectionId") or ""), str(payload.get("connectionId") or ""))
            folder_id = payload.get("folderId")
            if folder_id:
                payload["folderId"] = folder_id_map.get(str(folder_id), str(folder_id))
            existing = db.get(SavedSqlFileState, payload["id"])
            if existing is None:
                file_created += 1
            else:
                file_updated += 1
            self.runtime_state_service.save_saved_sql_file(db, owner_user_id, payload, commit=False)

        return {
            "foldersCreated": folder_created,
            "foldersUpdated": folder_updated,
            "filesCreated": file_created,
            "filesUpdated": file_updated,
        }

    def _import_ai_conversations(
        self,
        db: Session,
        *,
        owner_user_id: str,
        conversations: list[dict[str, Any]],
    ) -> dict[str, int]:
        created = 0
        updated = 0
        for conversation in conversations:
            payload = dict(conversation)
            payload["id"] = self._resolve_scoped_id(
                db,
                AiConversationState,
                str(payload.get("id") or uuid4()),
                owner_attr="owner_user_id",
                owner_user_id=owner_user_id,
                prefix="ai_conversation",
            )
            existing = db.get(AiConversationState, payload["id"])
            if existing is None:
                created += 1
            else:
                updated += 1
            self.runtime_state_service.save_ai_conversation(db, owner_user_id, payload, commit=False)
        return {"created": created, "updated": updated}

    def _load_sqlite_snapshot(self, path: Path) -> LegacyImportSnapshot:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            settings_json = self._read_single_json(conn, "app_settings", "settings_json")
            legacy_settings = settings_json if isinstance(settings_json, dict) else {}
            snapshot = LegacyImportSnapshot(
                source_kind="sqlite",
                source_label=str(path),
                connections=self._load_sqlite_connections(conn),
                sidebar_layout=self._read_single_json(conn, "sidebar_layout", "layout_json"),
                pinned_tree_node_ids=self._read_string_list(legacy_settings.get("pinned_tree_node_ids")),
                desktop_settings={"show_tray_icon": bool(legacy_settings.get("show_tray_icon", False))}
                if "show_tray_icon" in legacy_settings
                else None,
                ai_config=self._read_single_json(conn, "ai_config", "config_json"),
                ai_conversations=self._load_sqlite_ai_conversations(conn),
                history_entries=self._load_sqlite_history(conn),
                saved_sql_folders=self._load_sqlite_saved_sql_folders(conn),
                saved_sql_files=self._load_sqlite_saved_sql_files(conn),
                metadata={
                    "legacyPasswordHashPresent": bool(legacy_settings.get("password_hash")),
                },
            )
            return snapshot
        finally:
            conn.close()

    def _load_json_snapshot(self, path: Path) -> LegacyImportSnapshot:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, list):
            return LegacyImportSnapshot(source_kind="json", source_label=str(path), connections=parsed)
        if not isinstance(parsed, dict):
            raise ValueError("Legacy JSON import only supports a config array or manifest object.")

        saved_sql = parsed.get("savedSql") or parsed.get("saved_sql") or {}
        if not isinstance(saved_sql, dict):
            saved_sql = {}
        app_settings = parsed.get("appSettings") or parsed.get("app_settings") or {}
        if not isinstance(app_settings, dict):
            app_settings = {}
        desktop_settings = parsed.get("desktopSettings") or parsed.get("desktop_settings")
        if desktop_settings is None and isinstance(app_settings, dict) and "show_tray_icon" in app_settings:
            desktop_settings = {"show_tray_icon": bool(app_settings.get("show_tray_icon"))}

        pinned_tree_node_ids = parsed.get("pinnedTreeNodeIds")
        if pinned_tree_node_ids is None and isinstance(app_settings, dict):
            pinned_tree_node_ids = app_settings.get("pinned_tree_node_ids")

        connections = self._ensure_list(parsed.get("connections"))

        return LegacyImportSnapshot(
            source_kind="json",
            source_label=str(path),
            connections=connections,
            sidebar_layout=parsed.get("layout") or parsed.get("sidebarLayout") or parsed.get("sidebar_layout"),
            pinned_tree_node_ids=self._read_string_list(pinned_tree_node_ids),
            desktop_settings=desktop_settings if isinstance(desktop_settings, dict) else None,
            editor_settings=(parsed.get("editorSettings") or parsed.get("editor_settings"))
            if isinstance(parsed.get("editorSettings") or parsed.get("editor_settings"), dict)
            else None,
            ai_config=(parsed.get("aiConfig") or parsed.get("ai_config"))
            if isinstance(parsed.get("aiConfig") or parsed.get("ai_config"), dict)
            else None,
            ai_conversations=self._ensure_list(parsed.get("aiConversations") or parsed.get("ai_conversations")),
            history_entries=self._ensure_list(
                parsed.get("history") or parsed.get("historyEntries") or parsed.get("history_entries")
            ),
            saved_sql_folders=self._ensure_list(saved_sql.get("folders")),
            saved_sql_files=self._ensure_list(saved_sql.get("files")),
            metadata={"format": parsed.get("format")},
        )

    def _load_sqlite_connections(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "connections"):
            return []
        rows = conn.execute("SELECT id, config_json FROM connections").fetchall()
        connections: list[dict[str, Any]] = []
        for row in rows:
            config = dict(json.loads(row["config_json"]))
            config["id"] = row["id"]
            for secret_key in ("password", "ssh_password", "ssh_key_passphrase", "proxy_password", "connection_string"):
                secret_value = self._read_connection_secret(conn, row["id"], secret_key)
                if secret_value:
                    config[secret_key] = secret_value
            connections.append(config)
        return connections

    def _load_sqlite_history(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "history"):
            return []
        rows = conn.execute(
            "SELECT id, connection_id, connection_name, database, sql_text, executed_at, execution_time_ms, "
            "success, error, activity_kind, operation, target, affected_rows, rollback_sql, details_json "
            "FROM history ORDER BY executed_at DESC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "connection_id": row["connection_id"],
                "connection_name": row["connection_name"],
                "database": row["database"],
                "sql": row["sql_text"],
                "executed_at": row["executed_at"],
                "execution_time_ms": row["execution_time_ms"],
                "success": bool(row["success"]),
                "error": row["error"],
                "activity_kind": row["activity_kind"],
                "operation": row["operation"],
                "target": row["target"],
                "affected_rows": row["affected_rows"],
                "rollback_sql": row["rollback_sql"],
                "details_json": self._parse_json_if_possible(row["details_json"]),
            }
            for row in rows
        ]

    def _load_sqlite_ai_conversations(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "ai_conversations"):
            return []
        rows = conn.execute(
            "SELECT id, title, connection_name, database, messages_json, created_at, updated_at "
            "FROM ai_conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "connectionName": row["connection_name"],
                "database": row["database"],
                "messages": self._parse_json_if_possible(row["messages_json"]) or [],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def _load_sqlite_saved_sql_folders(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "saved_sql_folders"):
            return []
        rows = conn.execute(
            "SELECT id, connection_id, name, created_at, updated_at FROM saved_sql_folders ORDER BY created_at ASC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "connectionId": row["connection_id"],
                "name": row["name"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def _load_sqlite_saved_sql_files(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "saved_sql_files"):
            return []
        rows = conn.execute(
            "SELECT id, connection_id, folder_id, name, database_name, schema_name, sql_text, created_at, updated_at "
            "FROM saved_sql_files ORDER BY created_at ASC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "connectionId": row["connection_id"],
                "folderId": row["folder_id"],
                "name": row["name"],
                "database": row["database_name"],
                "schema": row["schema_name"],
                "sql": row["sql_text"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def _read_single_json(self, conn: sqlite3.Connection, table: str, column: str) -> Any:
        if not self._table_exists(conn, table):
            return None
        row = conn.execute(f"SELECT {column} AS payload FROM {table} LIMIT 1").fetchone()
        if row is None:
            return None
        return self._parse_json_if_possible(row["payload"])

    def _read_connection_secret(self, conn: sqlite3.Connection, connection_id: str, key: str) -> str:
        if not self._table_exists(conn, "connection_secrets"):
            return ""
        row = conn.execute(
            "SELECT secret FROM connection_secrets WHERE connection_id = ? AND key = ?",
            (connection_id, key),
        ).fetchone()
        return "" if row is None else str(row["secret"] or "")

    def _connection_identity(self, config: dict[str, Any] | None) -> tuple[str, str, int] | None:
        if not config:
            return None
        name = str(config.get("name") or "").strip().lower()
        host = str(config.get("host") or "").strip().lower()
        port = int(config.get("port") or 0)
        if not name:
            return None
        return (name, host, port)

    def _canonicalize_connection(self, config: dict[str, Any]) -> dict[str, Any]:
        if config.get("db_type") == "mysql" and str(config.get("driver_profile") or "").lower() == "tdengine":
            config["db_type"] = "tdengine"
            config["driver_profile"] = "tdengine"
            config["port"] = 6041 if int(config.get("port") or 0) in {0, 6030} else int(config.get("port") or 6041)
        elif config.get("db_type") == "tdengine":
            config["driver_profile"] = "tdengine"
            config["port"] = int(config.get("port") or 6041)
        return config

    def _resolve_connection_profile_id(self, db: Session, owner_user_id: str, original_id: str) -> str:
        existing = db.get(ConnectionProfile, original_id)
        if existing is None or existing.owner_user_id == owner_user_id:
            return original_id
        return self._stable_scoped_id("connection", owner_user_id, original_id)

    def _resolve_scoped_id(
        self,
        db: Session,
        model: type[Any],
        original_id: str,
        *,
        owner_attr: str,
        owner_user_id: str,
        prefix: str,
    ) -> str:
        existing = db.get(model, original_id)
        if existing is None or getattr(existing, owner_attr) == owner_user_id:
            return original_id
        return self._stable_scoped_id(prefix, owner_user_id, original_id)

    def _stable_scoped_id(self, prefix: str, owner_user_id: str, original_id: str) -> str:
        return hashlib.sha256(f"{prefix}:{owner_user_id}:{original_id}".encode("utf-8")).hexdigest()[:32]

    def _remap_sidebar_layout(self, layout: dict[str, Any], connection_id_map: dict[str, str]) -> dict[str, Any]:
        if not connection_id_map:
            return layout
        cloned = deepcopy(layout)
        order = cloned.get("order")
        if isinstance(order, list):
            for entry in order:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "connection" and entry.get("id") in connection_id_map:
                    entry["id"] = connection_id_map[str(entry["id"])]
                elif entry.get("type") == "group" and isinstance(entry.get("connectionIds"), list):
                    entry["connectionIds"] = [
                        connection_id_map.get(str(connection_id), str(connection_id))
                        for connection_id in entry["connectionIds"]
                    ]
        return cloned

    def _remap_tree_node_id(self, value: str, connection_id_map: dict[str, str]) -> str:
        remapped = connection_id_map.get(value)
        if remapped:
            return remapped
        for original_id, next_id in connection_id_map.items():
            for delimiter in ("::", ":"):
                prefix = f"{original_id}{delimiter}"
                if value.startswith(prefix):
                    return f"{next_id}{value[len(original_id):]}"
        return value

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _read_string_list(self, value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            return None
        return [str(item) for item in value if str(item).strip()]

    def _ensure_list(self, value: Any) -> list[Any]:
        return list(value) if isinstance(value, list) else []

    def _parse_json_if_possible(self, value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, (dict, list, bool, int, float)):
            return value
        try:
            return json.loads(str(value))
        except json.JSONDecodeError:
            return value

    def _sha256(self, source_path: str | Path) -> str:
        path = Path(source_path).expanduser().resolve()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _actor_for_import(self, owner: UserIdentity) -> AuditActor:
        return self.audit_service.build_actor(user=owner)
