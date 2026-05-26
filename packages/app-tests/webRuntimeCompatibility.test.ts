import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { strict as assert } from "node:assert";
import test from "node:test";

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = `${dir}/${entry}`;
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|vue|md|mdx|json|ya?ml)$/.test(entry) ? [path] : [];
  });
}

test("repo no longer ships the src-tauri desktop shell", () => {
  assert.equal(existsSync("src-tauri"), false);
});

test("frontend source is free of @tauri-apps imports", () => {
  const offenders = sourceFiles("apps/desktop/src").filter((path) =>
    readFileSync(path, "utf8").includes("@tauri-apps"),
  );
  assert.deepEqual(offenders, []);
});

test("package scripts and docs advertise the web-only runtime", () => {
  const packageJson = readFileSync("package.json", "utf8");
  const readme = readFileSync("README.md", "utf8");
  const readmeZh = readFileSync("README.zh-CN.md", "utf8");

  assert.doesNotMatch(packageJson, /@tauri-apps/);
  assert.doesNotMatch(packageJson, /dev:tauri/);
  assert.doesNotMatch(readme, /dev:tauri|tauri build|src-tauri/);
  assert.doesNotMatch(readmeZh, /dev:tauri|tauri build|src-tauri/);
});

test("deploy Dockerfile builds the FastAPI web image instead of Rust/Tauri artifacts", () => {
  const dockerfile = readFileSync("deploy/Dockerfile", "utf8");

  assert.match(dockerfile, /FROM python:3\.12-slim/);
  assert.match(dockerfile, /COPY backend\/app \.\/app/);
  assert.doesNotMatch(dockerfile, /cargo zigbuild|src-tauri|dbx-web/);
});

test("vite dev server proxies all API traffic to the FastAPI backend", () => {
  const viteConfig = readFileSync("apps/desktop/vite.config.ts", "utf8");

  assert.match(viteConfig, /target: "http:\/\/localhost:8000"/);
  assert.doesNotMatch(viteConfig, /TAURI_DEV_HOST|TAURI_ENV_ARCH|localhost:4224/);
});
