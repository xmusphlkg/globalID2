export const SUPPORTED_LOCALES = ["en", "zh", "ja", "ko", "es", "fr", "de", "pt"] as const;

const LOCALES = new Set<string>(SUPPORTED_LOCALES);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseJsonObject(value: string): Record<string, unknown> {
  if (!value.trim()) return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function normalizeLocale(value: string, fallback: string): string {
  const normalized = value.trim().toLowerCase().replace(/_/g, "-");
  if (LOCALES.has(normalized)) return normalized;
  const base = normalized.split("-")[0];
  if (LOCALES.has(base)) return base;
  return fallback;
}

export function normalizeEmail(value: string): string {
  const email = value.trim().toLowerCase();
  if (!email || email.length > 254) return "";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return "";
  return email;
}

export function normalizeCode(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9_:-]/g, "").slice(0, 80);
}

export function boundedText(value: string, fallback: string, maxLength: number): string {
  const text = value.trim();
  return text ? text.slice(0, maxLength) : fallback;
}

export function valueAsString(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return "";
}

export function toArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => toArray(item));
  }
  if (typeof value === "string") {
    return value.split(",").map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
