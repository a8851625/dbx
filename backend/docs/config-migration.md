# DBX Enterprise Config Migration

This branch completes the PostgreSQL-side migration path for legacy DBX runtime state and closes the main persistence gap left in the web runtime.

## Scope

The backend now stores and tracks:

- `system_setting`: migration bookkeeping stored as scoped keys in PostgreSQL, including per-user import summaries and preserved legacy auth metadata.
- `legacy_import_job`: execution records for legacy SQLite/JSON imports.
- `connection_secret`: encrypted per-connection credentials moved out of `connection_profile.config`.
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
3. Imports the discovered legacy runtime state into PostgreSQL for the current authenticated user.
4. Records the per-user result in `system_setting.key = 'config_migration.auto_import.<user-id>'`.
5. Skips repeated imports only when the same source checksum was already imported successfully for that same user.

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
- The latest successful import summary is written to `system_setting.key = 'config_migration.last_import.<user-id>'`.
- Automatic imports write `system_setting.key = 'config_migration.auto_import.<user-id>'`.
- If a legacy `password_hash` is present, it is preserved in `system_setting.key = 'config_migration.legacy_auth.<user-id>'` for traceability.
- Legacy global keys remain readable only as a compatibility fallback when their payload already belongs to the current user.
- SQLite, DuckDB, and Access connections are skipped in web mode because the browser runtime no longer exposes those local desktop drivers.
- Existing connections are merged by ID first, then by `db_type + name + host + port`.
- Browser editor settings are now loaded from `/api/editor-settings` in web mode and only fall back to `localStorage` when the backend save fails.

## Connection Secret Storage

Connection profiles keep non-sensitive metadata in `connection_profile.config`. These fields are stripped before JSONB persistence and written to `connection_secret` instead:

- `password`
- `ssh_password`
- `ssh_key_passphrase`
- `proxy_password`
- `connection_string`

Each secret row is encrypted by the FastAPI backend with `DBX_CONNECTION_SECRET_KEY` before it is stored in PostgreSQL. The key must be a Fernet key and must remain stable across restarts, upgrades, rollbacks, and shared-test/prod deployments.

Generate a key for each environment:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Local Docker Compose uses `DBX_CONNECTION_SECRET_KEY` from the shell when provided and falls back to the development value in `.env.example`. Shared test and production must inject their own value through the environment/secret manager. Do not rotate this value without first re-encrypting all `connection_secret.encrypted_value` rows.

`GET /api/connection/list` returns `********` for fields that have stored credentials. Editing a connection preserves that placeholder unless the user enters an empty value to clear the secret or a new value to replace it. Runtime connection tests and query execution resolve the encrypted secret server-side and never require the browser to receive cleartext credentials.

Legacy import still accepts cleartext secrets from SQLite `connection_secrets`, split `secrets.json`, or manifest connection payloads, but writes them into `connection_secret` instead of `connection_profile.config`. Import summaries and audit payloads include only counts and field names, not secret values.

## Rollback Notes

The `0008_connection_secret_store` migration intentionally removes sensitive fields from `connection_profile.config` before the application can write encrypted rows. If a rollback is required after secrets have been stored, keep a database backup that includes `connection_secret` and the exact `DBX_CONNECTION_SECRET_KEY`; otherwise connection passwords cannot be recovered. Downgrading the migration drops `connection_secret` and does not write cleartext secrets back into JSONB.

## Web Runtime Local State Boundary

Persisted in PostgreSQL per user:

- Connections
- Sidebar layout
- Pinned tree node IDs
- AI config
- Desktop settings
- Editor settings
- Saved SQL library
- Query history
- AI conversations

Still intentionally browser-local in web runtime:

- Open tabs: `dbx-open-tabs`
- Active tab: `dbx-active-tab`
- Panel widths: `dbx-sidebar-width`, `dbx-ai-panel-width`, `dbx-history-width`
- Active connection selection: `dbx-active-connection`
- Theme preference: `dbx-theme-mode`
- Locale preference: `dbx-locale`

Legacy browser storage used only as one-time migration/fallback input:

- `dbx-pinned-tree-nodes`
- `dbx-ai-config`
- `dbx-editor-settings`
- `dbx-query-editor-font-size`
- `dbx-saved-sql-library`

This boundary avoids cross-user leakage for account-owned runtime state while keeping clearly browser-specific UI preferences local to each browser.

## Integration Notes

- OIDC, RBAC, approval, audit, and runtime-state routes continue to share the same PostgreSQL database.
- The app does not add a dedicated "import now" UI; the mainline upgrade path is now automatic and the CLI remains the operational fallback.
- Shared test handoff still needs a `test/**` branch push and deployment evidence before QA can mark the parent line as passed.
