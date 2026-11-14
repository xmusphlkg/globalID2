import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import type { Env } from "../../src/types.ts";

/** Local SQLite adapter: all SQL is executed against disposable in-memory data. */
export function subscriptionFixture() {
  const sqlite = new DatabaseSync(":memory:");
  const migrations = new URL("../../migrations/", import.meta.url);
  for (const name of readdirSync(migrations).filter(name => name.endsWith(".sql")).sort()) {
    sqlite.exec(readFileSync(new URL(name, migrations), "utf8"));
  }
  const db = {
    prepare(query: string) {
      let values: SQLInputValue[] = [];
      const statement = {
        bind(...params: SQLInputValue[]) { values = params; return statement; },
        async first() { return sqlite.prepare(query).get(...values) || null; },
        async all() { return { results: sqlite.prepare(query).all(...values) }; },
        async run() { return { meta: { changes: Number(sqlite.prepare(query).run(...values).changes) } }; },
      };
      return statement;
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const result = [];
        for (const statement of statements) result.push(await statement.run());
        sqlite.exec("COMMIT");
        return result;
      } catch (error) { sqlite.exec("ROLLBACK"); throw error; }
    },
  };
  const now = new Date().toISOString();
  sqlite.prepare("INSERT INTO subscribers (id, locale, timezone, status, created_at, updated_at) VALUES ('reader', 'en', 'UTC', 'active', ?, ?)").run(now, now);
  sqlite.prepare("INSERT INTO subscriber_contacts (id, subscriber_id, channel, address, address_hash, status, created_at, updated_at) VALUES ('contact', 'reader', 'email', 'reader@example.test', 'test-hash', 'active', ?, ?)").run(now, now);
  sqlite.prepare("INSERT INTO subscriptions (id, subscriber_id, contact_id, list_id, status, frequency, management_generation, created_at, updated_at) VALUES ('subscription', 'reader', 'contact', 'list_reports', 'pending', 'weekly', 'generation-one', ?, ?)").run(now, now);
  sqlite.prepare("INSERT INTO confirmation_email_outbox (id, subscriber_id, contact_id, locale, source, request_ids_json, status, claim_token, created_at, updated_at) VALUES ('outbox', 'reader', 'contact', 'en', 'test', '[]', 'sending', 'claim-one', ?, ?)").run(now, now);
  // The adapter implements the D1 methods exercised here; platform-only methods
  // are deliberately absent so accidental use fails inside the local test.
  const env = { DB: db, DELIVERY_MODE: "full", TOKEN_SIGNING_SECRET: "local-fixture-secret", PUBLIC_BASE_URL: "https://worker.example.test" } as unknown as Env;
  return { sqlite, env };
}
