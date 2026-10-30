import { expect, test as base, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Every API request is intercepted, including mutations. These tests never enqueue
// real collection/model/release work, even when pointed at a running dashboard.
const taskUuid = "11111111-1111-4111-8111-111111111111";
const releaseUuid = "22222222-2222-4222-8222-222222222222";
const timestamp = "2026-10-30T00:00:00Z";
const sourcePath = `data/raw/surveillance/${taskUuid}/source-${"long-".repeat(25)}.csv`;
const run = {
  task_uuid: taskUuid,
  task_type: "crawl_data",
  line: "surveillance",
  country_code: "US",
  source: "nndss_api",
  status: "failed",
  attempt: 2,
  stages: [1, 2].flatMap((attempt) =>
    ["collect", "organize", "store"].map((stage) => ({
      stage,
      attempt,
      status:
        stage === "collect"
          ? "completed"
          : stage === "organize"
            ? "failed"
            : "skipped",
      metrics: { artifact_count: 1, rows: 42 },
      error:
        stage === "organize"
          ? `Invalid source columns (attempt ${attempt})`
          : null,
      started_at: timestamp,
      finished_at: "2026-10-30T00:00:02Z",
    })),
  ),
  artifacts: [1, 2].map((attempt) => ({
    attempt,
    relative_path: sourcePath,
    sha256: "a".repeat(64),
    byte_size: 120034,
    source_url: "https://example.org/source.csv",
    kind: "csv_page",
  })),
  publication: [
    {
      line: "surveillance",
      scope_key: "US",
      published_at: null,
      published_task_uuid: null,
    },
  ],
};
const pending = {
  surveillance: ["US", "JP"],
  literature: true,
  situation: false,
  knowledge: ["D001", "sources"],
};
const workItem = {
  id: 17,
  parent_task_uuid: taskUuid,
  line: "knowledge_brief",
  priority: "low",
  status: "queued",
  attempt: 2,
  available_at: timestamp,
  error: null,
  capability: "text",
  route_key: null,
  lease_owner: null,
  lease_expires_at: null,
  created_at: timestamp,
  finished_at: null,
  payload: { disease_id: "D001" },
  result: null,
};
const overview = {
  generated_at: timestamp,
  tasks: { queued: 2, running: 0, retrying: 0, failed: 1 },
  schedules: { enabled: 3, total: 4 },
  action_items: [],
  runtime: { services: [], heartbeat_available: true },
  pipeline: [],
  recent_tasks: [],
};

type State = {
  requests: { path: string; method: string; body: unknown }[];
  requestUrls: string[];
  priority: string;
  conflict: boolean;
  preflightFails: boolean;
  preflightBlocked: boolean;
  empty: boolean;
  pendingFails: boolean;
  runStatus: string;
  collectionConflict: boolean;
  publishFails: boolean;
  queueStatus: string;
  delayReads: boolean;
  releaseStatus: string;
  withCountries: boolean;
};
const test = base.extend<{ state: State }>({
  state: async ({ page }, use) => {
    const state: State = {
      requests: [],
      requestUrls: [],
      priority: "low",
      conflict: false,
      preflightFails: false,
      preflightBlocked: false,
      empty: false,
      pendingFails: false,
      runStatus: "failed",
      collectionConflict: false,
      publishFails: false,
      queueStatus: "queued",
      delayReads: false,
      releaseStatus: "queued",
      withCountries: false,
    };
    const errors: string[] = [];
    const unexpected: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/v1/**", async (route) => {
      const req = route.request();
      state.requestUrls.push(req.url());
      const url = new URL(req.url());
      const path = url.pathname.replace("/api/v1", "");
      const method = req.method();
      if (state.delayReads && method === "GET" && path === "/publish/pending")
        await new Promise((resolve) => setTimeout(resolve, 1200));
      state.requests.push({
        path,
        method,
        body: req.postData() ? req.postDataJSON() : null,
      });
      const respond = (data: unknown, status = 200) =>
        route.fulfill({ status, json: { data, meta: {} } });
      const problem = (detail: string, status = 409) =>
        route.fulfill({
          status,
          contentType: "application/problem+json",
          body: JSON.stringify({ detail }),
        });
      const allowedMutations = [
        "/collection/runs", `/tasks/${taskUuid}/retry`, "/models/queue/17/priority", "/publish/runs", "/knowledge/source-runs",
      ];
      if (method !== "GET" && !(method === "POST" && allowedMutations.includes(path))) {
        unexpected.push(`${method} ${path}`);
        return problem(`Unexpected fixture mutation: ${method} ${path}`, 500);
      }
      if (path === "/events/stream")
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: ": fixture\n\n",
        });
      if (path === "/overview") return respond(overview);
      if (path === "/countries") return respond(state.withCountries ? [
        { id: 1, code: "US", name: "United States", name_en: "United States", name_zh: "美国", is_active: true, language: "en", timezone: "America/New_York", location_type: "country" },
        { id: 2, code: "JP", name: "Japan", name_en: "Japan", name_zh: "日本", is_active: true, language: "ja", timezone: "Asia/Tokyo", location_type: "country" },
      ] : []);
      if (path === "/sources/config") return respond(["US", "JP"].map((code) => ({
        country_code: code, country_name: code === "US" ? "United States" : "Japan", country_name_en: code === "US" ? "United States" : "Japan", country_name_zh: code === "US" ? "美国" : "日本", language: "en", timezone: "UTC", supports_crawl: true, supports_fill_missing: false, default_fill_missing: false, default_source: "fixture_feed", supports_start_year: false, supports_source_file: false, supports_source_dir: false,
        source_options: [{ value: "fixture_feed", label: `Fixture ${code} feed`, label_en: `Fixture ${code} feed`, label_zh: `${code} 测试来源`, source_kind: "current", supports_start_year: false, supports_fill_missing: false, default_fill_missing: false }],
      })));
      if (path === "/sources/flow") return respond(state.empty ? [] : [{
        data_source: "Fixture surveillance source", country_code: url.searchParams.get("country_code") || "US", country_name: "United States", record_count: 42, source_series_count: 2, source_observation_count: 42, series_availability: {}, source_availability: {}, observation_quality: {}, metric_types: {}, mapping_relations: {}, comparability: {}, latest_date: "2026-10-29", stages: [], latest_task_uuid: taskUuid, latest_task_status: "completed",
      }]);
      if (path === "/disease-ontology/series") return respond([]);
      if (path === "/situation/sources") return respond(state.empty ? [] : [{
        source_id: "fixture-situation", label_en: "Fixture situation source", label_zh: "测试态势来源", agency_en: "Fixture agency", homepage: "https://example.org", endpoint: "https://example.org/feed", redistribution: "allowed", stored_notices: 12, status: "healthy", last_checked_at: timestamp, last_success_at: timestamp, last_error: null, history: [],
      }]);
      if (path === "/research-radar/dashboard") return respond({ total_articles: state.empty ? 0 : 24, published_articles: 20, review_queue: 4, excluded_articles: 0, featured_articles: 0, published_last_7_days: 4, summaries_awaiting_review: 2, latest_articles: [], latest_runs: state.empty ? [] : [{ run_uuid: "literature-run-1", source: "crossref", status: "completed", started_at: timestamp, completed_at: timestamp, from_indexed_at: timestamp, through_indexed_at: timestamp, counts: { fetched: 24, stored: 24 }, error: null }], schedule: { enabled: true, next_run_at: timestamp }, automation: {}, surveillance_context: {} });
      if (path === "/knowledge") return respond(state.empty ? [] : [{ disease_id: "D001", name_en: "Fixture disease", name_zh: "测试疾病", source_count: 2, knowledge_status: "ready", knowledge_completeness: 1, brief_statuses: {}, brief_tiers: {}, published_languages: [], blocked_languages: [] }]);
      if (path === "/knowledge/D001") return respond({ disease_id: "D001", name_en: "Fixture disease", sources: [{ id: "source-1", title: "Fixture knowledge source", url: "https://example.org/knowledge", source_type: "official", source_name: "Fixture agency", status: "completed", language: "en", content_text: "Source evidence excerpt", raw_excerpt_hash: "b".repeat(64), fetched_at: timestamp, retrieved_at: timestamp }], briefs: [] });
      if (path === "/mappings/summary") return respond({ total: 10, pending: 2, approved: 7, rejected: 1, unmapped: 2, mapped: 8, ai_review_ready_total: 2, active_release: { release_code: "fixture-release" } });
      if (path === "/mappings/coverage") return respond({ holding_series_total: 2, mapping_coverage: 0.75, countries: [{ country_code: "US", holding_series_count: 2, mapping_coverage: 0.75 }] });
      if (path === "/settings" && method === "GET") return respond({
        smtp: { source: "database", smtp_host: "", smtp_port: 587, smtp_username: "", smtp_from_email: "", smtp_use_tls: true, admin_emails_raw: "", admin_emails: [], smtp_password_present: false, smtp_configured: false, alerting_ready: false },
        github: { source: "database", github_data_share_repo_url: "", github_data_share_repo_branch: "main", github_data_share_raw_base_url: "", github_data_share_raw_base_url_effective: "", raw_archive_enabled: false, raw_archive_repo_url: "", raw_archive_branch: "main", raw_archive_configured: false, default_github_remote: "origin", default_github_branch: "main", github_configured: false, release_defaults_ready: false },
        cloudflare: { source: "database", cloudflare_api_token_present: false, cloudflare_account_id_present: false, default_cloudflare_project_name: "fixture", cloudflare_configured: false },
        site: { source: "database", public_ga4_measurement_id: "", ga4_configured: false },
      });
      if (path === "/collection/summary")
        return respond({
          failed_stages: state.empty
            ? []
            : [
                {
                  task_uuid: taskUuid,
                  stage: "organize",
                  attempt: 2,
                  error: "Invalid source columns",
                },
              ],
          model_queue: { queued: 1, leased: 0 },
          pending_publish: { surveillance: 2, knowledge: 2 },
          overdue_schedules: [],
        });
      if (path === "/collection/lines")
        return respond(
          state.empty
            ? []
            : ["surveillance", "literature", "situation", "knowledge"].map(
                (line) => ({
                  line,
                  schedule_ids: [],
                  next_run_at: timestamp,
                  latest_task_uuid: taskUuid,
                  latest_stage: "organize",
                  latest_stage_status: "failed",
                  pending_publish_count: 2,
                }),
              ),
        );
      if (path === "/schedules") {
        const requestedKind = url.searchParams.get("kind");
        return respond(state.empty ? [] : ["ingestion", "release", "literature", "situation"]
          .filter((kind) => !requestedKind || kind === requestedKind)
          .map((kind) => ({ id: `${kind}:fixture`, job_id: "fixture", name: `${kind} schedule`, kind, enabled: true, next_run_at: timestamp, last_status: "completed", interval_minutes: 30, country_code: null })));
      }
      if (path === "/tasks") {
        const selectedTypes = (url.searchParams.get("task_type") || "").split(",");
        const tasks = [
          { task_uuid: taskUuid, task_type: "crawl_data", task_name: "US source collection" },
          { task_uuid: "33333333-3333-4333-8333-333333333333", task_type: "sync_literature", task_name: "Literature source collection" },
          { task_uuid: releaseUuid, task_type: "export_data", task_name: "PUBLICATION MUST STAY SEPARATE" },
          { task_uuid: "44444444-4444-4444-8444-444444444444", task_type: "generate_report", task_name: "AI report generation" },
        ].filter((task) => !selectedTypes[0] || selectedTypes.includes(task.task_type))
          .map((task, index) => ({ ...task, id: index + 1, status: "completed", priority: "normal", progress: 100, country_id: null, country_code: null, country_name: null, report_id: null, description: null, last_error: null, created_at: timestamp, started_at: timestamp, completed_at: timestamp, actual_duration: 2, workbook_count: 0, cancel_requested: false, cancel_requested_at: null }));
        return route.fulfill({ status: 200, json: { data: tasks, meta: { pagination: { page: 1, page_size: 50, total: tasks.length, total_pages: 1 } } } });
      }
      if (path === "/runtime/services") return respond({ generated_at: timestamp, heartbeat_available: true, services: [] });
      if (path === `/tasks/${releaseUuid}`) return respond({ id: 3, task_uuid: releaseUuid, task_name: "PUBLICATION MUST STAY SEPARATE", task_type: "export_data", status: state.releaseStatus, priority: "normal", progress: 0, created_at: timestamp, started_at: null, completed_at: null, actual_duration: null, description: null, last_error: null, country_id: null, country_code: null, country_name: null, report_id: null, parent_task_id: null, input_data: { full: false }, output_data: null, workbook_count: 0, workbook_entries: [], cancel_requested: false, cancel_requested_at: null });
      if (path === `/collection/runs/${taskUuid}`)
        return respond({ ...run, status: state.runStatus });
      if (path === `/collection/runs/${releaseUuid}`)
        return respond({
          ...run,
          task_uuid: releaseUuid,
          stages: [],
          artifacts: [],
          publication: [],
          status: state.releaseStatus,
          task_type: "export_data",
          line: null,
          attempt: 0,
        });
      if (path === "/knowledge/source-runs" && method === "POST") return respond({ requested_disease_ids: ["D001"], source_groups: ["who", "who_don", "web_search", "wikidata", "wikipedia", "pubmed"], force: false, created_count: 1, skipped_count: 0, created_tasks: [{ task_uuid: taskUuid, status: "queued", resource_id: taskUuid, resource_type: "task", href: `/collection/runs/${taskUuid}` }], skipped: [] }, 202);
      if (path === "/collection/runs" && method === "POST") {
        if (state.collectionConflict) return problem("Collection already active for US");
        return respond({ task_uuid: taskUuid, status: "queued" }, 202);
      }
      if (path === `/tasks/${taskUuid}/retry` && method === "POST") {
        state.runStatus = "queued";
        return respond({ task_uuid: taskUuid, status: "queued" }, 202);
      }
      if (path === "/models/routes")
        return respond(
          state.empty
            ? []
            : [
                {
                  model_key: "fixture-model",
                  available_for_routing: false,
                  runtime_model_capacity: 4,
                  in_flight: 2,
                  rate_limit_cooldown_until: "2026-10-30T00:05:00Z",
                  runtime_failure_cooldown_until: null,
                  routing_status_reason: "Rate limit cooldown",
                },
              ],
        );
      if (path === "/models/queue")
        return respond(
          state.empty || url.searchParams.get("status") !== state.queueStatus
            ? []
            : [{ ...workItem, priority: state.priority, status: state.queueStatus, lease_expires_at: state.queueStatus === "leased" ? "2026-10-30T00:15:00Z" : null }],
        );
      if (path === "/models/queue/17")
        return respond({
          ...workItem,
          priority: state.priority,
          status: state.queueStatus,
          lease_expires_at: state.queueStatus === "leased" ? "2026-10-30T00:15:00Z" : null,
          payload: { disease_id: "D001" },
        });
      if (path === "/models/queue/17/priority" && method === "POST") {
        if (state.conflict)
          return problem("Only queued work items can change priority");
        state.priority = req.postDataJSON().priority;
        return respond({ ...workItem, priority: state.priority });
      }
      if (path === "/publish/pending") {
        if (state.pendingFails)
          return problem("Pending scopes unavailable", 503);
        return respond(
          state.empty
            ? {
                surveillance: [],
                literature: false,
                situation: false,
                knowledge: [],
              }
            : pending,
        );
      }
      if (path === "/releases/site-release/checks") {
        if (state.preflightFails) return problem("Preflight unavailable", 503);
        return respond({
          checked_at: timestamp,
          overall_ready: !state.preflightBlocked,
          blockers: state.preflightBlocked ? ["Repository requires review"] : [],
          git: { env_var: "GITHUB_REPOSITORY", branch: "main", read_access_ok: true, write_access_ok: true, require_clean_worktree: true, dirty_blocking_paths: [], dirty_release_paths: [] },
          cloudflare: { token_present: true, account_id_present: true, project_access_ok: true, project_name: "fixture" },
          commands: { python_path: "/fixture/python", python_exists: true, wrangler_available: true },
          repository_boundary: { enforced: true, tracked_paths: [], generated_paths: [] },
        });
      }
      if (path === "/publish/runs" && method === "POST") {
        if (state.publishFails) return problem("Release temporarily unavailable", 503);
        return respond({ task_uuid: releaseUuid, status: "queued" }, 202);
      }
      unexpected.push(`${method} ${path}`);
      return problem(`Unexpected fixture request: ${path}`, 500);
    });
    await use(state);
    expect(errors, "No uncaught browser errors").toEqual([]);
    expect(unexpected, "Every API request has an explicit fixture").toEqual([]);
  },
});

async function fitsViewport(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: innerWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport + 2);
}

test("overview → failed run → attempts, artifacts and publication", async ({
  page,
  state,
}, testInfo) => {
  await page.goto("/overview");
  await expect(page.getByRole("link", { name: /Failed organize.*Attempt 2/ })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("overview.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  await page.getByRole("link", { name: /Failed organize.*Attempt 2/ }).click();
  await expect(page).toHaveURL(new RegExp(`/collection/runs/${taskUuid}$`));
  await expect(page.getByRole("heading", { name: /^Attempt/ })).toHaveText([
    "Attempt 2",
    "Attempt 1",
  ]);
  for (const attempt of [2, 1]) {
    const section = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: `Attempt ${attempt}`,
        exact: true,
      }),
    });
    for (const name of ["collect", "organize", "store"])
      await expect(
        section.getByRole("heading", { name, exact: true }),
      ).toBeVisible();
    await expect(
      section.getByText(`Invalid source columns (attempt ${attempt})`),
    ).toBeVisible();
    await expect(section.getByText("2.0 s", { exact: true })).toHaveCount(3);
  }
  await expect(page.getByText(sourcePath, { exact: true })).toHaveCount(2);
  await expect(page.getByText(`SHA256: ${"a".repeat(64)}`)).toHaveCount(2);
  await expect(
    page.getByText("Attempt 2 · csv_page · 120,034 bytes"),
  ).toBeVisible();
  await expect(
    page.getByText("Source: https://example.org/source.csv"),
  ).toHaveCount(2);
  await expect(page.getByRole("link", { name: /download/i })).toHaveCount(0);
  await fitsViewport(page);
  await page.screenshot({
    path: testInfo.outputPath("run-details.png"),
    fullPage: true, style: "nextjs-portal { display: none !important; }",
  });
  await page.getByRole("link", { name: "Review unpublished scopes" }).click();
  await expect(page.getByRole("cell").filter({ hasText: /US.*JP/ })).toBeVisible();
  await expect(page.getByRole("cell").filter({ hasText: /D001.*sources/ })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /collect|crawl|fetch|download/i }),
  ).toHaveCount(0);
  await fitsViewport(page);
  await page.screenshot({
    path: testInfo.outputPath("publish.png"),
    fullPage: true, style: "nextjs-portal { display: none !important; }",
  });
  expect(state.requests.every((request) => request.method === "GET")).toBe(
    true,
  );
});

test("old task bookmark preserves task, kind, repeated and encoded parameters", async ({
  page,
  state,
}) => {
  await page.goto(
    `/operations/tasks?task=${taskUuid}&kind=ingestion&tag=a&tag=b&note=a%26b&empty=`,
  );
  await expect(page).toHaveURL(new RegExp(`/collection/runs/${taskUuid}\\?`), {
    timeout: 15_000,
  });
  await expect(
    page.getByRole("heading", { name: "Run details" }),
  ).toBeVisible();
  const url = new URL(page.url());
  expect(url.pathname).toBe(`/collection/runs/${taskUuid}`);
  expect(url.searchParams.get("task")).toBe(taskUuid);
  expect(url.searchParams.get("kind")).toBe("ingestion");
  expect(url.searchParams.getAll("tag")).toEqual(["a", "b"]);
  expect(url.searchParams.get("note")).toBe("a&b");
  expect(url.searchParams.get("empty")).toBe("");
  await expect(
    page.getByRole("heading", { name: "Attempt 2", exact: true }),
  ).toBeVisible();
  expect(
    state.requests.some(
      (request) => request.path === `/collection/runs/${taskUuid}`,
    ),
  ).toBe(true);
});

test("models show route capacity, cooldown, queue details and priority changes", async ({
  page,
  state,
}, testInfo) => {
  await page.goto("/overview");
  await page.getByRole("link", { name: /^Queued .*Leased$/ }).click();
  const routeCard = page.locator("article").filter({ hasText: "fixture-model" });
  await expect(routeCard.getByText("Unavailable", { exact: true })).toBeVisible();
  await expect(routeCard.locator("dt").filter({ hasText: /^Capacity$/ }).locator("..").locator("dd")).toHaveText("4");
  await expect(routeCard.locator("dt").filter({ hasText: /^In flight$/ }).locator("..").locator("dd")).toHaveText("2");
  await expect(
    routeCard.locator("dt").filter({ hasText: /^Rate limit cooldown$/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /#17 Knowledge brief/ }).click();
  await expect(
    page.getByRole("heading", { name: "Work item #17 details" }),
  ).toBeVisible();
  await expect(page.locator("pre").first()).toContainText('"disease_id": "D001"');
  await page.getByLabel("Priority for work item 17").selectOption("high");
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Priority updated." }),
  ).toBeVisible();
  await expect(page.getByLabel("Priority for work item 17")).toHaveValue(
    "high",
  );
  expect(
    state.requests.find((request) => request.path.endsWith("/priority"))?.body,
  ).toEqual({ priority: "high" });
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("models.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  await page
    .getByRole("combobox", { name: "Status", exact: true })
    .selectOption("leased");
  await expect(page.getByText("No work items in this status.")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Work item #17 details" }),
  ).toHaveCount(0);
});

test("priority conflict can be corrected without a stale error", async ({
  page,
  state,
}) => {
  state.conflict = true;
  await page.goto("/models/queue");
  await page.getByLabel("Priority for work item 17").selectOption("high");
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "only queued work can change priority" }),
  ).toBeVisible();
  await expect(page.getByLabel("Priority for work item 17")).toBeEnabled();
  state.conflict = false;
  await page.getByLabel("Priority for work item 17").selectOption("urgent");
  await expect(page.getByLabel("Priority for work item 17")).toHaveValue(
    "urgent",
  );
  await expect(
    page.locator("main").getByRole("alert").filter({ hasText: "only queued work" }),
  ).toHaveCount(0);
});

test("ingestion source opens a configured collection dialog and its accepted run", async ({ page, state }, testInfo) => {
  state.withCountries = true;
  await page.goto("/collection/sources/ingestion?country=US");
  await expect(page.getByText("Fixture surveillance source", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New collection run" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("combobox", { name: "Data Source" })).toHaveValue("fixture_feed");
  await dialog.getByRole("button", { name: "Start collection" }).click();
  await expect(dialog.getByRole("link", { name: "Open collection run" })).toBeVisible();
  expect(state.requests.find((request) => request.method === "POST")?.body).toMatchObject({ line: "surveillance", country_code: "US", source: "fixture_feed", fill_missing: false, process: true, save_raw: true });
  await fitsViewport(page);
  await dialog.getByRole("link", { name: "Open collection run" }).click();
  await expect(page.getByRole("heading", { name: "Run details" })).toBeVisible();
  await page.goto("/collection/sources/ingestion?country=US");
  await expect(page.getByText("Fixture surveillance source", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("source-ingestion.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
});

test("publication recovers a preflight error and submits only a release", async ({
  page,
  state,
}) => {
  state.preflightFails = true;
  await page.goto("/publish");
  await page.getByRole("button", { name: "Run preflight" }).click();
  await expect(
    page.locator("main").getByRole("alert").filter({ hasText: "Preflight unavailable" }),
  ).toBeVisible();
  state.preflightFails = false;
  await page.getByRole("button", { name: "Run preflight" }).click();
  await expect(
    page.getByRole("heading", { name: "Preflight results" }),
  ).toBeVisible();
  await expect(
    page.locator("main").getByRole("alert").filter({ hasText: "Preflight unavailable" }),
  ).toHaveCount(0);
  await page.getByLabel("Full generation").check();
  await page.getByRole("button", { name: "Publish site-release" }).click();
  await expect(
    page.getByRole("link", { name: "Open release run" }),
  ).toBeVisible();
  expect(state.requests.filter((request) => request.method === "POST")).toEqual(
    [
      {
        path: "/publish/runs",
        method: "POST",
        body: { job_id: "site-release", full: true },
      },
    ],
  );
  await page.getByRole("link", { name: "Open release run" }).click();
  await expect(page).toHaveURL(new RegExp(`/publish/tasks\\?detail=${releaseUuid}$`));
  await expect(page.getByRole("dialog").getByRole("heading", { name: "PUBLICATION MUST STAY SEPARATE" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("empty states and failed reads remain navigable and recoverable", async ({
  page,
  state,
}) => {
  state.empty = true;
  await page.goto("/collection/sources/situation");
  await expect(page.getByText("No source records available.")).toBeVisible();
  await page.goto("/models/queue");
  await expect(page.getByText("No model routes configured.")).toBeVisible();
  await expect(page.getByText("No work items in this status.")).toBeVisible();
  state.pendingFails = true;
  await page.goto("/publish");
  await expect(
    page.getByRole("button", { name: "Reload unpublished scopes" }),
  ).toBeVisible();
  await expect(page.getByText("No unpublished changes.")).toHaveCount(0);
  state.pendingFails = false;
  await page.getByRole("button", { name: "Reload unpublished scopes" }).click();
  await expect(page.getByText("No unpublished changes.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Reload unpublished scopes" }),
  ).toHaveCount(0);
  await fitsViewport(page);
});

test("ingestion source honors the country from the URL in reads and submission", async ({ page, state }) => {
  state.withCountries = true;
  await page.goto("/collection/sources/ingestion?country=JP");
  await expect(page.getByRole("button", { name: "Active country or region" })).toContainText("Japan");
  await page.getByRole("button", { name: "New collection run" }).click();
  await expect(page.getByRole("dialog").getByRole("combobox", { name: "Data Source" })).toContainText("Fixture JP feed");
  await page.getByRole("dialog").getByRole("button", { name: "Start collection" }).click();
  await expect(page.getByRole("link", { name: "Open collection run" })).toBeVisible();
  expect(state.requests.find((request) => request.method === "POST")?.body).toMatchObject({ line: "surveillance", country_code: "JP", source: "fixture_feed" });
  expect(state.requestUrls.some((url) => new URL(url).pathname === "/api/v1/sources/flow" && new URL(url).searchParams.get("country_code") === "JP")).toBe(true);
});

test("run retry preserves history and a later failure remains retryable", async ({
  page,
  state,
}) => {
  await page.goto(`/collection/runs/${taskUuid}`);
  await page.getByRole("button", { name: "Retry this run" }).click();
  await expect(
    page.getByText("Retry queued. Waiting for the next attempt."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Retry this run" }),
  ).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /^Attempt/ })).toHaveText([
    "Attempt 2",
    "Attempt 1",
  ]);
  expect(
    state.requests.filter((request) => request.method === "POST"),
  ).toHaveLength(1);
  state.runStatus = "failed";
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Retry this run" }),
  ).toBeEnabled();
});

test("a successful collection can run again without discarding attempt history", async ({ page, state }) => {
  state.runStatus = "completed";
  await page.goto(`/collection/runs/${taskUuid}`);
  await page.getByRole("button", { name: /Retry this run|Re-execute collection/ }).click();
  await expect(page.getByRole("heading", { name: /^Attempt/ })).toHaveText(["Attempt 2", "Attempt 1"]);
  await expect(page.getByText(/Retry queued/)).toBeVisible();
  expect(state.requests.filter((request) => request.method === "POST")).toEqual([
    { path: `/tasks/${taskUuid}/retry`, method: "POST", body: null },
  ]);
});

test("same-country collection conflict remains in the dialog and is recoverable", async ({ page, state }) => {
  state.collectionConflict = true;
  state.withCountries = true;
  await page.goto("/collection/sources/ingestion?country=US");
  await page.getByRole("button", { name: "New collection run" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Start collection" }).click();
  await expect(dialog.getByRole("alert")).toContainText(/already active|conflict/i);
  await expect(dialog.getByRole("button", { name: "Start collection" })).toBeEnabled();
  state.collectionConflict = false;
  await dialog.getByRole("button", { name: "Start collection" }).click();
  await expect(dialog.getByRole("link", { name: "Open collection run" })).toBeVisible();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
});

test("incremental publication recovers a submission error", async ({ page, state }) => {
  await page.goto("/publish");
  await page.getByRole("button", { name: "Run preflight" }).click();
  await expect(page.getByRole("heading", { name: "Preflight results" })).toBeVisible();
  state.publishFails = true;
  await page.getByRole("button", { name: "Publish site-release" }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("Release temporarily unavailable");
  state.publishFails = false;
  await page.getByRole("button", { name: "Publish site-release" }).click();
  await expect(page.getByRole("link", { name: "Open release run" })).toBeVisible();
  await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
  const mutations = state.requests.filter((request) => request.method === "POST");
  expect(mutations).toHaveLength(2);
  expect(mutations.every((request) => request.path === "/publish/runs")).toBe(true);
  expect(mutations[1].body).toEqual({ job_id: "site-release", full: false });
  await expect(page.getByRole("button", { name: "Publish site-release" })).toBeDisabled();
  state.releaseStatus = "failed";
  await expect(page.getByRole("button", { name: "Publish site-release" })).toBeEnabled({ timeout: 12_000 });
  await expect(page.getByRole("cell").filter({ hasText: /US.*JP/ })).toBeVisible();
});

test("queue terminal and lease states cannot change priority", async ({ page, state }) => {
  await page.goto("/models/queue");
  for (const status of ["leased", "succeeded", "failed", "cancelled"]) {
    state.queueStatus = status;
    await page.getByRole("combobox", { name: "Status", exact: true }).selectOption(status);
    await expect(page.getByLabel("Priority for work item 17")).toBeDisabled();
    await page.getByRole("button", { name: /#17/ }).click();
    await expect(page.getByRole("heading", { name: "Work item #17 details" })).toBeVisible();
  }
  expect(state.requests.every((request) => request.method === "GET")).toBe(true);
});

test("loading state is explicit and does not masquerade as empty", async ({ page, state }) => {
  state.delayReads = true;
  state.empty = true;
  await page.goto("/publish");
  await expect(page.getByRole("status").filter({ hasText: /Loading/ })).toBeVisible();
  await expect(page.getByText("No unpublished changes.")).toHaveCount(0);
  await expect(page.getByText("No unpublished changes.")).toBeVisible();
});

test("keyboard navigation exposes all six workspaces and settings entries", async ({ page, state }, testInfo) => {
  void state;
  await page.goto("/models/queue");
  if (testInfo.project.name !== "desktop") {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
  }
  const navigation = page.getByRole("navigation", { name: "Control center workspaces" });
  for (const name of ["Overview", "Ingestion & Tasks", "Data Governance", "AI", "Publish", "Settings"])
    await expect(navigation.getByRole("link", { name, exact: true }).first()).toBeVisible();
  if (testInfo.project.name !== "desktop") {
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
  }
  await page.keyboard.press("Tab");
  const focus = await page.evaluate(() => {
    const element = document.activeElement as HTMLElement;
    const style = getComputedStyle(element);
    return { tag: element.tagName, visible: element.matches(":focus-visible"), outline: style.outlineStyle, shadow: style.boxShadow };
  });
  expect(focus.visible).toBe(true);
  expect(focus.outline !== "none" || focus.shadow !== "none").toBe(true);
  await fitsViewport(page);
});

test("core control center pages meet automated accessibility checks", async ({ page, state }, testInfo) => {
  void state;
  test.skip(testInfo.project.name !== "desktop", "Semantic audit is shared across viewport sizes");
  test.setTimeout(90_000);
  for (const path of ["/overview", "/collection", `/collection/runs/${taskUuid}`, "/models/queue", "/publish", "/settings/integrations"]) {
    await page.goto(path);
    await expect(page.locator("main h1")).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: /^Loading/ })).toHaveCount(0);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(result.violations, `${path} accessibility`).toEqual([]);
  }
});

test("language selection persists and translates the control center", async ({ page, state }) => {
  void state;
  await page.goto("/models/queue");
  await page.getByRole("button", { name: "Switch language" }).click();
  await expect(page.locator("main h1")).toHaveText("模型");
  await page.reload();
  await expect(page.locator("main h1")).toHaveText("模型");
  await page.goto("/collection");
  await expect(page.locator("main h1")).toHaveText("来源 - 监测");
  await page.goto("/publish");
  await expect(page.locator("main h1")).toHaveText("发布");
  await expect(page.getByRole("button", { name: "切换语言" })).toBeVisible();
  await fitsViewport(page);
});

test("settings retain configuration controls and recovered navigation entries", async ({ page, state }, testInfo) => {
  await page.goto("/settings/integrations");
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save email settings" })).toBeVisible();
  if (testInfo.project.name !== "desktop") await page.getByRole("button", { name: "Open navigation" }).click();
  const navigation = page.getByRole("navigation", { name: "Control center workspaces" });
  for (const href of ["/settings/models", "/settings/integrations"])
    await expect(navigation.locator(`a[href="${href}"]`).first()).toBeVisible();
  if (testInfo.project.name !== "desktop") await page.keyboard.press("Escape");
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("settings.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  await page.goto("/collection/sources/ingestion");
  await expect(page.locator("main h1")).toHaveText("Source - Ingestion");
  if (testInfo.project.name !== "desktop") await page.getByRole("button", { name: "Open navigation" }).click();
  for (const href of ["/collection/sources/situation", "/collection/sources/ingestion", "/collection/sources/literature", "/collection/sources/knowledge", "/collection/runs", "/collection/schedules", "/collection/runtime"])
    await expect(navigation.locator(`a[href="${href}"]`).first()).toBeVisible();
  expect(state.requests.every((request) => request.method === "GET")).toBe(true);
});


test("a blocking preflight prevents release until checks pass", async ({ page, state }) => {
  state.preflightBlocked = true;
  await page.goto("/publish");
  await page.getByRole("button", { name: "Run preflight" }).click();
  await expect(page.getByText("Repository requires review")).toBeVisible();
  await expect(page.getByRole("button", { name: "Publish site-release" })).toBeDisabled();
  state.preflightBlocked = false;
  await page.getByRole("button", { name: "Run preflight" }).click();
  await expect(page.getByRole("button", { name: "Publish site-release" })).toBeEnabled();
  await expect(page.getByText("Repository requires review")).toHaveCount(0);
  expect(state.requests.every((request) => request.method === "GET")).toBe(true);
});

test("collapsed desktop preference keeps mobile navigation usable and search restores focus", async ({ page, state }, testInfo) => {
  void state;
  await page.addInitScript(() => localStorage.setItem("globalid-dashboard-store", JSON.stringify({ state: { sidebarCollapsed: true, lang: "en" }, version: 0 })));
  await page.goto("/models/queue");
  if (testInfo.project.name !== "desktop") {
    await page.getByRole("button", { name: "Open navigation" }).click();
    const navigation = page.getByRole("dialog").getByRole("navigation", { name: "Control center workspaces" });
    for (const name of ["Overview", "Ingestion & Tasks", "Data Governance", "AI", "Publish", "Settings"])
      await expect(navigation.getByRole("link", { name, exact: true }).first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
  }
  const trigger = page.getByRole("button", { name: "Switch language" });
  await trigger.focus();
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "Search workspaces and tools" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(trigger).toBeFocused();
});

const collectionTaskTypes = ["crawl_data", "sync_literature", "refresh_situation_sources", "refresh_disease_knowledge_sources"];
function taskQueries(state: State) {
  return state.requestUrls.map((url) => new URL(url)).filter((url) => url.pathname === "/api/v1/tasks");
}

test("collection history scopes the server query and URL filters cannot introduce publication", async ({ page, state }, testInfo) => {
  await page.goto("/collection/runs?task_type=export_data&task_type=generate_report&kind=release&country=JP");
  await expect(page.getByRole("link", { name: "US source collection", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Literature source collection", exact: true })).toBeVisible();
  await expect(page.locator("main").getByText("PUBLICATION MUST STAY SEPARATE")).toHaveCount(0);
  await expect(page.locator("main").getByText("AI report generation")).toHaveCount(0);
  expect(taskQueries(state).length).toBeGreaterThan(0);
  for (const url of taskQueries(state)) {
    expect(url.searchParams.get("task_type")?.split(",").sort()).toEqual([...collectionTaskTypes].sort());
    expect(url.searchParams.get("country_code")).toBeNull();
  }
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("collection-tasks.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  await page.getByRole("link", { name: "US source collection", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/collection/runs/${taskUuid}(?:\\?|$)`));
  await expect(page.getByRole("heading", { name: "Attempt 2", exact: true })).toBeVisible();
});

test("publication task history has a separate server scope", async ({ page, state }, testInfo) => {
  await page.goto("/publish/tasks?task_type=crawl_data");
  await expect(page.getByRole("link", { name: "PUBLICATION MUST STAY SEPARATE", exact: true })).toBeVisible();
  await expect(page.locator("main").getByText("US source collection")).toHaveCount(0);
  await expect(page.locator("main").getByText("Literature source collection")).toHaveCount(0);
  expect(taskQueries(state).length).toBeGreaterThan(0);
  for (const url of taskQueries(state)) expect(url.searchParams.get("task_type")).toBe("export_data");
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("publish-tasks.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
});

test("collection schedules query only collection kinds even with a release URL filter", async ({ page, state }, testInfo) => {
  await page.goto("/collection/schedules?kind=release");
  for (const kind of ["ingestion", "literature", "situation"])
    await expect(page.getByText(`${kind} schedule`, { exact: true })).toBeVisible();
  await expect(page.locator("main").getByText("release schedule", { exact: true })).toHaveCount(0);
  await expect(page.locator("main").getByRole("button", { name: /^Release$/i })).toHaveCount(0);
  const kinds = state.requestUrls.map((url) => new URL(url)).filter((url) => url.pathname === "/api/v1/schedules").map((url) => url.searchParams.get("kind"));
  expect([...new Set(kinds)].sort()).toEqual(["ingestion", "literature", "situation"]);
  expect(state.requests.every((request) => request.method === "GET")).toBe(true);
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("collection-schedules.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  await page.goto("/collection/runtime");
  await expect(page.locator("main h1")).toHaveText("Shared runtime");
  expect(state.requests.some((request) => request.path === "/runtime/services")).toBe(true);
});

test("situation source reads its registry and collects notices without AI extraction", async ({ page, state }, testInfo) => {
  await page.goto("/collection/sources/situation");
  await expect(page.getByRole("heading", { name: "Source - Situation", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Fixture situation source", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Collect notice sources", exact: true }).click();
  await expect(page.getByRole("link", { name: "Open collection run" })).toBeVisible();
  expect(state.requests.filter((request) => request.method === "POST")).toEqual([{ path: "/collection/runs", method: "POST", body: { line: "situation", mode: "metadata_only" } }]);
  expect(state.requests.some((request) => request.path === "/situation/sources")).toBe(true);
  expect(state.requests.some((request) => ["/sources/flow", "/research-radar/dashboard", "/knowledge", "/publish/runs"].includes(request.path))).toBe(false);
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("source-situation.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
});

test("literature source exposes provider runs and submits a bounded metadata sync", async ({ page, state }, testInfo) => {
  await page.goto("/collection/sources/literature");
  await expect(page.getByRole("heading", { name: "Source - Literature", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: /crossref/ })).toBeVisible();
  await page.getByLabel("Indexed since (optional)").fill("2026-10-01T12:00");
  const since = await page.evaluate(() => new Date("2026-10-01T12:00").toISOString());
  await page.getByRole("button", { name: "Sync literature metadata", exact: true }).click();
  await expect(page.getByRole("link", { name: "Open collection run" })).toBeVisible();
  expect(state.requests.filter((request) => request.method === "POST")).toEqual([{ path: "/collection/runs", method: "POST", body: { line: "literature", since } }]);
  expect(state.requests.some((request) => request.path === "/research-radar/dashboard")).toBe(true);
  expect(state.requests.some((request) => ["/sources/flow", "/situation/sources", "/knowledge", "/research-radar/enrich", "/publish/runs"].includes(request.path))).toBe(false);
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("source-literature.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
});

test("knowledge source shows provenance and enqueues source collection without brief generation", async ({ page, state }, testInfo) => {
  await page.goto("/collection/sources/knowledge");
  await expect(page.getByRole("heading", { name: "Source - Knowledge", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Fixture disease/ }).click();
  await expect(page.getByText("Fixture knowledge source", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Collect knowledge sources", exact: true })).toBeEnabled();
  await expect(page.getByRole("link", { name: "https://example.org/knowledge", exact: true })).toHaveAttribute("href", "https://example.org/knowledge");
  await page.getByRole("button", { name: "Collect knowledge sources", exact: true }).click();
  await expect(page.getByRole("link", { name: "Open collection run" })).toHaveAttribute("href", `/collection/runs/${taskUuid}`);
  expect(state.requests.filter((request) => request.method === "POST")).toEqual([{ path: "/knowledge/source-runs", method: "POST", body: { disease_ids: ["D001"], source: ["who", "search", "wikidata", "wikipedia", "pubmed"], force: false, priority: "normal" } }]);
  expect(state.requests.some((request) => request.path === "/knowledge/D001")).toBe(true);
  expect(state.requests.some((request) => ["/knowledge/runs", "/collection/runs", "/publish/runs"].includes(request.path))).toBe(false);
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("source-knowledge.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
});

test("governance, AI and settings have distinct working entry points", async ({ page, state }, testInfo) => {
  await page.goto("/data");
  await expect(page.locator("main h1")).toHaveText("Data Governance");
  await expect(page.getByText("fixture-release", { exact: true })).toBeVisible();
  for (const href of ["/data/quality", "/data/mappings", "/data/audit", "/data/explorer"])
    await expect(page.locator(`main a[href="${href}"]`).first()).toBeVisible();
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("data-governance.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  await page.goto("/models");
  await expect(page.locator("main h1")).toHaveText("AI workbench");
  for (const href of ["/models/tasks", "/models/queue", "/models/agents", "/models/reports", "/models/knowledge", "/models/literature"])
    await expect(page.locator(`main a[href="${href}"]`).first()).toBeVisible();
  expect(taskQueries(state).every((url) => !url.searchParams.get("task_type")?.split(",").includes("export_data") && !url.searchParams.get("task_type")?.split(",").includes("crawl_data"))).toBe(true);
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("ai-workbench.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  await page.goto("/settings");
  await expect(page.locator("main h1")).toHaveText("Settings");
  for (const section of ["smtp", "github", "archive", "cloudflare", "site"])
    await expect(page.locator(`main a[href="/settings/integrations?section=${section}"]`)).toBeVisible();
  await fitsViewport(page);
  await page.screenshot({ path: testInfo.outputPath("settings-workbench.png"), fullPage: true, style: "nextjs-portal { display: none !important; }" });
  expect(state.requests.every((request) => request.method === "GET")).toBe(true);
});

test("source pages and new workbenches meet automated accessibility checks", async ({ page, state }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Semantic accessibility audit is shared across viewport sizes");
  test.setTimeout(120_000);
  state.withCountries = true;
  for (const path of ["/collection/sources/situation", "/collection/sources/ingestion?country=US", "/collection/sources/literature", "/collection/sources/knowledge", "/data", "/models", "/settings", "/collection/runs", "/collection/schedules"]) {
    await page.goto(path);
    await expect(page.locator("main h1")).toBeVisible();
    await expect(page.locator("main").getByRole("status").filter({ hasText: /^Loading/ })).toHaveCount(0);
    if (path.includes("ingestion")) await page.getByRole("button", { name: "New collection run" }).click();
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(result.violations, `${path} accessibility`).toEqual([]);
  }
});

test("legacy publication bookmarks resolve into publication history and preserve query values", async ({ page, state }) => {
  await page.goto(`/operations/tasks?task=${releaseUuid}&kind=release&tag=a&tag=b&note=a%26b&empty=`);
  await expect(page).toHaveURL(/\/publish\/tasks\?/);
  const url = new URL(page.url());
  expect(url.searchParams.get("detail")).toBe(releaseUuid);
  expect(url.searchParams.get("task")).toBe(releaseUuid);
  expect(url.searchParams.get("kind")).toBe("release");
  expect(url.searchParams.getAll("tag")).toEqual(["a", "b"]);
  expect(url.searchParams.get("note")).toBe("a&b");
  expect(url.searchParams.get("empty")).toBe("");
  await expect(page.getByRole("dialog")).toContainText("PUBLICATION MUST STAY SEPARATE");
  expect(taskQueries(state).every((query) => query.searchParams.get("task_type") === "export_data")).toBe(true);
  expect(state.requests.every((request) => request.method === "GET")).toBe(true);
});

test("legacy release list and schedule bookmarks resolve to publication workspaces", async ({ page, state }) => {
  await page.goto("/operations/tasks?task_type=export_data&tag=a&tag=b&note=a%26b&empty=");
  await expect(page).toHaveURL(/\/publish\/tasks\?/);
  await expect(page.getByRole("link", { name: "PUBLICATION MUST STAY SEPARATE", exact: true })).toBeVisible();
  let url = new URL(page.url());
  expect(url.searchParams.getAll("tag")).toEqual(["a", "b"]);
  expect(url.searchParams.get("note")).toBe("a&b");
  expect(url.searchParams.get("empty")).toBe("");
  expect(taskQueries(state).every((query) => query.searchParams.get("task_type") === "export_data")).toBe(true);
  await page.goto("/operations/schedules?kind=release&tag=a&tag=b&note=a%26b&empty=");
  await expect(page).toHaveURL(/\/publish\/schedules\?/);
  await expect(page.getByText("release schedule", { exact: true })).toBeVisible();
  url = new URL(page.url());
  expect(url.searchParams.getAll("tag")).toEqual(["a", "b"]);
  expect(url.searchParams.get("note")).toBe("a&b");
  expect(url.searchParams.get("empty")).toBe("");
  await expect(page.locator("main").getByText("ingestion schedule", { exact: true })).toHaveCount(0);
  const schedules = state.requestUrls.map((value) => new URL(value)).filter((value) => value.pathname === "/api/v1/schedules");
  expect(schedules.every((query) => query.searchParams.get("kind") === "release")).toBe(true);
});
