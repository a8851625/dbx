import { readFileSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";
import { generateDatabaseExportId } from "../../apps/desktop/src/lib/databaseExport.ts";

const databaseExportSource = readFileSync("apps/desktop/src/lib/databaseExport.ts", "utf8");
const exportFormatsSource = readFileSync("apps/desktop/src/lib/exportFormats.ts", "utf8");
const treeItemSource = readFileSync("apps/desktop/src/components/sidebar/TreeItem.vue", "utf8");
const objectBrowserSource = readFileSync("apps/desktop/src/components/objects/ObjectBrowser.vue", "utf8");
const apiSource = readFileSync("apps/desktop/src/lib/api.ts", "utf8");
const tauriSource = readFileSync("apps/desktop/src/lib/tauri.ts", "utf8");
const httpSource = readFileSync("apps/desktop/src/lib/http.ts", "utf8");
const rustSource = readFileSync("crates/dbx-core/src/database_export.rs", "utf8");

test("generates export ids when crypto.randomUUID is unavailable", () => {
  const originalCrypto = globalThis.crypto;

  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {},
    });

    assert.match(generateDatabaseExportId(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  } finally {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: originalCrypto,
    });
  }
});

test("frontend database SQL export helpers delegate executable SQL generation to backend APIs", () => {
  assert.match(databaseExportSource, /return api\.buildExportInsertStatements\(options\)/);
  assert.match(databaseExportSource, /return api\.buildDatabaseSqlExport\(/);
  assert.match(exportFormatsSource, /return api\.buildExportSqlInsert\(/);
  assert.doesNotMatch(databaseExportSource, /INSERT INTO|formatSqlLiteral|replace\(\s*\/'/);
  assert.doesNotMatch(exportFormatsSource, /INSERT INTO|VALUES|quoteIdent|replace\(\s*\/'/);
});

test("SQL export callers await backend INSERT builders", () => {
  assert.match(treeItemSource, /await formatSqlInsert\(/);
  assert.match(objectBrowserSource, /await formatSqlInsert\(/);
});

test("shared API exposes backend database export SQL builders", () => {
  assert.match(apiSource, /export \* from "\.\/http";/);
  assert.match(httpSource, /export async function buildExportInsertStatements/);
  assert.match(httpSource, /export async function buildExportSqlInsert/);
  assert.match(httpSource, /export async function buildDatabaseSqlExport/);
  assert.match(tauriSource, /invoke\("build_export_insert_statements"/);
  assert.match(tauriSource, /invoke\("build_export_sql_insert"/);
  assert.match(tauriSource, /invoke\("build_database_sql_export"/);
  assert.match(httpSource, /\/api\/query\/build-export-insert-statements/);
  assert.match(httpSource, /\/api\/query\/build-export-sql-insert/);
  assert.match(httpSource, /\/api\/query\/build-database-sql-export/);
});

test("Rust database export SQL exposes INSERT and full export builders", () => {
  assert.match(rustSource, /pub fn format_export_sql_literal/);
  assert.match(rustSource, /pub fn build_export_insert_statements/);
  assert.match(rustSource, /pub fn build_export_sql_insert/);
  assert.match(rustSource, /pub fn build_database_sql_export/);
});
