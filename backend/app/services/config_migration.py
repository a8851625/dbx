from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.auth import UserIdentity
from app.services.legacy_import import LegacyImportService
from app.services.system_settings import SystemSettingService

logger = logging.getLogger(__name__)

LAST_IMPORT_KEY = "config_migration.last_import"
AUTO_IMPORT_KEY = "config_migration.auto_import"
AUTO_IMPORT_DESCRIPTION = "Automatic legacy configuration import status for DBX enterprise migration."


class ConfigMigrationService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        legacy_import_service: LegacyImportService | None = None,
        system_setting_service: SystemSettingService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.legacy_import_service = legacy_import_service or LegacyImportService()
        self.system_setting_service = system_setting_service or SystemSettingService()

    def ensure_auto_import_for_user(self, db: Session, user: UserIdentity) -> dict[str, Any] | None:
        if not self.settings.config_migration_auto_enabled:
            return None

        source_path = self.legacy_import_service.discover_auto_source(
            preferred_path=self.settings.config_migration_auto_source or None,
            data_dir=self.settings.dbx_data_dir or None,
        )
        if source_path is None:
            return None

        source_checksum = self.legacy_import_service.source_checksum(source_path)
        auto_status, auto_status_key = self._get_user_scoped_setting(db, AUTO_IMPORT_KEY, user.id)
        if self._status_matches(auto_status, source_checksum, "succeeded", user.id):
            return auto_status

        latest_import, _ = self._get_user_scoped_setting(db, LAST_IMPORT_KEY, user.id)
        if self._last_import_matches(latest_import, source_checksum, user.id):
            payload = self._status_payload(
                status="succeeded",
                source_path=str(source_path),
                source_checksum=source_checksum,
                target_user_id=user.id,
                overwrite_existing=bool(self.settings.config_migration_auto_overwrite_existing),
                completed_at=latest_import.get("completedAt"),
                job_id=latest_import.get("jobId"),
                summary=latest_import.get("summary"),
                reused_existing=True,
            )
            if auto_status != payload:
                self.system_setting_service.set(
                    db,
                    key=auto_status_key,
                    value=payload,
                    description=AUTO_IMPORT_DESCRIPTION,
                    updated_by_user_id=user.id,
                )
            return payload

        if self._status_matches(auto_status, source_checksum, "running", user.id):
            return auto_status

        started_at = datetime.now(UTC).isoformat()
        running_payload = self._status_payload(
            status="running",
            source_path=str(source_path),
            source_checksum=source_checksum,
            target_user_id=user.id,
            overwrite_existing=bool(self.settings.config_migration_auto_overwrite_existing),
            started_at=started_at,
        )
        self.system_setting_service.set(
            db,
            key=auto_status_key,
            value=running_payload,
            description=AUTO_IMPORT_DESCRIPTION,
            updated_by_user_id=user.id,
        )

        try:
            job = self.legacy_import_service.import_source(
                db,
                source_path=source_path,
                owner_user_id=user.id,
                created_by_user_id=user.id,
                overwrite_existing=bool(self.settings.config_migration_auto_overwrite_existing),
            )
        except Exception as exc:
            logger.exception("Automatic config migration failed for source %s", source_path)
            failure_payload = self._status_payload(
                status="failed",
                source_path=str(source_path),
                source_checksum=source_checksum,
                target_user_id=user.id,
                overwrite_existing=bool(self.settings.config_migration_auto_overwrite_existing),
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
                error=str(exc),
            )
            self.system_setting_service.set(
                db,
                key=auto_status_key,
                value=failure_payload,
                description=AUTO_IMPORT_DESCRIPTION,
                updated_by_user_id=user.id,
            )
            return failure_payload

        success_payload = self._status_payload(
            status="succeeded",
            source_path=str(source_path),
            source_checksum=source_checksum,
            target_user_id=user.id,
            overwrite_existing=bool(self.settings.config_migration_auto_overwrite_existing),
            started_at=started_at,
            completed_at=(job.completed_at or datetime.now(UTC)).isoformat(),
            job_id=job.id,
            summary=job.result,
        )
        self.system_setting_service.set(
            db,
            key=auto_status_key,
            value=success_payload,
            description=AUTO_IMPORT_DESCRIPTION,
            updated_by_user_id=user.id,
        )
        return success_payload

    def _status_matches(
        self,
        payload: Any,
        source_checksum: str,
        expected_status: str,
        target_user_id: str,
    ) -> bool:
        return (
            isinstance(payload, dict)
            and payload.get("status") == expected_status
            and payload.get("sourceChecksum") == source_checksum
            and payload.get("targetUserId") == target_user_id
        )

    def _last_import_matches(self, payload: Any, source_checksum: str, target_user_id: str) -> bool:
        return (
            isinstance(payload, dict)
            and payload.get("sourceChecksum") == source_checksum
            and payload.get("targetUserId") == target_user_id
        )

    def _get_user_scoped_setting(
        self,
        db: Session,
        base_key: str,
        user_id: str,
    ) -> tuple[dict[str, Any] | None, str]:
        scoped_key = self._user_scoped_key(base_key, user_id)
        scoped_payload = self.system_setting_service.get(db, scoped_key, default=None)
        if isinstance(scoped_payload, dict):
            return scoped_payload, scoped_key

        legacy_payload = self.system_setting_service.get(db, base_key, default=None)
        if isinstance(legacy_payload, dict) and legacy_payload.get("targetUserId") == user_id:
            return legacy_payload, scoped_key
        return None, scoped_key

    def _user_scoped_key(self, base_key: str, user_id: str) -> str:
        return f"{base_key}.{user_id}"

    def _status_payload(
        self,
        *,
        status: str,
        source_path: str,
        source_checksum: str,
        target_user_id: str,
        overwrite_existing: bool,
        started_at: str | None = None,
        completed_at: str | None = None,
        job_id: str | None = None,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
        reused_existing: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": "automatic",
            "status": status,
            "sourcePath": source_path,
            "sourceChecksum": source_checksum,
            "targetUserId": target_user_id,
            "overwriteExisting": overwrite_existing,
            "reusedExisting": reused_existing,
        }
        if started_at:
            payload["startedAt"] = started_at
        if completed_at:
            payload["completedAt"] = completed_at
        if job_id:
            payload["jobId"] = job_id
        if summary is not None:
            payload["summary"] = summary
        if error:
            payload["error"] = error
        return payload
