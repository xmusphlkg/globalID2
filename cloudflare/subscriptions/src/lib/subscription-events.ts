import { boundedText } from "./input.ts";
import { sha256Hex } from "./security.ts";

import { type JsonValue } from "./http.ts";

import type { Env } from "../types.ts";

import { signingSecret } from "./token-service.ts";
export async function recordEvent(env: Env, input: {
  subscriberId?: string;
  subscriptionId?: string;
  eventType: string;
  actorType: string;
  request: Request;
  metadata?: JsonValue;
}): Promise<void> {
  const now = new Date().toISOString();
  const ip = input.request.headers.get("CF-Connecting-IP") || "";
  const ipHash = ip ? await sha256Hex(`${signingSecret(env)}:ip:${ip}`) : null;
  await env.DB.prepare(
    `INSERT INTO subscription_events (
       id, subscriber_id, subscription_id, event_type, actor_type, ip_hash,
       user_agent, metadata_json, created_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    crypto.randomUUID(),
    input.subscriberId || null,
    input.subscriptionId || null,
    input.eventType,
    input.actorType,
    ipHash,
    boundedText(input.request.headers.get("user-agent") || "", "", 500),
    input.metadata ? JSON.stringify(input.metadata) : null,
    now,
  ).run();
}
