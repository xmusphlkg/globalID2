import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "@/lib/api";
import type { CountrySourceConfig } from "@/features/operations/sources/api";
import { CreateCrawlModal } from "./create-crawl-modal";

vi.mock("@/lib/api", async (importOriginal) => ({ ...await importOriginal<typeof import("@/lib/api")>(), apiFetch: vi.fn() }));
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

const config = {
  country_code: "CA", supports_crawl: true, default_fill_missing: true,
  source_options: [{ value: "history", label: "Reviewed history", source_kind: "history", supports_start_year: true, default_start_year: 2003, default_fill_missing: false, supports_fill_missing: true,
    source_policy: { supports_current_month: true, default_include_current_month: true, dynamic_revision_enabled: true, default_revision_window: 8, default_revision_window_months: 3 } }],
} as CountrySourceConfig;

function mount(sourceConfig = config) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const result = render(<QueryClientProvider client={client}><CreateCrawlModal open countryId={1} countryName="Canada" countryCode=" ca " sourceConfig={sourceConfig} lang="en" onClose={vi.fn()} /></QueryClientProvider>);
  return { ...result, invalidate };
}

describe("source crawl creation", () => {
  it("submits source-specific defaults once and refreshes tasks and flow after success", async () => {
    let finish!: (value: unknown) => void;
    vi.mocked(apiFetch).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { invalidate } = mount();
    await screen.findByRole("dialog");
    const form = document.querySelector("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    const [url, request] = vi.mocked(apiFetch).mock.calls[0];
    expect(url).toBe("/collection/runs");
    expect(JSON.parse(request!.body as string)).toEqual({ line: "surveillance", country_code: "CA", source: "history", force: false, process: true, save_raw: true, fill_missing: false, include_current_month: true, revision_window_months: 8, start_year: 2003, priority: "normal" });
    finish({ task_uuid: "local-mocked-task" });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["tasks"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sources-flow"] });
  });

  it("gates unsupported revision/current-month options and surfaces conflicts", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(409, "Existing active collection"));
    mount({ ...config, source_options: [{ ...config.source_options[0], source_policy: { ...config.source_options[0].source_policy!, supports_current_month: false, dynamic_revision_enabled: false } }] });
    await screen.findByRole("dialog");
    fireEvent.submit(document.querySelector("form")!);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Existing active collection"));
    expect(JSON.parse(vi.mocked(apiFetch).mock.calls[0][1]!.body as string)).toMatchObject({ include_current_month: false, revision_window_months: 3 });
  });
});
