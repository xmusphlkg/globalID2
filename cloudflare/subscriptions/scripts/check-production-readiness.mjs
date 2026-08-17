import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { statSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const helper = join(root, "scripts", "wrangler-env.sh");
const fixtureEnvironment = {
  ...process.env,
  CLOUDFLARE_API_TOKEN: "",
  CLOUDFLARE_ACCOUNT_ID: "0123456789abcdef0123456789abcdef",
  SUBSCRIPTIONS__WORKER_NAME: "globalid-subscriptions",
  SUBSCRIPTIONS__COMPATIBILITY_DATE: "2026-08-17",
  SUBSCRIPTIONS__PUBLIC_BASE_URL: "https://subscriptions.example.invalid",
  SUBSCRIPTIONS__ALLOWED_ORIGINS: "https://www.example.invalid",
  SUBSCRIPTIONS__SITUATION_PUBLIC_ORIGINS: "https://www.example.invalid",
  SUBSCRIPTIONS__D1_BINDING: "DB",
  SUBSCRIPTIONS__D1_DATABASE_NAME: "globalid-subscriptions-production",
  SUBSCRIPTIONS__D1_DATABASE_ID: "00000000-0000-4000-8000-000000000001",
  SUBSCRIPTIONS__WORKERS_DEV: "false",
  SUBSCRIPTIONS__DEBUG_RETURN_TOKENS: "false",
  SUBSCRIPTIONS__SITUATION_ALERT_QUEUE_NAME: "globalid-situation-alerts",
  SUBSCRIPTIONS__SITUATION_ALERT_DEAD_LETTER_QUEUE: "globalid-situation-alerts-dlq",
  SUBSCRIPTIONS__SMTP_HOST: "",
  SUBSCRIPTIONS__SMTP_FROM_EMAIL: "",
  AUTOMATION__SMTP_HOST: "",
  AUTOMATION__SMTP_FROM_EMAIL: "",
  SUBSCRIPTIONS__ALLOW_DEPLOY: "",
  SUBSCRIPTIONS__ALLOW_REMOTE_MIGRATION: "",
  SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC: "",
  SUBSCRIPTIONS__ALLOW_SECRET_SYNC: "",
};

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function assertCurrentCompatibilityDate(dateText) {
  const date = new Date(`${dateText}T00:00:00Z`);
  assert.ok(Number.isFinite(date.valueOf()), "compatibility_date must be valid");
  const ageDays = (Date.now() - date.valueOf()) / 86_400_000;
  assert.ok(ageDays >= 0, "compatibility_date must not be in the future");
  assert.ok(ageDays <= 180, "compatibility_date must be no more than 180 days old");
}

const typesConfig = readJson(join(root, "wrangler.types.jsonc"));
assert.equal(typesConfig.$schema, "./node_modules/wrangler/config-schema.json");
assert.equal(typesConfig.main, "src/index.ts");
assert.deepEqual(typesConfig.compatibility_flags, ["nodejs_compat"]);
assertCurrentCompatibilityDate(typesConfig.compatibility_date);
assert.equal(typesConfig.d1_databases?.[0]?.binding, "DB");
assert.equal(typesConfig.queues?.producers?.[0]?.binding, "SITUATION_ALERT_QUEUE");
assert.equal(typesConfig.observability?.enabled, true);
assert.equal(typesConfig.observability?.logs?.invocation_logs, true);
assert.equal(typesConfig.observability?.traces?.enabled, true);
assert.equal(typesConfig.send_email, undefined, "native Email Sending must be an explicit future adoption");

const generatedTypes = readFileSync(join(root, "worker-configuration.d.ts"), "utf8");
assert.match(generatedTypes, /DB: D1Database;/);
assert.match(generatedTypes, /SITUATION_ALERT_QUEUE: Queue;/);
assert.match(generatedTypes, /interface CloudflareBindings extends/);

const migrationNames = readdirSync(join(root, "migrations"))
  .filter((name) => /^\d{4}_.+\.sql$/.test(name))
  .sort();
assert.ok(migrationNames.length > 0, "at least one D1 migration is required");
migrationNames.forEach((name, index) => {
  assert.equal(Number(name.slice(0, 4)), index + 1, `migration sequence gap at ${name}`);
});

const configPath = execFileSync(helper, ["config-path", "production"], {
  cwd: root,
  env: fixtureEnvironment,
  encoding: "utf8",
}).trim();
const generated = readJson(configPath);
assert.equal(statSync(configPath).mode & 0o777, 0o600, "generated config must be owner-only");
assert.equal(generated.name, "globalid-subscriptions-local");
assert.equal(generated.vars.ENVIRONMENT, "local");
assert.equal(generated.vars.PUBLIC_BASE_URL, "http://localhost:8787");
assert.equal(generated.d1_databases[0].database_name, "globalid-subscriptions-local");
assert.equal(generated.d1_databases[0].database_id, undefined);
assert.equal(generated.env.production.name, "globalid-subscriptions");
assert.equal(generated.env.production.workers_dev, false);
assert.equal(generated.env.production.vars.ENVIRONMENT, "production");
assert.equal(generated.env.production.d1_databases[0].database_id, fixtureEnvironment.SUBSCRIPTIONS__D1_DATABASE_ID);
assert.equal(generated.env.production.queues.producers[0].binding, "SITUATION_ALERT_QUEUE");
assert.equal(generated.env.production.triggers.crons[0], "*/5 * * * *");

const serializedConfig = JSON.stringify(generated);
for (const forbidden of [
  "TOKEN_SIGNING_SECRET",
  "ADMIN_API_TOKEN",
  "SITUATION_ALERT_INGEST_TOKEN",
  "TURNSTILE_SECRET_KEY",
  "SMTP_USERNAME",
  "SMTP_PASSWORD",
]) {
  assert.equal(serializedConfig.includes(forbidden), false, `${forbidden} must not enter Wrangler config`);
}

for (const [command, gate] of [
  ["deploy", "SUBSCRIPTIONS__ALLOW_DEPLOY"],
  ["migrate-remote", "SUBSCRIPTIONS__ALLOW_REMOTE_MIGRATION"],
  ["sync-options-remote", "SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC"],
  ["sync-secrets", "SUBSCRIPTIONS__ALLOW_SECRET_SYNC"],
]) {
  const result = spawnSync(helper, [command, "production"], {
    cwd: root,
    env: { ...fixtureEnvironment, CLOUDFLARE_API_TOKEN: "fixture-token" },
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0, `${command} must be fail-closed without its gate`);
  assert.match(result.stderr, new RegExp(`${gate} must equal 'production'`));
}

const stagingPath = execFileSync(helper, ["config-path", "staging"], {
  cwd: root,
  env: {
    ...fixtureEnvironment,
    SUBSCRIPTIONS__D1_DATABASE_NAME: "globalid-subscriptions-staging",
    SUBSCRIPTIONS__WORKERS_DEV: "true",
  },
  encoding: "utf8",
}).trim();
const staging = readJson(stagingPath);
assert.equal(staging.env.staging.name, "globalid-subscriptions-staging");
assert.equal(staging.env.staging.workers_dev, true);
assert.equal(staging.env.staging.vars.ENVIRONMENT, "staging");
assert.equal(staging.env.staging.d1_databases[0].database_name, "globalid-subscriptions-staging");
assert.equal(staging.env.production, undefined);

console.log(JSON.stringify({
  ok: true,
  compatibility_date: typesConfig.compatibility_date,
  migrations: migrationNames.length,
  environment_isolation: true,
  remote_guards: true,
  secret_config_leakage: false,
}));
