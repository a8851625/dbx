from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.models.audit import AuditEvent, QueryAudit
from app.services.audit import AuditService


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return []


class FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, _: object) -> None:
        return None

    def execute(self, _stmt):
        return FakeResult(None)


class AuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AuditService()

    def test_build_actor_reads_request_and_trace_headers(self) -> None:
        request = SimpleNamespace(
            headers={"user-agent": "pytest", "x-forwarded-for": "10.0.0.1, 10.0.0.2"},
            state=SimpleNamespace(request_id="req-1", trace_id="trace-1"),
            url=SimpleNamespace(path="/api/query/execute"),
            method="POST",
            client=SimpleNamespace(host="127.0.0.1"),
        )

        actor = self.service.build_actor(request=request)

        self.assertEqual(actor.source_ip, "10.0.0.1")
        self.assertEqual(actor.user_agent, "pytest")
        self.assertEqual(actor.request_id, "req-1")
        self.assertEqual(actor.trace_id, "trace-1")

    def test_record_event_persists_request_and_trace_context(self) -> None:
        db = FakeDB()
        actor = self.service.build_actor(request_id="req-1", trace_id="trace-1")

        event = self.service.record_event(
            db,
            event_type="api.connection.test.post",
            category="connection",
            action="create",
            actor=actor,
            commit=True,
        )

        self.assertIsInstance(event, AuditEvent)
        self.assertEqual(event.request_id, "req-1")
        self.assertEqual(event.trace_id, "trace-1")
        self.assertEqual(db.commit_count, 1)

    def test_record_query_persists_failure_details_and_context(self) -> None:
        db = FakeDB()
        actor = self.service.build_actor(request_id="req-2", trace_id="trace-2")

        query = self.service.record_query(
            db,
            datasource_id="analytics",
            database_name="warehouse",
            sql_text="select * from missing_table",
            actor=actor,
            status="failed",
            error_code="ProgrammingError",
            error_message="relation does not exist",
            metadata={"route": "/api/query/execute"},
            commit=True,
        )

        self.assertIsInstance(query, QueryAudit)
        self.assertEqual(query.request_id, "req-2")
        self.assertEqual(query.trace_id, "trace-2")
        self.assertEqual(query.status, "failed")
        self.assertEqual(query.error_code, "ProgrammingError")
        self.assertEqual(query.details["route"], "/api/query/execute")
        self.assertEqual(db.commit_count, 1)

    def test_record_query_allows_empty_sql_for_pre_execution_failure(self) -> None:
        db = FakeDB()

        query = self.service.record_query(
            db,
            datasource_id="missing",
            database_name="warehouse",
            status="failed",
            error_code="HTTPException",
            error_message="Connection was not found",
        )

        self.assertEqual(query.sql_text, "")
        self.assertEqual(query.sql_summary, "")
        self.assertEqual(query.status, "failed")


if __name__ == "__main__":
    unittest.main()
