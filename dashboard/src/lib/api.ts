const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "/api/v1").replace(/\/$/, "");
const WS_BASE = (process.env.NEXT_PUBLIC_WS_URL || "/api/v1").replace(/\/$/, "");
const API_TIMEOUT_MS = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS || 10000);
type ApiFetchInit = RequestInit & {
  timeoutMs?: number;
};

function joinPath(base: string, path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${base}${normalizedPath}`;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function performApiFetch(path: string, init?: ApiFetchInit): Promise<Response> {
  const timeoutMs = init?.timeoutMs ?? API_TIMEOUT_MS;
  const { timeoutMs: _timeoutMs, ...requestInit } = init ?? {};
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  const mergedSignal = requestInit.signal ?? controller.signal;

  let res: Response;
  try {
    res = await fetch(joinPath(API_BASE, path), {
      ...requestInit,
      signal: mergedSignal,
      headers: { "Content-Type": "application/json", ...requestInit.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(408, `Request timeout after ${timeoutMs}ms`);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new ApiError(res.status, text);
  }
  return res;
}

export async function apiFetch<T>(path: string, init?: ApiFetchInit): Promise<T> {
  const res = await performApiFetch(path, init);
  return res.json();
}

export async function apiFetchWithHeaders<T>(
  path: string,
  init?: ApiFetchInit,
): Promise<{ data: T; headers: Headers }> {
  const res = await performApiFetch(path, init);
  const data = await res.json() as T;
  return { data, headers: res.headers };
}

export function wsUrl(path: string): string {
  const url = joinPath(WS_BASE, path);
  if (/^wss?:\/\//i.test(url)) {
    return url;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const normalizedUrl = url.startsWith("/") ? url : `/${url}`;
  return `${protocol}//${window.location.host}${normalizedUrl}`;
}
