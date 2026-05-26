import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";

test("desktop release workflow is not shipped anymore", () => {
  assert.equal(existsSync(".github/workflows/release.yml"), false);
});

test("CI no longer installs AppImage packaging dependencies", () => {
  const workflow = readFileSync(".github/workflows/ci.yml", "utf8");

  assert.doesNotMatch(workflow, /\bxdg-utils\b/);
  assert.doesNotMatch(workflow, /\bAppImage\b/);
});
