import { cookies } from "next/headers";

import {
  PANEL_SESSION_COOKIE,
  PANEL_SESSION_MAX_AGE_SECONDS,
  checkLoginRateLimit,
  clearLoginFailures,
  extractClientKey,
  isHttpsRequest,
  issueSession,
  panelPassword,
  passwordMatches,
  recordFailedLoginAttempt,
  sessionValid,
} from "@/lib/panel-session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function currentToken(): Promise<string | undefined> {
  const jar = await cookies();
  return jar.get(PANEL_SESSION_COOKIE)?.value;
}

export async function GET() {
  const required = Boolean(panelPassword());
  return Response.json({
    required,
    ok: !required || sessionValid(await currentToken()),
  });
}

export async function POST(request: Request) {
  if (!panelPassword()) return Response.json({ ok: true, required: false });
  const clientKey = extractClientKey(request);
  const rateLimit = checkLoginRateLimit(clientKey);
  if (rateLimit.limited) {
    return Response.json(
      { ok: false, required: true, error: "Too many failed login attempts" },
      { status: 429, headers: { "Retry-After": String(rateLimit.retryAfterSeconds) } },
    );
  }
  const body = await request.json().catch(() => null) as { password?: unknown } | null;
  const password = body && typeof body.password === "string" ? body.password : "";
  if (!passwordMatches(password)) {
    recordFailedLoginAttempt(clientKey);
    return Response.json({ ok: false, required: true }, { status: 401 });
  }
  clearLoginFailures(clientKey);
  const jar = await cookies();
  jar.set(PANEL_SESSION_COOKIE, issueSession(), {
    httpOnly: true,
    sameSite: "lax",
    secure: isHttpsRequest(request),
    path: "/",
    maxAge: PANEL_SESSION_MAX_AGE_SECONDS,
  });
  return Response.json({ ok: true, required: true });
}

export async function DELETE(request?: Request) {
  const jar = await cookies();
  jar.set(PANEL_SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: request ? isHttpsRequest(request) : false,
    path: "/",
    maxAge: 0,
  });
  return Response.json({ ok: true, required: Boolean(panelPassword()) });
}
