export interface ConfirmationRateLimit {
  allowed: boolean;
  retryAfterSeconds: number;
}

export function submissionRateLimited(count: unknown, limit: number): boolean {
  return Number(count || 0) >= limit;
}

export function confirmationRateLimit(
  count: unknown,
  oldest: string | null | undefined,
  limit: number,
  nowMs = Date.now(),
): ConfirmationRateLimit {
  if (Number(count || 0) < limit) return { allowed: true, retryAfterSeconds: 0 };
  const cutoffMs = nowMs - 10 * 60 * 1000;
  const parsedOldest = oldest ? Date.parse(oldest) : Number.NaN;
  const oldestMs = Number.isFinite(parsedOldest) ? parsedOldest : cutoffMs;
  return {
    allowed: false,
    retryAfterSeconds: Math.max(60, Math.ceil((oldestMs + 10 * 60 * 1000 - nowMs) / 1000)),
  };
}
