import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useInteractionContext } from "./use-interaction-context";
import { useAIInteractions, useAIInteractionSummary } from "@/features/ai/api";
import { useTaskDetail, useTaskEventStream } from "@/features/operations/tasks/api";

const navigation = vi.hoisted(() => ({ query: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/ai/interactions",
  useSearchParams: () => new URLSearchParams(navigation.query),
  useRouter: () => ({ replace: navigation.replace }),
}));
vi.mock("@/stores/app-store", () => ({ useAppStore: () => ({ countryCode: "CA", countryName: "Canada", lang: "en" }) }));
vi.mock("@/features/ai/api", () => ({
  useAIInteractions: vi.fn(() => ({ data: [], isLoading: false, isError: false, error: null })),
  useAIInteractionSummary: vi.fn(() => ({ data: undefined })),
}));
vi.mock("@/features/operations/tasks/api", () => ({
  useTaskDetail: vi.fn(() => ({ data: undefined, isFetching: false })),
  usePaginatedTasks: vi.fn(() => ({ data: { items: [] } })),
  useTaskEventStream: vi.fn(),
}));
beforeEach(() => { navigation.query = ""; vi.clearAllMocks(); });
afterEach(cleanup);

describe("interaction URL and live query context", () => {
  it("keeps legacy task links and follows navigation to another task", async () => {
    navigation.query = "task_uuid=task-one&disease=Measles";
    const { result, rerender } = renderHook(useInteractionContext);
    expect(result.current.activeTaskUuid).toBe("task-one");
    expect(useTaskDetail).toHaveBeenLastCalledWith("task-one");
    expect(navigation.replace).toHaveBeenCalledWith("/ai/interactions?disease=Measles&task=task-one", { scroll: false });
    expect(useAIInteractions).toHaveBeenLastCalledWith(expect.objectContaining({ taskUuid: "task-one", disease: "Measles", limit: 1000 }), { refetchIntervalMs: 3000 });
    navigation.query = "task=task-two";
    rerender();
    await waitFor(() => expect(result.current.activeTaskUuid).toBe("task-two"));
    expect(result.current.diseaseFilter).toBe("");
    expect(useTaskDetail).toHaveBeenLastCalledWith("task-two");
  });

  it("preserves report compatibility mode and its disease filter", () => {
    navigation.query = "uuid=report-one&disease=Influenza";
    const { result } = renderHook(useInteractionContext);
    expect(result.current.hasTaskScope).toBe(true);
    expect(result.current.activeTaskUuid).toBe("");
    expect(result.current.activeReportUuid).toBe("report-one");
    expect(useAIInteractionSummary).toHaveBeenLastCalledWith(expect.objectContaining({ reportUuid: "report-one", disease: "Influenza" }), { refetchIntervalMs: 3000 });
    expect(useTaskDetail).toHaveBeenLastCalledWith(null);
  });

  it("retains country-scoped default limits and event invalidation families", () => {
    renderHook(useInteractionContext);
    expect(useAIInteractions).toHaveBeenLastCalledWith(expect.objectContaining({ countryCode: "CA", limit: 200 }), { refetchIntervalMs: 5000 });
    expect(useTaskEventStream).toHaveBeenLastCalledWith({ extraQueryKeys: [["ai-interactions"], ["ai-interactions-summary"], ["reports"], ["report-runs"]] });
  });
});
