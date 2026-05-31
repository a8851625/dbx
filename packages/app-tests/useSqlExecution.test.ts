import { strict as assert } from "node:assert";
import test from "node:test";
import {
  approvalDraftSqlFromClassification,
  isDangerousSql,
  requiresApprovalFromClassification,
  stripSqlComments,
} from "../../apps/desktop/src/composables/useSqlExecution.ts";

test("stripSqlComments removes block comments", () => {
  assert.equal(stripSqlComments("SELECT /* drop */ 1").includes("drop"), false);
});

test("stripSqlComments removes line comments", () => {
  assert.equal(stripSqlComments("SELECT 1 -- DROP TABLE").includes("DROP"), false);
});

test("stripSqlComments removes MySQL hash comments", () => {
  assert.equal(stripSqlComments("SELECT 1 # DELETE").includes("DELETE"), false);
});

test("SELECT with REPLACE function is not dangerous", () => {
  assert.equal(isDangerousSql("SELECT REPLACE(col, ';', ',') FROM t"), false);
});

test("SELECT with TRUNCATE function is not dangerous", () => {
  assert.equal(isDangerousSql("SELECT TRUNCATE(1.234, 2)"), false);
});

test("REPLACE INTO is dangerous", () => {
  assert.equal(isDangerousSql("REPLACE INTO t VALUES (1, 2)"), true);
});

test("DELETE FROM is dangerous", () => {
  assert.equal(isDangerousSql("DELETE FROM t WHERE id = 1"), true);
});

test("DROP TABLE is dangerous", () => {
  assert.equal(isDangerousSql("DROP TABLE t"), true);
});

test("UPDATE is dangerous", () => {
  assert.equal(isDangerousSql("UPDATE t SET col = 1"), true);
});

test("ALTER TABLE is dangerous", () => {
  assert.equal(isDangerousSql("ALTER TABLE t ADD COLUMN c INT"), true);
});

test("TRUNCATE TABLE is dangerous", () => {
  assert.equal(isDangerousSql("TRUNCATE TABLE t"), true);
});

test("multi-statement with danger in second statement is dangerous", () => {
  assert.equal(isDangerousSql("SELECT 1; DROP TABLE t"), true);
});

test("danger keyword inside comment is not dangerous", () => {
  assert.equal(isDangerousSql("SELECT 1 /* DROP TABLE t */"), false);
});

test("leading whitespace before danger keyword is dangerous", () => {
  assert.equal(isDangerousSql("  DELETE FROM t"), true);
});

test("complex SELECT with joins and functions is not dangerous", () => {
  const sql = `select mkey, replace(xxxx,';',',') as xxxx_content,
    SUBSTRING_INDEX(SUBSTRING_INDEX(pmid,'/',3),'/',-1) as xxx
    from m_xxx left join (
      select xxx from m_alarm where alarm_time >= SUBDATE(now(), interval 3 minute)
    ) as xxxx on m_alarm.mid = m_alarm_tmp.mid`;
  assert.equal(isDangerousSql(sql), false);
});

test("query classification requires approval for DDL and DML statements", () => {
  assert.equal(
    requiresApprovalFromClassification({
      requires_approval: true,
      statements: [
        {
          order: 1,
          statement_text: "ALTER TABLE users ADD COLUMN note text",
          statement_type: "ddl",
          keyword: "alter",
          risk_level: "medium",
          risk_tags: ["structure_change"],
        },
      ],
    }),
    true,
  );
});

test("query classification does not require approval for read and metadata statements", () => {
  assert.equal(
    requiresApprovalFromClassification({
      requires_approval: false,
      statements: [
        {
          order: 1,
          statement_text: "SELECT 1",
          statement_type: "read",
          keyword: "select",
          risk_level: "low",
          risk_tags: [],
        },
        {
          order: 2,
          statement_text: "EXPLAIN SELECT 1",
          statement_type: "metadata",
          keyword: "explain",
          risk_level: "low",
          risk_tags: [],
        },
      ],
    }),
    false,
  );
});

test("approval draft uses only DDL and DML statements from mixed SQL", () => {
  assert.equal(
    approvalDraftSqlFromClassification(
      {
        requires_approval: true,
        statements: [
          {
            order: 1,
            statement_text: "SELECT 1",
            statement_type: "read",
            keyword: "select",
            risk_level: "low",
            risk_tags: [],
          },
          {
            order: 2,
            statement_text: "ALTER TABLE users ADD COLUMN note text",
            statement_type: "ddl",
            keyword: "alter",
            risk_level: "medium",
            risk_tags: ["structure_change"],
          },
          {
            order: 3,
            statement_text: "UPDATE users SET note = 'x' WHERE id = 1",
            statement_type: "dml",
            keyword: "update",
            risk_level: "medium",
            risk_tags: [],
          },
        ],
      },
      "SELECT 1; ALTER TABLE users ADD COLUMN note text; UPDATE users SET note = 'x' WHERE id = 1",
    ),
    "ALTER TABLE users ADD COLUMN note text;\nUPDATE users SET note = 'x' WHERE id = 1",
  );
});
