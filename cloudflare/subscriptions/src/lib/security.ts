const TEXT = new TextEncoder();

export type SubscriptionTokenKind = "confirm" | "unsubscribe";

export async function signSubscriptionToken(
  secret: string,
  kind: SubscriptionTokenKind,
  subscriptionId: string,
  ttlSeconds: number,
  nowMs = Date.now(),
): Promise<string> {
  const payload = base64UrlEncode(JSON.stringify({
    k: kind,
    sid: subscriptionId,
    exp: Math.floor(nowMs / 1000) + ttlSeconds,
  }));
  const signature = await hmacSha256(payload, secret);
  return `${payload}.${signature}`;
}

export async function verifySubscriptionToken(
  secret: string,
  token: string,
  kind: SubscriptionTokenKind,
  nowMs = Date.now(),
): Promise<{ subscriptionId: string } | null> {
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return null;
  const expected = await hmacSha256(payload, secret);
  if (!constantTimeEqual(signature, expected)) return null;

  try {
    const parsed = JSON.parse(base64UrlDecode(payload)) as { k?: string; sid?: string; exp?: number };
    if (parsed.k !== kind || !parsed.sid || !parsed.exp) return null;
    if (parsed.exp < Math.floor(nowMs / 1000)) return null;
    return { subscriptionId: parsed.sid };
  } catch {
    return null;
  }
}

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", TEXT.encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function hmacSha256(value: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    TEXT.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, TEXT.encode(value));
  return base64UrlEncode(signature);
}

function base64UrlEncode(value: string | ArrayBuffer): string {
  let binary = "";
  if (typeof value === "string") {
    binary = value;
  } else {
    for (const byte of new Uint8Array(value)) {
      binary += String.fromCharCode(byte);
    }
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlDecode(value: string): string {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return atob(padded);
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let index = 0; index < a.length; index += 1) {
    result |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return result === 0;
}
