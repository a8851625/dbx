from __future__ import annotations

import unittest

from cryptography.fernet import Fernet

from app.config import Settings
from app.models.runtime_state import ConnectionProfile, ConnectionSecret
from app.services.audit import AuditService
from app.services.connection_secrets import SECRET_PLACEHOLDER, ConnectionSecretService
from app.services.runtime_state import RuntimeStateService


class FakeScalarResult:
    def __init__(self, items):
        self.items = list(items)

    def all(self):
        return self.items


class FakeExecuteResult:
    def __init__(self, items):
        self.items = list(items)

    def scalars(self):
        return FakeScalarResult(self.items)

    def scalar_one_or_none(self):
        return self.items[0] if self.items else None


class FakeRuntimeDB:
    def __init__(self) -> None:
        self.secrets: dict[tuple[str, str], ConnectionSecret] = {}
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.commit_count = 0
        self.flush_count = 0

    def get(self, model, key):
        return None

    def execute(self, stmt):
        text = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        if "connection_secret.key" in text and "connection_secret.encrypted_value" not in text:
            return FakeExecuteResult([key for _, key in self.secrets.keys()])
        if "connection_secret.connection_id" in text and "connection_secret.key" in text:
            for (connection_id, key), secret in self.secrets.items():
                if f"'{connection_id}'" in text and f"'{key}'" in text:
                    return FakeExecuteResult([secret])
            return FakeExecuteResult([])
        return FakeExecuteResult(list(self.secrets.values()))

    def add(self, obj) -> None:
        self.added.append(obj)
        if isinstance(obj, ConnectionSecret):
            self.secrets[(obj.connection_id, obj.key)] = obj

    def delete(self, obj) -> None:
        self.deleted.append(obj)
        if isinstance(obj, ConnectionSecret):
            self.secrets.pop((obj.connection_id, obj.key), None)

    def commit(self) -> None:
        self.commit_count += 1

    def flush(self) -> None:
        self.flush_count += 1


class ConnectionSecretServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Fernet.generate_key().decode("ascii")
        self.secret_service = ConnectionSecretService(Settings(dbx_connection_secret_key=self.key))
        self.runtime = RuntimeStateService(secret_service=self.secret_service)

    def test_save_profile_moves_sensitive_fields_out_of_config(self) -> None:
        db = FakeRuntimeDB()
        profile = ConnectionProfile(id="conn-1", owner_user_id="user-1", name="Warehouse", config={})

        result = self.runtime.save_connection_profile_config(
            db,
            profile,
            {
                "id": "conn-1",
                "name": "Warehouse",
                "db_type": "postgres",
                "host": "pg.internal",
                "username": "dbx",
                "password": "secret",
                "proxy_password": "proxy-secret",
            },
        )

        self.assertEqual(result, {"stored": 2, "cleared": 0, "preserved": 0})
        self.assertNotIn("password", profile.config)
        self.assertNotIn("proxy_password", profile.config)
        self.assertIn(("conn-1", "password"), db.secrets)
        self.assertIn(("conn-1", "proxy_password"), db.secrets)
        self.assertNotEqual(db.secrets[("conn-1", "password")].encrypted_value, "secret")
        self.assertEqual(self.secret_service.decrypt(db.secrets[("conn-1", "password")].encrypted_value), "secret")

    def test_placeholder_preserves_existing_secret_and_empty_value_clears(self) -> None:
        db = FakeRuntimeDB()
        profile = ConnectionProfile(id="conn-1", owner_user_id="user-1", name="Warehouse", config={})
        self.runtime.save_connection_profile_config(db, profile, {"id": "conn-1", "name": "Warehouse", "password": "secret"})
        encrypted = db.secrets[("conn-1", "password")].encrypted_value

        result = self.runtime.save_connection_profile_config(
            db,
            profile,
            {"id": "conn-1", "name": "Warehouse", "password": SECRET_PLACEHOLDER},
        )

        self.assertEqual(result, {"stored": 0, "cleared": 0, "preserved": 1})
        self.assertEqual(db.secrets[("conn-1", "password")].encrypted_value, encrypted)

        result = self.runtime.save_connection_profile_config(
            db,
            profile,
            {"id": "conn-1", "name": "Warehouse", "password": ""},
        )

        self.assertEqual(result, {"stored": 0, "cleared": 1, "preserved": 0})
        self.assertNotIn(("conn-1", "password"), db.secrets)

    def test_payload_redaction_cleans_nested_sensitive_values(self) -> None:
        redacted = AuditService()._redact_sensitive_payload(
            {
                "connection": {"password": "secret", "host": "pg.internal"},
                "connectionSecrets": {"connection:conn-1:password": "secret"},
                "items": [{"proxy_password": "proxy-secret"}],
                "api_token": "token",
                "secretsStored": 1,
            }
        )

        self.assertEqual(redacted["connection"]["password"], SECRET_PLACEHOLDER)
        self.assertEqual(redacted["connection"]["host"], "pg.internal")
        self.assertEqual(redacted["connectionSecrets"], SECRET_PLACEHOLDER)
        self.assertEqual(redacted["items"][0]["proxy_password"], SECRET_PLACEHOLDER)
        self.assertEqual(redacted["api_token"], SECRET_PLACEHOLDER)
        self.assertEqual(redacted["secretsStored"], 1)


if __name__ == "__main__":
    unittest.main()
