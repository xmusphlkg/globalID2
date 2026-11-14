import { type JsonValue } from "./http.ts";

import { maintainSituationAlerts } from "../handlers/situation-alerts.ts";
import type { Env } from "../types.ts";

export async function runMaintenance(env: Env, expirePending: (env: Env) => Promise<Record<string, JsonValue>>): Promise<JsonValue> {
  const now = new Date().toISOString();
  const pending = await expirePending(env);
  await env.DB.prepare("DELETE FROM preference_link_limits WHERE expires_at < ?").bind(now).run();

  const situationAlerts = await maintainSituationAlerts(env);

  return {
    ok: true,
    ran_at: now,
    ...pending,
    situation_alerts: situationAlerts,
  };
}
