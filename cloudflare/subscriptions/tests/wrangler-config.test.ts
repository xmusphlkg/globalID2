import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
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
    migrations: 17,
    environment_isolation: true,
    remote_guards: true,
    secret_config_leakage: false,
  });
});

test("remote availability flags require valid activation watermarks", () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const helper = join(root, "scripts", "wrangler-env.sh");
  const baseline = {
    ...process.env,
    SUBSCRIPTIONS__WORKER_NAME: "globalid-subscriptions",
    SUBSCRIPTIONS__PUBLIC_BASE_URL: "https://subscriptions.example.invalid",
    SUBSCRIPTIONS__ALLOWED_ORIGINS: "https://www.example.invalid",
    SUBSCRIPTIONS__D1_DATABASE_NAME: "example",
    SUBSCRIPTIONS__D1_DATABASE_ID: "00000000-0000-0000-0000-000000000001",
    SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED: "false", SUBSCRIPTIONS__RESEARCH_DIGEST_START_WEEK: "",
    SUBSCRIPTIONS__REPORT_DIGEST_ENABLED: "false", SUBSCRIPTIONS__REPORT_DIGEST_START_WEEK: "",
    SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED: "false", SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE: "",
    SUBSCRIPTIONS__SES_SNS_FEEDBACK_ENABLED: "false",
    SUBSCRIPTIONS__SITUATION_ALERT_QUEUE_NAME: "", SUBSCRIPTIONS__SITUATION_ALERT_DEAD_LETTER_QUEUE: "",
  };
  const check = (extra: Record<string, string>) => spawnSync(helper, ["config-path", "production"], {
    cwd: root, env: { ...baseline, ...extra }, encoding: "utf8",
  });
  for (const name of ["RESEARCH_DIGEST", "REPORT_DIGEST"]) {
    for (const value of ["", "2026-W00", "2026-W54", "2026-01"]) {
      const result = check({ [`SUBSCRIPTIONS__${name}_ENABLED`]: "true", [`SUBSCRIPTIONS__${name}_START_WEEK`]: value });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /must be a valid ISO week/);
    }
    assert.equal(check({ [`SUBSCRIPTIONS__${name}_ENABLED`]: "true", [`SUBSCRIPTIONS__${name}_START_WEEK`]: "2026-W46" }).status, 0);
  }
  for (const value of ["", "2026-02-30", "20261104"]) {
    const result = check({ SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED: "true", SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE: value });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /must be YYYY-MM-DD/);
  }
  assert.equal(check({ SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED: "true", SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE: "2026-11-04" }).status, 0);
});
