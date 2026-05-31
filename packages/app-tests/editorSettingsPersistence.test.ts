import { readFileSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";

const settingsStoreSource = readFileSync("apps/desktop/src/stores/settingsStore.ts", "utf8");
const appSource = readFileSync("apps/desktop/src/App.vue", "utf8");
const runtimeRouteSource = readFileSync("backend/app/api/routes/web_runtime.py", "utf8");

test("web runtime migrates editor settings through backend storage", () => {
  assert.match(settingsStoreSource, /api\.loadEditorSettings\(\)/);
  assert.match(settingsStoreSource, /api\.saveEditorSettings\(editorSettings\.value/);
  assert.match(settingsStoreSource, /const isDesktop = isTauriRuntime\(\)/);
});

test("app startup initializes editor settings migration", () => {
  assert.match(appSource, /settingsStore\.initEditorSettings\(\);/);
});

test("web runtime exposes editor settings endpoints", () => {
  assert.match(runtimeRouteSource, /@router\.get\("\/editor-settings"\)/);
  assert.match(runtimeRouteSource, /@router\.post\("\/editor-settings"\)/);
});

test("web runtime exposes schema tree cache endpoints", () => {
  assert.match(runtimeRouteSource, /@router\.get\("\/schema\/cache"\)/);
  assert.match(runtimeRouteSource, /@router\.post\("\/schema\/cache"\)/);
  assert.match(runtimeRouteSource, /@router\.delete\("\/schema\/cache-prefix"\)/);
});
