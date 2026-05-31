from __future__ import annotations

import unittest

from app.models.auth import UserIdentity
from app.models.rbac import ResourcePolicy
from app.services.authorization import AccessContext, AuthorizationService
from app.services.query_runtime import QueryPolicyViolation, QueryRuntimeService


class AuthorizationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AuthorizationService()
        self.user = UserIdentity(
            id="user-1",
            provider_id="default",
            subject="sub-1",
            email="user@example.com",
            role="developer",
        )

    def test_permission_check_requires_permission_code(self) -> None:
        context = AccessContext(
            user=self.user,
            roles=("developer",),
            permissions=frozenset({"datasource.browse"}),
            policies=(),
        )

        decision = self.service.check_permission(context, "query.execute")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "Missing permission: query.execute")

    def test_permission_check_allows_admin_without_datasource_policy(self) -> None:
        context = AccessContext(
            user=self.user,
            roles=("admin",),
            permissions=frozenset({"query.execute"}),
            policies=(),
        )

        decision = self.service.check_permission(context, "query.execute", datasource_id="analytics")

        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.reason)

    def test_permission_check_requires_matching_datasource_scope(self) -> None:
        context = AccessContext(
            user=self.user,
            roles=("developer",),
            permissions=frozenset({"query.execute"}),
            policies=(
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="allow",
                    conditions={
                        "databases": ["warehouse"],
                        "schemas": ["warehouse.public"],
                        "tables": ["warehouse.public.orders"],
                    },
                    enabled=True,
                ),
            ),
        )

        allowed = self.service.check_permission(
            context,
            "query.execute",
            datasource_id="analytics",
            database="warehouse",
            schema="public",
            table="orders",
        )
        denied = self.service.check_permission(
            context,
            "query.execute",
            datasource_id="analytics",
            database="warehouse",
            schema="public",
            table="payments",
        )

        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "Datasource scope denied: analytics")

    def test_deny_policy_wins_over_allow_policy(self) -> None:
        context = AccessContext(
            user=self.user,
            roles=("developer",),
            permissions=frozenset({"query.execute"}),
            policies=(
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="allow",
                    conditions={"databases": ["warehouse"], "schemas": ["public"], "tables": ["orders"]},
                    enabled=True,
                ),
                ResourcePolicy(
                    principal_type="user",
                    principal_ref="user-1",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="deny",
                    conditions={"databases": ["warehouse"], "schemas": ["public"], "tables": ["orders"]},
                    enabled=True,
                ),
            ),
        )

        decision = self.service.check_permission(
            context,
            "query.execute",
            datasource_id="analytics",
            database="warehouse",
            schema="public",
            table="orders",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "Datasource scope denied: analytics")

    def test_deny_policy_requires_matching_concrete_context(self) -> None:
        context = AccessContext(
            user=self.user,
            roles=("developer",),
            permissions=frozenset({"query.execute"}),
            policies=(
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="allow",
                    conditions={"databases": ["warehouse"]},
                    enabled=True,
                ),
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="deny",
                    conditions={"databases": ["restricted"]},
                    enabled=True,
                ),
            ),
        )

        decision = self.service.check_permission(
            context,
            "query.execute",
            datasource_id="analytics",
            database="warehouse",
        )

        self.assertTrue(decision.allowed)

    def test_table_scope_can_match_schema_qualified_table(self) -> None:
        context = AccessContext(
            user=self.user,
            roles=("developer",),
            permissions=frozenset({"datasource.browse"}),
            policies=(
                ResourcePolicy(
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="datasource.browse",
                    effect="allow",
                    conditions={"tables": ["public.orders"]},
                    enabled=True,
                ),
            ),
        )

        decision = self.service.check_permission(
            context,
            "datasource.browse",
            datasource_id="analytics",
            database="warehouse",
            schema="public",
            table="orders",
        )

        self.assertTrue(decision.allowed)


class QueryPolicyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = QueryRuntimeService()
        self.user = UserIdentity(
            id="user-1",
            provider_id="default",
            subject="sub-1",
            email="user@example.com",
            role="developer",
        )
        self.config = {"db_type": "postgres"}

    def _context(self, conditions: dict) -> AccessContext:
        return AccessContext(
            user=self.user,
            roles=("developer",),
            permissions=frozenset({"query.execute"}),
            policies=(
                ResourcePolicy(
                    id="policy-1",
                    principal_type="role",
                    principal_ref="developer",
                    resource_type="datasource",
                    resource_key="analytics",
                    permission_code="query.execute",
                    effect="allow",
                    conditions=conditions,
                    enabled=True,
                ),
            ),
        )

    def test_policy_plan_injects_row_filter_and_masks_visible_columns(self) -> None:
        plan = self.service.build_query_policy_plan(
            self._context(
                {
                    "tables": ["public.orders"],
                    "row_filter": "tenant_id = 'acme'",
                    "visible_columns": ["id", "email", "status"],
                    "masked_columns": {"email": {"prefix": 2, "suffix": 4}},
                }
            ),
            self.config,
            datasource_id="analytics",
            database="warehouse",
            schema="public",
            sql="SELECT id, email, status, total FROM public.orders ORDER BY id",
        )

        self.assertIn("WHERE (tenant_id = 'acme') ORDER BY id", plan.sql)
        rows, columns, summary = self.service._apply_result_policy(
            [(1, "alice@example.com", "paid", 99)],
            ["id", "email", "status", "total"],
            plan,
        )

        self.assertEqual(columns, ["id", "email", "status"])
        self.assertEqual(rows, [[1, "al***.com", "paid"]])
        self.assertEqual(summary["hidden_columns"], ["total"])
        self.assertEqual(summary["masked_columns"], ["email"])

    def test_policy_plan_rejects_complex_column_policy(self) -> None:
        with self.assertRaises(QueryPolicyViolation):
            self.service.build_query_policy_plan(
                self._context({"visible_columns": ["id"]}),
                self.config,
                datasource_id="analytics",
                database="warehouse",
                schema="public",
                sql="SELECT * FROM public.orders JOIN public.users ON users.id = orders.user_id",
            )

    def test_policy_plan_rejects_aliased_masked_projection(self) -> None:
        with self.assertRaises(QueryPolicyViolation):
            self.service.build_query_policy_plan(
                self._context({"masked_columns": ["email"]}),
                self.config,
                datasource_id="analytics",
                database="warehouse",
                schema="public",
                sql="SELECT email AS e FROM public.orders",
            )

    def test_policy_plan_rewrites_known_pagination_wrapper(self) -> None:
        plan = self.service.build_query_policy_plan(
            self._context({"tables": ["public.orders"], "row_filter": "tenant_id = current_setting('app.tenant')"}),
            self.config,
            datasource_id="analytics",
            database="warehouse",
            schema="public",
            sql='SELECT * FROM (SELECT * FROM public.orders) AS dbx_page LIMIT 50 OFFSET 0',
        )

        self.assertIn("WHERE (tenant_id = current_setting('app.tenant'))", plan.sql)
        self.assertIn("AS dbx_page LIMIT 50 OFFSET 0", plan.sql)

    def test_query_policy_control_ids_identifies_policy_bypass_risk(self) -> None:
        policy_ids = self.service.query_policy_control_ids(
            self._context({"row_filter": "tenant_id = 'acme'"}),
            self.config,
            datasource_id="analytics",
            database="warehouse",
            schema="public",
        )

        self.assertEqual(policy_ids, ["policy-1"])


if __name__ == "__main__":
    unittest.main()
