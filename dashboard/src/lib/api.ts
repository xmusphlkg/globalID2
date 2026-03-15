const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "/api/v1").replace(/\/$/, "");
const WS_BASE = (process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/api/v1").replace(/\/$/, "");

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

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(joinPath(API_BASE, path), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    throw new ApiError(res.status, text);
  }
  return res.json();
}

export function wsUrl(path: string): string {
  return joinPath(WS_BASE, path);
}
