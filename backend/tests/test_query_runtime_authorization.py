from __future__ import annotations

import unittest

from app.services.query_runtime import QueryRuntimeService


class QueryRuntimeAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = QueryRuntimeService()

    def test_extract_table_references_from_common_query_shapes(self) -> None:
        refs = self.service.extract_table_references(
            """
            SELECT *
            FROM public.orders o
            JOIN warehouse.dim.customer c ON c.id = o.customer_id;
            UPDATE public.payments SET status = 'done' WHERE id = 1;
            """,
            default_schema="public",
        )

        self.assertEqual(
            [(item.database, item.schema, item.table) for item in refs],
            [
                (None, "public", "orders"),
                ("warehouse", "dim", "customer"),
                (None, "public", "payments"),
            ],
        )

    def test_extract_table_references_from_ddl(self) -> None:
        refs = self.service.extract_table_references(
            "ALTER TABLE IF EXISTS public.orders ADD COLUMN note text; DROP TABLE warehouse.audit.events;"
        )

        self.assertEqual(
            [(item.database, item.schema, item.table) for item in refs],
            [(None, "public", "orders"), ("warehouse", "audit", "events")],
        )


if __name__ == "__main__":
    unittest.main()
