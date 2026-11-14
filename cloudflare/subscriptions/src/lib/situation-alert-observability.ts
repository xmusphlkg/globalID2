import { type JsonValue } from "./http.ts";
import { boundedText } from "./input.ts";

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function deliveryErrorMessage(error: unknown): string {
  const redacted = errorMessage(error)
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[redacted-email]")
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, "[redacted-ip]");
  return boundedText(redacted, "smtp_delivery_failed", 500);
}

export function structuredLog(
  severity: "info" | "error",
  message: string,
  fields: Record<string, JsonValue>,
): void {
  const entry = JSON.stringify({ message, ...fields });
  if (severity === "error") console.error(entry);
  else console.log(entry);
}
