import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/api";
import { RunRoute } from "./run-route";
const replace=vi.hoisted(() => vi.fn());
vi.mock("next/navigation",() => ({useRouter:() => ({replace}),useSearchParams:() => new URLSearchParams("task=run-1&tag=a&tag=%E4%B8%AD%E6%96%87&kind=release&empty=")}));
vi.mock("@/lib/api",() => ({apiFetch:vi.fn()}));
vi.mock("./collection",() => ({RunView:() => <p>Collection stage detail</p>}));
afterEach(() => {cleanup();vi.clearAllMocks();});
function show() {render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><RunRoute taskUuid="run-1" /></QueryClientProvider>);}
it("moves a legacy release bookmark to publication while retaining query values",async () => {
 vi.mocked(apiFetch).mockResolvedValue({task_uuid:"run-1",task_type:"export_data",line:null});show();
 await waitFor(() => expect(replace).toHaveBeenCalled());
 const url=new URL(replace.mock.calls[0][0],"http://localhost");
 expect(url.pathname).toBe("/publish/tasks");expect(url.searchParams.get("detail")).toBe("run-1");expect(url.searchParams.getAll("tag")).toEqual(["a","中文"]);expect(url.searchParams.has("empty")).toBe(true);
 expect(screen.queryByText("Collection stage detail")).toBeNull();
});
it("routes knowledge generation to AI even when the old API labels it as collection",async () => {
 vi.mocked(apiFetch).mockResolvedValue({task_uuid:"run-1",task_type:"update_disease_knowledge",line:"knowledge"});show();
 await waitFor(() => expect(replace).toHaveBeenCalledWith(expect.stringContaining("/models/tasks?")));
});
it("keeps actual source collection on the three-stage run page",async () => {
 vi.mocked(apiFetch).mockResolvedValue({task_uuid:"run-1",task_type:"refresh_disease_knowledge_sources",line:"knowledge"});show();
 expect(await screen.findByText("Collection stage detail")).toBeVisible();expect(replace).not.toHaveBeenCalled();
});
