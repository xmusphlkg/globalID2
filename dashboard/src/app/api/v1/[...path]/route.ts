import type { NextRequest } from "next/server";
import { randomUUID } from "node:crypto";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type ProxyContext = {
  params: Promise<{ path?: string[] }>;
};

const HOP_BY_HOP_HEADERS = [
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

function proxyTarget() {
  return (process.env.API_PROXY_TARGET || "http://127.0.0.1:8000").replace(/\/$/, "");
}

function requestHeaders(request: NextRequest) {
  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP_HEADERS) {
    headers.delete(name);
  }

  const dashboardApiKey = process.env.DASHBOARD_API_KEY?.trim();
  if (dashboardApiKey) {
    headers.set("x-dashboard-api-key", dashboardApiKey);
  }
  if (!headers.get("x-request-id")) headers.set("x-request-id", randomUUID());
  return headers;
}

function validateMutationOrigin(request: NextRequest) {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase())) return;
  const origin = request.headers.get("origin");
  if (!origin) return;
  let originHost: string;
  try {
    originHost = new URL(origin).host.toLowerCase();
  } catch {
    throw new Error("Cross-origin control-plane mutation rejected");
  }
  const requestHosts = [
    request.headers.get("host"),
    request.headers.get("x-forwarded-host")?.split(",")[0]?.trim(),
    request.nextUrl.host,
  ]
    .filter((value): value is string => Boolean(value))
    .map((value) => value.toLowerCase());
  if (!requestHosts.includes(originHost)) throw new Error("Cross-origin control-plane mutation rejected");
}

function responseHeaders(upstream: Response) {
  const headers = new Headers(upstream.headers);
  for (const name of HOP_BY_HOP_HEADERS) {
    headers.delete(name);
  }
  headers.delete("content-encoding");
  return headers;
}

async function proxyRequest(request: NextRequest, context: ProxyContext) {
  const requestId = request.headers.get("x-request-id") || randomUUID();
  try {
    validateMutationOrigin(request);
  } catch {
    return Response.json(
      {
        type: "https://globalid.dev/problems/origin_rejected",
        title: "Request rejected",
        status: 403,
        detail: "Control-plane mutations must be same-origin.",
        code: "origin_rejected",
        request_id: requestId,
      },
      { status: 403, headers: { "content-type": "application/problem+json", "x-request-id": requestId } },
    );
  }
  const params = await context.params;
  const path = (params.path || []).map(encodeURIComponent).join("/");
  const upstreamUrl = new URL(`/api/v1/${path}`, proxyTarget());
  upstreamUrl.search = request.nextUrl.search;

  const hasBody = !["GET", "HEAD"].includes(request.method.toUpperCase());
  const headers = requestHeaders(request);
  headers.set("x-request-id", requestId);
  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      signal: request.signal,
    });
  } catch {
    return Response.json(
      {
        type: "https://globalid.dev/problems/upstream_unavailable",
        title: "Control-plane API unavailable",
        status: 502,
        detail: "The dashboard could not reach the control-plane API.",
        code: "upstream_unavailable",
        request_id: requestId,
      },
      { status: 502, headers: { "content-type": "application/problem+json", "x-request-id": requestId } },
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders(upstream),
  });
}

export async function GET(request: NextRequest, context: ProxyContext) {
  return proxyRequest(request, context);
}

export async function POST(request: NextRequest, context: ProxyContext) {
  return proxyRequest(request, context);
}

export async function PUT(request: NextRequest, context: ProxyContext) {
  return proxyRequest(request, context);
}

export async function PATCH(request: NextRequest, context: ProxyContext) {
  return proxyRequest(request, context);
}

export async function DELETE(request: NextRequest, context: ProxyContext) {
  return proxyRequest(request, context);
}

export async function OPTIONS(request: NextRequest, context: ProxyContext) {
  return proxyRequest(request, context);
}
