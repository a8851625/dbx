# DBX Enterprise Config Migration

This branch completes the PostgreSQL-side migration path for legacy DBX runtime state and closes the main persistence gap left in the web runtime.

## Scope

The backend now stores and tracks:

- `system_setting`: system-wide configuration metadata such as the last migration summary and preserved legacy auth metadata.
- `legacy_import_job`: execution records for legacy SQLite/JSON imports.
- `editor_settings`: browser editor preferences persisted through PostgreSQL user preferences instead of browser-only `localStorage`.
- `config_migration.auto_import`: one-shot automatic migration status for the legacy Docker/web data source.

The legacy import flow supports:

- Legacy desktop SQLite state databases.
- Plain DBX connection export JSON files.
- JSON manifest files that bundle connections, layout, saved SQL, history, AI config, AI conversations, desktop settings, pinned nodes, and editor settings.
- Legacy data directories containing split JSON files such as `connections.json`, `secrets.json`, `query_history.json`, `ai_config.json`, and `sidebar_layout.json`.

## Automatic Migration

On the first authenticated `GET /api/v1/access/me` after deployment, the FastAPI backend now:

1. Scans `CONFIG_MIGRATION_AUTO_SOURCE` when explicitly configured.
2. Falls back to `DBX_DATA_DIR` (default `/app/data`) and prefers `dbx.db` over the split JSON directory when both exist.
3. Imports the discovered legacy runtime state into PostgreSQL for the first authenticated user.
4. Records the result in `system_setting.key = 'config_migration.auto_import'`.
5. Skips repeated imports when the same source checksum was already imported successfully.

The standard compose file mounts the legacy Docker volume into `/app/data` as read-only:

```yaml
services:
  dbx:
    volumes:
      - dbx-data:/app/data:ro
```

This keeps the normal Docker upgrade path automatic when moving from the legacy file-backed runtime to the PostgreSQL enterprise runtime.

## CLI Fallback

Keep the CLI for break-glass retries or alternate snapshot paths.

```bash
cd backend
python -m app.scripts.import_legacy_config /path/to/dbx.sqlite --owner-email admin@example.com
```

Optional flags:

- `--owner-user-id <uuid>`: use a DBX user ID directly.
- `--created-by-user-id <uuid>`: record the operator in `legacy_import_job`.
- `--overwrite-existing`: update matching connection identities instead of skipping them.
- `--dry-run`: parse the source and print a preview summary without writing to PostgreSQL.

## Accepted JSON Manifest Shape

For full-state imports, use a JSON object shaped like:

```json
{
  "connections": [],
  "layout": { "groups": [], "order": [] },
  "editorSettings": {},
  "appSettings": {
    "show_tray_icon": false,
    "pinned_tree_node_ids": []
  },
  "aiConfig": {},
  "aiConversations": [],
  "history": [],
  "savedSql": {
    "folders": [],
    "files": []
  }
}
```

Also supported:

- Raw connection arrays.
- `{ "format": "dbx-config", "connections": [...] }`.
- A directory that contains one or more of the split legacy JSON files listed above.

## Import Behavior

- User-scoped runtime state is imported into PostgreSQL and tracked in `legacy_import_job`.
- The latest successful import summary is written to `system_setting.key = 'config_migration.last_import'`.
- Automatic imports additionally write `system_setting.key = 'config_migration.auto_import'`.
- If a legacy `password_hash` is present, it is preserved in `system_setting.key = 'config_migration.legacy_auth'` for traceability.
- SQLite, DuckDB, and Access connections are skipped in web mode because the browser runtime no longer exposes those local desktop drivers.
- Existing connections are merged by ID first, then by `db_type + name + host + port`.
- Browser editor settings are now loaded from `/api/editor-settings` in web mode and only fall back to `localStorage` when the backend save fails.

## Integration Notes

- OIDC, RBAC, approval, audit, and runtime-state routes continue to share the same PostgreSQL database.
- The app does not add a dedicated "import now" UI; the mainline upgrade path is now automatic and the CLI remains the operational fallback.
- Shared test handoff still needs a `test/**` branch push and deployment evidence before QA can mark the parent line as passed.
