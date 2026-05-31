from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import UserIdentity
from app.services.legacy_import import LegacyImportService, LegacyImportSnapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import legacy DBX desktop SQLite/JSON state into the PostgreSQL enterprise backend."
    )
    parser.add_argument("source", help="Path to the legacy SQLite database, JSON file, or JSON directory.")
    parser.add_argument(
        "--owner-user-id",
        help="Target user ID that will own the imported runtime state.",
    )
    parser.add_argument(
        "--owner-email",
        help="Target user email. Use this instead of --owner-user-id when the user ID is unknown.",
    )
    parser.add_argument(
        "--created-by-user-id",
        help="Optional operator user ID to record in legacy_import_job and audit metadata.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Update matching connection identities instead of skipping them.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the source contents without writing to PostgreSQL.",
    )
    return parser


def resolve_owner_user_id(owner_user_id: str | None, owner_email: str | None) -> str:
    if not owner_user_id and not owner_email:
        raise SystemExit("Either --owner-user-id or --owner-email is required.")

    db = SessionLocal()
    try:
        if owner_user_id:
            user = db.get(UserIdentity, owner_user_id)
        else:
            user = db.execute(select(UserIdentity).where(UserIdentity.email == owner_email)).scalar_one_or_none()
        if user is None:
            lookup = owner_user_id or owner_email or "<unknown>"
            raise SystemExit(f"Target user not found: {lookup}")
        return user.id
    finally:
        db.close()


def preview_snapshot(snapshot: LegacyImportSnapshot) -> dict[str, object]:
    return {
        "sourceKind": snapshot.source_kind,
        "sourceLabel": snapshot.source_label,
        "connections": len(snapshot.connections),
        "connectionSecretFields": sorted(
            {
                key
                for connection in snapshot.connections
                for key in ("password", "ssh_password", "ssh_key_passphrase", "proxy_password", "connection_string")
                if connection.get(key)
            }
        ),
        "historyEntries": len(snapshot.history_entries),
        "savedSqlFolders": len(snapshot.saved_sql_folders),
        "savedSqlFiles": len(snapshot.saved_sql_files),
        "aiConversations": len(snapshot.ai_conversations),
        "preferences": {
            "sidebarLayoutPresent": snapshot.sidebar_layout is not None,
            "pinnedTreeNodeIdsPresent": snapshot.pinned_tree_node_ids is not None,
            "desktopSettingsPresent": snapshot.desktop_settings is not None,
            "editorSettingsPresent": snapshot.editor_settings is not None,
            "aiConfigPresent": snapshot.ai_config is not None,
            "legacyPasswordHashPresent": bool(snapshot.legacy_password_hash),
        },
        "metadata": snapshot.metadata,
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    source_path = Path(args.source)
    service = LegacyImportService()

    if args.dry_run:
        snapshot = service.load_source(source_path)
        print(json.dumps(preview_snapshot(snapshot), indent=2, ensure_ascii=False))
        return

    owner_user_id = resolve_owner_user_id(args.owner_user_id, args.owner_email)
    db = SessionLocal()
    try:
        job = service.import_source(
            db,
            source_path=source_path,
            owner_user_id=owner_user_id,
            created_by_user_id=args.created_by_user_id,
            overwrite_existing=bool(args.overwrite_existing),
        )
        print(
            json.dumps(
                {
                    "jobId": job.id,
                    "status": job.status,
                    "targetUserId": job.target_user_id,
                    "sourceKind": job.source_kind,
                    "sourceLabel": job.source_label,
                    "result": job.result,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
