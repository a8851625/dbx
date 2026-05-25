import { readdirSync, readFileSync, statSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";

const appSource = readFileSync("apps/desktop/src/App.vue", "utf8");
const connectionDialogSource = readFileSync("apps/desktop/src/components/connection/ConnectionDialog.vue", "utf8");
const driverStoreSource = readFileSync("apps/desktop/src/components/config/DriverStoreDialog.vue", "utf8");
const exportDialogSource = readFileSync("apps/desktop/src/components/export/DatabaseExportDialog.vue", "utf8");
const appToolbarSource = readFileSync("apps/desktop/src/components/layout/AppToolbar.vue", "utf8");

function appSourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = `${dir}/${entry}`;
    if (statSync(path).isDirectory()) return appSourceFiles(path);
    return /\.(ts|vue)$/.test(entry) ? [path] : [];
  });
}

test("web runtime keeps driver store entry accessible from the toolbar", () => {
  assert.match(appToolbarSource, /@click="emit\('open-driver-store'\)"/);
  assert.doesNotMatch(appToolbarSource, /<Button\s+v-if="isDesktop"[\s\S]*?@click="emit\('open-driver-store'\)"/);
});

test("web runtime handles driver store open events", () => {
  assert.match(appSource, /showDriverStore\.value = true;/);
  assert.doesNotMatch(appSource, /if \(!isDesktop\) return;\s+showDriverStore\.value = true;/);
});

test("web runtime hides desktop-only local database connection types", () => {
  assert.match(connectionDialogSource, /desktopOnlyDbOptionValues = new Set\(\["sqlite", "duckdb", "access"\]\)/);
  assert.match(connectionDialogSource, /dbOptions\.filter\(\(option\) => isDesktop \|\| !desktopOnlyDbOptionValues\.has\(option\.value\)\)/);
});

test("web runtime can show driver install hints", () => {
  assert.match(connectionDialogSource, /showAgentDriverInstallHint\(form\.value\.db_type, agentDrivers\.value, selectedType\.value\)/);
  assert.doesNotMatch(connectionDialogSource, /isDesktop &&\s+showAgentDriverInstallHint/);
});

test("driver store uses the shared API instead of direct Tauri calls", () => {
  assert.doesNotMatch(appSource, /@tauri-apps\/api\/core/);
  assert.match(appSource, /api\.listInstalledAgents\(/);
  assert.doesNotMatch(driverStoreSource, /@tauri-apps\/api\/core/);
  assert.doesNotMatch(driverStoreSource, /@tauri-apps\/api\/event/);
  assert.match(driverStoreSource, /api\.listInstalledAgents/);
  assert.match(driverStoreSource, /api\.listenAgentInstallProgress/);
});

test("web runtime guards desktop-only file drop wiring", () => {
  const fileDropSource = readFileSync("apps/desktop/src/composables/useFileDrop.ts", "utf8");
  assert.match(fileDropSource, /if \(!isTauriRuntime\(\)\) return;/);
  assert.match(fileDropSource, /api\.buildDroppedFilePreviewSql\(\{ path \}\)/);
});

test("web runtime downloads database export through HTTP endpoint", () => {
  assert.match(exportDialogSource, /\/api\/export\/database\/download\/\$\{encodeURIComponent\(exportId\.value\)\}/);
  assert.match(exportDialogSource, /anchor\.download = filePath\.split/);
});

test("web server stores browser exports under the data dir before download", () => {
  const exportRouteSource = readFileSync("crates/dbx-web/src/routes/database_export.rs", "utf8");
  assert.match(exportRouteSource, /requested\.starts_with\("__web_export_"\)/);
  assert.match(exportRouteSource, /state\.data_dir\.join\("exports"\)\.join\(format!/);
  assert.match(exportRouteSource, /canonicalize\(path\)/);
  assert.match(exportRouteSource, /remove\(&export_id\)/);
  assert.match(exportRouteSource, /remove_file\(&download\.path\)/);
});

test("web server exposes database export download route", () => {
  const webMainSource = readFileSync("crates/dbx-web/src/main.rs", "utf8");
  const exportRouteSource = readFileSync("crates/dbx-web/src/routes/database_export.rs", "utf8");
  assert.match(webMainSource, /\/export\/database\/download\/\{exportId\}/);
  assert.match(exportRouteSource, /CONTENT_DISPOSITION/);
  assert.match(exportRouteSource, /application\/sql; charset=utf-8/);
});

test("web runtime uses the shared uuid helper instead of direct randomUUID calls", () => {
  const directRandomUuidCalls = appSourceFiles("apps/desktop/src")
    .filter((path) => path !== "apps/desktop/src/lib/utils.ts")
    .filter((path) => readFileSync(path, "utf8").includes("crypto.randomUUID("));

  assert.deepEqual(directRandomUuidCalls, []);
});
