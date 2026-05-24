import type { NextRequest } from "next/server";

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
  return headers;
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
  const params = await context.params;
  const path = (params.path || []).map(encodeURIComponent).join("/");
  const upstreamUrl = new URL(`/api/v1/${path}`, proxyTarget());
  upstreamUrl.search = request.nextUrl.search;

  const hasBody = !["GET", "HEAD"].includes(request.method.toUpperCase());
  const upstream = await fetch(upstreamUrl, {
    method: request.method,
    headers: requestHeaders(request),
    body: hasBody ? await request.arrayBuffer() : undefined,
    redirect: "manual",
  });

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
