from __future__ import annotations

import unittest

from app.config import Settings
from app.models.auth import UserIdentity
from app.services.config_migration import ConfigMigrationService


class FakeLegacyImportService:
    def __init__(self, *, source_path: str = "/tmp/dbx.db", checksum: str = "checksum-1") -> None:
        self.source_path = source_path
        self.checksum = checksum
        self.calls: list[dict[str, object]] = []
        self.next_job = type(
            "Job",
            (),
            {
                "id": "job-1",
                "completed_at": None,
                "result": {
                    "connections": {
                        "created": 1,
                        "updated": 0,
                        "skipped": 0,
                        "unsupported": [],
                        "secretsStored": 1,
                        "secretsCleared": 0,
                    }
                },
            },
        )()

    def discover_auto_source(self, *, preferred_path=None, data_dir=None):
        return self.source_path

    def source_checksum(self, source_path):
        return self.checksum

    def import_source(self, db, *, source_path, owner_user_id, created_by_user_id=None, overwrite_existing=False):
        self.calls.append(
            {
                "source_path": source_path,
                "owner_user_id": owner_user_id,
                "created_by_user_id": created_by_user_id,
                "overwrite_existing": overwrite_existing,
            }
        )
        return self.next_job


class FakeSystemSettingService:
    def __init__(self, initial: dict[str, object] | None = None) -> None:
        self.values = dict(initial or {})
        self.set_calls: list[dict[str, object]] = []

    def get(self, db, key: str, *, default=None):
        return self.values.get(key, default)

    def set(self, db, *, key: str, value, description=None, updated_by_user_id=None, commit=True):
        self.values[key] = value
        self.set_calls.append(
            {
                "key": key,
                "value": value,
                "description": description,
                "updated_by_user_id": updated_by_user_id,
                "commit": commit,
            }
        )
        return value


def make_user(user_id: str) -> UserIdentity:
    return UserIdentity(
        id=user_id,
        provider_id="default",
        subject=f"sub-{user_id}",
        email=f"{user_id}@example.com",
        role="admin",
    )


class ConfigMigrationServiceTests(unittest.TestCase):
    def test_reuses_only_matching_user_scoped_last_import(self) -> None:
        user = make_user("user-1")
        legacy_import = FakeLegacyImportService(checksum="checksum-1")
        settings = FakeSystemSettingService(
            {
                "config_migration.last_import.user-1": {
                    "jobId": "job-9",
                    "targetUserId": "user-1",
                    "sourceChecksum": "checksum-1",
                    "completedAt": "2026-05-27T00:00:00+00:00",
                    "summary": {"connections": {"created": 2}},
                },
                "config_migration.last_import.user-2": {
                    "jobId": "job-10",
                    "targetUserId": "user-2",
                    "sourceChecksum": "checksum-1",
                    "completedAt": "2026-05-27T00:00:00+00:00",
                    "summary": {"connections": {"created": 3}},
                },
            }
        )
        service = ConfigMigrationService(
            settings=Settings(config_migration_auto_enabled=True),
            legacy_import_service=legacy_import,
            system_setting_service=settings,
        )

        payload = service.ensure_auto_import_for_user(object(), user)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["targetUserId"], "user-1")
        self.assertEqual(payload["jobId"], "job-9")
        self.assertEqual(payload["summary"], {"connections": {"created": 2}})
        self.assertEqual(payload["reusedExisting"], True)
        self.assertEqual(legacy_import.calls, [])
        self.assertEqual(settings.set_calls[-1]["key"], "config_migration.auto_import.user-1")

    def test_ignores_global_last_import_owned_by_another_user(self) -> None:
        user = make_user("user-1")
        legacy_import = FakeLegacyImportService(checksum="checksum-1")
        settings = FakeSystemSettingService(
            {
                "config_migration.last_import": {
                    "jobId": "job-2",
                    "targetUserId": "user-2",
                    "sourceChecksum": "checksum-1",
                    "completedAt": "2026-05-27T00:00:00+00:00",
                    "summary": {"connections": {"created": 5}},
                }
            }
        )
        service = ConfigMigrationService(
            settings=Settings(config_migration_auto_enabled=True),
            legacy_import_service=legacy_import,
            system_setting_service=settings,
        )

        payload = service.ensure_auto_import_for_user(object(), user)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["targetUserId"], "user-1")
        self.assertEqual(len(legacy_import.calls), 1)
        self.assertEqual(legacy_import.calls[0]["owner_user_id"], "user-1")
        self.assertEqual(settings.set_calls[0]["key"], "config_migration.auto_import.user-1")
        self.assertEqual(settings.set_calls[-1]["key"], "config_migration.auto_import.user-1")


if __name__ == "__main__":
    unittest.main()
