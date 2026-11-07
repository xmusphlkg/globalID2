import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { RealtimePublishView } from "./view";
import { realtimePublishMessages } from "./messages";
import { realtimeIssue, realtimeProblem, realtimeText } from "./presentation";
import type { RealtimeChannel, RealtimeChannelOverview, RealtimeOverview, RealtimePage, RealtimePending, RealtimeRun } from "./types";

vi.mock("@/lib/api", async original => ({ ...(await original<typeof import("@/lib/api")>()), apiFetch: vi.fn() }));
const clients: QueryClient[] = [];

function run(overrides: Partial<RealtimeRun> = {}): RealtimeRun {
  return {
    task_uuid: "release-task-1", status: "completed", progress: 100,
    created_at: "2026-11-07T04:00:00Z", started_at: "2026-11-07T04:00:01Z", completed_at: "2026-11-07T04:00:09Z",
    job_id: "site-release", release_id: "data-release-task-1", manifest_sha256: "a".repeat(64),
    country_count: 2, selected_change_count: 3,
    retry: { status: "none", scheduled_attempts: 0, max_attempts: 3, next_attempt_at: null, retryable: false, exhausted: false, category: null },
    continuation: { status: "none", remaining_count: 0, task_uuid: null },
    release_timing: { export_seconds: 2.5, publish_and_ack_seconds: 3.2, elapsed_seconds: 8 },
    href: "https://unexpected.example/never-follow-this", ...overrides,
  };
}

function channel(key: RealtimeChannel): RealtimeChannelOverview {
  const oldest = { count: 3, scope_count: 2, oldest_at: "2026-11-07T03:50:00Z", oldest_age_seconds: 120 };
  return {
    channel: key, enabled: true, manual_enabled: true, paused: false, effective_enabled: true,
    control_updated_at: null, control_error_code: null,
    backlog: { total: oldest, publishable: { ...oldest, count: 2 }, blocked: { ...oldest, count: 1 } },
    queue: { pending: 0, queued: 0, running: 0, retrying: 0 }, current_run: null,
    latest_success: run(), latest_failure: run({ task_uuid: "failed-task-1", status: "failed", retry: { status: "terminal", scheduled_attempts: 3, max_attempts: 3, next_attempt_at: null, retryable: true, exhausted: true, category: "transient_external" } }),
    failed_last_24h: 1,
    manifest: { release_id: `${key}-version-1`, sha256: "b".repeat(64), generated_at: "2026-01-01T00:00:00Z", observed_at: "2026-11-07T04:00:00Z", available: true, stale: false },
    monitor: { checked_at: "2026-11-07T04:00:00Z", age_seconds: 15, stale: false, status: "ok", http_status: 200, sample_verified: true, issues: [] },
  };
}

function overview(): RealtimeOverview {
  return { checked_at: "2026-11-07T04:00:15Z", channels: { surveillance: channel("surveillance"), situation: channel("situation") }, independence: { static_site_separate: true, message: "independent" } };
}

function pending(key: RealtimeChannel, id = 1): RealtimePending {
  return { id, channel: key, scope_key: id === 1 ? "JP" : "ZZ", marked_at: "2026-11-07T03:50:00Z", age_seconds: 120, source_task_uuid: "collection-task-1", precise: true, disease_ids: ["influenza"], publishable: id === 1, blocked_reason: id === 1 ? null : "unknown_country", affected_country_count: 1 };
}

function page<T>(items: T[], overrides: Partial<RealtimePage<T>> = {}): RealtimePage<T> {
  return { items, page: 1, page_size: 20, total: items.length, total_pages: items.length ? 1 : 0, ...overrides };
}

type MockOptions = {
  pending?: (url: URL, key: RealtimeChannel) => RealtimePage<RealtimePending>;
  runs?: (url: URL, key: RealtimeChannel) => RealtimePage<RealtimeRun>;
  mutation?: (path: string, init: RequestInit) => unknown | Promise<unknown>;
};

function mockApi(state = overview(), options: MockOptions = {}) {
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method) {
      if (options.mutation) return Promise.resolve(options.mutation(path, init));
      const key = path.includes("/situation/") ? "situation" : "surveillance";
      if (init.method === "PATCH") {
        const paused = JSON.parse(String(init.body)).paused as boolean;
        state.channels[key].paused = paused;
        state.channels[key].effective_enabled = !paused;
        state.channels[key].control_error_code = null;
        return Promise.resolve({ channel: key, enabled: true, paused, effective_enabled: !paused, updated_at: "2026-11-07T04:01:00Z", error_code: null });
      }
      return Promise.resolve({ channel: key, status: "queued", task_uuid: "new-release-1", reason: null, href: "https://unexpected.example/never-follow-this" });
    }
    if (path === "/publish/realtime/overview") return Promise.resolve(structuredClone(state));
    const url = new URL(path, "https://api.example");
    const key = url.pathname.includes("/situation/") ? "situation" : "surveillance";
    if (url.pathname.endsWith("/pending")) return Promise.resolve(options.pending?.(url, key) ?? page([pending(key)]));
    if (url.pathname.endsWith("/runs")) return Promise.resolve(options.runs?.(url, key) ?? page([run()]));
    return Promise.reject(new Error(`Unexpected test API route: ${path}`));
  });
  return state;
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}><RealtimePublishView /></QueryClientProvider>);
}

beforeEach(() => { vi.resetAllMocks(); useAppStore.setState({ lang: "en" }); });
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); });

describe("realtime publication control panel", () => {
  it("separates automatic pause from the deployment switch and keeps paused manual publication available", async () => {
    const state = overview();
    state.channels.surveillance.paused = true;
    state.channels.surveillance.effective_enabled = false;
    Object.assign(state.channels.situation, { enabled: false, manual_enabled: false, effective_enabled: false });
    mockApi(state);
    show();
    const surveillance = await screen.findByRole("region", { name: "Surveillance" });
    expect(within(surveillance).getByText("Automatic publication paused")).toBeInTheDocument();
    expect(within(surveillance).getByRole("button", { name: "Resume automatic publication · Surveillance" })).toBeEnabled();
    expect(screen.getByText(/Queued and running tasks continue; manual publication stays available/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish selected JSON" })).toBeEnabled();
    const situation = screen.getByRole("region", { name: "Situation" });
    expect(within(situation).getByText("Disabled by deployment settings")).toBeInTheDocument();
    expect(within(situation).getByRole("button", { name: "Pause automatic publication · Situation" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Publication channel"), { target: { value: "situation" } });
    expect(screen.getByRole("button", { name: "Publish selected JSON" })).toBeDisabled();
  });

  it("publishes only the selected channel with a bounded JSON request and suppresses duplicate clicks", async () => {
    let finish: (value: unknown) => void = () => undefined;
    mockApi(overview(), { mutation: () => new Promise(resolve => { finish = resolve; }) });
    show();
    await screen.findByRole("region", { name: "Surveillance" });
    fireEvent.change(screen.getByLabelText("Publication channel"), { target: { value: "situation" } });
    const button = screen.getByRole("button", { name: "Publish selected JSON" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1));
    expect(apiFetch).toHaveBeenCalledWith("/publish/realtime/situation/runs", { method: "POST", body: JSON.stringify({}) });
    finish({ channel: "situation", status: "queued", task_uuid: "situation-new-1", reason: null, href: "https://unexpected.example" });
    const manual = screen.getByRole("region", { name: "Manual JSON publication" });
    expect(await within(manual).findByRole("status")).toHaveTextContent("Situation: Publication queued");
    expect(within(manual).getByRole("link", { name: "Open task" })).toHaveAttribute("href", "/publish/tasks?detail=situation-new-1");
    expect(screen.queryByRole("link", { name: /unexpected/ })).toBeNull();
  });

  it("persists a channel pause without cancelling work or disabling manual publication", async () => {
    const state = mockApi();
    state.channels.surveillance.current_run = run({ status: "running", progress: 20 });
    state.channels.surveillance.queue.running = 1;
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Pause automatic publication · Surveillance" }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/publish/realtime/surveillance/control", { method: "PATCH", body: JSON.stringify({ paused: true }) }));
    expect(await within(screen.getByRole("region", { name: "Manual JSON publication" })).findByRole("status")).toHaveTextContent("Surveillance: Automatic publication paused. Existing tasks continue.");
    expect(screen.getByRole("button", { name: "Publish selected JSON" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Resume automatic publication · Surveillance" })).toBeEnabled();
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => /cancel|stop|releases\/config/.test(path))).toBe(false);
  });

  it("allows a fresh manual retry after automatic attempts are exhausted and exposes continuation links and timings", async () => {
    const prior = run({ retry: { status: "terminal", scheduled_attempts: 3, max_attempts: 3, next_attempt_at: null, retryable: true, exhausted: true, category: "transient_external" }, continuation: { status: "queued", remaining_count: 8, task_uuid: "continuation-1" } });
    mockApi(overview(), { runs: () => page([prior]) });
    show();
    expect(await screen.findByText("Automatic attempts exhausted")).toBeInTheDocument();
    expect(screen.getByText("Remaining changes: 8")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open follow-up task" })).toHaveAttribute("href", "/publish/tasks?detail=continuation-1");
    expect(screen.getByText("2.5 sec")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry failed publication" }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/publish/realtime/surveillance/retry", { method: "POST", body: JSON.stringify({}) }));
  });

  it("paginates pending changes and resets pagination when the eligibility filter changes", async () => {
    mockApi(overview(), { pending: (url, key) => page([pending(key, Number(url.searchParams.get("page")))], { page: Number(url.searchParams.get("page")), total: 40, total_pages: 2 }) });
    show();
    const changes = screen.getByRole("region", { name: "Pending changes · Surveillance" });
    fireEvent.click(await within(changes).findByRole("button", { name: "Next page" }));
    expect(await within(changes).findByText("ZZ")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith("/publish/realtime/surveillance/pending?page=2&page_size=20&eligibility=all");
    fireEvent.change(screen.getByLabelText("Change eligibility"), { target: { value: "blocked" } });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/publish/realtime/surveillance/pending?page=1&page_size=20&eligibility=blocked"));
    expect(await within(changes).findByRole("link", { name: "Open task" })).toHaveAttribute("href", "/collection/runs/collection-task-1");
  });

  it("shows stale sampling without marking old generated data unhealthy, and refresh only reads recorded state", async () => {
    const state = overview();
    state.channels.situation.monitor.stale = true;
    state.channels.situation.monitor.age_seconds = 300;
    state.channels.situation.monitor.sample_verified = null;
    mockApi(state);
    show();
    const surveillance = await screen.findByRole("region", { name: "Surveillance" });
    expect(within(surveillance).getByText("Healthy")).toBeInTheDocument();
    const situation = screen.getByRole("region", { name: "Situation" });
    expect(within(situation).getByText("Monitoring snapshot stale")).toBeInTheDocument();
    expect(within(situation).queryByText("Sample SHA verified")).toBeNull();
    const refresh = await screen.findByRole("button", { name: "Refresh status" });
    const before = vi.mocked(apiFetch).mock.calls.length;
    fireEvent.click(refresh);
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls.length).toBeGreaterThan(before));
    expect(vi.mocked(apiFetch).mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it("handles a retry reservation conflict with safe feedback and the existing common task detail", async () => {
    const state = overview();
    mockApi(state, { mutation: () => {
      state.channels.surveillance.current_run = run({ task_uuid: "reserved-1", status: "retrying" });
      throw new ApiError(409, "SECRET_RAW_STORAGE_ERROR", "realtime_task_reserved");
    } });
    show();
    await screen.findByRole("region", { name: "Surveillance" });
    fireEvent.click(screen.getByRole("button", { name: "Retry failed publication" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Publication state has changed");
    expect(screen.queryByText(/SECRET_RAW_STORAGE_ERROR/)).toBeNull();
    const manual = screen.getByRole("region", { name: "Manual JSON publication" });
    expect(await within(manual).findByRole("link", { name: "Open task" })).toHaveAttribute("href", "/publish/tasks?detail=reserved-1");
  });

  it("links a conflicting task in the other channel from the sanitized problem identifier", async () => {
    mockApi(overview(), { mutation: () => {
      throw new ApiError(409, "SECRET_RAW_STORAGE_ERROR", "realtime_task_reserved", undefined, "situation-reservation-1");
    } });
    show();
    await screen.findByRole("region", { name: "Surveillance" });
    fireEvent.click(screen.getByRole("button", { name: "Retry failed publication" }));
    const manual = screen.getByRole("region", { name: "Manual JSON publication" });
    expect(await within(manual).findByRole("link", { name: "Open task" })).toHaveAttribute("href", "/publish/tasks?detail=situation-reservation-1");
    expect(screen.queryByText(/SECRET_RAW_STORAGE_ERROR/)).toBeNull();
  });

  it("renders complete Chinese terminology and retains fail-closed controls", async () => {
    useAppStore.setState({ lang: "zh" });
    const state = overview();
    state.channels.surveillance.control_error_code = "control_state_invalid";
    state.channels.surveillance.monitor.issues = [{ level: "critical", code: "control_state_invalid" }];
    state.channels.surveillance.monitor.status = "critical";
    mockApi(state);
    show();
    expect(await screen.findByRole("heading", { level: 1, name: "实时数据发布" })).toBeInTheDocument();
    expect(await screen.findByText("已保存的控制状态无法安全读取，自动发布已暂停。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "恢复自动发布 · 国家与疾病监测" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "发布所选通道 JSON" })).toBeEnabled();
    expect(Object.keys(realtimePublishMessages.zh).sort()).toEqual(Object.keys(realtimePublishMessages.en).sort());
  });

  it("repairs an invalid control only through an explicit resume and keeps the other channel paused", async () => {
    const state = overview();
    Object.assign(state.channels.surveillance, { paused: true, effective_enabled: false, control_error_code: "control_state_invalid" });
    Object.assign(state.channels.situation, { paused: true, effective_enabled: false });
    mockApi(state);
    show();
    const resume = await screen.findByRole("button", { name: "Resume automatic publication · Surveillance" });
    expect(resume).toBeEnabled();
    expect(vi.mocked(apiFetch).mock.calls.every(([, init]) => !init?.method)).toBe(true);
    fireEvent.click(resume);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/publish/realtime/surveillance/control", { method: "PATCH", body: JSON.stringify({ paused: false }) }));
    expect(await screen.findByRole("button", { name: "Pause automatic publication · Surveillance" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Resume automatic publication · Situation" })).toBeEnabled();
    expect(state.channels.situation.paused).toBe(true);
  });

  it("retains critical public-data faults when their monitoring snapshot is also stale", async () => {
    const state = overview();
    Object.assign(state.channels.surveillance.monitor, { stale: true, status: "critical", sample_verified: false, issues: [{ level: "critical", code: "object_checksum_mismatch" }] });
    mockApi(state);
    show();
    const card = await screen.findByRole("region", { name: "Surveillance" });
    expect(within(card).getByText("Action required")).toBeInTheDocument();
    expect(within(card).getByText("Monitoring snapshot stale")).toBeInTheDocument();
    expect(within(card).getByText("Sample SHA failed")).toBeInTheDocument();
    expect(within(card).queryByText("Healthy")).toBeNull();
  });

  it("fails closed on unavailable channel status and hides upstream exception details", async () => {
    vi.mocked(apiFetch).mockImplementation(path => path.endsWith("/overview") ? Promise.reject(new ApiError(403, "SECRET_UPSTREAM_TRACE")) : Promise.resolve(page([])));
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("Check your control-panel session");
    expect(screen.queryByText(/SECRET_UPSTREAM_TRACE/)).toBeNull();
    expect(screen.getByRole("button", { name: "Publish selected JSON" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry failed publication" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reload channel status" })).toBeInTheDocument();
  });

  it("describes an existing active task as skipped instead of a completed publication", async () => {
    mockApi(overview(), { mutation: () => ({ channel: "surveillance", status: "skipped", task_uuid: "existing-release-1", reason: "active_task_exists", href: "https://unexpected.example" }) });
    show();
    await screen.findByRole("region", { name: "Surveillance" });
    fireEvent.click(screen.getByRole("button", { name: "Publish selected JSON" }));
    const manual = screen.getByRole("region", { name: "Manual JSON publication" });
    expect(await within(manual).findByRole("status")).toHaveTextContent("A release is already active");
    expect(within(manual).getByRole("link", { name: "Open task" })).toHaveAttribute("href", "/publish/tasks?detail=existing-release-1");
    expect(within(manual).queryByText(/completed/i)).toBeNull();
  });
});

it("never renders raw exception text or unknown monitoring codes", () => {
  const text = realtimeText("en");
  expect(realtimeProblem(new Error("token=SECRET"), text)).not.toContain("SECRET");
  expect(realtimeIssue("upstream/path/token=SECRET", text)).toBe(text("issue_generic"));
  expect(realtimeIssue("automatic_publication_paused", text)).toContain("paused by an operator");
});
