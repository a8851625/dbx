from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.services.legacy_import import LegacyImportService


class LegacyImportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = LegacyImportService()

    def test_load_json_manifest_supports_editor_settings_and_saved_sql(self) -> None:
        payload = {
            "connections": [
                {
                    "id": "conn-1",
                    "name": "Warehouse",
                    "db_type": "postgres",
                    "host": "db.internal",
                    "port": 5432,
                }
            ],
            "layout": {"groups": [], "order": [{"type": "connection", "id": "conn-1"}]},
            "editorSettings": {"fontSize": 15, "theme": "xcode"},
            "appSettings": {"show_tray_icon": False, "pinned_tree_node_ids": ["conn-1"]},
            "savedSql": {
                "folders": [{"id": "folder-1", "connectionId": "conn-1", "name": "Ops"}],
                "files": [
                    {
                        "id": "file-1",
                        "connectionId": "conn-1",
                        "folderId": "folder-1",
                        "name": "Inspect",
                        "database": "warehouse",
                        "sql": "select 1",
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "legacy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            snapshot = self.service.load_source(path)

        self.assertEqual(snapshot.source_kind, "json")
        self.assertEqual(snapshot.connections[0]["name"], "Warehouse")
        self.assertEqual(snapshot.editor_settings, {"fontSize": 15, "theme": "xcode"})
        self.assertEqual(snapshot.desktop_settings, {"show_tray_icon": False})
        self.assertEqual(snapshot.pinned_tree_node_ids, ["conn-1"])
        self.assertEqual(snapshot.saved_sql_folders[0]["name"], "Ops")
        self.assertEqual(snapshot.saved_sql_files[0]["folderId"], "folder-1")

    def test_load_sqlite_snapshot_reads_connections_history_and_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            conn = sqlite3.connect(handle.name)
            try:
                conn.executescript(
                    """
                    CREATE TABLE connections (id TEXT PRIMARY KEY, config_json TEXT NOT NULL);
                    CREATE TABLE connection_secrets (
                      connection_id TEXT NOT NULL,
                      key TEXT NOT NULL,
                      secret TEXT NOT NULL,
                      PRIMARY KEY (connection_id, key)
                    );
                    CREATE TABLE history (
                      id TEXT PRIMARY KEY,
                      connection_id TEXT NOT NULL,
                      connection_name TEXT NOT NULL,
                      database TEXT NOT NULL,
                      sql_text TEXT NOT NULL,
                      executed_at TEXT NOT NULL,
                      execution_time_ms INTEGER NOT NULL,
                      success INTEGER NOT NULL,
                      error TEXT,
                      activity_kind TEXT NOT NULL,
                      operation TEXT NOT NULL,
                      target TEXT NOT NULL,
                      affected_rows INTEGER,
                      rollback_sql TEXT,
                      details_json TEXT
                    );
                    CREATE TABLE ai_config (id INTEGER PRIMARY KEY, config_json TEXT NOT NULL);
                    CREATE TABLE ai_conversations (
                      id TEXT PRIMARY KEY,
                      title TEXT NOT NULL,
                      connection_name TEXT NOT NULL,
                      database TEXT NOT NULL,
                      messages_json TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );
                    CREATE TABLE sidebar_layout (id INTEGER PRIMARY KEY, layout_json TEXT NOT NULL);
                    CREATE TABLE app_settings (id INTEGER PRIMARY KEY, settings_json TEXT NOT NULL);
                    CREATE TABLE saved_sql_folders (
                      id TEXT PRIMARY KEY,
                      connection_id TEXT NOT NULL,
                      name TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );
                    CREATE TABLE saved_sql_files (
                      id TEXT PRIMARY KEY,
                      connection_id TEXT NOT NULL,
                      folder_id TEXT,
                      name TEXT NOT NULL,
                      database_name TEXT NOT NULL,
                      schema_name TEXT,
                      sql_text TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO connections (id, config_json) VALUES (?, ?)",
                    (
                        "conn-1",
                        json.dumps(
                            {
                                "id": "conn-1",
                                "name": "Warehouse",
                                "db_type": "postgres",
                                "host": "pg.internal",
                                "port": 5432,
                                "username": "dbx",
                            }
                        ),
                    ),
                )
                conn.execute(
                    "INSERT INTO connection_secrets (connection_id, key, secret) VALUES (?, ?, ?)",
                    ("conn-1", "password", "secret"),
                )
                conn.execute(
                    "INSERT INTO history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "hist-1",
                        "conn-1",
                        "Warehouse",
                        "analytics",
                        "select * from orders",
                        "2026-05-26T00:00:00Z",
                        25,
                        1,
                        None,
                        "query",
                        "select",
                        "orders",
                        10,
                        None,
                        json.dumps({"table": "orders"}),
                    ),
                )
                conn.execute(
                    "INSERT INTO ai_config (id, config_json) VALUES (1, ?)",
                    (json.dumps({"provider": "openai", "model": "gpt-4o-mini"}),),
                )
                conn.execute(
                    "INSERT INTO ai_conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "conv-1",
                        "Incident",
                        "Warehouse",
                        "analytics",
                        json.dumps([{"role": "user", "content": "help"}]),
                        "2026-05-25T00:00:00Z",
                        "2026-05-25T01:00:00Z",
                    ),
                )
                conn.execute(
                    "INSERT INTO sidebar_layout (id, layout_json) VALUES (1, ?)",
                    (json.dumps({"groups": [], "order": [{"type": "connection", "id": "conn-1"}]}),),
                )
                conn.execute(
                    "INSERT INTO app_settings (id, settings_json) VALUES (1, ?)",
                    (json.dumps({"show_tray_icon": True, "pinned_tree_node_ids": ["conn-1"], "password_hash": "x"}),),
                )
                conn.execute(
                    "INSERT INTO saved_sql_folders VALUES (?, ?, ?, ?, ?)",
                    ("folder-1", "conn-1", "Ops", "2026-05-20T00:00:00Z", "2026-05-21T00:00:00Z"),
                )
                conn.execute(
                    "INSERT INTO saved_sql_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "file-1",
                        "conn-1",
                        "folder-1",
                        "Inspect",
                        "analytics",
                        "public",
                        "select 1",
                        "2026-05-20T00:00:00Z",
                        "2026-05-21T00:00:00Z",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            snapshot = self.service.load_source(handle.name)

        self.assertEqual(snapshot.source_kind, "sqlite")
        self.assertEqual(snapshot.connections[0]["password"], "secret")
        self.assertEqual(snapshot.history_entries[0]["details_json"], {"table": "orders"})
        self.assertEqual(snapshot.ai_config, {"provider": "openai", "model": "gpt-4o-mini"})
        self.assertEqual(snapshot.ai_conversations[0]["messages"][0]["content"], "help")
        self.assertEqual(snapshot.desktop_settings, {"show_tray_icon": True})
        self.assertEqual(snapshot.pinned_tree_node_ids, ["conn-1"])
        self.assertEqual(snapshot.legacy_password_hash, "x")
        self.assertTrue(snapshot.metadata["legacyPasswordHashPresent"])
        self.assertEqual(snapshot.saved_sql_files[0]["schema"], "public")

    def test_load_json_directory_merges_split_files_and_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "connections.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "conn-1",
                            "name": "Warehouse",
                            "db_type": "postgres",
                            "host": "pg.internal",
                            "port": 5432,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "secrets.json").write_text(
                json.dumps({"connection:conn-1:password": "secret"}),
                encoding="utf-8",
            )
            (root / "query_history.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "hist-1",
                            "connection_id": "conn-1",
                            "connection_name": "Warehouse",
                            "database": "analytics",
                            "sql": "select 1",
                            "executed_at": "2026-05-26T00:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "ai_config.json").write_text(
                json.dumps({"provider": "openai"}),
                encoding="utf-8",
            )
            (root / "sidebar_layout.json").write_text(
                json.dumps({"groups": [], "order": [{"type": "connection", "id": "conn-1"}]}),
                encoding="utf-8",
            )
            (root / "app_settings.json").write_text(
                json.dumps({"show_tray_icon": False, "pinned_tree_node_ids": ["conn-1"], "password_hash": "hash"}),
                encoding="utf-8",
            )

            snapshot = self.service.load_source(root)

        self.assertEqual(snapshot.source_kind, "json_dir")
        self.assertEqual(snapshot.connections[0]["password"], "secret")
        self.assertEqual(snapshot.history_entries[0]["connection_name"], "Warehouse")
        self.assertEqual(snapshot.ai_config, {"provider": "openai"})
        self.assertEqual(snapshot.desktop_settings, {"show_tray_icon": False})
        self.assertEqual(snapshot.legacy_password_hash, "hash")
        self.assertIn("connections.json", snapshot.metadata["loadedFiles"])
        self.assertIn("secrets.json", snapshot.metadata["loadedFiles"])

    def test_discover_auto_source_prefers_sqlite_file_inside_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sqlite_path = root / "dbx.db"
            sqlite_path.write_bytes(b"sqlite")
            (root / "connections.json").write_text("[]", encoding="utf-8")

            discovered = self.service.discover_auto_source(data_dir=root)

        self.assertEqual(discovered, sqlite_path.resolve())

    def test_discover_auto_source_returns_none_for_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            discovered = self.service.discover_auto_source(data_dir=tmpdir)

        self.assertIsNone(discovered)


if __name__ == "__main__":
    unittest.main()
