from __future__ import annotations

import math
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import sqlparse
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql import quoted_name
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.services.runtime_state import RuntimeStateService


POSTGRES_FAMILY = {"postgres", "redshift", "gaussdb", "kingbase", "highgo", "vastbase", "opengauss"}
MYSQL_FAMILY = {"mysql", "doris", "starrocks", "goldendb"}
SQLITE_FAMILY = {"sqlite"}
SUPPORTED_TYPES = POSTGRES_FAMILY | MYSQL_FAMILY | SQLITE_FAMILY


class QueryRuntimeService:
    _ephemeral_connections: dict[str, dict[str, Any]] = {}

    def __init__(self) -> None:
        self.runtime_state = RuntimeStateService()

    def register_connection(self, config: dict[str, Any]) -> None:
        connection_id = str(config.get("id") or "")
        if connection_id:
            self._ephemeral_connections[connection_id] = dict(config)

    def unregister_connection(self, connection_id: str) -> None:
        self._ephemeral_connections.pop(connection_id, None)

    def resolve_connection_config(
        self,
        db: Session,
        connection_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        if owner_user_id:
            for profile in self.runtime_state.load_connections(db, owner_user_id):
                if str(profile.get("id")) == connection_id:
                    return profile
        profile = self.runtime_state.load_connection_profile(db, connection_id)
        if profile is not None:
            return profile
        cached = self._ephemeral_connections.get(connection_id)
        if cached is not None:
            return dict(cached)
        raise RuntimeError(f"Connection '{connection_id}' was not found")

    def test_connection(self, config: dict[str, Any]) -> str:
        engine = self._create_engine(config)
        try:
            with engine.connect() as conn:
                conn.execute(text(self._healthcheck_sql(config)))
            return "Connection successful"
        finally:
            engine.dispose()

    def list_databases(self, config: dict[str, Any]) -> list[dict[str, str]]:
        database_type = self._database_type(config)
        if database_type in SQLITE_FAMILY:
            database_name = str(config.get("database") or Path(str(config.get("host") or "")).stem or "main")
            return [{"name": database_name}]

        engine = self._create_engine(config)
        try:
            with engine.connect() as conn:
                if database_type in MYSQL_FAMILY:
                    rows = conn.execute(text("SHOW DATABASES")).all()
                    return [{"name": str(row[0])} for row in rows]
                rows = conn.execute(
                    text("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
                ).all()
                return [{"name": str(row[0])} for row in rows]
        finally:
            engine.dispose()

    def list_schemas(self, config: dict[str, Any], database: str) -> list[str]:
        database_type = self._database_type(config)
        if database_type in MYSQL_FAMILY:
            return [database] if database else []
        if database_type in SQLITE_FAMILY:
            return []
        engine = self._create_engine(config, database_override=database or None)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name NOT IN ('information_schema', 'pg_catalog') "
                        "ORDER BY schema_name"
                    )
                ).all()
                return [str(row[0]) for row in rows]
        finally:
            engine.dispose()

    def list_tables(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        filter_text: str | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        database_type = self._database_type(config)
        engine = self._create_engine(config, database_override=database or None)
        try:
            if database_type in SQLITE_FAMILY:
                query = (
                    "SELECT name, type FROM sqlite_master "
                    "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
                with engine.connect() as conn:
                    rows = conn.execute(text(query)).all()
                items = [{"name": str(name), "table_type": str(kind).upper(), "comment": None} for name, kind in rows]
                return self._filter_named_rows(items, filter_text, limit)

            inspector = inspect(engine)
            effective_schema = self._effective_schema(database_type, schema)
            items = [
                {"name": name, "table_type": "TABLE", "comment": None}
                for name in inspector.get_table_names(schema=effective_schema)
            ]
            items.extend(
                {"name": name, "table_type": "VIEW", "comment": None}
                for name in inspector.get_view_names(schema=effective_schema)
            )
            items.sort(key=lambda item: (item["name"].lower(), item["table_type"]))
            return self._filter_named_rows(items, filter_text, limit)
        finally:
            engine.dispose()

    def list_objects(self, config: dict[str, Any], *, database: str, schema: str | None) -> list[dict[str, Any]]:
        return [
            {
                "name": item["name"],
                "object_type": item["table_type"],
                "schema": schema,
                "comment": item.get("comment"),
                "created_at": None,
                "updated_at": None,
            }
            for item in self.list_tables(
                config,
                database=database,
                schema=schema,
                filter_text=None,
                limit=None,
            )
        ]

    def get_object_source(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        name: str,
        object_type: str,
    ) -> dict[str, Any]:
        database_type = self._database_type(config)
        engine = self._create_engine(config, database_override=database or None)
        try:
            with engine.connect() as conn:
                if object_type.upper() == "VIEW":
                    if database_type in SQLITE_FAMILY:
                        row = conn.execute(
                            text("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = :name"),
                            {"name": name},
                        ).first()
                        source = "" if row is None else str(row[0] or "")
                    elif database_type in MYSQL_FAMILY:
                        row = conn.execute(text(f"SHOW CREATE VIEW {self._qualified_table_name(database_type, schema, name)}")).first()
                        source = "" if row is None else str(row[1] or "")
                    else:
                        row = conn.execute(
                            text("SELECT pg_get_viewdef(:qualified::regclass, true)"),
                            {"qualified": self._qualified_regclass(schema, name)},
                        ).first()
                        source = "" if row is None else f"CREATE VIEW {self._qualified_regclass(schema, name)} AS\n{row[0]}"
                    return {
                        "name": name,
                        "object_type": "VIEW",
                        "schema": schema,
                        "source": source,
                    }
        finally:
            engine.dispose()
        raise RuntimeError(f"{object_type} source is not supported for {database_type}")

    def get_columns(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        table: str,
    ) -> list[dict[str, Any]]:
        database_type = self._database_type(config)
        engine = self._create_engine(config, database_override=database or None)
        try:
            inspector = inspect(engine)
            effective_schema = self._effective_schema(database_type, schema)
            primary_key = set(inspector.get_pk_constraint(table, schema=effective_schema).get("constrained_columns") or [])
            columns = inspector.get_columns(table, schema=effective_schema)
            result: list[dict[str, Any]] = []
            for column in columns:
                result.append(
                    {
                        "name": column["name"],
                        "data_type": str(column.get("type") or ""),
                        "is_nullable": bool(column.get("nullable", True)),
                        "column_default": None if column.get("default") is None else str(column.get("default")),
                        "is_primary_key": column["name"] in primary_key,
                        "extra": None,
                        "comment": column.get("comment"),
                        "numeric_precision": self._int_or_none(column.get("precision")),
                        "numeric_scale": self._int_or_none(column.get("scale")),
                        "character_maximum_length": self._int_or_none(column.get("length")),
                    }
                )
            return result
        finally:
            engine.dispose()

    def list_indexes(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        table: str,
    ) -> list[dict[str, Any]]:
        database_type = self._database_type(config)
        engine = self._create_engine(config, database_override=database or None)
        try:
            inspector = inspect(engine)
            effective_schema = self._effective_schema(database_type, schema)
            indexes = [
                {
                    "name": item.get("name") or "",
                    "columns": list(item.get("column_names") or []),
                    "is_unique": bool(item.get("unique")),
                    "is_primary": False,
                    "filter": None,
                    "index_type": None,
                    "included_columns": None,
                    "comment": None,
                }
                for item in inspector.get_indexes(table, schema=effective_schema)
            ]
            pk = inspector.get_pk_constraint(table, schema=effective_schema)
            pk_columns = list(pk.get("constrained_columns") or [])
            if pk_columns:
                indexes.insert(
                    0,
                    {
                        "name": pk.get("name") or f"{table}_pkey",
                        "columns": pk_columns,
                        "is_unique": True,
                        "is_primary": True,
                        "filter": None,
                        "index_type": None,
                        "included_columns": None,
                        "comment": None,
                    },
                )
            return indexes
        finally:
            engine.dispose()

    def list_foreign_keys(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        table: str,
    ) -> list[dict[str, Any]]:
        database_type = self._database_type(config)
        engine = self._create_engine(config, database_override=database or None)
        try:
            inspector = inspect(engine)
            effective_schema = self._effective_schema(database_type, schema)
            result: list[dict[str, Any]] = []
            for item in inspector.get_foreign_keys(table, schema=effective_schema):
                columns = list(item.get("constrained_columns") or [])
                ref_columns = list(item.get("referred_columns") or [])
                ref_table = str(item.get("referred_table") or "")
                for index, column in enumerate(columns):
                    result.append(
                        {
                            "name": item.get("name") or f"{table}_{column}_fkey",
                            "column": column,
                            "ref_table": ref_table,
                            "ref_column": ref_columns[index] if index < len(ref_columns) else "",
                        }
                    )
            return result
        finally:
            engine.dispose()

    def list_triggers(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        table: str,
    ) -> list[dict[str, Any]]:
        database_type = self._database_type(config)
        engine = self._create_engine(config, database_override=database or None)
        try:
            with engine.connect() as conn:
                if database_type in SQLITE_FAMILY:
                    rows = conn.execute(
                        text(
                            "SELECT name, sql FROM sqlite_master "
                            "WHERE type = 'trigger' AND tbl_name = :table ORDER BY name"
                        ),
                        {"table": table},
                    ).all()
                    return [{"name": str(row[0]), "event": "", "timing": ""} for row in rows]
                if database_type in MYSQL_FAMILY:
                    rows = conn.execute(
                        text(
                            "SELECT TRIGGER_NAME, EVENT_MANIPULATION, ACTION_TIMING "
                            "FROM information_schema.triggers "
                            "WHERE EVENT_OBJECT_SCHEMA = :schema_name AND EVENT_OBJECT_TABLE = :table "
                            "ORDER BY TRIGGER_NAME"
                        ),
                        {"schema_name": schema or database, "table": table},
                    ).all()
                    return [{"name": str(row[0]), "event": str(row[1]), "timing": str(row[2])} for row in rows]
                rows = conn.execute(
                    text(
                        "SELECT trigger_name, event_manipulation, action_timing "
                        "FROM information_schema.triggers "
                        "WHERE event_object_schema = :schema_name AND event_object_table = :table "
                        "ORDER BY trigger_name"
                    ),
                    {"schema_name": schema or "public", "table": table},
                ).all()
                return [{"name": str(row[0]), "event": str(row[1]), "timing": str(row[2])} for row in rows]
        finally:
            engine.dispose()

    def get_table_ddl(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        table: str,
    ) -> str:
        database_type = self._database_type(config)
        engine = self._create_engine(config, database_override=database or None)
        try:
            if database_type in SQLITE_FAMILY:
                with engine.connect() as conn:
                    row = conn.execute(
                        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table"),
                        {"table": table},
                    ).first()
                    if row and row[0]:
                        return str(row[0])
            metadata = MetaData()
            reflected = Table(
                quoted_name(table, quote=True),
                metadata,
                schema=self._effective_schema(database_type, schema),
                autoload_with=engine,
            )
            return f"{CreateTable(reflected).compile(engine)};"
        finally:
            engine.dispose()

    def execute_query(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        sql: str,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        return self._execute_single(config, database=database, schema=schema, sql=sql, max_rows=max_rows)

    def execute_multi(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        sql: str,
        max_rows: int | None = None,
    ) -> list[dict[str, Any]]:
        statements = self.split_sql(sql)
        return [
            self._execute_single(config, database=database, schema=schema, sql=statement, max_rows=max_rows)
            for statement in statements
        ]

    def execute_batch(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        statements: list[str],
        transactional: bool,
    ) -> dict[str, Any]:
        engine = self._create_engine(config, database_override=database or None)
        start = time.perf_counter()
        affected_rows = 0
        try:
            context_manager = engine.begin() if transactional else engine.connect()
            with context_manager as conn:
                self._apply_schema(conn, config, schema)
                for statement in statements:
                    result = conn.execute(text(statement))
                    if result.returns_rows:
                        result.all()
                    affected_rows += max(result.rowcount or 0, 0)
            duration_ms = int((time.perf_counter() - start) * 1000)
            return {
                "columns": [],
                "rows": [],
                "affected_rows": affected_rows,
                "execution_time_ms": duration_ms,
                "truncated": False,
                "session_id": None,
                "has_more": False,
            }
        finally:
            engine.dispose()

    def build_table_select_sql(self, options: dict[str, Any]) -> str:
        database_type = options.get("databaseType")
        schema = options.get("schema")
        table_name = str(options["tableName"])
        limit = int(options.get("limit") or 100)
        offset = int(options.get("offset") or 0)
        where_input = str(options.get("whereInput") or "").strip()
        qualified = self._qualified_table_name(database_type, schema, table_name)
        sql = f"SELECT * FROM {qualified}"
        if where_input:
            if where_input.lower().startswith("where "):
                sql += f" {where_input}"
            else:
                sql += f" WHERE {where_input}"
        sql += f" LIMIT {max(limit, 1)}"
        if offset > 0:
            sql += f" OFFSET {offset}"
        return sql

    def build_sorted_query_sql(self, options: dict[str, Any]) -> dict[str, Any]:
        original_sql = str(options.get("originalSql") or "").strip().rstrip(";")
        if not original_sql:
            return {"ok": False, "reason": "empty"}
        if len(self.split_sql(original_sql)) != 1:
            return {"ok": False, "reason": "multi"}
        if not original_sql.lower().startswith("select"):
            return {"ok": False, "reason": "not_select"}
        column = str(options.get("column") or "")
        direction = "DESC" if str(options.get("direction") or "").lower() == "desc" else "ASC"
        quoted = self._quote_identifier(options.get("databaseType"), column)
        return {"ok": True, "sql": f"SELECT * FROM ({original_sql}) AS dbx_sorted ORDER BY {quoted} {direction}"}

    def build_explain_sql(self, options: dict[str, Any]) -> dict[str, Any]:
        sql = str(options.get("sql") or "").strip().rstrip(";")
        if not sql:
            return {"ok": False, "reason": "empty"}
        database_type = options.get("databaseType")
        if database_type in SQLITE_FAMILY:
            return {"ok": True, "sql": f"EXPLAIN QUERY PLAN {sql}"}
        if database_type in SUPPORTED_TYPES or database_type is None:
            return {"ok": True, "sql": f"EXPLAIN {sql}"}
        return {"ok": False, "reason": "unsupported"}

    def prepare_pagination_plan(self, options: dict[str, Any]) -> dict[str, Any]:
        query_base_sql = str(options.get("queryBaseSql") or options.get("sql") or "").strip().rstrip(";")
        pagination = options.get("pagination") or {}
        limit = max(int(pagination.get("limit") or 100), 1)
        offset = max(int(pagination.get("offset") or 0), 0)
        paged_sql = f"SELECT * FROM ({query_base_sql}) AS dbx_page LIMIT {limit} OFFSET {offset}"
        return {
            "sqlToExecute": paged_sql,
            "pageSql": paged_sql,
            "pageLimit": limit,
            "pageOffset": offset,
            "countSql": f"SELECT COUNT(*) AS total_rows FROM ({query_base_sql}) AS dbx_count",
            "useAgentResultSession": False,
        }

    def find_statement_at_cursor(self, sql: str, cursor_pos: int) -> str:
        statements = self._statements_with_offsets(sql)
        for statement, start, end in statements:
            if start <= cursor_pos <= end:
                return statement
        return sql

    def split_sql(self, sql: str) -> list[str]:
        return [statement.strip() for statement in sqlparse.split(sql) if statement.strip()]

    def _execute_single(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        sql: str,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        engine = self._create_engine(config, database_override=database or None)
        start = time.perf_counter()
        try:
            with engine.begin() as conn:
                self._apply_schema(conn, config, schema)
                result = conn.execute(text(sql))
                duration_ms = int((time.perf_counter() - start) * 1000)
                if result.returns_rows:
                    rows = result.fetchmany(max_rows or 500)
                    columns = list(result.keys())
                    return {
                        "columns": columns,
                        "rows": [[self._to_jsonable(value) for value in row] for row in rows],
                        "affected_rows": len(rows),
                        "execution_time_ms": duration_ms,
                        "truncated": False,
                        "session_id": None,
                        "has_more": False,
                    }
                return {
                    "columns": [],
                    "rows": [],
                    "affected_rows": max(result.rowcount or 0, 0),
                    "execution_time_ms": duration_ms,
                    "truncated": False,
                    "session_id": None,
                    "has_more": False,
                }
        finally:
            engine.dispose()

    def _create_engine(self, config: dict[str, Any], *, database_override: str | None = None) -> Engine:
        database_type = self._database_type(config)
        if database_type not in SUPPORTED_TYPES:
            raise RuntimeError(f"Database type '{database_type}' is not supported in the pure web runtime yet")

        query = dict(parse_qsl(str(config.get("url_params") or ""), keep_blank_values=True))
        if database_type in POSTGRES_FAMILY:
            database_name = database_override or str(config.get("database") or "")
            url = (
                f"postgresql+psycopg://{config.get('username') or ''}:{config.get('password') or ''}"
                f"@{config.get('host') or '127.0.0.1'}:{int(config.get('port') or 5432)}/{database_name}"
            )
            if query:
                url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
            return create_engine(url, poolclass=NullPool)
        if database_type in MYSQL_FAMILY:
            database_name = database_override or str(config.get("database") or "")
            url = (
                f"mysql+pymysql://{config.get('username') or ''}:{config.get('password') or ''}"
                f"@{config.get('host') or '127.0.0.1'}:{int(config.get('port') or 3306)}/{database_name}"
            )
            if query:
                url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
            return create_engine(url, poolclass=NullPool)
        path = str(config.get("host") or config.get("database") or "")
        if path == ":memory:":
            sqlite_url = "sqlite+pysqlite:///:memory:"
        else:
            sqlite_url = f"sqlite+pysqlite:///{Path(path).expanduser()}"
        return create_engine(sqlite_url, poolclass=NullPool)

    def _apply_schema(self, conn: Any, config: dict[str, Any], schema: str | None) -> None:
        database_type = self._database_type(config)
        if not schema:
            return
        if database_type in POSTGRES_FAMILY:
            conn.execute(text(f"SET search_path TO {self._quote_identifier(database_type, schema)}"))

    def _database_type(self, config: dict[str, Any]) -> str:
        profile = str(config.get("driver_profile") or config.get("db_type") or "").lower()
        db_type = str(config.get("db_type") or "").lower()
        return profile if profile in SUPPORTED_TYPES else db_type

    def _effective_schema(self, database_type: str | None, schema: str | None) -> str | None:
        if database_type in MYSQL_FAMILY:
            return None
        return schema or ("public" if database_type in POSTGRES_FAMILY else None)

    def _qualified_table_name(self, database_type: str | None, schema: str | None, table: str) -> str:
        if schema and database_type not in MYSQL_FAMILY and database_type not in SQLITE_FAMILY:
            return f"{self._quote_identifier(database_type, schema)}.{self._quote_identifier(database_type, table)}"
        return self._quote_identifier(database_type, table)

    def _qualified_regclass(self, schema: str | None, table: str) -> str:
        return f"{schema}.{table}" if schema else table

    def _quote_identifier(self, database_type: str | None, value: str) -> str:
        if database_type in MYSQL_FAMILY:
            return f"`{value.replace('`', '``')}`"
        escaped = value.replace('"', '""')
        return f'"{escaped}"'

    def _healthcheck_sql(self, config: dict[str, Any]) -> str:
        if self._database_type(config) in SQLITE_FAMILY:
            return "SELECT 1"
        return "SELECT 1"

    def _filter_named_rows(
        self,
        rows: list[dict[str, Any]],
        filter_text: str | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        filtered = rows
        if filter_text:
            needle = filter_text.lower()
            filtered = [row for row in rows if needle in str(row.get("name") or "").lower()]
        if limit is not None and limit > 0:
            filtered = filtered[:limit]
        return filtered

    def _to_jsonable(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            if value.is_finite() and value == value.to_integral():
                return int(value)
            return float(value)
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return str(value)
        return value

    def _statements_with_offsets(self, sql: str) -> list[tuple[str, int, int]]:
        statements = self.split_sql(sql)
        result: list[tuple[str, int, int]] = []
        cursor = 0
        for statement in statements:
            start = sql.find(statement, cursor)
            if start < 0:
                continue
            end = start + len(statement)
            result.append((statement, start, end))
            cursor = end
        return result

    def _int_or_none(self, value: Any) -> int | None:
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None
