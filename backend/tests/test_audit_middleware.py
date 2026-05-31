from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.middleware.audit_context import _api_category, _api_event_name, _redact_payload_text, _should_audit_request


def fake_request(path: str, method: str = "POST"):
    return SimpleNamespace(url=SimpleNamespace(path=path), method=method)


class AuditMiddlewareTests(unittest.TestCase):
    def test_should_audit_key_api_paths_but_skip_query_ingest_duplicates(self) -> None:
        self.assertTrue(_should_audit_request(fake_request("/api/v1/access/admin/role-bindings")))
        self.assertTrue(_should_audit_request(fake_request("/api/connection/test")))
        self.assertFalse(_should_audit_request(fake_request("/api/query/execute")))
        self.assertFalse(_should_audit_request(fake_request("/api/v1/audit/events", "GET")))

    def test_event_name_and_category_are_stable(self) -> None:
        request = fake_request("/api/v1/approval/tickets/ticket-1/approve")

        self.assertEqual(_api_event_name(request), "approval.tickets.ticket-1.approve.post")
        self.assertEqual(_api_category(request.url.path), "approval")

    def test_redact_payload_text_masks_secret_like_bodies(self) -> None:
        self.assertEqual(_redact_payload_text('{"password":"secret"}')["redacted"], True)
        self.assertEqual(_redact_payload_text('{"title":"safe"}')["text"], '{"title":"safe"}')


if __name__ == "__main__":
    unittest.main()
