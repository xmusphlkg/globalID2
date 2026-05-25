export function readSearchParam(
  params: URLSearchParams,
  key: string,
  fallback = "",
): string {
  return params.get(key) ?? fallback;
}

export function writeSearchParam(
  params: URLSearchParams,
  key: string,
  value: string | null | undefined,
): URLSearchParams {
  const next = new URLSearchParams(params);
  const normalized = (value ?? "").trim();
  if (normalized) {
    next.set(key, normalized);
  } else {
    next.delete(key);
  }
  return next;
}

export function numberSearchParam(
  params: URLSearchParams,
  key: string,
): number | null {
  const value = params.get(key);
  if (!value) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
