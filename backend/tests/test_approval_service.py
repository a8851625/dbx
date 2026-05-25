from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from app.models.approval import ApprovalInstance, ApprovalInstanceStep, ChangeTicket
from app.models.auth import UserIdentity
from app.services.approval import ApprovalService, TicketBundle
from app.services.authorization import AccessContext


class FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_count = 0
        self.refreshed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, obj: object) -> None:
        self.refreshed.append(obj)


def make_user(*, role: str = "admin") -> UserIdentity:
    return UserIdentity(
        id="user-1",
        provider_id="default",
        subject="sub-1",
        email="admin@example.com",
        role=role,
    )


def make_access_context(user: UserIdentity) -> AccessContext:
    return AccessContext(
        user=user,
        roles=(user.role,),
        permissions=frozenset({"approval.ticket.approve", "approval.ticket.view"}),
        policies=(),
    )


def make_ticket(*, current_status: str = "pending_approval") -> ChangeTicket:
    return ChangeTicket(
        id="ticket-1",
        ticket_no="TKT-1",
        ticket_type="ddl",
        title="Alter table",
        datasource_id="analytics",
        target_database="warehouse",
        risk_level="medium",
        sql_text="ALTER TABLE orders ADD COLUMN note text",
        submitter_id="user-1",
        current_status=current_status,
    )


def make_step(*, step_no: int, status: str) -> ApprovalInstanceStep:
    return ApprovalInstanceStep(
        id=f"step-{step_no}",
        instance_id="instance-1",
        flow_step_id=f"flow-step-{step_no}",
        step_no=step_no,
        step_name=f"Step {step_no}",
        approval_mode="any_one",
        approver_type="role",
        approver_ref="admin",
        rule={},
        status=status,
        started_at=datetime.now(UTC) if status == "pending" else None,
    )


class ApprovalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ApprovalService()
        self.user = make_user()
        self.access_context = make_access_context(self.user)

    def test_classify_sql_keeps_semicolons_inside_strings(self) -> None:
        statements = self.service.classify_sql(
            "UPDATE orders SET note = 'a;b' WHERE id = 1; DELETE FROM audit_log WHERE id = 2;"
        )

        self.assertEqual(len(statements), 2)
        self.assertEqual(statements[0].statement_type, "dml")
        self.assertEqual(statements[0].risk_level, "medium")
        self.assertEqual(statements[1].statement_type, "dml")
        self.assertEqual(statements[1].risk_level, "high")

    def test_classify_sql_marks_destructive_and_structure_risks(self) -> None:
        statements = self.service.classify_sql("ALTER TABLE orders ADD COLUMN note text; DELETE FROM orders;")

        self.assertEqual(statements[0].risk_tags, ["structure_change"])
        self.assertIn("destructive", statements[1].risk_tags)
        self.assertIn("no_where_clause", statements[1].risk_tags)
        self.assertEqual(statements[1].risk_level, "high")

    def test_approve_ticket_advances_to_next_step_without_queueing(self) -> None:
        ticket = make_ticket()
        instance = ApprovalInstance(
            id="instance-1",
            ticket_id=ticket.id,
            flow_id="flow-1",
            status="pending",
            current_step_no=1,
            started_at=datetime.now(UTC),
        )
        first_step = make_step(step_no=1, status="pending")
        second_step = make_step(step_no=2, status="waiting")
        bundle = TicketBundle(
            ticket=ticket,
            statements=[],
            approval_instance=instance,
            approval_steps=[first_step, second_step],
            approval_actions={},
            execution_jobs=[],
            execution_results={},
        )
        db = FakeDB()

        with patch.object(self.service, "load_ticket_bundle", return_value=bundle):
            result = self.service.approve_ticket(
                db,
                ticket.id,
                self.user,
                self.access_context,
                comment="looks good",
            )

        self.assertFalse(result.should_queue_execution)
        self.assertEqual(ticket.current_status, "pending_approval")
        self.assertEqual(instance.current_step_no, 2)
        self.assertEqual(first_step.status, "approved")
        self.assertEqual(second_step.status, "pending")
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(len(db.added), 1)

    def test_approve_ticket_finishes_flow_and_requests_execution(self) -> None:
        ticket = make_ticket()
        instance = ApprovalInstance(
            id="instance-1",
            ticket_id=ticket.id,
            flow_id="flow-1",
            status="pending",
            current_step_no=1,
            started_at=datetime.now(UTC),
        )
        only_step = make_step(step_no=1, status="pending")
        bundle = TicketBundle(
            ticket=ticket,
            statements=[],
            approval_instance=instance,
            approval_steps=[only_step],
            approval_actions={},
            execution_jobs=[],
            execution_results={},
        )
        db = FakeDB()

        with patch.object(self.service, "load_ticket_bundle", return_value=bundle):
            result = self.service.approve_ticket(
                db,
                ticket.id,
                self.user,
                self.access_context,
                comment=None,
            )

        self.assertTrue(result.should_queue_execution)
        self.assertEqual(ticket.current_status, "approved")
        self.assertEqual(instance.status, "approved")
        self.assertIsNone(instance.current_step_no)
        self.assertEqual(only_step.status, "approved")


if __name__ == "__main__":
    unittest.main()
