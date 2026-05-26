import { strict as assert } from "node:assert";
import test from "node:test";
import { isTauriRuntime } from "../../apps/desktop/src/lib/tauriRuntime.ts";

test("detects plain browser-like globals as non-Tauri runtime", () => {
  assert.equal(isTauriRuntime({}), false);
});

test("treats legacy Tauri globals as browser runtime after desktop removal", () => {
  assert.equal(isTauriRuntime({ __TAURI_INTERNALS__: {} }), false);
  assert.equal(isTauriRuntime({ __TAURI__: {} }), false);
});
