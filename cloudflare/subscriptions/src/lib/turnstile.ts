import { HttpError } from "./http.ts";
import { isRecord } from "./input.ts";
import type { Env, Payload } from "../types.ts";

export type TurnstileAction = "subscribe" | "manage_link";

export function publicTurnstile(env: Env): { enabled: true; sitekey: string } | { enabled: false } {
  if (env.TURNSTILE_ENABLED === "false") return { enabled: false };
  const sitekey = env.TURNSTILE_SITE_KEY?.trim();
  return sitekey ? { enabled: true, sitekey } : { enabled: false };
}

export async function verifyTurnstileIfConfigured(
  payload: Payload, request: Request, env: Env, action: TurnstileAction = "subscribe",
  fetcher: typeof fetch = fetch,
): Promise<void> {
  if (env.TURNSTILE_ENABLED === "false") return;
  if (!env.TURNSTILE_SITE_KEY && !env.TURNSTILE_SECRET_KEY) return;
  const hostnames = new Set((env.TURNSTILE_HOSTNAMES || "").split(",").map(value => value.trim()).filter(Boolean));
  if (!env.TURNSTILE_SECRET_KEY?.trim() || !env.TURNSTILE_SITE_KEY?.trim() || !hostnames.size
      || (env.ENVIRONMENT === "production" && (hostnames.has("localhost") || hostnames.has("127.0.0.1")))) {
    throw new HttpError(503, "turnstile_not_configured");
  }
  const token = payload.turnstileToken || payload["cf-turnstile-response"];
  if (typeof token !== "string" || !token.trim() || token.length > 2048) {
    throw new HttpError(403, "turnstile_token_required");
  }
  const body = new URLSearchParams({ secret: env.TURNSTILE_SECRET_KEY, response: token });
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) body.set("remoteip", ip);
  let result: unknown;
  try {
    const response = await fetcher("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body, signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error("siteverify_unavailable");
    result = await response.json();
  } catch {
    throw new HttpError(503, "turnstile_unavailable");
  }
  if (!isRecord(result) || result.success !== true || result.action !== action
      || typeof result.hostname !== "string" || !hostnames.has(result.hostname)) {
    throw new HttpError(403, "turnstile_verification_failed");
  }
}
