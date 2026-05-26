from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permission
from app.config import get_settings
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.services.query_runtime import QueryRuntimeService
from app.services.runtime_state import RuntimeStateService

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


def _runtime_error_to_http(exc: Exception) -> HTTPException:
    message = str(exc) or exc.__class__.__name__
    if "not supported" in message.lower():
        return HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


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
    _: UserIdentity = Depends(require_permission("datasource.manage")),
) -> str:
    config = dict(payload.get("config") or {})
    try:
        query_runtime_service.register_connection(config)
        return query_runtime_service.test_connection(config)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/connection/connect")
def connect_db(
    payload: dict[str, Any],
    _: UserIdentity = Depends(require_permission("datasource.connect")),
) -> str:
    config = dict(payload.get("config") or {})
    if not config.get("id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connection id is required")
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
) -> dict[str, bool]:
    configs = list(payload.get("configs") or [])
    runtime_state_service.save_connections(db, current_user.id, configs)
    for config in configs:
        query_runtime_service.register_connection(dict(config))
    return {"ok": True}


@router.get("/connection/list")
def load_connections(
    current_user: UserIdentity = Depends(require_permission("menu.connections.view")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return runtime_state_service.load_connections(db, current_user.id)


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
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    try:
        return query_runtime_service.list_databases(config)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.get("/schema/schemas")
def list_schemas(
    connection_id: str = Query(alias="connection_id"),
    database: str = Query(default=""),
    current_user: UserIdentity = Depends(require_permission("datasource.browse")),
    db: Session = Depends(get_db),
) -> list[str]:
    config = _connection_or_400(db, connection_id, current_user.id)
    try:
        return query_runtime_service.list_schemas(config, database)
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
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    try:
        return query_runtime_service.list_tables(
            config,
            database=database,
            schema=schema,
            filter_text=filter_text,
            limit=limit,
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
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
    try:
        return query_runtime_service.list_objects(config, database=database, schema=schema)
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
) -> dict[str, Any]:
    config = _connection_or_400(db, connection_id, current_user.id)
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
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
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
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
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
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
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
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, connection_id, current_user.id)
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
) -> str:
    config = _connection_or_400(db, connection_id, current_user.id)
    try:
        return query_runtime_service.get_table_ddl(config, database=database, schema=schema, table=table)
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute")
def execute_query(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = _connection_or_400(db, str(payload.get("connectionId") or ""), current_user.id)
    try:
        return query_runtime_service.execute_query(
            config,
            database=str(payload.get("database") or ""),
            schema=payload.get("schema"),
            sql=str(payload.get("sql") or ""),
            max_rows=payload.get("maxRows"),
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute-multi")
def execute_multi(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    config = _connection_or_400(db, str(payload.get("connectionId") or ""), current_user.id)
    try:
        return query_runtime_service.execute_multi(
            config,
            database=str(payload.get("database") or ""),
            schema=payload.get("schema"),
            sql=str(payload.get("sql") or ""),
            max_rows=payload.get("maxRows"),
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute-batch")
def execute_batch(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = _connection_or_400(db, str(payload.get("connectionId") or ""), current_user.id)
    try:
        return query_runtime_service.execute_batch(
            config,
            database=str(payload.get("database") or ""),
            schema=payload.get("schema"),
            statements=list(payload.get("statements") or []),
            transactional=False,
        )
    except Exception as exc:
        raise _runtime_error_to_http(exc) from exc


@router.post("/query/execute-script")
def execute_script(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = _connection_or_400(db, str(payload.get("connectionId") or ""), current_user.id)
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


@router.post("/query/execute-in-transaction")
def execute_in_transaction(
    payload: dict[str, Any],
    current_user: UserIdentity = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    config = _connection_or_400(db, str(payload.get("connectionId") or ""), current_user.id)
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
    _: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> bool:
    return True


@router.post("/query/cancel")
def cancel_query(
    _: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> bool:
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
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> dict[str, Any]:
    return query_runtime_service.prepare_pagination_plan(dict(payload.get("options") or {}))


@router.post("/query/build-sorted-sql")
def build_sorted_query_sql(
    payload: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> dict[str, Any]:
    return query_runtime_service.build_sorted_query_sql(dict(payload.get("options") or {}))


@router.post("/query/build-explain-sql")
def build_explain_sql(
    payload: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> dict[str, Any]:
    return query_runtime_service.build_explain_sql(dict(payload.get("options") or {}))


@router.post("/query/build-dropped-file-preview-sql")
def build_dropped_file_preview_sql(
    _: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> None:
    return None


@router.post("/query/build-table-select-sql")
def build_table_select_sql(
    payload: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> str:
    return query_runtime_service.build_table_select_sql(dict(payload.get("options") or {}))


@router.post("/query/analyze-editability")
def analyze_editability(
    _: dict[str, Any],
    __: UserIdentity = Depends(require_permission("query.execute")),
) -> dict[str, Any]:
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
