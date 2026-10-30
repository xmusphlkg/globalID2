import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiFetchWithMeta } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { SituationSourcesView } from "./situation";
import { LiteratureSourcesView } from "./literature";
import { KnowledgeSourcesView } from "./knowledge";
import IngestionSourcesView from "@/features/operations/sources/flow/view";
import ResearchRadarView from "@/features/research-radar/view";

vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), apiFetch: vi.fn(), apiFetchWithMeta: vi.fn() }));
vi.mock("@/features/operations/tasks/api", () => ({ useTaskEventStream: vi.fn() }));
const clients: QueryClient[] = [];
function show(element: React.ReactNode) { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client); return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>); }
beforeEach(() => { useAppStore.setState({ lang: "en", countryId: 1, countryCode: "US", countryName: "United States" }); });
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; vi.resetAllMocks(); });
const source = { source_id: "who", label_en: "WHO notices", label_zh: "世卫通报", status: "failed", stored_notices: 3, last_error: "Source unavailable", history: [{ run_id: "source-run", started_at: "2026-10-30T00:00:00Z", status: "failed", fetched_count: 3, new_count: 1, updated_count: 0, error: "Source unavailable" }] };

describe("independent collection source workflows", () => {
 it("collects situation metadata without requesting AI or export", async () => {
  vi.mocked(apiFetch).mockImplementation((_path, init) => Promise.resolve(init?.method === "POST" ? { task_uuid: "situation-task", status: "queued" } : [source]));
  show(<SituationSourcesView />);
  await screen.findByRole("heading", { name: "WHO notices" });
  fireEvent.click(screen.getByRole("button", { name: "Collect notice sources" }));
  expect(await screen.findByRole("link", { name: "Open collection run" })).toHaveAttribute("href", "/collection/runs/situation-task");
  expect(apiFetch).toHaveBeenCalledWith("/collection/runs", { method: "POST", body: JSON.stringify({ line: "situation", mode: "metadata_only" }) });
  expect(screen.queryByRole("button", { name: /AI|export|publish/i })).toBeNull();
 });
 it("uses literature synchronization records and submits an indexed time window", async () => {
  vi.mocked(apiFetch).mockImplementation((_path, init) => Promise.resolve(init?.method === "POST" ? { task_uuid: "literature-task" } : { total_articles: 7, latest_articles: [], latest_runs: [{ run_uuid: "provider-run", source: "crossref", status: "completed", started_at: "2026-10-30T00:00:00Z", counts: { fetched: 7 } }, { run_uuid: "governance-run", source: "research-radar-autopilot", status: "completed", started_at: "2026-10-30T00:00:00Z", counts: {} }] }));
  show(<LiteratureSourcesView />);
  await screen.findByText("provider-run");
  expect(screen.queryByText("governance-run")).toBeNull();
  fireEvent.change(screen.getByLabelText("Indexed since (optional)"), { target: { value: "2026-10-01T10:00" } });
  fireEvent.click(screen.getByRole("button", { name: "Sync literature metadata" }));
  await screen.findByRole("link", { name: "Open collection run" });
  const mutation = vi.mocked(apiFetch).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(mutation[0]).toBe("/collection/runs");
  expect(JSON.parse(String(mutation[1]?.body))).toEqual({ line: "literature", since: new Date("2026-10-01T10:00").toISOString() });
  expect(screen.queryByRole("button", { name: /enrich|summarize|publish/i })).toBeNull();
 });
 it("shows knowledge source documents without substituting brief generation for collection", async () => {
  vi.mocked(apiFetch).mockImplementation((path, init) => Promise.resolve(init?.method === "POST" ? { requested_disease_ids: ["D1"], source_groups: ["who"], force: false, created_count: 1, skipped_count: 0, created_tasks: [{ task_uuid: "knowledge-source-task", status: "queued", href: "/collection/runs/knowledge-source-task" }], skipped: [] } : path === "/knowledge" ? [{ disease_id: "D1", name_en: "Influenza", source_count: 1 }] : { disease_id: "D1", sources: [{ id: 1, source_name: "WHO", source_type: "who", title: "Influenza fact sheet", status: "completed", language: "en", url: "https://example.org/flu", raw_excerpt_hash: "abc", content_text: "Stored evidence" }] }));
  show(<KnowledgeSourcesView />);
  expect(screen.getByRole("button", { name: "Collect knowledge sources" })).toBeDisabled();
  fireEvent.click(await screen.findByRole("button", { name: /Influenza/ }));
  expect(await screen.findByText("Influenza fact sheet")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Collect knowledge sources" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Collect knowledge sources" }));
  expect(await screen.findByRole("link", { name: /Open collection run/ })).toHaveAttribute("href", "/collection/runs/knowledge-source-task");
  const mutations = vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "POST");
  expect(mutations).toEqual([["/knowledge/source-runs", { method: "POST", body: JSON.stringify({ disease_ids: ["D1"], source: ["who", "search", "wikidata", "wikipedia", "pubmed"], force: false, priority: "normal" }) }]]);
 });
 it("recovers a knowledge source error and reports an already active run without generating a brief", async () => {
  let attempts = 0;
  vi.mocked(apiFetch).mockImplementation((path, init) => {
   if (init?.method === "POST") return ++attempts === 1 ? Promise.reject(new Error("Source queue unavailable")) : Promise.resolve({ requested_disease_ids: ["D1"], source_groups: ["who"], force: false, created_count: 0, skipped_count: 1, created_tasks: [], skipped: [{ disease_id: "D1", reason: "active_task_exists", task_uuid: "existing-source-run" }] });
   return Promise.resolve(path === "/knowledge" ? [{ disease_id: "D1", name_en: "Influenza", source_count: 0 }] : { disease_id: "D1", sources: [] });
  });
  show(<KnowledgeSourcesView />);
  fireEvent.click(await screen.findByRole("button", { name: /Influenza/ }));
  fireEvent.click(screen.getByRole("button", { name: "Collect knowledge sources" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Source queue unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Collect knowledge sources" }));
  expect(await screen.findByRole("link", { name: "Open existing run" })).toHaveAttribute("href", "/collection/runs/existing-source-run");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("link", { name: /Open collection run/ })).toBeNull();
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "POST").map(([path]) => path)).toEqual(["/knowledge/source-runs", "/knowledge/source-runs"]);
 });
 it("preserves country-source file and year options while using the collection facade", async () => {
  vi.mocked(apiFetch).mockImplementation((path, init) => Promise.resolve(init?.method === "POST" ? { task_uuid: "ingestion-task", status: "queued" } : path.startsWith("/sources/config") ? [{ country_code: "US", country_name: "United States", supports_crawl: true, supports_source_file: true, supports_source_dir: true, supports_start_year: true, default_start_year: 2000, default_fill_missing: false, source_options: [{ value: "history", label_en: "Historical file", supports_start_year: true, default_start_year: 2000 }] }] : []));
  show(<IngestionSourcesView />);
  fireEvent.click(await screen.findByRole("button", { name: "New collection run" }));
  fireEvent.change(screen.getByLabelText("Start year"), { target: { value: "2012" } });
  fireEvent.change(screen.getByLabelText("Source file"), { target: { value: "data/raw/us/import.csv" } });
  fireEvent.click(screen.getByRole("button", { name: "Start collection" }));
  await screen.findByRole("link", { name: "Open collection run" });
  const mutation = vi.mocked(apiFetch).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(mutation[0]).toBe("/collection/runs");
  expect(JSON.parse(String(mutation[1]?.body))).toMatchObject({ line: "surveillance", country_code: "US", source: "history", start_year: 2012, source_file: "data/raw/us/import.csv" });
  expect(screen.queryByText("Global Situation notice sources")).toBeNull();
 });
 it("keeps literature governance browse-only and moves synchronization out of AI", async () => {
  vi.mocked(apiFetch).mockResolvedValue({ total_articles: 0, latest_articles: [], latest_runs: [], schedule: { jobs: [{ ai_enrichment_enabled: true }] }, automation: {} });
  vi.mocked(apiFetchWithMeta).mockResolvedValue({ data: [], meta: {}, headers: new Headers() });
  const view = show(<ResearchRadarView mode="governance" />);
  await waitFor(() => expect(apiFetchWithMeta).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: /Generate next batch|Discover evidence|Reconcile signals|Sync sources|Run literature automation policy/ })).toBeNull();
  expect(screen.getByRole("link", { name: "Source - Literature" })).toHaveAttribute("href", "/collection/sources/literature");
  view.unmount();
  show(<ResearchRadarView mode="ai" />);
  expect(await screen.findByRole("button", { name: "Generate next batch" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Sync sources" })).toBeNull();
  expect(screen.getByRole("button", { name: "Run literature automation policy" })).toBeInTheDocument();
 });
});
