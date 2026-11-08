import { normalizeEmail } from "./input.ts";
export interface DeliveryPolicy { DELIVERY_MODE?: string; PILOT_RECIPIENTS?: string }
export function deliveryMode(env: DeliveryPolicy): "pilot" | "full" {
  return env.DELIVERY_MODE === "full" ? "full" : "pilot";
}
export function pilotRecipients(env: DeliveryPolicy): string[] {
  return [...new Set((env.PILOT_RECIPIENTS || "").split(",").map(normalizeEmail).filter(Boolean))];
}
export function recipientAllowed(env: DeliveryPolicy, address: string): boolean {
  const normalized = normalizeEmail(address);
  return !!normalized && (deliveryMode(env) === "full" || pilotRecipients(env).includes(normalized));
}
// Filter before claiming durable jobs so excluded recipients remain queued.
export function recipientSql(env: DeliveryPolicy, column: string): { sql: string; binds: string[] } {
  if (deliveryMode(env) === "full") return { sql: "1=1", binds: [] };
  const recipients = pilotRecipients(env);
  return recipients.length ? { sql: `LOWER(TRIM(${column})) IN (${recipients.map(() => "?").join(",")})`, binds: recipients }
    : { sql: "1=0", binds: [] };
}
