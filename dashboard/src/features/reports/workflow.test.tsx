import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/api";
import { ReportWorkflow } from "./workflow";

vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const workflow = {
  country_code: "JP", method_version: "report_v4.5", max_figures: 2, max_figures_per_section: 1,
  figure_catalog: [{ figure_type: "epidemic_curve", section_type: "disease_context", title: { en: "Reported case curve", zh: "病例时间曲线" } }],
  schedule: { enabled: true, interval_minutes: 15 },
  periods: [{ period_start: "2026-01-05", period_end: "2026-01-11", latest_observation: "2026-01-05", frequency: "weekly", record_count: 85, fresh: false, state: "ready" }],
};
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><ReportWorkflow lang="zh" /></QueryClientProvider>);
}

it("shows the dated Japan scope and submits once through the existing scheduler", async () => {
  let submitted = false;
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === "POST") { submitted = true; return Promise.resolve({ status: "queued", task_uuid: "jp-run" }); }
    if (path === "/tasks/jp-run") return Promise.resolve({ status: "running", progress: 45 });
    return Promise.resolve({ ...workflow, periods: workflow.periods.map(period => ({ ...period, state: submitted ? "active" : "ready" })) });
  });
  show();
  expect(await screen.findByText("历史数据，仅手动生成；不能作为当前疫情报告。")).toBeInTheDocument();
  const button = await screen.findByRole("button", { name: "按所示周期生成日本报告" });
  fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(screen.getByText("45%")).toBeInTheDocument());
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(apiFetch).toHaveBeenCalledWith("/schedules/report:reports-auto/runs", { method: "POST" });
  expect(button).toBeDisabled();
  expect(screen.getByRole("link", { name: "进入增量发布" })).toHaveAttribute("href", "/publish");
});

it("does not offer duplicate generation for an existing period", async () => {
  vi.mocked(apiFetch).mockResolvedValue({ ...workflow, periods: [{ ...workflow.periods[0], state: "existing" }] });
  show();
  expect(await screen.findByText("已有该周期报告，跳过重复生成")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "按所示周期生成日本报告" })).toBeDisabled();
});
