import { readFileSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";

const httpSource = readFileSync("apps/desktop/src/lib/http.ts", "utf8");
const runtimeRouteSource = readFileSync("backend/app/api/routes/web_runtime.py", "utf8");

test("web runtime disables schema diff and data compare before backend calls", () => {
  assert.match(httpSource, /function disabledWebRuntimeFeature/);
  assert.match(httpSource, /disabledWebRuntimeFeature\("Schema diff"\)/);
  assert.match(httpSource, /disabledWebRuntimeFeature\("Data compare"\)/);
  assert.doesNotMatch(httpSource, /post\("\/api\/schema-diff\/prepare"/);
  assert.doesNotMatch(httpSource, /post\("\/api\/data-compare\/prepare"/);
});

test("web runtime no longer exposes explicit 501 placeholders", () => {
  assert.doesNotMatch(runtimeRouteSource, /HTTP_501_NOT_IMPLEMENTED/);
  assert.doesNotMatch(runtimeRouteSource, /not available in web-only mode/);
});
