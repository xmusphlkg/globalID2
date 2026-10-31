import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "@/lib/api";
import {
  CollectionSummary,
  CollectionView,
  ModelsView,
  PublishView,
  RunView,
} from "./views";
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  apiFetch: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function show(element: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>{element}</QueryClientProvider>,
    ),
  };
}
describe("control-plane workflow", () => {
  it("shows failed stages and artifact provenance without download actions", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      task_uuid: "run-1",
      status: "failed",
      attempt: 1,
      stages: ["collect", "organize", "store"].map((stage) => ({
        stage,
        attempt: 1,
        status: stage === "organize" ? "failed" : "skipped",
        started_at: "2026-10-30T00:00:00Z",
        finished_at: "2026-10-30T00:00:01Z",
        metrics: {},
        error: stage === "organize" ? "Invalid source columns" : null,
      })),
      artifacts: [
        {
          attempt: 1,
          relative_path: "data/raw/us/run-1/page.csv",
          sha256: "abc123",
          byte_size: 42,
          source_url: "https://example.org/source",
          kind: "csv_page",
        },
      ],
      publication: [],
    });
    show(<RunView taskUuid="run-1" />);
    expect(
      await screen.findByText("Invalid source columns"),
    ).toBeInTheDocument();
    for (const name of ["collect", "organize", "store"])
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    expect(screen.getByText("data/raw/us/run-1/page.csv")).toBeInTheDocument();
    expect(screen.getByText("SHA256: abc123")).toBeInTheDocument();
    expect(
      screen.getByText("Source: https://example.org/source"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download/i })).toBeNull();
  });
  it("lists unpublished scopes and only offers publication actions", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      surveillance: ["US"],
      literature: false,
      situation: true,
      knowledge: ["D001"],
    });
    show(<PublishView />);
    expect(await screen.findByText("US")).toBeInTheDocument();
    expect(screen.getByText("D001")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Publish site-release" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /collect|crawl|fetch|download/i }),
    ).toBeNull();
  });
});

const emptyRun = {
  task_uuid: "run-1",
  status: "queued",
  attempt: 0,
  stages: [],
  artifacts: [],
  publication: [],
};
const pending = {
  surveillance: [],
  literature: false,
  situation: false,
  knowledge: [],
};
const workItem = {
  id: 1,
  parent_task_uuid: "run-1",
  line: "knowledge_brief",
  status: "queued",
  priority: "low",
  attempt: 1,
  available_at: "2026-10-30T00:00:00Z",
  error: null,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("control-plane recovery and interactions", () => {
  it("shows collection and schedule loading states before empty results", async () => {
    const response = deferred<unknown[]>();
    vi.mocked(apiFetch).mockReturnValue(response.promise);
    show(<CollectionView />);
    expect(screen.getByText("Loading collection lines…")).toBeInTheDocument();
    expect(screen.getByText("Loading schedules…")).toBeInTheDocument();
    expect(screen.queryByText("No collection lines available.")).toBeNull();
    await act(async () => response.resolve([]));
    expect(
      await screen.findByText("No collection lines available."),
    ).toBeInTheDocument();
    expect(screen.getByText("No schedules configured.")).toBeInTheDocument();
  });

  it("recovers a run read error without retrying the business task", async () => {
    vi.mocked(apiFetch)
      .mockRejectedValueOnce(new ApiError(404, "Task not found"))
      .mockResolvedValue(emptyRun);
    show(<RunView taskUuid="run-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Task not found",
    );
    expect(screen.queryByRole("button", { name: "Retry this run" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reload run" }));
    expect(
      await screen.findByText("No collection stages recorded for this task."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No publication changes recorded."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Attempt 0/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });

  it("orders attempts numerically and shows missing stages in the current attempt", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      ...emptyRun,
      status: "failed",
      attempt: 10,
      stages: [2, 1].map((attempt) => ({
        stage: "collect",
        attempt,
        status: "failed",
        started_at: "2026-10-30T00:00:00Z",
        finished_at: "2026-10-30T00:00:01Z",
        metrics: {},
        error: "source failed",
      })),
    });
    show(<RunView taskUuid="run-1" />);
    await screen.findByRole("heading", { name: "Attempt 10" });
    expect(
      screen
        .getAllByRole("heading", { name: /^Attempt/ })
        .map((el) => el.textContent),
    ).toEqual(["Attempt 10", "Attempt 2", "Attempt 1"]);
    const current = screen
      .getByRole("heading", { name: "Attempt 10" })
      .closest("section")!;
    expect(within(current).getAllByText("Not started")).toHaveLength(3);
  });

  it("prevents duplicate retry submissions and keeps historical stages", async () => {
    const response = deferred<unknown>();
    const run = { ...emptyRun, status: "failed", attempt: 1 };
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      init?.method === "POST" ? response.promise : Promise.resolve(run),
    );
    show(<RunView taskUuid="run-1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Retry this run" }),
    );
    const retry = screen.getByRole("button", { name: "Retrying…" });
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(
      vi
        .mocked(apiFetch)
        .mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
    run.status = "queued";
    await act(async () =>
      response.resolve({ task_uuid: "run-1", status: "queued" }),
    );
    expect(
      await screen.findByText("Retry queued. Waiting for the next attempt."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry this run" })).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Attempt 1" }),
    ).toBeInTheDocument();
  });

  it("validates collection input and explicitly disables fill_missing", async () => {
    const line = {
      line: "surveillance",
      next_run_at: null,
      latest_task_uuid: null,
      latest_stage: null,
      latest_stage_status: null,
      pending_publish_count: 0,
      schedule_ids: [],
    };
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      Promise.resolve(
        init?.method === "POST"
          ? { task_uuid: "queued-run" }
          : path === "/collection/lines"
            ? [line]
            : [],
      ),
    );
    show(<CollectionView />);
    const country = await screen.findByLabelText("Country code");
    fireEvent.change(country, { target: { value: " " } });
    expect(
      screen.getByRole("button", { name: "Start collection" }),
    ).toBeDisabled();
    fireEvent.change(country, { target: { value: " us " } });
    fireEvent.click(screen.getByRole("button", { name: "Start collection" }));
    expect(
      await screen.findByRole("link", { name: "Open queued run" }),
    ).toHaveAttribute("href", "/collection/runs/queued-run");
    expect(apiFetch).toHaveBeenCalledWith("/collection/runs", {
      method: "POST",
      body: JSON.stringify({
        line: "surveillance",
        country_code: "US",
        fill_missing: false,
      }),
    });
  });

  it("rejects separator-only knowledge IDs and reports skipped tasks", async () => {
    const line = {
      line: "knowledge",
      next_run_at: null,
      latest_task_uuid: null,
      pending_publish_count: 0,
    };
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      Promise.resolve(
        init?.method === "POST"
          ? { created_tasks: [], skipped: [{ disease_id: "D001" }] }
          : path === "/collection/lines"
            ? [line]
            : [],
      ),
    );
    show(<CollectionView />);
    const ids = await screen.findByLabelText("Disease IDs");
    fireEvent.change(ids, { target: { value: " , , " } });
    expect(
      screen.getByRole("button", { name: "Start collection" }),
    ).toBeDisabled();
    fireEvent.change(ids, { target: { value: "D001" } });
    fireEvent.click(screen.getByRole("button", { name: "Start collection" }));
    expect(
      await screen.findByText("Tasks queued / skipped: 0 / 1"),
    ).toBeInTheDocument();
  });

  it("shows route and queue empty states independently", async () => {
    vi.mocked(apiFetch).mockResolvedValue([]);
    show(<ModelsView />);
    expect(
      await screen.findByText("No model routes configured."),
    ).toBeInTheDocument();
    expect(screen.getByText("No work items in this status.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Status"), {
      target: { value: "failed" },
    });
    expect(
      await screen.findByText("No work items in this status."),
    ).toBeInTheDocument();
  });

  it("does not show a late detail response for a previously selected item", async () => {
    const first = deferred<unknown>();
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path === "/models/routes") return Promise.resolve([]);
      if (path === "/models/queue/1") return first.promise;
      if (path === "/models/queue/2")
        return Promise.resolve({ payload: "second detail" });
      return Promise.resolve([workItem, { ...workItem, id: 2 }]);
    });
    show(<ModelsView />);
    fireEvent.click(
      await screen.findByRole("button", { name: "#1 Knowledge brief" }),
    );
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "#2 Knowledge brief" }));
    expect(await screen.findByText(/second detail/)).toBeInTheDocument();
    await act(async () => first.resolve({ payload: "first detail" }));
    expect(screen.queryByText(/first detail/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("heading", { name: /Work item #/ })).toBeNull();
  });

  it("refreshes a newly leased item after a priority conflict", async () => {
    let leased = false;
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === "POST") {
        leased = true;
        return Promise.reject(
          new ApiError(409, "Only queued work items can change priority"),
        );
      }
      return Promise.resolve(
        path === "/models/routes"
          ? []
          : [{ ...workItem, status: leased ? "leased" : "queued" }],
      );
    });
    show(<ModelsView />);
    fireEvent.change(await screen.findByLabelText("Priority for work item 1"), {
      target: { value: "high" },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "only queued work can change priority",
    );
    await waitFor(() =>
      expect(screen.getByText("Leased", { selector: "span" })).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Priority for work item 1")).toBeDisabled();
  });

  it("clears failed preflight output and submits full publication with a run link", async () => {
    let preflightAttempts = 0;
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path === "/releases/site-release/checks")
        return ++preflightAttempts === 1
          ? Promise.reject(new Error("Preflight unavailable"))
          : Promise.resolve({ overall_ready: true, checked_at: "2026-10-30T00:00:00Z", blockers: [], git: { read_access_ok: true, write_access_ok: true }, cloudflare: { token_present: true, account_id_present: true, project_access_ok: true }, commands: { python_exists: true, wrangler_available: true }, repository_boundary: { enforced: true } });
      return Promise.resolve(
        path === "/publish/pending"
          ? pending
          : { task_uuid: "release-1", status: "queued" },
      );
    });
    show(<PublishView />);
    expect(
      await screen.findByText("No unpublished changes."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Preflight unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    expect(
      await screen.findByRole("heading", { name: "Preflight results" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByLabelText("Full generation"));
    fireEvent.click(
      screen.getByRole("button", { name: "Publish site-release" }),
    );
    expect(
      await screen.findByRole("link", { name: "Open release run" }),
    ).toHaveAttribute("href", "/publish/tasks?detail=release-1");
    expect(apiFetch).toHaveBeenCalledWith("/publish/runs", {
      method: "POST",
      body: JSON.stringify({ job_id: "site-release", full: true }),
    });
  });

  it("distinguishes an already active release from a newly queued release", async () => {
    vi.mocked(apiFetch).mockImplementation((path) =>
      Promise.resolve(
        path === "/publish/pending"
          ? pending
          : {
              status: "skipped",
              reason: "already_running",
              task_uuid: "release-1",
            },
      ),
    );
    show(<PublishView />);
    fireEvent.click(
      screen.getByRole("button", { name: "Publish site-release" }),
    );
    expect(
      await screen.findByText("Release not queued: A release is already running."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Release request accepted/)).toBeNull();
  });

  it("links failed overview stages directly to run details", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      failed_stages: [
        {
          task_uuid: "run-1",
          task_name: "Refresh GIDS Research Radar",
          task_type: "sync_literature",
          stage: "organize",
          attempt: 2,
          error: "Invalid source",
        },
      ],
      model_queue: { queued: 3, leased: 1 },
      pending_publish: { surveillance: 2 },
      overdue_schedules: [],
    });
    show(<CollectionSummary />);
    expect(
      await screen.findByRole("link", { name: /Failed organize/ }),
    ).toHaveAttribute("href", "/collection/runs/run-1");
    expect(screen.getByText("Refresh GIDS Research Radar")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Unpublished 2/ }),
    ).toHaveAttribute("href", "/publish");
  });

  it("opens a non-collection failed stage in that task workspace", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      failed_stages: [
        {
          task_uuid: "agent-1",
          task_name: "Model operation: knowledge_reconcile",
          task_type: "agent_workflow",
          stage: "store",
          attempt: 1,
          error: "TimeoutError",
        },
      ],
      model_queue: { queued: 0, leased: 0 },
      pending_publish: {},
      overdue_schedules: [],
    });
    show(<CollectionSummary />);
    expect(
      await screen.findByRole("link", { name: /Model operation: knowledge_reconcile/ }),
    ).toHaveAttribute("href", "/models/tasks?detail=agent-1");
  });
});

it("allows retrying a later failure even when a non-collection task has no attempt history", async () => {
  let status = "failed";
  vi.mocked(apiFetch).mockImplementation((_path, init) => {
    if (init?.method === "POST") {
      status = "queued";
      return Promise.resolve({ status });
    }
    return Promise.resolve({ ...emptyRun, status });
  });
  const { client } = show(<RunView taskUuid="run-1" />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Retry this run" }),
  );
  expect(
    await screen.findByText("Retry queued. Waiting for the next attempt."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry this run" })).toBeNull();
  status = "failed";
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["/collection/runs/run-1"] });
  });
  expect(
    await screen.findByRole("button", { name: "Retry this run" }),
  ).toBeEnabled();
  expect(
    screen.queryByText("Retry queued. Waiting for the next attempt."),
  ).toBeNull();
});
