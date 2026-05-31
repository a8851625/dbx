from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_service, get_current_user, require_permission
from app.config import get_settings
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.services.authorization import AuthorizationService
from app.services.query_runtime import QueryRuntimeService
from app.services.runtime_state import RuntimeStateService
from app.services.sql_classification import (
    ClassifiedStatement,
    classify_sql_statements,
    resolve_ticket_type,
    summarize_sql,
)

router = APIRouter()
runtime_state_service = RuntimeStateService()
query_runtime_service = QueryRuntimeService()


def _internal_token_guard(x_dbx_internal_token: str | None = Header(default=None)) -> None:
    expected = get_settings().dbx_web_internal_token
    if not expected or x_dbx_internal_token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal service token")


def _connection_or_400(db: Session, connection_id: str, user_id: str | None = None) -> dict[str, Any]:
    try:
        return query_runtime_service.resolve_connection_config(db, connection_id, owner_user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _ensure_resource_permission(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    permission_code: str,
    *,
    datasource_id: str | None = None,
    database: str | None = None,
    schema: str | None = None,
    table: str | None = None,
) -> None:
    context = authorization_service.build_access_context(db, current_user)
    decision = authorization_service.check_permission(
        context,
        permission_code,
        datasource_id=datasource_id,
        database=database or None,
        schema=schema or None,
        table=table or None,
    )
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason or "Forbidden")


def _resource_allowed(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    permission_code: str,
    *,
    datasource_id: str | None = None,
    database: str | None = None,
    schema: str | None = None,
    table: str | None = None,
) -> bool:
    context = authorization_service.build_access_context(db, current_user)
    return authorization_service.check_permission(
        context,
        permission_code,
        datasource_id=datasource_id,
        database=database or None,
        schema=schema or None,
        table=table or None,
    ).allowed


def _authorize_configured_connection(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    permission_code: str,
    config: dict[str, Any],
    *,
    database: str | None = None,
    schema: str | None = None,
    table: str | None = None,
) -> None:
    _ensure_resource_permission(
        db,
        authorization_service,
        current_user,
        permission_code,
        datasource_id=str(config.get("id") or ""),
        database=database,
        schema=schema,
        table=table,
    )


def _authorize_query_scope(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    payload: dict[str, Any],
    *,
    sql: str | None = None,
) -> None:
    datasource_id = str(payload.get("connectionId") or "")
    database = str(payload.get("database") or "") or None
    schema = payload.get("schema") if isinstance(payload.get("schema"), str) else None
    table = payload.get("tableName") or payload.get("table")
    table = str(table) if table else None
    references = query_runtime_service.extract_table_references(sql or "", default_schema=schema)
    if not references:
        _ensure_resource_permission(
            db,
            authorization_service,
            current_user,
            "query.execute",
            datasource_id=datasource_id,
            database=database,
            schema=schema,
            table=table,
        )
        return

    for reference in references:
        _ensure_resource_permission(
            db,
            authorization_service,
            current_user,
            "query.execute",
            datasource_id=datasource_id,
            database=reference.database or database,
            schema=reference.schema,
            table=reference.table,
        )


def _authorize_query_builder_options(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    options: dict[str, Any],
) -> None:
    datasource_id = options.get("connectionId")
    if not datasource_id:
        return
    _ensure_resource_permission(
        db,
        authorization_service,
        current_user,
        "query.execute",
        datasource_id=str(datasource_id),
        database=str(options.get("database") or "") or None,
        schema=str(options.get("schema") or "") or None,
        table=str(options.get("tableName") or options.get("table") or "") or None,
    )


def _filter_authorized_connections(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    configs: list[dict[str, Any]],
    permission_code: str,
) -> list[dict[str, Any]]:
    return [
        config
        for config in configs
        if _resource_allowed(
            db,
            authorization_service,
            current_user,
            permission_code,
            datasource_id=str(config.get("id") or ""),
            database=str(config.get("database") or "") or None,
        )
    ]


def _filter_authorized_databases(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    config: dict[str, Any],
    databases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    datasource_id = str(config.get("id") or "")
    return [
        database
        for database in databases
        if _resource_allowed(
            db,
            authorization_service,
            current_user,
            "datasource.browse",
            datasource_id=datasource_id,
            database=str(database.get("name") or "") or None,
        )
    ]


def _filter_authorized_schemas(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    config: dict[str, Any],
    *,
    database: str,
    schemas: list[str],
) -> list[str]:
    datasource_id = str(config.get("id") or "")
    return [
        schema
        for schema in schemas
        if _resource_allowed(
            db,
            authorization_service,
            current_user,
            "datasource.browse",
            datasource_id=datasource_id,
            database=database,
            schema=schema,
        )
    ]


def _filter_authorized_tables(
    db: Session,
    authorization_service: AuthorizationService,
    current_user: UserIdentity,
    config: dict[str, Any],
    *,
    database: str,
    schema: str | None,
    tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    datasource_id = str(config.get("id") or "")
    return [
        table
        for table in tables
        if _resource_allowed(
            db,
            authorization_service,
            current_user,
            "datasource.browse",
            datasource_id=datasource_id,
            database=database,
            schema=schema,
            table=str(table.get("name") or "") or None,
        )
    ]


def _runtime_error_to_http(exc: Exception) -> HTTPException:
    message = str(exc) or exc.__class__.__name__
    if "not supported" in message.lower():
        return HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _statement_payload(statement: ClassifiedStatement) -> dict[str, Any]:
    return {
        "order": statement.order,
        "statement_text": statement.text,
        "statement_type": statement.statement_type,
        "keyword": statement.keyword,
        "risk_level": statement.risk_level,
        "risk_tags": statement.risk_tags,
    }


def _approval_required_error(
    *,
    connection_id: str,
    database: str,
    schema: str | None,
    sql: str,
    statements: list[ClassifiedStatement],
) -> HTTPException:
    change_statements = [statement for statement in statements if statement.requires_approval]
    ticket_type = resolve_ticket_type(change_statements)
    ticket_sql = ";\n".join(statement.text for statement in change_statements)
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "DDL_DML_APPROVAL_REQUIRED",
            "message": "DDL/DML statements must be submitted as approval tickets before execution.",
            "ticket_draft": {
                "title": "SQL change request",
                "datasource_id": connection_id,
                "target_database": database,
                "target_schema": schema,
                "target_table": None,
                "sql_text": ticket_sql.strip() or sql.strip(),
                "scheduled_at": None,
            },
            "statement_count": len(statements),
            "ticket_type": ticket_type,
            "sql_summary": summarize_sql(change_statements),
            "statements": [_statement_payload(statement) for statement in statements],
        },
    )


def _ensure_direct_query_allowed(
    *,
    connection_id: str,
    database: str,
    schema: str | None,
    sql: str,
    statements: list[str] | None = None,
) -> None:
    sql_text = ";\n".join(str(statement) for statement in statements or []) if statements is not None else sql
    try:
        classified = classify_sql_statements(sql_text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SQL payload is empty") from exc
    if any(statement.requires_approval for statement in classified):
        raise _approval_required_error(
            connection_id=connection_id,
            database=database,
            schema=schema,
            sql=sql_text,
            statements=classified,
        )


def _payload_context(payload: dict[str, Any]) -> tuple[str, str, str | None]:
    return str(payload.get("connectionId") or ""), str(payload.get("database") or ""), payload.get("schema")


def _payload_sql(payload: dict[str, Any]) -> tuple[str, list[str] | None]:
    if "statements" in payload:
        return "", list(payload.get("statements") or [])
    return str(payload.get("sql") or ""), None


@router.get("/version")
def get_version() -> dict[str, str]:
    settings = get_settings()
    return {"version": settings.app_version}


@router.get("/update/check")
def check_updates() -> dict[str, Any]:
    version = get_settings().app_version
    return {
        "current_version": version,
        "latest_version": version,
        "update_available": False,
        "release_name": f"DBX Enterprise Web {version}",
        "release_url": "",
        "release_notes": "Pure web runtime does not provide in-app updater.",
    }


@router.post("/connection/test")
def test_connection(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("datasource.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> str:
    config = dict(payload.get("config") or {})
    if config.get("id"):
        _authorize_configured_connection(
            db,
            authorization_service,
            current_user,
            "datasource.connect",
            config,
            database=str(config.get("database") or "") or None,
        )
    try:
        query_runtime_service.register_connection(config)
        return query_runtime_service.test_connection(config)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/connection/connect")
def connect_db(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("datasource.connect")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> str:
    config = dict(payload.get("config") or {})
    if not config.get("id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connection id is required")
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.connect",
        config,
        database=str(config.get("database") or "") or None,
    )
    try:
        query_runtime_service.test_connection(config)
        query_runtime_service.register_connection(config)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc
    return str(config["id"])


@router.post("/connection/disconnect")
def disconnect_db(
    payload: dict[str, Any],
    _: UserIdentity = Depends(require_permission("datasource.connect")),
) -> dict[str, bool]:
    connection_id = str(payload.get("connectionId") or "")
    query_runtime_service.unregister_connection(connection_id)
    return {"ok": True}


@router.post("/connection/save")
def save_connections(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("datasource.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, bool]:
    configs = list(payload.get("configs") or [])
    for config in configs:
        if not isinstance(config, dict):
            continue
        _authorize_configured_connection(
            db,
            authorization_service,
            current_user,
            "datasource.manage",
            config,
            database=str(config.get("database") or "") or None,
        )
    runtime_state_service.save_connections(db, current_user.id, configs)
    for config in configs:
        query_runtime_service.register_connection(dict(config))
    return {"ok": True}


@router.get("/connection/list")
def load_connections(
    current_user: UserIdentity = Depends(require_permission("menu.connections.view")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    configs = runtime_state_service.load_connections(db, current_user.id)
    return _filter_authorized_connections(db, authorization_service, current_user, configs, "datasource.browse")


@router.get("/layout/sidebar")
def load_sidebar_layout(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any] | None:
    return runtime_state_service.load_sidebar_layout(db, current_user.id)


@router.post("/layout/sidebar")
def save_sidebar_layout(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.save_sidebar_layout(db, current_user.id, dict(payload.get("layout") or {}))
    return {"ok": True}


@router.get("/app-settings/pinned-tree-node-ids")
def load_pinned_tree_node_ids(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[str]:
    return runtime_state_service.load_pinned_tree_node_ids(db, current_user.id)


@router.post("/app-settings/pinned-tree-node-ids")
def save_pinned_tree_node_ids(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.save_pinned_tree_node_ids(db, current_user.id, list(payload.get("ids") or []))
    return {"ok": True}


@router.get("/saved-sql")
def load_saved_sql_library(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return runtime_state_service.load_saved_sql_library(db, current_user.id)


@router.post("/saved-sql/folders")
def save_saved_sql_folder(
    folder: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return runtime_state_service.save_saved_sql_folder(db, current_user.id, folder)


@router.delete("/saved-sql/folders/{folder_id}")
def delete_saved_sql_folder(
    folder_id: str,
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.delete_saved_sql_folder(db, current_user.id, folder_id)
    return {"ok": True}


@router.post("/saved-sql")
def save_saved_sql_file(
    file: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return runtime_state_service.save_saved_sql_file(db, current_user.id, file)


@router.delete("/saved-sql/{file_id}")
def delete_saved_sql_file(
    file_id: str,
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.delete_saved_sql_file(db, current_user.id, file_id)
    return {"ok": True}


@router.get("/history")
def load_history(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: UserIdentity = Depends(require_permission("history.view")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return runtime_state_service.load_history_entries(db, current_user.id, limit, offset)


@router.post("/history/save")
def save_history(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.save_history_entry(db, current_user.id, dict(payload.get("entry") or {}))
    return {"ok": True}


@router.delete("/history")
def clear_history(
    current_user: UserIdentity = Depends(require_permission("history.view")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.clear_history_entries(db, current_user.id)
    return {"ok": True}


@router.delete("/history/{entry_id}")
def delete_history_entry(
    entry_id: str,
    current_user: UserIdentity = Depends(require_permission("history.view")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.delete_history_entry(db, current_user.id, entry_id)
    return {"ok": True}


@router.get("/ai/config")
def load_ai_config(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any] | None:
    return runtime_state_service.load_ai_config(db, current_user.id)


@router.post("/ai/config")
def save_ai_config(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.save_ai_config(db, current_user.id, dict(payload.get("config") or {}))
    return {"ok": True}


@router.get("/ai/conversations")
def load_ai_conversations(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return runtime_state_service.load_ai_conversations(db, current_user.id)


@router.post("/ai/conversation")
def save_ai_conversation(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.save_ai_conversation(db, current_user.id, dict(payload.get("conversation") or {}))
    return {"ok": True}


@router.delete("/ai/conversation/{conversation_id}")
def delete_ai_conversation(
    conversation_id: str,
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.delete_ai_conversation(db, current_user.id, conversation_id)
    return {"ok": True}


@router.get("/desktop-settings")
def load_desktop_settings(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return runtime_state_service.load_desktop_settings(db, current_user.id)


@router.post("/desktop-settings")
def save_desktop_settings(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.save_desktop_settings(db, current_user.id, dict(payload.get("settings") or {}))
    return {"ok": True}


@router.get("/editor-settings")
def load_editor_settings(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any] | None:
    return runtime_state_service.load_editor_settings(db, current_user.id)


@router.post("/editor-settings")
def save_editor_settings(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    runtime_state_service.save_editor_settings(db, current_user.id, dict(payload.get("settings") or {}))
    return {"ok": True}


@router.get("/schema/databases")
def list_databases(
    connection_id: str = Query(alias="connection_id"),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(db, authorization_service, current_user, "datasource.browse", config)
    try:
        databases = query_runtime_service.list_databases(config)
        return _filter_authorized_databases(db, authorization_service, current_user, config, databases)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/schemas")
def list_schemas(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[str]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
    )
    try:
        schemas = query_runtime_service.list_schemas(config, database)
        return _filter_authorized_schemas(
            db,
            authorization_service,
            current_user,
            config,
            database=database,
            schemas=schemas,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/tables")
def list_tables(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    filter_text: str | None = Query(default=None, alias="filter"),
    limit: int | None = Query(default=None),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
    )
    try:
        tables = query_runtime_service.list_tables(
            config,
            database=database,
            schema=schema,
            filter_text=filter_text,
            limit=limit,
        )
        return _filter_authorized_tables(
            db,
            authorization_service,
            current_user,
            config,
            database=database,
            schema=schema,
            tables=tables,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/objects")
def list_objects(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
    )
    try:
        objects = query_runtime_service.list_objects(config, database=database, schema=schema)
        return _filter_authorized_tables(
            db,
            authorization_service,
            current_user,
            config,
            database=database,
            schema=schema,
            tables=objects,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/object-source")
def get_object_source(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    table: str = Query(default=""),
    object_type: str = Query(default="VIEW"),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
        table=table,
    )
    try:
        return query_runtime_service.get_object_source(
            config,
            database=database,
            schema=schema,
            name=table,
            object_type=object_type,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/columns")
def get_columns(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    table: str = Query(default=""),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
        table=table,
    )
    try:
        return query_runtime_service.get_columns(config, database=database, schema=schema, table=table)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/indexes")
def list_indexes(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    table: str = Query(default=""),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
        table=table,
    )
    try:
        return query_runtime_service.list_indexes(config, database=database, schema=schema, table=table)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/foreign-keys")
def list_foreign_keys(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    table: str = Query(default=""),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
        table=table,
    )
    try:
        return query_runtime_service.list_foreign_keys(config, database=database, schema=schema, table=table)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/triggers")
def list_triggers(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    table: str = Query(default=""),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
        table=table,
    )
    try:
        return query_runtime_service.list_triggers(config, database=database, schema=schema, table=table)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/ddl")
def get_ddl(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    schema: str | None = Query(default=None),
    table: str = Query(default=""),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> str:
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_configured_connection(
        db,
        authorization_service,
        current_user,
        "datasource.browse",
        config,
        database=database,
        schema=schema,
        table=table,
    )
    try:
        return query_runtime_service.get_table_ddl(config, database=database, schema=schema, table=table)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute")
def execute_query(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    connection_id = str(payload.get("connectionId") or "")
    database = str(payload.get("database") or "")
    schema = payload.get("schema")
    sql = str(payload.get("sql") or "")
    _ensure_direct_query_allowed(connection_id=connection_id, database=database, schema=schema, sql=sql)
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_query_scope(db, authorization_service, current_user, payload, sql=sql)
    try:
        return query_runtime_service.execute_query(
            config,
            database=database,
            schema=schema,
            sql=sql,
            max_rows=payload.get("maxRows"),
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute-multi")
def execute_multi(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[dict[str, Any]]:
    connection_id = str(payload.get("connectionId") or "")
    database = str(payload.get("database") or "")
    schema = payload.get("schema")
    sql = str(payload.get("sql") or "")
    _ensure_direct_query_allowed(connection_id=connection_id, database=database, schema=schema, sql=sql)
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_query_scope(db, authorization_service, current_user, payload, sql=sql)
    try:
        return query_runtime_service.execute_multi(
            config,
            database=database,
            schema=schema,
            sql=sql,
            max_rows=payload.get("maxRows"),
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute-batch")
def execute_batch(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    connection_id = str(payload.get("connectionId") or "")
    database = str(payload.get("database") or "")
    schema = payload.get("schema")
    statements = list(payload.get("statements") or [])
    _ensure_direct_query_allowed(
        connection_id=connection_id,
        database=database,
        schema=schema,
        sql="",
        statements=statements,
    )
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_query_scope(
        db,
        authorization_service,
        current_user,
        payload,
        sql=";\n".join(str(statement) for statement in statements),
    )
    try:
        return query_runtime_service.execute_batch(
            config,
            database=database,
            schema=schema,
            statements=statements,
            transactional=False,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute-script")
def execute_script(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    connection_id = str(payload.get("connectionId") or "")
    database = str(payload.get("database") or "")
    schema = payload.get("schema")
    sql = str(payload.get("sql") or "")
    _ensure_direct_query_allowed(connection_id=connection_id, database=database, schema=schema, sql=sql)
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_query_scope(db, authorization_service, current_user, payload, sql=sql)
    try:
        statements = query_runtime_service.split_sql(sql)
        return query_runtime_service.execute_batch(
            config,
            database=database,
            schema=schema,
            statements=statements,
            transactional=False,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute-in-transaction")
def execute_in_transaction(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    connection_id = str(payload.get("connectionId") or "")
    database = str(payload.get("database") or "")
    schema = payload.get("schema")
    statements = list(payload.get("statements") or [])
    _ensure_direct_query_allowed(
        connection_id=connection_id,
        database=database,
        schema=schema,
        sql="",
        statements=statements,
    )
    config = _connection_or_400(db, connection_id, current_user.id)
    _authorize_query_scope(
        db,
        authorization_service,
        current_user,
        payload,
        sql=";\n".join(str(statement) for statement in statements),
    )
    try:
        return query_runtime_service.execute_batch(
            config,
            database=database,
            schema=schema,
            statements=statements,
            transactional=True,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/classify")
def classify_query(
    payload: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> dict[str, Any]:
    try:
        statements = classify_sql_statements(str(payload.get("sql") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SQL payload is empty") from exc
    change_statements = [statement for statement in statements if statement.requires_approval]
    return {
        "requires_approval": bool(change_statements),
        "ticket_type": resolve_ticket_type(change_statements) if change_statements else None,
        "sql_summary": summarize_sql(change_statements) if change_statements else summarize_sql(statements),
        "statements": [_statement_payload(statement) for statement in statements],
    }


@router.post("/query/classify-execution")
def classify_execution_query(
    payload: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> dict[str, Any]:
    connection_id, database, schema = _payload_context(payload)
    sql, statements = _payload_sql(payload)
    try:
        _ensure_direct_query_allowed(
            connection_id=connection_id,
            database=database,
            schema=schema,
            sql=sql,
            statements=statements,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT and isinstance(exc.detail, dict):
            return {"allowed": False, **exc.detail}
        raise
    return {"allowed": True}


@router.post("/internal/query/execute-in-transaction", dependencies=[Depends(_internal_token_guard)])
def execute_in_transaction_internal(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = _connection_or_400(db, str(payload.get("connectionId") or ""))
    try:
        return query_runtime_service.execute_batch(
            config,
            database=str(payload.get("database") or ""),
            schema=payload.get("schema"),
            statements=list(payload.get("statements") or []),
            transactional=True,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/internal/query/execute-script", dependencies=[Depends(_internal_token_guard)])
def execute_script_internal(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = _connection_or_400(db, str(payload.get("connectionId") or ""))
    try:
        statements = query_runtime_service.split_sql(str(payload.get("sql") or ""))
        return query_runtime_service.execute_batch(
            config,
            database=str(payload.get("database") or ""),
            schema=payload.get("schema"),
            statements=statements,
            transactional=False,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/close-session")
def close_session(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> bool:
    _authorize_query_scope(db, authorization_service, current_user, payload)
    return True


@router.post("/query/cancel")
def cancel_query(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> bool:
    if payload.get("connectionId"):
        _authorize_query_scope(db, authorization_service, current_user, payload)
    return False


@router.post("/query/analyze-sql-references")
def analyze_sql_references(
    _: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> dict[str, list[dict[str, Any]]]:
    return {"tables": [], "columns": []}


@router.post("/query/find-statement-at-cursor")
def find_statement_at_cursor(
    payload: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> str:
    return query_runtime_service.find_statement_at_cursor(
        str(payload.get("sql") or ""),
        int(payload.get("cursorPos") or 0),
    )


@router.post("/query/prepare-pagination-plan")
def prepare_query_pagination_execution_plan(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    options = dict(payload.get("options") or {})
    _authorize_query_builder_options(db, authorization_service, current_user, options)
    if options.get("connectionId"):
        _authorize_query_scope(
            db,
            authorization_service,
            current_user,
            {
                "connectionId": options.get("connectionId"),
                "database": options.get("database"),
                "schema": options.get("schema"),
            },
            sql=str(options.get("queryBaseSql") or options.get("sql") or ""),
        )
    return query_runtime_service.prepare_pagination_plan(options)


@router.post("/query/build-sorted-sql")
def build_sorted_query_sql(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    options = dict(payload.get("options") or {})
    _authorize_query_builder_options(db, authorization_service, current_user, options)
    if options.get("connectionId"):
        _authorize_query_scope(
            db,
            authorization_service,
            current_user,
            {
                "connectionId": options.get("connectionId"),
                "database": options.get("database"),
                "schema": options.get("schema"),
            },
            sql=str(options.get("originalSql") or ""),
        )
    return query_runtime_service.build_sorted_query_sql(options)


@router.post("/query/build-explain-sql")
def build_explain_sql(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    options = dict(payload.get("options") or {})
    _authorize_query_builder_options(db, authorization_service, current_user, options)
    if options.get("connectionId"):
        _authorize_query_scope(
            db,
            authorization_service,
            current_user,
            {
                "connectionId": options.get("connectionId"),
                "database": options.get("database"),
                "schema": options.get("schema"),
            },
            sql=str(options.get("sql") or ""),
        )
    return query_runtime_service.build_explain_sql(options)


@router.post("/query/build-dropped-file-preview-sql")
def build_dropped_file_preview_sql(
    _: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> None:
    return None


@router.post("/query/build-table-select-sql")
def build_table_select_sql(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> str:
    options = dict(payload.get("options") or {})
    _authorize_query_builder_options(db, authorization_service, current_user, options)
    return query_runtime_service.build_table_select_sql(options)


@router.post("/query/analyze-editability")
def analyze_editability(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> dict[str, Any]:
    if payload.get("connectionId"):
        _authorize_query_scope(db, authorization_service, current_user, payload, sql=str(payload.get("sql") or ""))
    return {"editable": False, "reason": "metadata-unavailable"}


@router.post("/schema-diff/prepare")
def schema_diff_prepare(_: dict[str, Any]) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Schema diff is not available in web-only mode")


@router.post("/schema-diff/generate-sync-sql")
def schema_diff_generate(_: dict[str, Any]) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Schema diff is not available in web-only mode")


@router.post("/data-compare/prepare")
def data_compare_prepare(_: dict[str, Any]) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Data compare is not available in web-only mode")


@router.post("/data-compare/prepare-from-tables")
def data_compare_prepare_from_tables(_: dict[str, Any]) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Data compare is not available in web-only mode")
