import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";

test("settings about panel uses the app version prop instead of a hard-coded version", () => {
  const source = readFileSync("apps/desktop/src/components/editor/EditorSettingsDialog.vue", "utf8");

  assert.equal(source.includes("v0.5.0"), false);
  assert.match(source, /appVersion/);
});

test("desktop release workflow is removed from the web-only runtime", () => {
  assert.equal(existsSync(".github/workflows/release.yml"), false);
});
