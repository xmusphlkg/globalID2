import { expect, test as base, type Page } from "@playwright/test";
import type { RealtimeChannel, RealtimeOverview, RealtimePending, RealtimeRun } from "../src/features/realtime-publish/types";

export const currentTask = "11111111-1111-4111-8111-111111111111";
export const failedTask = "22222222-2222-4222-8222-222222222222";
export const otherChannelTask = "33333333-3333-4333-8333-333333333333";
export const newTask = "44444444-4444-4444-8444-444444444444";
const timestamp = "2026-11-07T04:30:00Z";

function run(task_uuid: string, status: RealtimeRun["status"]): RealtimeRun {
  return {
    task_uuid, status, progress: status === "completed" ? 100 : 30,
    created_at: timestamp, started_at: timestamp, completed_at: status === "completed" || status === "failed" ? timestamp : null,
    job_id: "configured-release-job", release_id: `data-${task_uuid}`, manifest_sha256: "a".repeat(64),
    country_count: 2, selected_change_count: 3,
    retry: { status: status === "failed" ? "terminal" : "none", scheduled_attempts: status === "failed" ? 3 : 0, max_attempts: 3, next_attempt_at: null, retryable: status === "failed", exhausted: status === "failed", category: status === "failed" ? "transient_external" : null },
    continuation: { status: "queued", remaining_count: 8, task_uuid: otherChannelTask },
    release_timing: { export_seconds: 2.5, publish_and_ack_seconds: 3.2, elapsed_seconds: 8 },
    // The UI must build task links from identifiers, rather than trust href.
    href: "https://unexpected.invalid/never-follow-this",
  };
}

function overview(): RealtimeOverview {
  const channels = {} as RealtimeOverview["channels"];
  for (const channel of ["surveillance", "situation"] as const) {
    const count = channel === "surveillance" ? 22 : 2;
    const summary = { count, scope_count: count, oldest_at: timestamp, oldest_age_seconds: 120, expanded_country_count: 2, scopes: [] };
    channels[channel] = {
      channel, enabled: true, manual_enabled: true, paused: false, effective_enabled: true,
      control_updated_at: null, control_error_code: null,
      backlog: { total: summary, publishable: { ...summary, count: count - Number(channel === "surveillance") }, blocked: { ...summary, count: Number(channel === "surveillance"), scope_count: Number(channel === "surveillance") } },
      queue: { pending: 0, queued: 0, running: 0, retrying: 0 }, current_run: null,
      latest_success: run(currentTask, "completed"), latest_failure: channel === "surveillance" ? run(failedTask, "failed") : null,
      failed_last_24h: Number(channel === "surveillance"),
      manifest: { release_id: `${channel}-${currentTask}`, sha256: "b".repeat(64), generated_at: "2026-01-01T00:00:00Z", observed_at: timestamp, available: true, stale: channel === "surveillance" },
      monitor: { checked_at: timestamp, age_seconds: channel === "surveillance" ? 300 : 15, stale: channel === "surveillance", status: channel === "surveillance" ? "critical" : "ok", http_status: 200, sample_verified: channel !== "surveillance", issues: channel === "surveillance" ? [{ level: "critical", code: "object_checksum_mismatch" }] : [] },
    };
  }
  return { checked_at: timestamp, channels, independence: { static_site_separate: true, message: "Independent data channels" } };
}

export interface RealtimeFixtureState {
  overview: RealtimeOverview;
  requests: Array<{ path: string; method: string; body: unknown }>;
  unexpected: string[];
  pageErrors: string[];
  reservedConflict: boolean;
}

export const test = base.extend<{ state: RealtimeFixtureState }>({
  state: async ({ page, baseURL }, use, testInfo) => {
    const allowedOrigin = new URL(baseURL!).origin;
    const state: RealtimeFixtureState = { overview: overview(), requests: [], unexpected: [], pageErrors: [], reservedConflict: false };
    page.on("pageerror", error => state.pageErrors.push(error.message));
    await page.addInitScript(() => localStorage.setItem("globalid-dashboard-store", JSON.stringify({ state: { lang: "en", countryId: 1, countryCode: "JP", countryName: "Japan", sidebarCollapsed: false }, version: 0 })));
    await page.route("**/*", async route => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();
      if (url.origin !== allowedOrigin) {
        state.unexpected.push(`external ${method} ${url.origin}${url.pathname}`);
        return route.abort();
      }
      if (url.pathname === "/api/panel-session" && method === "GET") return route.fulfill({ json: { required: false, ok: true } });
      if (!url.pathname.startsWith("/api/v1/")) {
        if (method === "GET" || method === "HEAD") return route.continue();
        state.unexpected.push(`unmocked ${method} ${url.pathname}`);
        return route.fulfill({ status: 500, json: { detail: "Unmocked fixture mutation" } });
      }
      const path = url.pathname.slice("/api/v1".length);
      state.requests.push({ path: path + url.search, method, body: request.postData() ? request.postDataJSON() : null });
      const respond = (data: unknown, status = 200) => route.fulfill({ status, json: { data, meta: {} } });
      if (path === "/events/stream" && method === "GET") return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
      if (path === "/overview" && method === "GET") return respond({ generated_at: timestamp, tasks: { queued: 0, running: 0, retrying: 0, failed: 0 }, schedules: { enabled: 1, total: 1 }, action_items: [], runtime: { services: [], heartbeat_available: true }, pipeline: [], recent_tasks: [] });
      if (path === "/countries" && method === "GET") return respond([{ id: 1, code: "JP", name: "Japan", name_en: "Japan", name_zh: "日本", name_local: "日本", language: "ja", timezone: "Asia/Tokyo", is_active: true }]);
      if (path === "/publish/realtime/overview" && method === "GET") return respond(state.overview);
      const match = /^\/publish\/realtime\/(surveillance|situation)\/(pending|runs|retry|control)$/.exec(path);
      if (match) {
        const channel = match[1] as RealtimeChannel;
        const resource = match[2];
        const target = state.overview.channels[channel];
        if (resource === "control" && method === "PATCH") {
          const paused = request.postDataJSON().paused as boolean;
          target.paused = paused;
          target.effective_enabled = target.enabled && !paused;
          target.control_error_code = null;
          return respond({ channel, enabled: target.enabled, paused, effective_enabled: target.effective_enabled, updated_at: timestamp, error_code: null });
        }
        if ((resource === "runs" || resource === "retry") && method === "POST") {
          if (resource === "retry" && state.reservedConflict) return route.fulfill({ status: 409, contentType: "application/problem+json", body: JSON.stringify({ title: "Publication reservation", detail: "SECRET_RAW_UPSTREAM_ERROR", code: "realtime_task_reserved", task_uuid: otherChannelTask, href: "https://unexpected.invalid/never-follow-this" }) });
          target.current_run = run(newTask, "queued");
          target.queue.queued = 1;
          return respond({ channel, status: "queued", task_uuid: newTask, reason: null, href: "https://unexpected.invalid/never-follow-this" }, 202);
        }
        if (resource === "pending" && method === "GET") {
          let items: RealtimePending[] = Array.from({ length: channel === "surveillance" ? 22 : 2 }, (_, index) => ({ id: 15 + index, channel, scope_key: channel === "situation" ? "*" : index === 0 ? "IE" : `JP-${index}`, marked_at: timestamp, age_seconds: 120, source_task_uuid: currentTask, precise: channel === "surveillance", disease_ids: channel === "surveillance" ? ["influenza"] : [], publishable: channel !== "surveillance" || index !== 0, blocked_reason: channel === "surveillance" && index === 0 ? "disabled_country" : null, affected_country_count: 1 }));
          const eligibility = url.searchParams.get("eligibility") || "all";
          if (eligibility !== "all") items = items.filter(item => item.publishable === (eligibility === "publishable"));
          const selectedPage = Number(url.searchParams.get("page") || 1);
          return respond({ items: items.slice((selectedPage - 1) * 20, selectedPage * 20), page: selectedPage, page_size: 20, total: items.length, total_pages: Math.ceil(items.length / 20) });
        }
        if (resource === "runs" && method === "GET") {
          let items = [run(currentTask, "completed"), run(failedTask, "failed")];
          const statuses = url.searchParams.get("status")?.split(",");
          if (statuses) items = items.filter(item => statuses.includes(item.status));
          return respond({ items, page: 1, page_size: 20, total: items.length, total_pages: items.length ? 1 : 0 });
        }
      }
      state.unexpected.push(`${method} ${path}`);
      return route.fulfill({ status: 500, json: { detail: "Unexpected fixture route" } });
    });
    await use(state);
    await testInfo.attach("fixture-network-and-errors", { body: JSON.stringify(state, null, 2), contentType: "application/json" });
    expect(state.unexpected).toEqual([]);
    expect(state.pageErrors).toEqual([]);
  },
});

export async function noHorizontalOverflow(page: Page) {
  const size = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  expect(size.document, JSON.stringify(size)).toBeLessThanOrEqual(size.viewport + 1);
  expect(size.body, JSON.stringify(size)).toBeLessThanOrEqual(size.viewport + 1);
  expect(size.viewport).toBeLessThanOrEqual((page.viewportSize()?.width ?? size.viewport) + 1);
}

export { expect };
