from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.system_state import SystemSetting


class SystemSettingService:
    def get(self, db: Session, key: str, *, default: Any = None) -> Any:
        item = db.get(SystemSetting, key)
        return default if item is None else item.value

    def set(
        self,
        db: Session,
        *,
        key: str,
        value: Any,
        description: str | None = None,
        updated_by_user_id: str | None = None,
        commit: bool = True,
    ) -> SystemSetting:
        item = db.get(SystemSetting, key)
        if item is None:
            item = SystemSetting(key=key, value=value)
            db.add(item)
        else:
            item.value = value
        item.description = description
        item.updated_by_user_id = updated_by_user_id
        if commit:
            db.commit()
            db.refresh(item)
        else:
            db.flush()
        return item
