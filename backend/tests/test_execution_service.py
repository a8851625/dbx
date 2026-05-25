from __future__ import annotations

import sys
import types
import unittest

from app.config import Settings
from app.models.approval import ChangeTicket, ChangeTicketStatement, ExecutionJob

fake_session_module = types.ModuleType("app.db.session")
fake_session_module.SessionLocal = None
sys.modules.setdefault("app.db.session", fake_session_module)

from app.services.execution import ExecutionService


class FakeExecutionDB:
    def __init__(self, job: ExecutionJob, ticket: ChangeTicket) -> None:
        self.job = job
        self.ticket = ticket
        self.added: list[object] = []
        self.commit_count = 0

    def get(self, model: type[object], key: str) -> object | None:
        if model is ExecutionJob and key == self.job.id:
            return self.job
        if model is ChangeTicket and key == self.ticket.id:
            return self.ticket
        return None

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_count += 1


class ExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ExecutionService(Settings())

    def test_extract_failure_index(self) -> None:
        self.assertEqual(self.service._extract_failure_index("Statement 3 failed: syntax error"), 3)
        self.assertIsNone(self.service._extract_failure_index("connection timeout"))

    def test_record_failure_marks_rollback_and_remaining_statements(self) -> None:
        ticket = ChangeTicket(
            id="ticket-1",
            ticket_no="TKT-1",
            ticket_type="ddl",
            title="Alter table",
            datasource_id="analytics",
            target_database="warehouse",
            risk_level="medium",
            sql_text="ALTER TABLE orders ADD COLUMN note text",
            submitter_id="user-1",
            current_status="executing",
        )
        job = ExecutionJob(
            id="job-1",
            ticket_id=ticket.id,
            run_key="manual:1",
            status="running",
            executor_type="user",
            execution_mode="immediate",
        )
        statements = [
            ChangeTicketStatement(
                id="stmt-1",
                ticket_id=ticket.id,
                statement_order=1,
                statement_text="BEGIN",
                statement_type="ddl",
                risk_tags=[],
                risk_level="low",
            ),
            ChangeTicketStatement(
                id="stmt-2",
                ticket_id=ticket.id,
                statement_order=2,
                statement_text="ALTER TABLE orders ADD COLUMN note text",
                statement_type="ddl",
                risk_tags=["structure_change"],
                risk_level="medium",
            ),
            ChangeTicketStatement(
                id="stmt-3",
                ticket_id=ticket.id,
                statement_order=3,
                statement_text="COMMIT",
                statement_type="ddl",
                risk_tags=[],
                risk_level="low",
            ),
        ]
        db = FakeExecutionDB(job, ticket)

        self.service._record_failure(db, job.id, "Statement 2 failed: syntax error", statements)

        self.assertEqual(job.status, "failed")
        self.assertEqual(ticket.current_status, "failed")
        self.assertEqual(job.result_summary, {"error": "Statement 2 failed: syntax error"})
        self.assertEqual(len(db.added), 3)
        first, second, third = db.added
        self.assertTrue(first.success)
        self.assertEqual(first.result, {"rolled_back": True})
        self.assertFalse(second.success)
        self.assertEqual(second.db_error_message, "Statement 2 failed: syntax error")
        self.assertFalse(third.success)
        self.assertEqual(third.result, {"not_executed": True})


if __name__ == "__main__":
    unittest.main()
