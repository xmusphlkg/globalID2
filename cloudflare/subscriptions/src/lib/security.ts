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
  if (!(await secureTextEqual(signature, expected))) return null;

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

export async function secureTextEqual(provided: string, expected: string): Promise<boolean> {
  const [providedHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", TEXT.encode(provided)),
    crypto.subtle.digest("SHA-256", TEXT.encode(expected)),
  ]);
  if (typeof crypto.subtle.timingSafeEqual === "function") {
    return crypto.subtle.timingSafeEqual(providedHash, expectedHash);
  }

  // Node's Web Crypto test runtime does not yet expose the Workers extension.
  const left = new Uint8Array(providedHash);
  const right = new Uint8Array(expectedHash);
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference === 0;
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
