export type WorkerRoute =
  | { name: "health" }
  | { name: "subscription_options" }
  | { name: "create_subscription" }
  | { name: "confirm_subscription" }
  | { name: "unsubscribe" }
  | { name: "admin_audience" }
  | { name: "admin_stats" }
  | { name: "admin_subscriptions" }
  | { name: "admin_notifications"; operation: "list" | "create" }
  | { name: "admin_notification"; operation: "get" | "process"; campaignId: string }
  | { name: "situation_alert_ingest" }
  | { name: "admin_situation_alerts"; operation: "list" | "process" }
  | { name: "admin_maintenance" }
  | { name: "not_found" };

export function matchWorkerRoute(method: string, pathname: string): WorkerRoute {
  const path = normalizePath(pathname);
  if (method === "GET" && path === "/health") return { name: "health" };
  if (method === "GET" && path === "/api/subscriptions/options") return { name: "subscription_options" };
  if (method === "POST" && path === "/api/subscriptions") return { name: "create_subscription" };
  if (method === "GET" && path === "/api/subscriptions/confirm") return { name: "confirm_subscription" };
  if (method === "GET" && path === "/api/subscriptions/unsubscribe") return { name: "unsubscribe" };
  if (method === "POST" && path === "/api/internal/situation-alerts") return { name: "situation_alert_ingest" };
  if (method === "POST" && path === "/api/admin/audience") return { name: "admin_audience" };
  if (method === "GET" && path === "/api/admin/stats") return { name: "admin_stats" };
  if (method === "GET" && path === "/api/admin/subscriptions") return { name: "admin_subscriptions" };
  if (path === "/api/admin/notifications" && (method === "GET" || method === "POST")) {
    return { name: "admin_notifications", operation: method === "GET" ? "list" : "create" };
  }

  const processMatch = path.match(/^\/api\/admin\/notifications\/([^/]+)\/process$/);
  if (method === "POST" && processMatch) {
    return { name: "admin_notification", operation: "process", campaignId: decodeURIComponent(processMatch[1]) };
  }
  const notificationMatch = path.match(/^\/api\/admin\/notifications\/([^/]+)$/);
  if (method === "GET" && notificationMatch) {
    return { name: "admin_notification", operation: "get", campaignId: decodeURIComponent(notificationMatch[1]) };
  }
  if (method === "GET" && path === "/api/admin/situation-alerts") {
    return { name: "admin_situation_alerts", operation: "list" };
  }
  if (method === "POST" && path === "/api/admin/situation-alerts/process") {
    return { name: "admin_situation_alerts", operation: "process" };
  }
  if (method === "POST" && path === "/api/admin/maintenance") return { name: "admin_maintenance" };
  return { name: "not_found" };
}

export function normalizePath(path: string): string {
  if (path === "/") return path;
  return path.replace(/\/+$/, "");
}
