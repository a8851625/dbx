# DBX Enterprise Config Migration

This branch completes the PostgreSQL-side migration path for legacy DBX runtime state and closes the main persistence gap left in the web runtime.

## Scope

The backend now stores and tracks:

- `system_setting`: system-wide configuration metadata such as the last migration summary.
- `legacy_import_job`: execution records for legacy SQLite/JSON imports.
- `editor_settings`: browser editor preferences persisted through PostgreSQL user preferences instead of browser-only `localStorage`.

The legacy import flow supports:

- Legacy desktop SQLite state databases.
- Plain DBX connection export JSON files.
- JSON manifest files that bundle connections, layout, saved SQL, history, AI config, AI conversations, desktop settings, pinned nodes, and editor settings.

## CLI

Run the import from the backend directory after the target user has logged in at least once and exists in `user_identity`.

```bash
cd backend
python -m app.scripts.import_legacy_config /path/to/dbx.sqlite --owner-email admin@example.com
```

Optional flags:

- `--owner-user-id <uuid>`: use a DBX user ID directly.
- `--created-by-user-id <uuid>`: record the operator in `legacy_import_job`.
- `--overwrite-existing`: update matching connection identities instead of skipping them.

The script prints a JSON summary with the created `legacy_import_job.id`.

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
- `{ "format": "dbx-config", "connections": [...] }`

## Import Behavior

- User-scoped runtime state is imported into PostgreSQL and tracked in `legacy_import_job`.
- The latest successful import summary is written to `system_setting.key = 'config_migration.last_import'`.
- SQLite, DuckDB, and Access connections are skipped in web mode because the browser runtime no longer exposes those local desktop drivers.
- Existing connections are merged by ID first, then by `name + host + port`.
- Browser editor settings are now loaded from `/api/editor-settings` in web mode and only fall back to `localStorage` when the backend save fails.

## Integration Notes

- OIDC, RBAC, approval, audit, and runtime-state routes continue to share the same PostgreSQL database.
- This change does not add a UI for one-off imports; import remains an operational script in this iteration.
- Shared test handoff still needs a `test/**` branch push and deployment evidence before QA can mark the parent line as passed.
