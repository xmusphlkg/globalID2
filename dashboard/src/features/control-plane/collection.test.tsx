import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { CollectionView, RunView } from "./collection";
import RunsPage from "@/app/collection/runs/page";

vi.mock("next/navigation", () => ({ permanentRedirect: (href: string) => { throw new Error(href); } }));

vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), apiFetch: vi.fn() }));
const clients: QueryClient[] = [];
beforeEach(() => useAppStore.setState({ lang: "en" }));
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; vi.resetAllMocks(); useAppStore.setState({ lang: "en" }); });
function show(component: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client);
  const view = render(<QueryClientProvider client={client}>{component}</QueryClientProvider>);
  return { ...view, rerender: (next: React.ReactNode) => view.rerender(<QueryClientProvider client={client}>{next}</QueryClientProvider>) };
}
const stage = { stage: "collect", attempt: 1, status: "completed", metrics: { records: 3 }, started_at: "2026-10-30T00:00:00Z", finished_at: "2026-10-30T00:00:01Z" };
const completedRun = { task_uuid: "same-uuid", task_type: "crawl_data", line: "surveillance", country_code: "US", status: "completed", attempt: 1, stages: [stage], artifacts: [{ attempt: 1, relative_path: "data/raw/us/one.csv", sha256: "a".repeat(64), byte_size: 256, source_url: "https://example.org/data.csv", kind: "csv_page" }], publication: [] };
const lines = ["surveillance", "knowledge"].map((line) => ({ line, schedule_ids: [], pending_publish_count: 0, latest_stage: "collect", latest_stage_status: "completed", latest_task_uuid: "same-uuid" }));

describe("collection operations", () => {
  it("submits the newly selected country when URL context changes", async () => {
    vi.mocked(apiFetch).mockImplementation((path, init) => Promise.resolve(init?.method === "POST" ? { task_uuid: "jp-run" } : path === "/collection/lines" ? lines : []));
    const view = show(<CollectionView initialCountry="US" />);
    expect(await screen.findByLabelText("Country code")).toHaveValue("US");
    view.rerender(<CollectionView initialCountry="JP" />);
    expect(screen.getByLabelText("Country code")).toHaveValue("JP");
    fireEvent.click(within(screen.getByRole("heading", { name: "Surveillance" }).closest("section")!).getByRole("button", { name: "Start collection" }));
    await screen.findByRole("link", { name: "Open queued run" });
    expect(apiFetch).toHaveBeenCalledWith("/collection/runs", { method: "POST", body: JSON.stringify({ line: "surveillance", country_code: "JP", fill_missing: false }) });
  });

  it("re-executes a completed collection with the same UUID while retaining attempt and provenance", async () => {
    let run = completedRun;
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === "POST") { run = { ...completedRun, status: "queued" }; return Promise.resolve({ task_uuid: "same-uuid", status: "queued", href: "/tasks/same-uuid" }); }
      return Promise.resolve(run);
    });
    show(<RunView taskUuid="same-uuid" />);
    fireEvent.click(await screen.findByRole("button", { name: "Re-execute collection" }));
    expect(await screen.findByText("Retry queued. Waiting for the next attempt.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Attempt 1" })).toBeInTheDocument();
    expect(screen.getByText("data/raw/us/one.csv")).toBeInTheDocument();
    expect(screen.getByText(`SHA256: ${"a".repeat(64)}`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Re-execute collection" })).toBeNull();
    expect(apiFetch).toHaveBeenCalledWith("/tasks/same-uuid/retry", { method: "POST" });
  });

  it("preserves the previous run and explains a same-country conflict", async () => {
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === "POST" ? Promise.reject(new ApiError(409, "US already active")) : Promise.resolve(path === "/collection/lines" ? lines : []));
    show(<CollectionView initialCountry="US" />);
    const line = (await screen.findByRole("heading", { name: "Surveillance" })).closest("section")!;
    fireEvent.click(within(line).getByRole("button", { name: "Start collection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Another run for this country is active.");
    expect(screen.getByRole("alert")).toHaveTextContent("US already active");
    expect(within(line).getByRole("link", { name: "Open latest run" })).toHaveAttribute("href", "/collection/runs/same-uuid");
    await waitFor(() => expect(within(line).getByRole("button", { name: "Start collection" })).toBeEnabled());
  });

  it("provides every batch task reference and deduplicates disease IDs", async () => {
    vi.mocked(apiFetch).mockImplementation((path, init) => Promise.resolve(init?.method === "POST" ? { created_tasks: [{ task_uuid: "first" }, { task_uuid: "second" }], skipped: ["third"] } : path === "/collection/lines" ? lines : []));
    show(<CollectionView />);
    fireEvent.change(await screen.findByLabelText("Disease IDs"), { target: { value: "D1， D2, D1" } });
    const line = screen.getByRole("heading", { name: "Knowledge" }).closest("section")!;
    fireEvent.click(within(line).getByRole("button", { name: "Start collection" }));
    expect(await screen.findByRole("link", { name: "Open queued run 1" })).toHaveAttribute("href", "/collection/runs/first");
    expect(screen.getByRole("link", { name: "Open queued run 2" })).toHaveAttribute("href", "/collection/runs/second");
    expect(apiFetch).toHaveBeenCalledWith("/collection/runs", { method: "POST", body: JSON.stringify({ line: "knowledge", disease_ids: ["D1", "D2"] }) });
  });

  it("localizes stage and retry controls with the existing language store", async () => {
    useAppStore.setState({ lang: "zh" });
    vi.mocked(apiFetch).mockResolvedValue(completedRun);
    show(<RunView taskUuid="same-uuid" />);
    expect(await screen.findByRole("button", { name: "重新执行收集" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "收集" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "整理" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "入库" })).toBeInTheDocument();
    expect(screen.queryByText("Source artifacts")).toBeNull();
  });

  it("filters schedule kinds without hiding collection lines", async () => {
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path === "/collection/lines" ? lines : [
      { id: "ingestion:us", kind: "ingestion", name: "US daily", enabled: true, last_status: "completed" },
      { id: "release:site", kind: "release", name: "Site daily", enabled: false, last_status: "completed" },
    ]));
    show(<CollectionView />);
    await screen.findByText("Site daily");
    fireEvent.change(screen.getByLabelText("Filter schedules"), { target: { value: "ingestion" } });
    expect(screen.getByText("US daily")).toBeInTheDocument();
    expect(screen.queryByText("Site daily")).toBeNull();
    expect(screen.getByRole("heading", { name: "Surveillance" })).toBeInTheDocument();
  });

  it("does not erase history while a retry submission remains pending", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((done) => { resolve = done; });
    vi.mocked(apiFetch).mockImplementation((_path, init) => init?.method === "POST" ? pending : Promise.resolve({ ...completedRun, status: "failed" }));
    show(<RunView taskUuid="same-uuid" />);
    const retryButton = await screen.findByRole("button", { name: "Retry this run" });
    fireEvent.click(retryButton);
    fireEvent.click(retryButton);
    expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Attempt 1" })).toBeInTheDocument();
    expect(screen.getByText("data/raw/us/one.csv")).toBeInTheDocument();
    await act(async () => resolve({ status: "queued" }));
  });
});


describe("run history bookmarks", () => {
  it("preserves repeated, encoded and empty values in the run-detail redirect", async () => {
    const values = { task: ["run / 1", "second"], kind: ["ingestion", "literature"], search: "a+b & c/中文", empty: "" };
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) for (const part of Array.isArray(value) ? value : [value]) params.append(key, part);
    await expect(RunsPage({ searchParams: Promise.resolve(values) })).rejects.toThrow(`/collection/runs/run%20%2F%201?${params}`);
  });
});
