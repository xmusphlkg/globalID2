import { cookies } from "next/headers";

import {
  PANEL_SESSION_COOKIE,
  PANEL_SESSION_MAX_AGE_SECONDS,
  issueSession,
  panelPassword,
  passwordMatches,
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
  const body = await request.json().catch(() => null) as { password?: unknown } | null;
  const password = body && typeof body.password === "string" ? body.password : "";
  if (!passwordMatches(password)) {
    return Response.json({ ok: false, required: true }, { status: 401 });
  }
  const jar = await cookies();
  jar.set(PANEL_SESSION_COOKIE, issueSession(), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: PANEL_SESSION_MAX_AGE_SECONDS,
  });
  return Response.json({ ok: true, required: true });
}

export async function DELETE() {
  const jar = await cookies();
  jar.set(PANEL_SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return Response.json({ ok: true, required: Boolean(panelPassword()) });
}
