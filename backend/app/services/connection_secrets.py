from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings


SENSITIVE_CONNECTION_FIELDS = frozenset(
    {
        "password",
        "ssh_password",
        "ssh_key_passphrase",
        "proxy_password",
        "connection_string",
    }
)
SECRET_PLACEHOLDER = "********"
SECRET_METADATA_FIELDS = frozenset({"secret_fields", "_secret_fields", "secretRefs", "secret_refs"})


@dataclass(frozen=True)
class ConnectionSecretSplit:
    safe_config: dict[str, Any]
    secret_values: dict[str, str]
    clear_secret_fields: set[str]
    preserve_secret_fields: set[str]


class ConnectionSecretService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def split_config(
        self,
        config: dict[str, Any],
        *,
        existing_secret_fields: set[str] | None = None,
    ) -> ConnectionSecretSplit:
        existing_secret_fields = existing_secret_fields or set()
        safe_config = {
            key: value
            for key, value in dict(config).items()
            if key not in SENSITIVE_CONNECTION_FIELDS and key not in SECRET_METADATA_FIELDS
        }
        secret_values: dict[str, str] = {}
        clear_secret_fields: set[str] = set()
        preserve_secret_fields: set[str] = set()

        for field in SENSITIVE_CONNECTION_FIELDS:
            if field not in config:
                continue
            raw_value = config.get(field)
            if raw_value == SECRET_PLACEHOLDER:
                if field in existing_secret_fields:
                    preserve_secret_fields.add(field)
                continue
            if raw_value is None or raw_value == "":
                clear_secret_fields.add(field)
                continue
            secret_values[field] = str(raw_value)

        return ConnectionSecretSplit(
            safe_config=safe_config,
            secret_values=secret_values,
            clear_secret_fields=clear_secret_fields,
            preserve_secret_fields=preserve_secret_fields,
        )

    def encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_value: str) -> str:
        try:
            return self._fernet().decrypt(encrypted_value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Connection secret cannot be decrypted with the configured key") from exc

    def _fernet(self) -> Fernet:
        key = self.settings.dbx_connection_secret_key.strip()
        if not key:
            raise RuntimeError("DBX_CONNECTION_SECRET_KEY must be configured before storing connection secrets")
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("DBX_CONNECTION_SECRET_KEY must be a valid Fernet key") from exc
