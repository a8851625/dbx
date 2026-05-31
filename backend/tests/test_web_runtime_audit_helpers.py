from __future__ import annotations

import unittest
import sys
import types

fake_session_module = types.ModuleType("app.db.session")
fake_session_module.SessionLocal = None
fake_session_module.get_db = lambda: None
session_module = sys.modules.setdefault("app.db.session", fake_session_module)
session_module.get_db = getattr(session_module, "get_db", fake_session_module.get_db)

fake_sqlparse_module = types.ModuleType("sqlparse")
fake_sqlparse_module.split = lambda sql: [part for part in str(sql).split(";") if part.strip()]
sys.modules.setdefault("sqlparse", fake_sqlparse_module)

from fastapi import HTTPException

from app.api.routes.web_runtime import _audit_sql_text, _query_result_summary, _runtime_error_to_http


class WebRuntimeAuditHelperTests(unittest.TestCase):
    def test_audit_sql_text_joins_batch_statements(self) -> None:
        payload = {"statements": ["update users set active = true", "delete from jobs where done = true"]}

        self.assertEqual(
            _audit_sql_text(payload, "batch"),
            "update users set active = true;\ndelete from jobs where done = true",
        )

    def test_query_result_summary_aggregates_list_results(self) -> None:
        summary = _query_result_summary(
            [
                {"affected_rows": 2, "rows": [[1], [2]], "truncated": False},
                {"affected_rows": 3, "rows": [], "truncated": True},
            ]
        )

        self.assertEqual(summary["statement_results"], 2)
        self.assertEqual(summary["affected_rows"], 5)
        self.assertEqual(summary["row_count"], 2)
        self.assertTrue(summary["truncated"])

    def test_runtime_error_to_http_preserves_existing_http_exception(self) -> None:
        exc = HTTPException(status_code=404, detail="missing")

        self.assertIs(_runtime_error_to_http(exc), exc)


if __name__ == "__main__":
    unittest.main()
