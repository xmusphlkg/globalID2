import { signSubscriptionToken, secureTextEqual, verifySubscriptionToken } from "./security.ts";

import { HttpError } from "./http.ts";

import type { Env } from "../types.ts";

export async function createSignedToken(
  env: Env,
  kind: "confirm" | "unsubscribe" | "manage",
  subscriptionId: string,
  ttlSeconds: number,
): Promise<string> {
  if (kind === "manage") {
    const subscription = await env.DB.prepare("SELECT management_generation FROM subscriptions WHERE id = ?")
      .bind(subscriptionId).first<{ management_generation: string }>();
    if (!subscription) throw new HttpError(404, "subscription_not_found");
    return signSubscriptionToken(signingSecret(env), kind, subscriptionId, ttlSeconds, Date.now(), subscription.management_generation);
  }
  return signSubscriptionToken(signingSecret(env), kind, subscriptionId, ttlSeconds);
}

export async function verifySignedToken(
  env: Env,
  token: string,
  kind: "confirm" | "unsubscribe" | "manage",
): Promise<{ subscriptionId: string; version?: string } | null> {
  const parsed = await verifySubscriptionToken(signingSecret(env), token, kind);
  if (!parsed || kind !== "manage") return parsed;
  if (typeof parsed.version !== "string") return null;
  const subscription = await env.DB.prepare("SELECT management_generation FROM subscriptions WHERE id = ?")
    .bind(parsed.subscriptionId).first<{ management_generation: string }>();
  return subscription && await secureTextEqual(parsed.version, subscription.management_generation) ? parsed : null;
}

export function signingSecret(env: Env): string {
  if (!env.TOKEN_SIGNING_SECRET) {
    throw new HttpError(500, "TOKEN_SIGNING_SECRET_not_configured");
  }
  return env.TOKEN_SIGNING_SECRET;
}
