import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/generated/api";
import { ApiError, apiFetch } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { publicationKindLabel } from "@/features/operations/publication-kind";
import { PublishView } from "./publish";
import { PublicationPlanning, PublicationUsagePanel, type PublicationCapabilities } from "./publication-planning";

vi.mock("@/lib/api", async original => ({ ...(await original<typeof import("@/lib/api")>()), apiFetch: vi.fn() }));
const clients: QueryClient[] = [];
beforeEach(() => { vi.resetAllMocks(); useAppStore.setState({ lang: "en" }); });
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); });
function show(element: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

const capabilities: PublicationCapabilities = {
  schema_version: 1, plan_preview: true, plan_execution: false, rollback: false,
  channels: [
    { id: "surveillance", label: "Surveillance", realtime_controls: true, enabled: true, automatic: true, rollback: false, supported_modes: ["content"] },
    { id: "literature", label: "Literature", realtime_controls: false, enabled: true, automatic: true, rollback: false, supported_modes: ["content"], reason: "realtime_controls_not_available" },
    { id: "situation", label: "Situation", realtime_controls: true, enabled: true, automatic: true, rollback: false, supported_modes: ["content"] },
    { id: "knowledge", label: "Knowledge", realtime_controls: false, enabled: false, automatic: false, rollback: false, supported_modes: [], reason: "static_publication_only" },
  ],
  modes: [{ id: "theme", supported: false, reason: "plan_execution_not_available" }],
  legacy_modes: ["full", "reports_only", "code_only"],
};
const plan: components["schemas"]["PublicationPlanOut"] = {
  plan_id: "plan-1", plan_digest: "a".repeat(64), job_id: "site-release", created_at: "2026-11-13T01:00:00Z", expires_at: "2026-11-13T01:15:00Z",
  kind: "theme", channels: [], source_revision: "b".repeat(40), input_digest: "c".repeat(64), expected_generations: {},
  affected_templates: [], content_record_count: null, html_page_count: null, asset_compile_scope: ["theme"],
  pages_deploy_required: true, worker_deploy_required: false, validation_scope: ["theme"], cost_estimate_confidence: "unknown",
  changed_files: [{ path: "shared/site-theme-v1.json", kind: "theme", reason: "theme_candidate_requires_compatibility_validation" }],
  warnings: ["independent_theme_publication_not_available"], dirty_ids: [], pending_scopes: {}, preview_only: true, executable: false,
};

describe("publication planning", () => {
  it("uses capabilities to classify literature as independent JSON and keeps legacy static work available", async () => {
    vi.mocked(apiFetch).mockImplementation(path => Promise.resolve(path === "/publish/capabilities" ? capabilities
      : path === "/publish/pending" ? { surveillance: ["JP"], literature: true, situation: true, knowledge: [] }
        : { status: "unknown", plan: "unknown", services: [] }));
    show(<PublishView />);
    const scopes = await screen.findByRole("region", { name: "Static publication scopes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Check changes" })).toBeEnabled());
    expect(within(scopes).queryByText("Literature")).toBeNull();
    expect(within(scopes).getByText("Knowledge")).toBeInTheDocument();
    expect(screen.getAllByText("JSON channel enabled")).toHaveLength(3);
    expect(screen.getByText(/its controls are not yet available on the real-time page/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish static site" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Publish website code" })).toBeEnabled();
    expect(publicationKindLabel("literature_json", "en")).toBe("Literature JSON");
    expect(publicationKindLabel("literature_json", "zh")).toBe("文献 JSON");
  });

  it("checks a plan once on duplicate clicks without creating a publication task or promising a theme fast path", async () => {
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(apiFetch).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    show(<PublicationPlanning capabilities={capabilities} unavailable={false} loading={false} lang="en" />);
    const check = screen.getByRole("button", { name: "Check changes" });
    fireEvent.click(check); fireEvent.click(check);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/publish/plans", { method: "POST", body: JSON.stringify({ job_id: "site-release" }), timeoutMs: 120_000 });
    finish(plan);
    expect(await screen.findByText("Plan preview")).toBeInTheDocument();
    expect(screen.getByText(/Independent theme publication is not enabled/)).toBeInTheDocument();
    expect(screen.getByText("HTML pages to generate").parentElement).toHaveTextContent("Unknown");
    expect(screen.getByText("Pages deployment").parentElement).toHaveTextContent("Required");
    expect(screen.queryByRole("button", { name: /publish|execute/i })).toBeNull();
  });

  it("handles an expired plan with a safe recheck and never exposes raw error details", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new ApiError(409, "SECRET_TRACE", "publication_source_changed")).mockResolvedValueOnce(plan);
    show(<PublicationPlanning capabilities={capabilities} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check changes again");
    expect(screen.queryByText(/SECRET_TRACE/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    expect(await screen.findByText("Plan preview")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("retains existing publication controls when new discovery endpoints are absent", async () => {
    vi.mocked(apiFetch).mockImplementation(path => path === "/publish/pending"
      ? Promise.resolve({ surveillance: [], literature: true, situation: false, knowledge: [], literature_json_enabled: true })
      : Promise.reject(new ApiError(404, "not found")));
    show(<PublishView />);
    expect(await screen.findByText(/This service version does not provide publication plans/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check changes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Publish website code" })).toBeEnabled();
    expect(screen.getByText("Usage unknown")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Static publication scopes" })).queryByText("Literature")).toBeNull();
  });

  it("does not turn denied capabilities into an old-server or healthy usage message", async () => {
    vi.mocked(apiFetch).mockImplementation(path => path === "/publish/pending"
      ? Promise.resolve({ surveillance: [], literature: false, situation: false, knowledge: [] })
      : Promise.reject(new ApiError(403, "SECRET_TRACE")));
    show(<PublishView />);
    expect(await screen.findByText(/Publication capabilities are unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/This service version does not provide/)).toBeNull();
    expect(screen.getByRole("region", { name: "Service usage" })).toHaveTextContent("Usage unknown");
    expect(screen.queryByText(/SECRET_TRACE/)).toBeNull();
  });
});

describe("account usage", () => {
  it("renders missing account totals as unknown rather than zero", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ status: "unknown", plan: "unknown", account_total: null, remaining: null, services: [], source: "cloudflare_graphql_workersInvocationsAdaptive_estimate" });
    show(<PublicationUsagePanel lang="en" />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/publish/usage"));
    expect(screen.getByText("Account requests").parentElement).toHaveTextContent("Unknown");
    expect(screen.getByText("Remaining requests").parentElement).toHaveTextContent("Unknown");
    expect(screen.queryByText("0")).toBeNull();
    expect(await screen.findByText("Cloudflare adaptive sampling estimate")).toBeInTheDocument();
    expect(screen.getByText(/account plan has not been verified/)).toBeInTheDocument();
    expect(screen.queryByText("Healthy")).toBeNull();
  });

  it("marks stale samples explicitly and keeps account totals separate from service requests", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ status: "stale", plan: "free", account_total: 88000, limit: 100000, remaining: 12000,
      observed_at: "2026-11-12T01:00:00Z", source: "cloudflare", services: [{ id: "papers", requests: 5000 }], projected_usage: 110000 });
    show(<PublicationUsagePanel lang="en" />);
    expect(await screen.findByText("Snapshot stale")).toBeInTheDocument();
    expect(screen.getByText("Account requests").parentElement).toHaveTextContent("88,000");
    expect(screen.getByText("papers").parentElement).toHaveTextContent("5,000");
    expect(screen.getByText("Projected period requests").parentElement).toHaveTextContent("110,000");
    expect(screen.queryByText("Healthy")).toBeNull();
  });

  it("uses Chinese planning and quota terminology", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ status: "unknown", plan: "unknown", services: [] });
    show(<><PublicationPlanning capabilities={capabilities} unavailable={false} loading={false} lang="zh" /><PublicationUsagePanel lang="zh" /></>);
    expect(screen.getByRole("button", { name: "检查变更" })).toBeEnabled();
    expect(await screen.findByRole("region", { name: "服务用量" })).toHaveTextContent("用量未知");
    expect(screen.getByText(/缺失指标不代表请求为零或额度健康/)).toBeInTheDocument();
  });
});

describe("frozen publication execution", () => {
  const enabled = { ...capabilities, plan_execution: true,
    modes: [{ id: "theme", supported: true }, { id: "content", supported: true }, { id: "full", supported: true }] };
  const candidate = { ...plan, executable: true, preview_only: false, warnings: [],
    content_record_count: 0, html_page_count: 0, pages_deploy_required: false, expires_at: "2099-01-01T00:00:00Z" };

  it("submits a reviewed plan only once and monitors the accepted task", async () => {
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(apiFetch).mockImplementation(path => path === "/publish/plans" ? Promise.resolve(candidate)
      : path === "/publish/runs" ? new Promise(resolve => { finish = resolve; })
        : Promise.resolve({ status: "queued", task_uuid: "run-1", stages: [], artifacts: [], publication: [] }));
    show(<PublicationPlanning capabilities={enabled} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    const execute = await screen.findByRole("button", { name: "Publish reviewed plan" });
    expect(screen.getByText("HTML pages to generate").parentElement).toHaveTextContent("0");
    fireEvent.click(execute); fireEvent.click(execute);
    const calls = vi.mocked(apiFetch).mock.calls.filter(call => call[0] === "/publish/runs");
    expect(calls).toHaveLength(1);
    expect(JSON.parse(calls[0][1]?.body as string)).toEqual({ plan_id: "plan-1", plan_digest: "a".repeat(64), idempotency_key: "publication:plan-1" });
    finish({ status: "queued", task_uuid: "run-1" });
    expect(await screen.findByRole("link", { name: "View publication task" })).toHaveAttribute("href", "/publish/tasks?detail=run-1");
    expect(screen.getByText(/Completion requires public verification/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish reviewed plan" })).toBeDisabled();
  });

  it("reuses the same idempotency key after a lost run response", async () => {
    let runs = 0;
    vi.mocked(apiFetch).mockImplementation(path => path === "/publish/plans" ? Promise.resolve(candidate)
      : path === "/publish/runs" && ++runs === 1 ? Promise.reject(new ApiError(408, "timeout"))
        : Promise.resolve({ status: "queued", task_uuid: "recovered-task" }));
    show(<PublicationPlanning capabilities={enabled} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Publish reviewed plan" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Publish reviewed plan" }));
    await screen.findByRole("link", { name: "View publication task" });
    const calls = vi.mocked(apiFetch).mock.calls.filter(call => call[0] === "/publish/runs");
    expect(calls).toHaveLength(2);
    expect(calls[0][1]?.body).toEqual(calls[1][1]?.body);
  });

  it("requires a new plan after the server detects changed inputs", async () => {
    vi.mocked(apiFetch).mockImplementation(path => path === "/publish/plans" ? Promise.resolve(candidate) : Promise.reject(new ApiError(409, "PRIVATE_TRACE")));
    show(<PublicationPlanning capabilities={enabled} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Publish reviewed plan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check changes again");
    expect(screen.getByRole("button", { name: "Publish reviewed plan" })).toBeDisabled();
    expect(screen.queryByText(/PRIVATE_TRACE/)).toBeNull();
  });

  it("shows an authority commit awaiting public verification without claiming completion", async () => {
    vi.mocked(apiFetch).mockImplementation(path => Promise.resolve(path === "/publish/plans" ? candidate
      : path.endsWith("/receipt") ? { authoritative_committed: true, public_verified: false, database_confirmed: false,
        content_render_count: 0, html_assembly_count: 0, template_compile_count: 0 }
        : { status: "failed", task_uuid: "partial-run" }));
    show(<PublicationPlanning capabilities={enabled} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Publish reviewed plan" }));
    expect(await screen.findByText(/authority has committed this candidate, but public distribution is not verified/)).toBeInTheDocument();
    expect(screen.getByText("Authority commit").parentElement).toHaveTextContent("Verified");
    expect(screen.getByText("Public verification").parentElement).toHaveTextContent("Pending");
    expect(screen.getByText("Task confirmation").parentElement).toHaveTextContent("Pending");
  });

  it("keeps unavailable public evidence unknown even when an existing task completed", async () => {
    vi.mocked(apiFetch).mockImplementation(path => Promise.resolve(path === "/publish/plans" ? candidate
      : path.endsWith("/receipt") ? { authoritative_committed: true, public_verified: null, database_confirmed: true }
        : { status: "completed", task_uuid: "legacy-run" }));
    show(<PublicationPlanning capabilities={enabled} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Publish reviewed plan" }));
    await screen.findByText("Public verification");
    expect(screen.getByText("Public verification").parentElement).toHaveTextContent("Unknown");
    expect(screen.getByText("Task confirmation").parentElement).toHaveTextContent("Verified");
    expect(screen.queryByText(/authority has committed this candidate/)).toBeNull();
  });

  it("prevents executing an already expired candidate", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...candidate, expires_at: "2020-01-01T00:00:00Z" });
    show(<PublicationPlanning capabilities={enabled} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    expect(await screen.findByRole("button", { name: "Publish reviewed plan" })).toBeDisabled();
    expect(vi.mocked(apiFetch).mock.calls.some(call => call[0] === "/publish/runs")).toBe(false);
  });

  it("prepares rollback from a verified compatible version without executing it", async () => {
    vi.mocked(apiFetch).mockImplementation(path => path === "/publish/theme-history" ? Promise.resolve({ items: [
      { plan_id: "verified-old", created_at: plan.created_at, version: "d".repeat(64), public_verified: true, compatibility: "v1" },
      { plan_id: "unverified", created_at: plan.created_at, version: "e".repeat(64), public_verified: false, compatibility: "v1" },
    ] }) : Promise.resolve({ ...candidate, rollback_of: "verified-old" }));
    show(<PublicationPlanning capabilities={{ ...enabled, rollback: true }} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(await screen.findByRole("button", { name: "Check rollback" }));
    expect(await screen.findByRole("button", { name: "Publish rollback plan" })).toBeEnabled();
    expect(apiFetch).toHaveBeenCalledWith("/publish/rollback-plans", expect.objectContaining({ body: JSON.stringify({ kind: "theme", target_plan_id: "verified-old", job_id: "site-release" }) }));
    expect(vi.mocked(apiFetch).mock.calls.some(call => call[0] === "/publish/runs")).toBe(false);
  });

  it("loads a sandboxed fixed candidate preview only when requested", async () => {
    vi.mocked(apiFetch).mockImplementation(path => Promise.resolve(path === "/publish/plans" ? candidate : { pages: ["en/home", "fr/country"], missing: [] }));
    show(<PublicationPlanning capabilities={enabled} unavailable={false} loading={false} lang="en" />);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    const button = await screen.findByRole("button", { name: "Preview theme" });
    expect(document.querySelector("iframe")).toBeNull();
    fireEvent.click(button);
    const frame = await screen.findByTitle("Preview theme");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame).toHaveAttribute("src", "/api/v1/publish/plans/plan-1/preview/en/home");
    fireEvent.change(screen.getByRole("combobox", { name: "Language" }), { target: { value: "fr" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Page" }), { target: { value: "country" } });
    expect(await screen.findByTitle("Preview theme")).toHaveAttribute("src", "/api/v1/publish/plans/plan-1/preview/fr/country");
  });
});
