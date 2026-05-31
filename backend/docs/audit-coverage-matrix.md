# Audit Coverage Matrix

This matrix defines the first-pass enterprise audit coverage for DBX web runtime.

| Area | Route Pattern | Store | Success | Failure | Actor/Network Context | Payload/Result Summary |
| --- | --- | --- | --- | --- | --- | --- |
| Authentication | `/api/v1/auth/login`, `/api/v1/auth/callback`, `/api/v1/auth/logout` | `audit_event` | login redirect, login succeeded, logout | unconfigured provider, unavailable provider, invalid callback state, rejected claims, upstream OIDC failure | user when available, IP, user-agent, request_id, trace_id | provider/session identifiers, state, failure reason |
| Session Guard | any protected API through `get_current_user` | `audit_event` | covered by API middleware | missing/invalid session | anonymous IP, user-agent, request_id, trace_id | denied path and reason |
| Permission Guard | any `require_permission(...)` dependency | `audit_event` | covered by API middleware | permission denied | authenticated user, IP, user-agent, request_id, trace_id | permission code and denial reason |
| Access Admin | `/api/v1/access/admin/*` | `audit_event` | role binding and resource policy mutations plus API success events | middleware records HTTP failures | authenticated admin, IP, user-agent, request_id, trace_id | mutation target and policy summary |
| Connections | `/api/connection/*` | `audit_event` | test/connect/disconnect/save success | test/connect/save failures | authenticated user, IP, user-agent, request_id, trace_id | route, query, redacted request body, status code, duration |
| Config/User State | `/api/app-settings/*`, `/api/desktop-settings`, `/api/editor-settings`, `/api/layout/*`, `/api/saved-sql/*`, `/api/history/*`, `/api/ai/*` | `audit_event` | read/write/delete success | HTTP failures | authenticated user, IP, user-agent, request_id, trace_id | route, query, redacted request body, status code, duration |
| Approval Flow | `/api/v1/approval/flows*` | `audit_event` | create/update/delete and API success events | middleware records validation and service failures | authenticated user, IP, user-agent, request_id, trace_id | flow code, version, match rule, step count |
| Approval Ticket | `/api/v1/approval/tickets*` | `audit_event` | create/submit/approve/reject/retry and API success events | middleware records validation and service failures | authenticated user, IP, user-agent, request_id, trace_id | ticket number, status, datasource, schedule, comments |
| Execution Engine | background execution jobs | `audit_event`, `query_audit` | job started/succeeded and executed SQL summary | job failure and rollback statement status | executor when available | ticket/job IDs, run key, result summary, SQL status |
| Interactive Query | `/api/query/execute` | `query_audit` | query result summary | runtime error or unsupported target | authenticated user, IP, user-agent, request_id, trace_id | SQL summary/text, datasource, database, rows, affected rows, duration, error |
| Multi/Batch/Script Query | `/api/query/execute-multi`, `/api/query/execute-batch`, `/api/query/execute-script`, `/api/query/execute-in-transaction` | `query_audit` | statement count and aggregate result summary | runtime error or transactional failure | authenticated user, IP, user-agent, request_id, trace_id | SQL text/statements, operation type, duration, error |
| Internal Query Execution | `/api/internal/query/execute-*` | `query_audit` | internal execution result summary | internal execution failure | system actor, IP, user-agent, request_id, trace_id | route, datasource, database, SQL/statements, duration, error |
| Audit Center | `/api/v1/audit/events`, `/api/v1/audit/queries` | none for read endpoints | queryable by permission | denied reads captured by permission guard | viewer context for denied reads | filters include actor, resource/action, datasource/database, status, request_id, trace_id |

Notes:

- `X-Request-Id` and `X-Trace-Id` are accepted from upstream proxies and generated when missing. Both are returned on HTTP responses and persisted in `audit_event` / `query_audit`.
- Request body capture is capped and redacts payloads that contain password/token/secret markers.
- Query SQL text is still trimmed by `AuditService.MAX_SQL_TEXT_LENGTH`; `sql_summary` remains capped for list views.
