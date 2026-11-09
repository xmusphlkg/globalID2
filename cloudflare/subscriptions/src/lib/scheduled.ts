import type { Env } from "../types.ts";

export const SCHEDULED_TASKS = ["maintenance", "confirmation", "management", "campaign", "situation", "feedback"] as const;
export type ScheduledTask = typeof SCHEDULED_TASKS[number];

/** Each task gets its own invocation and D1 budget. All endpoints require the
 * admin bearer token; redirects and unexpected public-base URL forms fail closed. */
export async function dispatchScheduledTasks(
  env: Env,
  send: typeof fetch = fetch,
): Promise<void> {
  const token = env.ADMIN_API_TOKEN || "";
  const raw = env.PUBLIC_BASE_URL || "";
  if (!token || !raw) throw new Error("scheduled_dispatch_not_configured");
  const base = new URL(raw);
  const local = env.ENVIRONMENT === "local" && base.protocol === "http:" && ["localhost", "127.0.0.1"].includes(base.hostname);
  if ((!local && base.protocol !== "https:") || base.username || base.password || base.pathname !== "/" || base.search || base.hash) {
    throw new Error("invalid_scheduled_dispatch_origin");
  }
  for (const task of SCHEDULED_TASKS) {
    try {
      const response = await send(`${base.origin}/api/internal/scheduled/${task}`, {
        method: "POST", headers: { authorization: `Bearer ${token}`, "user-agent": "GIDS-Release-Verifier/1.0" }, redirect: "error",
        signal: AbortSignal.timeout(180_000),
      });
      if (!response.ok) throw new Error(`http_${response.status}`);
      await response.body?.cancel();
    } catch (error) {
      // Avoid logging request URLs, headers, or provider responses.
      console.error(JSON.stringify({ message: "subscription_scheduled_task_failed", task,
        error: error instanceof Error && /^http_\d+$/.test(error.message) ? error.message : "request_failed" }));
    }
  }
}
