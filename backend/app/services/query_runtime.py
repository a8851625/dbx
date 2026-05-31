from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import sqlparse
from sqlparse import tokens as sql_tokens
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql import quoted_name
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import Session

from app.services.authorization import AccessContext
from app.services.runtime_state import RuntimeStateService


POSTGRES_FAMILY = {"postgres", "redshift", "gaussdb", "kingbase", "highgo", "vastbase", "opengauss"}
MYSQL_FAMILY = {"mysql", "doris", "starrocks", "goldendb"}
SQLITE_FAMILY = {"sqlite"}
SUPPORTED_TYPES = POSTGRES_FAMILY | MYSQL_FAMILY | SQLITE_FAMILY


@dataclass(frozen=True)
class SqlTableReference:
    database: str | None
    schema: str | None
    table: str


@dataclass(frozen=True)
class ColumnMaskRule:
    prefix: int = 0
    suffix: int = 0
    replacement: str = "***"


@dataclass(frozen=True)
class QueryPolicyTarget:
    schema: str | None
    table: str


@dataclass(frozen=True)
class QueryPolicyPlan:
    original_sql: str
    sql: str
    target: QueryPolicyTarget | None
    policy_ids: tuple[str, ...]
    row_filter: str | None = None
    visible_columns: frozenset[str] | None = None
    masked_columns: dict[str, ColumnMaskRule] | None = None
    result_controls_enabled: bool = True

    @property
    def applied(self) -> bool:
        return bool(self.policy_ids) and (
            self.row_filter is not None
            or self.visible_columns is not None
            or bool(self.masked_columns)
        )

    def summary(
        self,
        *,
        hidden_columns: list[str] | None = None,
        returned_columns: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "policy_ids": list(self.policy_ids),
            "target_schema": self.target.schema if self.target else None,
            "target_table": self.target.table if self.target else None,
            "rewritten": self.sql != self.original_sql,
            "row_filter_applied": self.row_filter is not None,
            "visible_columns": sorted(self.visible_columns) if self.visible_columns is not None else None,
            "hidden_columns": hidden_columns or [],
            "masked_columns": sorted((self.masked_columns or {}).keys()),
            "returned_columns": returned_columns,
            "result_controls_enabled": self.result_controls_enabled,
        }


class QueryPolicyViolation(RuntimeError):
    def __init__(self, message: str, *, summary: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.summary = summary or {"rejected": True, "reason": message, "policy_ids": []}

    @property
    def policy_ids(self) -> list[str]:
        value = self.summary.get("policy_ids")
        if isinstance(value, list):
            return [str(item) for item in value]
        return []


@dataclass(frozen=True)
class _PolicyRewriteResult:
    sql: str
    target: QueryPolicyTarget
    result_controls_enabled: bool = True


@dataclass(frozen=True)
class _KnownWrapper:
    prefix: str
    inner_sql: str
    suffix: str
    result_controls_enabled: bool


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
        policy_plan: QueryPolicyPlan | None = None,
    ) -> dict[str, Any]:
        return self._execute_single(
            config,
            database=database,
            schema=schema,
            sql=policy_plan.sql if policy_plan is not None else sql,
            max_rows=max_rows,
            policy_plan=policy_plan,
        )

    def execute_multi(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        sql: str,
        max_rows: int | None = None,
        policy_plans: list[QueryPolicyPlan] | None = None,
    ) -> list[dict[str, Any]]:
        statements = [plan.sql for plan in policy_plans] if policy_plans is not None else self.split_sql(sql)
        return [
            self._execute_single(
                config,
                database=database,
                schema=schema,
                sql=statement,
                max_rows=max_rows,
                policy_plan=policy_plans[index] if policy_plans is not None else None,
            )
            for index, statement in enumerate(statements)
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

    def build_query_policy_plans(
        self,
        context: AccessContext,
        config: dict[str, Any],
        *,
        datasource_id: str,
        database: str,
        schema: str | None,
        sql: str,
        permission_code: str = "query.execute",
    ) -> list[QueryPolicyPlan]:
        statements = self.split_sql(sql)
        if not statements:
            return []
        database_type = self._database_type(config)
        return [
            self.build_query_policy_plan(
                context,
                config,
                datasource_id=datasource_id,
                database=database,
                schema=schema,
                sql=statement,
                permission_code=permission_code,
                database_type=database_type,
            )
            for statement in statements
        ]

    def build_query_policy_plan(
        self,
        context: AccessContext,
        config: dict[str, Any],
        *,
        datasource_id: str,
        database: str,
        schema: str | None,
        sql: str,
        permission_code: str = "query.execute",
        database_type: str | None = None,
    ) -> QueryPolicyPlan:
        database_type = database_type or self._database_type(config)
        trimmed_sql = sql.strip().rstrip(";")
        if database_type not in POSTGRES_FAMILY:
            return QueryPolicyPlan(original_sql=sql, sql=sql, target=None, policy_ids=())

        matching_policies = self._matching_query_policies(
            context,
            datasource_id=datasource_id,
            permission_code=permission_code,
            database=database,
            schema=schema,
        )
        control_policies = [
            policy
            for policy in matching_policies
            if policy.effect == "allow" and self._has_query_policy_controls(policy.conditions or {})
        ]
        if not self._is_plain_select(trimmed_sql):
            if not control_policies:
                return QueryPolicyPlan(original_sql=sql, sql=sql, target=None, policy_ids=())
            raise QueryPolicyViolation(
                "Row and column policies only support PostgreSQL SELECT statements",
                summary={
                    "rejected": True,
                    "reason": "not_select",
                    "policy_ids": [str(policy.id) for policy in control_policies],
                },
            )

        try:
            base_rewrite = self._rewrite_postgres_select(
                trimmed_sql,
                database_type=database_type,
                default_schema=schema,
                row_filter=None,
            )
        except QueryPolicyViolation as exc:
            raise QueryPolicyViolation(
                str(exc),
                summary={
                    "rejected": True,
                    "reason": str(exc),
                    "policy_ids": [str(policy.id) for policy in control_policies],
                },
            ) from exc
        table_control_policies = [
            policy
            for policy in control_policies
            if self._conditions_table_match(
                policy.conditions or {},
                database=database,
                schema=base_rewrite.target.schema,
                table=base_rewrite.target.table,
            )
        ]
        if not table_control_policies:
            return QueryPolicyPlan(
                original_sql=sql,
                sql=sql,
                target=base_rewrite.target,
                policy_ids=(),
                result_controls_enabled=base_rewrite.result_controls_enabled,
            )

        row_filter = self._merge_row_filters([policy.conditions or {} for policy in table_control_policies])
        visible_columns = self._merge_visible_columns([policy.conditions or {} for policy in table_control_policies])
        masked_columns = self._merge_masked_columns([policy.conditions or {} for policy in table_control_policies])
        policy_ids = tuple(str(policy.id) for policy in table_control_policies)

        try:
            rewrite = self._rewrite_postgres_select(
                trimmed_sql,
                database_type=database_type,
                default_schema=schema,
                row_filter=row_filter,
                require_safe_projection=visible_columns is not None or bool(masked_columns),
            )
        except QueryPolicyViolation as exc:
            raise QueryPolicyViolation(
                str(exc),
                summary={
                    "rejected": True,
                    "reason": str(exc),
                    "policy_ids": list(policy_ids),
                    "row_filter_applied": row_filter is not None,
                    "visible_columns": sorted(visible_columns) if visible_columns is not None else None,
                    "masked_columns": sorted(masked_columns.keys()),
                },
            ) from exc
        return QueryPolicyPlan(
            original_sql=trimmed_sql,
            sql=rewrite.sql,
            target=rewrite.target,
            policy_ids=policy_ids,
            row_filter=row_filter,
            visible_columns=visible_columns,
            masked_columns=masked_columns,
            result_controls_enabled=rewrite.result_controls_enabled,
        )

    def query_policy_control_ids(
        self,
        context: AccessContext,
        config: dict[str, Any],
        *,
        datasource_id: str,
        database: str,
        schema: str | None,
        permission_code: str = "query.execute",
    ) -> list[str]:
        if self._database_type(config) not in POSTGRES_FAMILY:
            return []
        return [
            str(policy.id)
            for policy in self._matching_query_policies(
                context,
                datasource_id=datasource_id,
                permission_code=permission_code,
                database=database,
                schema=schema,
            )
            if policy.effect == "allow" and self._has_query_policy_controls(policy.conditions or {})
        ]

    def find_statement_at_cursor(self, sql: str, cursor_pos: int) -> str:
        statements = self._statements_with_offsets(sql)
        for statement, start, end in statements:
            if start <= cursor_pos <= end:
                return statement
        return sql

    def split_sql(self, sql: str) -> list[str]:
        try:
            import sqlparse

            return [statement.strip() for statement in sqlparse.split(sql) if statement.strip()]
        except ModuleNotFoundError:
            return [statement.strip() for statement in sql.split(";") if statement.strip()]

    def extract_table_references(
        self,
        sql: str,
        *,
        default_schema: str | None = None,
    ) -> list[SqlTableReference]:
        references: list[SqlTableReference] = []
        seen: set[tuple[str | None, str | None, str]] = set()
        for statement in self.split_sql(sql):
            for raw_identifier in self._table_identifier_candidates(statement):
                reference = self._parse_table_identifier(raw_identifier, default_schema=default_schema)
                if reference is None:
                    continue
                key = (reference.database, reference.schema, reference.table)
                if key in seen:
                    continue
                seen.add(key)
                references.append(reference)
        return references

    def _execute_single(
        self,
        config: dict[str, Any],
        *,
        database: str,
        schema: str | None,
        sql: str,
        max_rows: int | None = None,
        policy_plan: QueryPolicyPlan | None = None,
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
                    rows, columns, policy_summary = self._apply_result_policy(rows, columns, policy_plan)
                    return {
                        "columns": columns,
                        "rows": [[self._to_jsonable(value) for value in row] for row in rows],
                        "affected_rows": len(rows),
                        "execution_time_ms": duration_ms,
                        "truncated": False,
                        "session_id": None,
                        "has_more": False,
                        "policy": policy_summary,
                    }
                return {
                    "columns": [],
                    "rows": [],
                    "affected_rows": max(result.rowcount or 0, 0),
                    "execution_time_ms": duration_ms,
                    "truncated": False,
                    "session_id": None,
                    "has_more": False,
                    "policy": policy_plan.summary(returned_columns=[]) if policy_plan and policy_plan.applied else None,
                }
        finally:
            engine.dispose()

    def _table_identifier_candidates(self, sql: str) -> list[str]:
        cleaned = re.sub(r"/\*[\s\S]*?\*/", " ", sql)
        cleaned = re.sub(r"--.*?$", " ", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"#.*?$", " ", cleaned, flags=re.MULTILINE)
        pattern = re.compile(
            r"\b(?:from|join|update|into)\s+([^\s,;()]+)"
            r"|\b(?:alter|drop|truncate|create)\s+table\s+"
            r"(?:if\s+(?:not\s+)?exists\s+)?([^\s,;()]+)",
            re.IGNORECASE,
        )
        candidates: list[str] = []
        for match in pattern.finditer(cleaned):
            value = match.group(1) or match.group(2)
            if value:
                candidates.append(value)
        return candidates

    def _parse_table_identifier(self, raw: str, *, default_schema: str | None) -> SqlTableReference | None:
        value = raw.strip().rstrip(",;")
        if not value or value.startswith("("):
            return None
        if value.lower() in {"select", "values", "set"}:
            return None
        parts = [self._unquote_identifier(part) for part in value.split(".")]
        parts = [part for part in parts if part]
        if not parts:
            return None
        if len(parts) >= 3:
            return SqlTableReference(database=parts[-3], schema=parts[-2], table=parts[-1])
        if len(parts) == 2:
            return SqlTableReference(database=None, schema=parts[0], table=parts[1])
        return SqlTableReference(database=None, schema=default_schema, table=parts[0])

    def _unquote_identifier(self, value: str) -> str:
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'", "`"}:
            stripped = stripped[1:-1]
        elif stripped.startswith("[") and stripped.endswith("]"):
            stripped = stripped[1:-1]
        return stripped.replace('""', '"').replace("``", "`").replace("]]", "]")

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

    def _apply_result_policy(
        self,
        rows: list[Any],
        columns: list[str],
        policy_plan: QueryPolicyPlan | None,
    ) -> tuple[list[list[Any]], list[str], dict[str, Any] | None]:
        row_values = [list(row) for row in rows]
        if policy_plan is None or not policy_plan.applied or not policy_plan.result_controls_enabled:
            return row_values, columns, None

        visible_columns = policy_plan.visible_columns
        masked_columns = policy_plan.masked_columns or {}
        hidden_columns: list[str] = []
        kept_indices: list[int] = []
        normalized_visible = {self._normalize_identifier(column) for column in visible_columns or frozenset()}

        for index, column in enumerate(columns):
            normalized = self._normalize_identifier(column)
            if visible_columns is not None and normalized not in normalized_visible:
                hidden_columns.append(column)
                continue
            kept_indices.append(index)

        filtered_columns = [columns[index] for index in kept_indices]
        normalized_masks = {self._normalize_identifier(column): rule for column, rule in masked_columns.items()}
        filtered_rows: list[list[Any]] = []
        for row in row_values:
            filtered_row: list[Any] = []
            for index in kept_indices:
                value = row[index]
                rule = normalized_masks.get(self._normalize_identifier(columns[index]))
                filtered_row.append(self._mask_value(value, rule) if rule else value)
            filtered_rows.append(filtered_row)

        return filtered_rows, filtered_columns, policy_plan.summary(
            hidden_columns=hidden_columns,
            returned_columns=filtered_columns,
        )

    def _matching_query_policies(
        self,
        context: AccessContext,
        *,
        datasource_id: str,
        permission_code: str,
        database: str | None,
        schema: str | None,
    ) -> list[Any]:
        matching: list[Any] = []
        for policy in context.policies:
            if not policy.enabled:
                continue
            if policy.resource_type != "datasource":
                continue
            if policy.resource_key not in {"*", datasource_id}:
                continue
            if policy.permission_code is not None and policy.permission_code != permission_code:
                continue
            if not self._conditions_scope_match(policy.conditions or {}, database=database, schema=schema):
                continue
            matching.append(policy)
        return matching

    def _conditions_scope_match(self, conditions: dict[str, Any], *, database: str | None, schema: str | None) -> bool:
        allowed_databases = self._condition_items(conditions.get("databases"))
        if database and allowed_databases and "*" not in allowed_databases and database not in allowed_databases:
            return False

        allowed_schemas = self._condition_items(conditions.get("schemas"))
        if schema and allowed_schemas:
            candidates = {schema, f"{database}.{schema}" if database else schema}
            if "*" not in allowed_schemas and candidates.isdisjoint(allowed_schemas):
                return False
        return True

    def _conditions_table_match(
        self,
        conditions: dict[str, Any],
        *,
        database: str | None,
        schema: str | None,
        table: str,
    ) -> bool:
        if not self._conditions_scope_match(conditions, database=database, schema=schema):
            return False
        allowed_tables = self._condition_items(conditions.get("tables"))
        if allowed_tables:
            candidates = {table}
            if schema:
                candidates.add(f"{schema}.{table}")
            if database and schema:
                candidates.add(f"{database}.{schema}.{table}")
            if "*" not in allowed_tables and candidates.isdisjoint(allowed_tables):
                return False
        return True

    def _has_query_policy_controls(self, conditions: dict[str, Any]) -> bool:
        return any(
            key in conditions
            for key in (
                "row_filter",
                "row_filters",
                "visible_columns",
                "masked_columns",
                "mask_columns",
                "column_masks",
            )
        )

    def _merge_row_filters(self, conditions_list: list[dict[str, Any]]) -> str | None:
        filters: list[str] = []
        for conditions in conditions_list:
            value = conditions.get("row_filter")
            if isinstance(value, str) and value.strip():
                filters.append(value.strip())
            elif isinstance(value, list):
                filters.extend(str(item).strip() for item in value if str(item).strip())
            extra_filters = conditions.get("row_filters")
            if isinstance(extra_filters, list):
                filters.extend(str(item).strip() for item in extra_filters if str(item).strip())
        if not filters:
            return None
        return " AND ".join(f"({item})" for item in filters)

    def _merge_visible_columns(self, conditions_list: list[dict[str, Any]]) -> frozenset[str] | None:
        visible: set[str] | None = None
        for conditions in conditions_list:
            items = self._condition_items(conditions.get("visible_columns"))
            if not items or "*" in items:
                continue
            normalized = {self._normalize_identifier(item) for item in items}
            visible = normalized if visible is None else visible & normalized
        return None if visible is None else frozenset(visible)

    def _merge_masked_columns(self, conditions_list: list[dict[str, Any]]) -> dict[str, ColumnMaskRule]:
        merged: dict[str, ColumnMaskRule] = {}
        for conditions in conditions_list:
            for column, rule in self._mask_rules_from_condition(conditions.get("masked_columns")).items():
                merged[self._normalize_identifier(column)] = rule
            for column, rule in self._mask_rules_from_condition(conditions.get("mask_columns")).items():
                merged[self._normalize_identifier(column)] = rule
            for column, rule in self._mask_rules_from_condition(conditions.get("column_masks")).items():
                merged[self._normalize_identifier(column)] = rule
        return merged

    def _mask_rules_from_condition(self, value: Any) -> dict[str, ColumnMaskRule]:
        if not value:
            return {}
        if isinstance(value, list):
            return {str(item).strip(): ColumnMaskRule() for item in value if str(item).strip()}
        if isinstance(value, dict):
            rules: dict[str, ColumnMaskRule] = {}
            for column, spec in value.items():
                column_name = str(column).strip()
                if not column_name:
                    continue
                if isinstance(spec, dict):
                    rules[column_name] = ColumnMaskRule(
                        prefix=max(self._int_or_default(spec.get("prefix"), 0), 0),
                        suffix=max(self._int_or_default(spec.get("suffix"), 0), 0),
                        replacement=str(spec.get("replacement") or spec.get("mask") or "***"),
                    )
                else:
                    rules[column_name] = ColumnMaskRule()
            return rules
        return {}

    def _rewrite_postgres_select(
        self,
        sql: str,
        *,
        database_type: str,
        default_schema: str | None,
        row_filter: str | None,
        require_safe_projection: bool = False,
    ) -> _PolicyRewriteResult:
        wrapper = self._unwrap_known_policy_wrapper(sql)
        if wrapper is not None:
            inner = self._rewrite_postgres_select(
                wrapper.inner_sql,
                database_type=database_type,
                default_schema=default_schema,
                row_filter=row_filter,
                require_safe_projection=require_safe_projection,
            )
            return _PolicyRewriteResult(
                sql=f"{wrapper.prefix}{inner.sql}{wrapper.suffix}",
                target=inner.target,
                result_controls_enabled=wrapper.result_controls_enabled and inner.result_controls_enabled,
            )

        parsed = sqlparse.parse(sql)
        if len(parsed) != 1:
            raise QueryPolicyViolation("Only one SELECT statement can be policy-rewritten")
        statement = parsed[0]
        if statement.get_type() != "SELECT":
            raise QueryPolicyViolation("Only SELECT statements can be policy-rewritten")
        tokens = [token for token in statement.tokens if not token.is_whitespace]
        if not tokens or tokens[0].normalized != "SELECT":
            raise QueryPolicyViolation("Only SELECT statements can be policy-rewritten")

        from_index = self._top_level_token_index(tokens, "FROM")
        if from_index is None or from_index + 1 >= len(tokens):
            raise QueryPolicyViolation("SELECT statement does not contain a safe FROM target")
        if self._top_level_token_index(tokens, "JOIN") is not None:
            raise QueryPolicyViolation("JOIN queries cannot be safely policy-rewritten yet")
        if require_safe_projection:
            self._validate_safe_projection(tokens, from_index)

        source_token = tokens[from_index + 1]
        table_schema, table_name = self._table_from_source_token(source_token, default_schema=default_schema)
        table_schema = table_schema or default_schema or "public"
        target = QueryPolicyTarget(schema=table_schema, table=table_name)

        if row_filter is None:
            return _PolicyRewriteResult(sql=sql, target=target)
        self._validate_row_filter(row_filter)

        wrapped_filter = row_filter if row_filter.startswith("(") else f"({row_filter})"
        existing_where_index = self._top_level_token_index(tokens, "WHERE")
        insertion_index = self._first_clause_token_index(
            tokens,
            start=from_index + 2,
            keywords={"GROUP BY", "HAVING", "ORDER BY", "LIMIT", "OFFSET", "FETCH", "FOR", "UNION", "INTERSECT", "EXCEPT"},
        )
        if existing_where_index is not None:
            where_token = tokens[existing_where_index]
            where_sql = str(where_token).strip()
            if not where_sql.upper().startswith("WHERE"):
                raise QueryPolicyViolation("Unable to parse WHERE clause for policy rewrite")
            rewritten_where = f"WHERE ({where_sql[5:].strip()}) AND {wrapped_filter}"
            return _PolicyRewriteResult(sql=self._replace_token_text(sql, where_token, rewritten_where), target=target)

        insert_pos = len(sql) if insertion_index is None else self._token_start(sql, tokens[insertion_index])
        prefix = sql[:insert_pos].rstrip()
        suffix = sql[insert_pos:].lstrip()
        rewritten = f"{prefix} WHERE {wrapped_filter}"
        if suffix:
            rewritten += f" {suffix}"
        return _PolicyRewriteResult(sql=rewritten, target=target)

    def _unwrap_known_policy_wrapper(self, sql: str) -> _KnownWrapper | None:
        stripped = sql.strip()
        patterns = (
            ("SELECT * FROM (", "DBX_PAGE", True),
            ("SELECT COUNT(*) AS total_rows FROM (", ") AS dbx_count", False),
            ("SELECT * FROM (", "DBX_SORTED", True),
        )
        upper = stripped.upper()
        for prefix, marker, result_controls_enabled in patterns:
            if not upper.startswith(prefix.upper()):
                continue
            close_index = self._find_matching_closing_paren(stripped, len(prefix) - 1)
            if close_index is None:
                return None
            suffix = stripped[close_index:]
            suffix_upper = suffix.upper()
            if marker in {"DBX_PAGE", "DBX_SORTED"}:
                if f") AS {marker}" not in suffix_upper:
                    continue
            elif not suffix_upper.startswith(marker.upper()):
                continue
            return _KnownWrapper(
                prefix=stripped[: len(prefix)],
                inner_sql=stripped[len(prefix) : close_index],
                suffix=suffix,
                result_controls_enabled=result_controls_enabled,
            )
        return None

    def _find_matching_closing_paren(self, sql: str, open_index: int) -> int | None:
        depth = 0
        quote: str | None = None
        index = open_index
        while index < len(sql):
            char = sql[index]
            if quote:
                if char == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if char in {"'", '"'}:
                quote = char
                index += 1
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
            index += 1
        return None

    def _top_level_token_index(self, tokens: list[Any], normalized_keyword: str) -> int | None:
        target = normalized_keyword.upper()
        for index, token in enumerate(tokens):
            raw = str(token).strip().upper()
            if token.normalized == target or raw == target or raw.startswith(f"{target} "):
                return index
        return None

    def _first_clause_token_index(self, tokens: list[Any], *, start: int, keywords: set[str]) -> int | None:
        normalized_keywords = {keyword.upper() for keyword in keywords}
        for index in range(start, len(tokens)):
            normalized = tokens[index].normalized
            raw = str(tokens[index]).strip().upper()
            if normalized in normalized_keywords:
                return index
            if raw in normalized_keywords:
                return index
        return None

    def _table_from_source_token(self, token: Any, *, default_schema: str | None) -> tuple[str | None, str]:
        if token.__class__.__name__ == "IdentifierList":
            raise QueryPolicyViolation("Multiple FROM targets cannot be safely policy-rewritten yet")
        source_sql = str(token)
        if "(" in source_sql or ")" in source_sql or "," in source_sql:
            raise QueryPolicyViolation("Function and subquery FROM targets cannot be safely policy-rewritten yet")
        get_real_name = getattr(token, "get_real_name", None)
        get_parent_name = getattr(token, "get_parent_name", None)
        table_name = str(get_real_name() if callable(get_real_name) else token).strip().strip('"')
        parent = get_parent_name() if callable(get_parent_name) else None
        schema = str(parent).strip().strip('"') if parent else default_schema
        if not table_name or any(char.isspace() for char in table_name):
            raise QueryPolicyViolation("Unable to resolve a safe table target for policy rewrite")
        return schema or None, table_name

    def _validate_safe_projection(self, tokens: list[Any], from_index: int) -> None:
        projection_sql = " ".join(str(token).strip() for token in tokens[1:from_index]).strip()
        if not projection_sql or projection_sql == "*":
            return
        parsed = sqlparse.parse(f"SELECT {projection_sql}")
        if len(parsed) != 1:
            raise QueryPolicyViolation("Column policies require a simple SELECT projection")
        projection_tokens = [token for token in parsed[0].tokens if not token.is_whitespace]
        if len(projection_tokens) < 2:
            raise QueryPolicyViolation("Column policies require a simple SELECT projection")
        projection_token = projection_tokens[1]
        if projection_token.__class__.__name__ == "IdentifierList":
            identifiers = list(projection_token.get_identifiers())
        else:
            identifiers = [projection_token]
        for identifier in identifiers:
            text_value = str(identifier).strip()
            if text_value == "*" or text_value.endswith(".*"):
                continue
            if "(" in text_value or ")" in text_value:
                raise QueryPolicyViolation("Column policies reject computed SELECT expressions")
            get_alias = getattr(identifier, "get_alias", None)
            if callable(get_alias) and get_alias():
                raise QueryPolicyViolation("Column policies reject aliased SELECT columns")
            get_real_name = getattr(identifier, "get_real_name", None)
            column_name = get_real_name() if callable(get_real_name) else text_value
            if not str(column_name or "").strip():
                raise QueryPolicyViolation("Column policies require named SELECT columns")

    def _replace_token_text(self, sql: str, token: Any, replacement: str) -> str:
        start = self._token_start(sql, token)
        end = start + len(str(token))
        return f"{sql[:start]}{replacement}{sql[end:]}"

    def _token_start(self, sql: str, token: Any) -> int:
        value = str(token)
        start = sql.find(value)
        if start < 0:
            raise QueryPolicyViolation("Unable to locate SQL clause for policy rewrite")
        return start

    def _validate_row_filter(self, row_filter: str) -> None:
        parsed = sqlparse.parse(f"SELECT 1 WHERE {row_filter}")
        if len(parsed) != 1:
            raise QueryPolicyViolation("Row filter must be a single boolean expression")
        forbidden = {";", "--", "/*", "*/"}
        lowered = row_filter.lower()
        if any(marker in lowered for marker in forbidden):
            raise QueryPolicyViolation("Row filter contains forbidden SQL control tokens")
        if any(keyword in lowered for keyword in (" drop ", " delete ", " update ", " insert ", " alter ", " truncate ")):
            raise QueryPolicyViolation("Row filter contains non-read SQL keywords")

    def _is_plain_select(self, sql: str) -> bool:
        parsed = sqlparse.parse(sql)
        if len(parsed) != 1:
            return False
        if parsed[0].get_type() != "SELECT":
            return False
        for token in parsed[0].flatten():
            if token.is_whitespace:
                continue
            if token.ttype in sql_tokens.Comment:
                continue
            return token.normalized == "SELECT"
        return False

    def _condition_items(self, value: Any) -> set[str]:
        if isinstance(value, list):
            return {str(item).strip() for item in value if str(item).strip()}
        return set()

    def _normalize_identifier(self, value: str) -> str:
        return value.strip().strip('"').lower()

    def _mask_value(self, value: Any, rule: ColumnMaskRule | None) -> Any:
        if value is None or rule is None:
            return value
        text_value = str(value)
        if not text_value:
            return text_value
        prefix = min(rule.prefix, len(text_value))
        suffix = min(rule.suffix, max(len(text_value) - prefix, 0))
        if prefix + suffix >= len(text_value):
            return rule.replacement
        return f"{text_value[:prefix]}{rule.replacement}{text_value[len(text_value) - suffix:] if suffix else ''}"

    def _int_or_default(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

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
