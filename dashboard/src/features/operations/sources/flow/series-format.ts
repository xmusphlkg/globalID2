export function formatSeriesMetadata(value: unknown, fallback = "Not specified") {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  return normalized ? normalized.replaceAll("_", " ") : fallback;
}
