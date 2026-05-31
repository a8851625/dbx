import { readFileSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";

const dataGridSource = readFileSync("apps/desktop/src/components/grid/DataGrid.vue", "utf8");
const queryStoreSource = readFileSync("apps/desktop/src/stores/queryStore.ts", "utf8");
const databaseTypesSource = readFileSync("apps/desktop/src/types/database.ts", "utf8");

test("query policy metadata can be carried by query results", () => {
  assert.match(queryStoreSource, /current\.result = results\[0\]/);
  assert.match(queryStoreSource, /current\.results = results/);
});

test("query results expose and render policy summaries", () => {
  assert.match(databaseTypesSource, /policy\?:\s*\{/);
  assert.match(dataGridSource, /showPolicySummary/);
  assert.match(dataGridSource, /policyHiddenColumnList/);
  assert.match(dataGridSource, /policyMaskedColumnList/);
});
