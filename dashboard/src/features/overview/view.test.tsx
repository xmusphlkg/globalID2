import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import View from "./view";
import { apiFetch } from "@/lib/api";
const refresh = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ useControlPlaneOverview: () => ({ isError: true, isLoading: false, isFetching: false, error: new Error("Runtime summary unavailable"), refetch: refresh }) }));
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const summary = { failed_stages: [{task_uuid: "failed-1",stage:"store",attempt:2,error:"Invalid record"}],model_queue:{queued:4,leased:1},pending_publish:{surveillance:2},overdue_schedules:[] };
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><View /></QueryClientProvider>);
}
it("keeps failed run actions usable when the separate runtime summary fails", async () => {
  vi.mocked(apiFetch).mockResolvedValue(summary);
  show();
  expect(await screen.findByRole("link", {name:/Failed store/})).toHaveAttribute("href", "/collection/runs/failed-1");
  expect(screen.getByText("Runtime summary unavailable")).toBeVisible();
  fireEvent.click(screen.getByRole("button", {name:"Refresh"}));
  await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
  expect(refresh).toHaveBeenCalledOnce();
});
it("recovers a failed collection summary without displaying misleading zero counts", async () => {
  vi.mocked(apiFetch).mockRejectedValueOnce(new Error("Collection summary unavailable")).mockResolvedValue(summary);
  show();
  await screen.findByText("Collection summary unavailable");
  expect(screen.queryByRole("link", {name:/Unpublished/})).toBeNull();
  fireEvent.click(screen.getAllByRole("button", {name:"Try again"})[0]);
  expect(await screen.findByRole("link", {name:/Unpublished 2/})).toHaveAttribute("href", "/publish");
});
