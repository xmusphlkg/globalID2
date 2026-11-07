import { expect, test } from "@playwright/test";

test("Japan pilot preview renders frozen ECharts data with the same section placement", async ({ page }, testInfo) => {
  const errors: string[] = [];
  const reportQueries: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("globalid-dashboard-store", JSON.stringify({ state: { lang: "zh", countryCode: "US", countryId: 1, countryName: "United States" }, version: 0 })));
  const figure = {
    id: "fig-curve", number: 1, renderer: "echarts", contract: "report_v4.figures.1", figure_type: "epidemic_curve",
    section_type: "disease_context", position: "after_content", data_key: "curve",
    title: { zh: "甲病 · 病例时间曲线", en: "Disease A · Case curve" }, caption: { zh: "报告窗口内的记录；缺失不视为零。" },
  };
  const report = { id: 51, report_uuid: "pilot-fixture", title: "日本图表试点报告", report_type: "monthly", status: "approved", country_id: 2, country_name: "Japan", period_start: "2026-01-01", period_end: "2026-01-31", quality_score: 1, section_count: 1, created_at: "2026-02-01T00:00:00Z" };
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    // Intercept all requests; this check never creates tasks or writes reports.
    if (route.request().method() !== "GET") return route.fulfill({ status: 405, json: { detail: "Read-only fixture" } });
    const respond = (data: unknown) => route.fulfill({ json: { data, meta: {} } });
    if (path === "/events/stream") return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
    if (path === "/overview") return respond({ generated_at: "2026-02-01T00:00:00Z", tasks: {}, schedules: { enabled: 1, total: 1 }, action_items: [], runtime: { services: [], heartbeat_available: true }, pipeline: [], recent_tasks: [] });
    if (path === "/reports") { reportQueries.push(url.searchParams.get("country_code") || ""); return respond([report]); }
    if (path === "/reports/workflow") return respond({ country_code: "JP", max_figures: 2, max_figures_per_section: 1, figure_catalog: [], schedule: { enabled: true, interval_minutes: 15 }, periods: [] });
    if (path === "/reports/pilot-fixture") return respond({ ...report, sections: [], metadata: {
      report_layout: "report_v4", quality_gate: { passed: true }, report_document_v4: {
        title: { zh: report.title }, summary: { zh: "本报告基于日本已存监测数据。" }, metrics: { record_count: 4, disease_count: 1 }, peer_review: { status: "published", figure_plan: [{ candidate_id: "epidemic_curve:A", rationale: "Describe the observed trend" }] },
        sections: [{ id: "disease_context", order: 1, type: "disease_context", title: { zh: "疾病背景" }, body: { zh: "甲病的报告病例在窗口内有所变化。" }, figures: [figure] }], figures: [figure],
        figure_data: { version: "report_v4.figures.1", datasets: { curve: { points: [{ period: "2026-01-05", cases: 0 }, { period: "2026-01-12", cases: null }, { period: "2026-01-19", cases: 12 }, { period: "2026-01-26", cases: 15 }] } } },
      },
    } });
    return respond([]);
  });
  await page.goto("/models/reports");
  await expect(page.getByRole("heading", { name: "日本报告试点" })).toBeVisible();
  await page.getByText("日本图表试点报告", { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("AI 同行评审通过", { exact: true })).toBeVisible();
  await expect(dialog.locator("canvas")).toHaveCount(1);
  await dialog.getByText("查看图表数据", { exact: true }).click();
  await expect(dialog.getByRole("cell", { name: "—", exact: true })).toBeVisible();
  await expect(dialog.getByRole("cell", { name: "0", exact: true })).toBeVisible();
  const box = await dialog.locator("canvas").boundingBox();
  expect(box?.width).toBeGreaterThan(150);
  expect(reportQueries.every(country => country === "JP")).toBe(true);
  expect(errors).toEqual([]);
  await page.screenshot({ path: `/tmp/globalid-report-pilot-${testInfo.project.name}.png` });
});
