from __future__ import annotations

import sys
import types
import unittest

from fastapi import HTTPException

fake_session_module = types.ModuleType("app.db.session")
fake_session_module.SessionLocal = None
fake_session_module.get_db = lambda: None
sys.modules.setdefault("app.db.session", fake_session_module)

from app.api.routes.web_runtime import (
    _authorize_query_scope,
    _filter_authorized_connections,
    _filter_authorized_databases,
    _filter_authorized_tables,
)
from app.models.auth import UserIdentity
from app.models.rbac import ResourcePolicy
from app.services.authorization import AccessContext, AuthorizationService


class InMemoryAuthorizationService(AuthorizationService):
    def __init__(self, context: AccessContext) -> None:
        self.context = context

    def build_access_context(self, db, user: UserIdentity) -> AccessContext:  # noqa: ANN001
        return self.context


class WebRuntimeAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = UserIdentity(
            id="user-1",
            provider_id="default",
            subject="sub-1",
            email="user@example.com",
            role="developer",
        )

    def _service(self, *, permissions: set[str], policies: tuple[ResourcePolicy, ...]) -> InMemoryAuthorizationService:
        return InMemoryAuthorizationService(
            AccessContext(
                user=self.user,
                roles=("developer",),
                permissions=frozenset(permissions),
                policies=policies,
            )
        )

    def test_connection_list_filters_by_datasource_policy(self) -> None:
        service = self._service(
            permissions={"datasource.browse"},
            policies=(
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="datasource.browse",
                    effect="allow",
                    conditions={},
                    enabled=True,
                ),
            ),
        )

        visible = _filter_authorized_connections(
            None,
            service,
            self.user,
            [{"id": "analytics", "name": "Analytics"}, {"id": "billing", "name": "Billing"}],
            "datasource.browse",
        )

        self.assertEqual([item["id"] for item in visible], ["analytics"])

    def test_schema_browse_filters_database_and_table_scope(self) -> None:
        service = self._service(
            permissions={"datasource.browse"},
            policies=(
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="datasource.browse",
                    effect="allow",
                    conditions={"databases": ["warehouse"], "tables": ["public.orders"]},
                    enabled=True,
                ),
            ),
        )

        databases = _filter_authorized_databases(
            None,
            service,
            self.user,
            {"id": "analytics"},
            [{"name": "warehouse"}, {"name": "billing"}],
        )
        tables = _filter_authorized_tables(
            None,
            service,
            self.user,
            {"id": "analytics"},
            database="warehouse",
            schema="public",
            tables=[{"name": "orders"}, {"name": "payments"}],
        )

        self.assertEqual([item["name"] for item in databases], ["warehouse"])
        self.assertEqual([item["name"] for item in tables], ["orders"])

    def test_query_scope_rejects_denied_referenced_table(self) -> None:
        service = self._service(
            permissions={"query.execute"},
            policies=(
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="allow",
                    conditions={"databases": ["warehouse"], "tables": ["public.orders", "public.payments"]},
                    enabled=True,
                ),
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="deny",
                    conditions={"databases": ["warehouse"], "tables": ["public.payments"]},
                    enabled=True,
                ),
            ),
        )

        with self.assertRaises(HTTPException) as raised:
            _authorize_query_scope(
                None,
                service,
                self.user,
                {"connectionId": "analytics", "database": "warehouse", "schema": "public"},
                sql="select * from public.orders join public.payments on payments.order_id = orders.id",
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail, "Datasource scope denied: analytics")


if __name__ == "__main__":
    unittest.main()
