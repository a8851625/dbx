from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auth import UserIdentity
from app.services.legacy_import import LegacyImportService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import legacy DBX desktop SQLite/JSON state into the PostgreSQL enterprise backend."
    )
    parser.add_argument("source", help="Path to the legacy SQLite database or JSON manifest.")
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    owner_user_id = resolve_owner_user_id(args.owner_user_id, args.owner_email)

    db = SessionLocal()
    try:
        job = LegacyImportService().import_source(
            db,
            source_path=Path(args.source),
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
