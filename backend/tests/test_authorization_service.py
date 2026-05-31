from __future__ import annotations

import unittest

from app.models.auth import UserIdentity
from app.models.rbac import ResourcePolicy
from app.services.authorization import AccessContext, AuthorizationService


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


if __name__ == "__main__":
    unittest.main()
