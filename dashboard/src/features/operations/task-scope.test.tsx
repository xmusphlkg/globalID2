import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AI_TASK_TYPES, COLLECTION_TASK_TYPES, PUBLISH_TASK_TYPES, scopedScheduleKinds, scopedTaskTypes, taskScopeHref } from "./task-scope";
import TasksView from "./tasks/all/view";
import SchedulesView from "./schedules/view";
import { type TaskItem, usePaginatedTasks, useTaskDetail } from "./tasks/api";
import { type PublicationKind, publicationKindLabel } from "./publication-kind";
import { useAppStore } from "@/stores/app-store";
import { controlPlaneClient } from "@/generated/client";
const navigation = vi.hoisted(() => ({ query: new URLSearchParams(), pathname: "/collection/runs", replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname, useSearchParams: () => navigation.query, useRouter: () => ({ replace: navigation.replace, push: navigation.push }) }));
vi.mock("./tasks/api", () => ({ usePaginatedTasks: vi.fn(() => ({ data: { items: [], totalCount: 0 }, isLoading: false, isError: false })), useTaskDetail: vi.fn(() => ({ data: undefined, isFetching: false })), useTaskEventStream: vi.fn() }));
vi.mock("@/components/charts/Chart", () => ({ Chart: () => null }));
vi.mock("@/components/tasks/TaskDetailPanel", () => ({ TaskDetailPanel: () => null }));
vi.mock("@/generated/client", () => ({ controlPlaneClient: { GET: vi.fn(), POST: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  navigation.query = new URLSearchParams(); navigation.pathname = "/collection/runs";
  useAppStore.setState({ lang: "en" });
  vi.mocked(usePaginatedTasks).mockReturnValue({ data: { items: [], totalCount: 0 }, isLoading: false, isError: false } as never);
  vi.mocked(useTaskDetail).mockReturnValue({ data: undefined, isFetching: false } as never);
});
function show(element: React.ReactNode) { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>); }

describe("task workspace boundaries", () => {
  it("keeps publication and model generation outside source collection", () => {
    expect(COLLECTION_TASK_TYPES).toEqual(["crawl_data", "sync_literature", "refresh_situation_sources", "refresh_disease_knowledge_sources"]);
    expect(PUBLISH_TASK_TYPES).toEqual(["export_data"]);
    expect(AI_TASK_TYPES).toContain("update_disease_knowledge");
    expect(AI_TASK_TYPES).toContain("enrich_literature");
    expect(AI_TASK_TYPES).toContain("discover_literature_gaps");
  });
  it("intersects URL filters with the mandatory whitelist", () => {
    expect(scopedTaskTypes("collection", "export_data")).toEqual(COLLECTION_TASK_TYPES);
    expect(scopedTaskTypes("collection", "export_data,sync_literature,sync_literature")).toEqual(["sync_literature"]);
    expect(scopedTaskTypes("publish", "crawl_data")).toEqual(["export_data"]);
    expect(scopedTaskTypes("ai", "update_disease_knowledge")).toEqual(["update_disease_knowledge"]);
  });
  it("passes scope to the server before pagination without a persisted country filter", () => {
    navigation.query = new URLSearchParams("task_type=export_data&country=US&page=2");
    show(<TasksView scope="collection" />);
    expect(usePaginatedTasks).toHaveBeenLastCalledWith(undefined, COLLECTION_TASK_TYPES.join(","), undefined, undefined, 50, 50);
  });
  it("isolates publication and AI task queries", () => {
    const view = show(<TasksView scope="publish" />);
    expect(usePaginatedTasks).toHaveBeenLastCalledWith(undefined, "export_data", undefined, undefined, 50, 0);
    view.rerender(<TasksView scope="ai" />);
    expect(usePaginatedTasks).toHaveBeenLastCalledWith(undefined, AI_TASK_TYPES.join(","), undefined, undefined, 50, 0);
  });
  it("keeps task identifiers encoded while routing by the actual task type", () => {
    expect(taskScopeHref({ task_uuid: "a/b ?", task_type: "crawl_data" })).toBe("/collection/runs/a%2Fb%20%3F");
    expect(taskScopeHref({ task_uuid: "release-1", task_type: "export_data" })).toBe("/publish/tasks?detail=release-1");
    expect(taskScopeHref({ task_uuid: "brief-1", task_type: "update_disease_knowledge" })).toBe("/models/tasks?detail=brief-1");
  });
  it("labels static and JSON publications from their compact server classification", () => {
    const release = (uuid: string, kind: PublicationKind, name: string): TaskItem => ({
      id: 1, task_uuid: uuid, task_name: name, task_type: "export_data", publication_kind: kind,
      status: "completed", priority: "normal", progress: 100, country_id: null, country_code: null,
      country_name: null, report_id: null, description: null, last_error: null,
      created_at: "2026-11-07T01:00:00Z", started_at: null, completed_at: null, actual_duration: null,
      workbook_count: 0, cancel_requested: false, cancel_requested_at: null,
    });
    const items = [release("static", "static", "A task named Situation JSON"),
      release("surv/a ?", "surveillance_json", "Observation update"),
      release("situation", "situation_json", "Notice update"), release("report", "reports", "Report update")];
    vi.mocked(usePaginatedTasks).mockReturnValue({ data: { items, totalCount: items.length }, isLoading: false, isError: false } as never);
    show(<TasksView scope="publish" />);
    for (const label of ["Static site", "Surveillance JSON", "Situation JSON", "Reports publication"]) {
      expect(screen.getByText(label, { exact: true })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Observation update" })).toHaveAttribute("href", "/publish/tasks?detail=surv%2Fa%20%3F");
    expect(publicationKindLabel("situation_json", "zh")).toBe("态势 JSON");
    expect(publicationKindLabel(undefined, "en")).toBeNull();
  });
  it("shows publication classification when a deep-linked task is outside the current page", () => {
    navigation.query = new URLSearchParams("detail=outside-page");
    vi.mocked(useTaskDetail).mockReturnValue({ data: { task_uuid: "outside-page", task_name: "External data publication",
      task_type: "export_data", publication_kind: "surveillance_json", status: "completed",
      created_at: "2026-11-07T01:00:00Z" }, isFetching: false } as never);
    show(<TasksView scope="publish" />);
    expect(screen.getByRole("heading", { name: "External data publication" })).toBeInTheDocument();
    expect(screen.getByText("Surveillance JSON", { exact: true })).toBeInTheDocument();
  });
});

describe("schedule workspace boundaries", () => {
  it("does not admit release schedules through a collection URL", () => {
    expect(scopedScheduleKinds("collection", "release")).toEqual(["ingestion", "literature", "situation"]);
    expect(scopedScheduleKinds("publish", "ingestion")).toEqual(["release"]);
  });
  it("requests only collection kinds and rejects an unexpected release row", async () => {
    navigation.pathname = "/collection/schedules";
    navigation.query = new URLSearchParams("kind=release");
    vi.mocked(controlPlaneClient.GET).mockResolvedValue({ data: { data: [{ id: "release:site-release", kind: "release", name: "Unexpected publication" }] } } as never);
    show(<SchedulesView scope="collection" />);
    await waitFor(() => expect(controlPlaneClient.GET).toHaveBeenCalledTimes(3));
    expect(vi.mocked(controlPlaneClient.GET).mock.calls.map((call) => (call[1] as { params?: { query?: { kind?: string } } } | undefined)?.params?.query)).toEqual([{ kind: "ingestion" }, { kind: "literature" }, { kind: "situation" }]);
    expect(screen.queryByText("Unexpected publication")).toBeNull();
    expect(controlPlaneClient.POST).not.toHaveBeenCalled();
  });
});
