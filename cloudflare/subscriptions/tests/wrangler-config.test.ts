import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

test("production readiness configuration is fail-closed and secret-free", () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const output = execFileSync(
    process.execPath,
    [join(root, "scripts", "check-production-readiness.mjs")],
    { cwd: root, encoding: "utf8" },
  ).trim();
  const report = JSON.parse(output.split("\n").at(-1) ?? "{}");
  assert.deepEqual(report, {
    ok: true,
    compatibility_date: "2026-08-17",
    migrations: 9,
    environment_isolation: true,
    remote_guards: true,
    secret_config_leakage: false,
  });
});
