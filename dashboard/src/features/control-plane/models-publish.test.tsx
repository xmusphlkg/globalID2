import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { ModelsView } from "./models";
import { PublishView } from "./publish";
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), apiFetch: vi.fn() }));
beforeEach(() => useAppStore.setState({ lang: "en" }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function show(element: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}
const pending = { surveillance: ["US"], literature: true, situation: false, knowledge: [] };
const item = { id: 1, parent_task_uuid: "parent-1", line: "knowledge_brief", priority: "normal", status: "queued", attempt: 2, available_at: "2026-10-30T01:00:00Z", route_key: null, lease_expires_at: null, error: null };
const checks = { overall_ready: true, checked_at: "2026-10-30T01:00:00Z", blockers: [], git: { read_access_ok: true, write_access_ok: true }, cloudflare: { project_access_ok: true, token_present: true, account_id_present: true }, commands: { python_exists: true, wrangler_available: true }, repository_boundary: { enforced: true } };
describe("model operations", () => {
  it("refreshes leased work after a conflicting priority mutation", async () => {
    let leased = false;
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === "POST") { leased = true; return Promise.reject(new ApiError(409, "Only queued work items can change priority")); }
      return Promise.resolve(path === "/models/routes" ? [] : [{ ...item, status: leased ? "leased" : "queued", lease_expires_at: "2099-01-01T00:00:00Z" }]);
    });
    show(<ModelsView />);
    fireEvent.change(await screen.findByLabelText("Priority for work item 1"), { target: { value: "urgent" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("only queued work can change priority");
    await waitFor(() => expect(screen.getByLabelText("Priority for work item 1")).toBeDisabled());
    expect(screen.getByText("Leased", { selector: "span" })).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith("/models/queue/1/priority", { method: "POST", body: JSON.stringify({ priority: "urgent" }) });
  });
  it("shows route capacity, lease and structured detail with translated work line", async () => {
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path === "/models/routes" ? [{ model_key: "route-a", runtime_model_capacity: 3, in_flight: 2, available_for_routing: false, rate_limit_cooldown_until: "2099-01-01T00:00:00Z" }] : path === "/models/queue/1" ? { ...item, status: "leased", lease_expires_at: "2099-01-01T00:00:00Z", payload: { prompt_ref: "prompt-one" }, result: { answer: "complete" } } : [item]));
    show(<ModelsView />);
    expect(await screen.findByText("route-a")).toBeInTheDocument();
    expect(screen.getByText("No routes are available. Queued work will wait for capacity.")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "#1 Knowledge brief" }));
    expect(await screen.findByText(/prompt-one/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Parent run: parent-1" })).toHaveAttribute("href", "/collection/runs/parent-1");
    fireEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("heading", { name: "Work item #1 details" })).toBeNull();
  });
});
describe("publication", () => {
  it("blocks a failed preflight then recovers and submits explicit full publication", async () => {
    let checkCount = 0;
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path === "/publish/pending") return Promise.resolve(pending);
      if (path === "/releases/site-release/checks") return Promise.resolve(++checkCount === 1 ? { ...checks, overall_ready: false, blockers: ["Hosting unavailable"] } : checks);
      if (path === "/publish/runs") return Promise.resolve({ task_uuid: "release-1", status: "queued" });
      return Promise.resolve({ task_uuid: "release-1", status: "running" });
    });
    show(<PublishView />);
    expect(await screen.findByText("Entire line")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    expect(await screen.findByText("Hosting unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish site-release" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Publish site-release" })).toBeEnabled());
    fireEvent.click(screen.getByLabelText("Full generation"));
    fireEvent.click(screen.getByRole("button", { name: "Publish site-release" }));
    expect(await screen.findByRole("link", { name: "Open release run" })).toHaveAttribute("href", "/publish/tasks?detail=release-1");
    expect(apiFetch).toHaveBeenCalledWith("/publish/runs", { method: "POST", body: JSON.stringify({ job_id: "site-release", full: true }) });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/collection/runs/release-1"));
    expect(screen.getByRole("button", { name: "Publish site-release" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /collect|retry|download/i })).toBeNull();
  });
  it("recovers from a submission error, defaults to incremental and allows a failed run to be published again", async () => {
    let submission = 0;
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path === "/publish/pending") return Promise.resolve(pending);
      if (path === "/publish/runs") return ++submission === 1 ? Promise.reject(new Error("Release unavailable")) : Promise.resolve({ task_uuid: "release-1", status: "queued" });
      return Promise.resolve({ task_uuid: "release-1", status: "failed" });
    });
    show(<PublishView />);
    fireEvent.click(screen.getByRole("button", { name: "Publish site-release" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Release unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Publish site-release" }));
    expect(await screen.findByText("Publication failed. Pending changes remain available for another release.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Publish site-release" })).toBeEnabled();
    expect(apiFetch).toHaveBeenCalledWith("/publish/runs", { method: "POST", body: JSON.stringify({ job_id: "site-release", full: false }) });
  });
  it("renders the same terminology in Chinese", async () => {
    useAppStore.setState({ lang: "zh" });
    vi.mocked(apiFetch).mockResolvedValue(pending);
    show(<PublishView />);
    expect(await screen.findByText("整条数据线")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "执行预检" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^发布$/ })).toBeInTheDocument();
  });
});
