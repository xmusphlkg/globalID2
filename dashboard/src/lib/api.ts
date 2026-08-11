const API_BASE = "/api/v1";
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
    public code = `http_${status}`,
    public requestId?: string,
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
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/problem+json")) {
      const problem = await res.json().catch(() => null) as null | {
        detail?: string;
        title?: string;
        code?: string;
        request_id?: string;
      };
      throw new ApiError(
        res.status,
        problem?.detail || problem?.title || "Request failed",
        problem?.code,
        problem?.request_id,
      );
    }
    const text = await res.text().catch(() => "Unknown error");
    throw new ApiError(res.status, text, `http_${res.status}`, res.headers.get("x-request-id") || undefined);
  }
  return res;
}

export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ResponseMeta {
  request_id?: string | null;
  pagination?: PaginationMeta | null;
}

interface ApiEnvelope<T> {
  data: T;
  meta: ResponseMeta;
}

function isEnvelope<T>(value: unknown): value is ApiEnvelope<T> {
  return !!value && typeof value === "object" && "data" in value && "meta" in value;
}

export async function apiFetch<T>(path: string, init?: ApiFetchInit): Promise<T> {
  const res = await performApiFetch(path, init);
  if (res.status === 204) return undefined as T;
  const payload = await res.json() as T | ApiEnvelope<T>;
  return isEnvelope<T>(payload) ? payload.data : payload;
}

export async function apiFetchWithMeta<T>(
  path: string,
  init?: ApiFetchInit,
): Promise<{ data: T; meta: ResponseMeta; headers: Headers }> {
  const res = await performApiFetch(path, init);
  const payload = await res.json() as T | ApiEnvelope<T>;
  if (isEnvelope<T>(payload)) return { data: payload.data, meta: payload.meta, headers: res.headers };
  return { data: payload, meta: { request_id: res.headers.get("x-request-id") }, headers: res.headers };
}

export function eventStreamUrl(): string {
  return joinPath(API_BASE, "/events/stream");
}
