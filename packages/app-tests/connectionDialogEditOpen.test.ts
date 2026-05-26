import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appDialogsSource = readFileSync(
  new URL("../../apps/desktop/src/components/layout/AppDialogs.vue", import.meta.url),
  "utf8",
);

test("connection dialog opens when editing connection config is available", () => {
  assert.match(appDialogsSource, /allowConnectionManagement\?: boolean;/);
  assert.match(appDialogsSource, /const shouldShowConnectionDialog = computed\(/);
  assert.match(appDialogsSource, /!!props\.allowConnectionManagement && \(props\.showConnectionDialog \|\| !!editConfig\.value\)/);
  assert.match(appDialogsSource, /:open="shouldShowConnectionDialog"/);
  assert.match(appDialogsSource, /v-if="shouldShowConnectionDialog"/);
});

const connectionDialogSource = readFileSync(
  new URL("../../apps/desktop/src/components/connection/ConnectionDialog.vue", import.meta.url),
  "utf8",
);

test("connection dialog initializes edit form on first mount", () => {
  assert.match(connectionDialogSource, /watch\(\s*\(\) => props\.editConfig,[\s\S]*?\{\s*immediate: true\s*\},\s*\)/);
});

test("connection dialog maps legacy Dameng configs to the DM profile", () => {
  assert.match(connectionDialogSource, /if \(config\.db_type === "dameng"\) return "dm";/);
});

test("connection dialog offers DuckDB file creation from the new connection form", () => {
  assert.match(connectionDialogSource, /async function createDuckDbFilePath\(\)/);
  assert.doesNotMatch(connectionDialogSource, /@tauri-apps/);
  assert.match(connectionDialogSource, /form\.db_type === 'duckdb'/);
  assert.match(connectionDialogSource, /@click="createDuckDbFilePath"/);
});
