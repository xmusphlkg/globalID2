import { escapeHtml } from "./input.ts";

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue | undefined };

export interface HttpEnv {
  ALLOWED_ORIGINS?: string;
  ADMIN_API_TOKEN?: string;
  PUBLIC_BASE_URL?: string;
}

export class HttpError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function requireAdmin(request: Request, env: HttpEnv): void {
  if (!env.ADMIN_API_TOKEN) {
    throw new HttpError(500, "ADMIN_API_TOKEN_not_configured");
  }
  if (request.headers.get("authorization") !== `Bearer ${env.ADMIN_API_TOKEN}`) {
    throw new HttpError(401, "unauthorized");
  }
}

export function publicBaseUrl(request: Request, env: HttpEnv): string {
  return (env.PUBLIC_BASE_URL || new URL(request.url).origin).replace(/\/+$/, "");
}

export function htmlPage(title: string, message: string): Response {
  const body = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${escapeHtml(title)} | GIDS</title>
    <style>
      body{margin:0;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#e2e8f0}
      main{min-height:100vh;display:grid;place-items:center;padding:24px}
      section{max-width:560px}
      h1{font-size:32px;line-height:1.1;margin:0 0 12px}
      p{color:#94a3b8;font-size:16px;line-height:1.6;margin:0}
    </style>
  </head>
  <body>
    <main>
      <section>
        <h1>${escapeHtml(title)}</h1>
        <p>${escapeHtml(message)}</p>
      </section>
    </main>
  </body>
</html>`;
  return new Response(body, {
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

export function json(data: JsonValue, request: Request, env: HttpEnv, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...corsHeaders(request, env),
    },
  });
}

export function corsHeaders(request: Request, env: HttpEnv): HeadersInit {
  const origin = request.headers.get("origin") || "";
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map((item) => item.trim()).filter(Boolean);
  const allowOrigin = allowed.length === 0 || allowed.includes(origin) ? (origin || "*") : allowed[0];
  return {
    "access-control-allow-origin": allowOrigin,
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,authorization",
    "access-control-max-age": "86400",
  };
}

export function configInt(value: string | undefined, fallback: number, min: number, max: number): number {
  const text = value?.trim();
  if (!text) return fallback;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

export function pick(value: string, allowed: Set<string>, fallback: string): string {
  const normalized = value.trim().toLowerCase();
  return allowed.has(normalized) ? normalized : fallback;
}
