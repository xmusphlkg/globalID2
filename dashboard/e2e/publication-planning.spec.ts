import { expect, test } from "@playwright/test";

// Every API call, including publication mutations, stays inside this fixture.
const capabilities = {
  schema_version: 1, plan_preview: true, plan_execution: true, rollback: true,
  channels: ["surveillance", "situation", "literature"].map(id => ({
    id, label: id, realtime_controls: true, enabled: true, automatic: true,
    rollback: false, supported_modes: ["content"],
  })),
  modes: ["content", "theme", "template", "full"].map(id => ({ id, supported: true })),
  legacy_modes: ["full", "reports_only", "code_only"],
};

for (const lang of ["en", "zh"] as const) {
  test(`${lang}: review, preview, idempotent execution and rollback planning`, async ({ page }, testInfo) => {
    const candidate = {
      plan_id: "browser-plan", plan_digest: "a".repeat(64), job_id: "site-release",
      created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 900_000).toISOString(),
      kind: "theme", channels: ["theme"], source_revision: "b".repeat(40), input_digest: "c".repeat(64),
      expected_generations: {}, affected_templates: [], content_record_count: 0, html_page_count: 0,
      asset_compile_scope: ["theme_v1"], pages_deploy_required: false, worker_deploy_required: false,
      validation_scope: ["theme"], cost_estimate_confidence: "high",
      changed_files: [{ path: "shared/site-theme-v1.json", kind: "theme", reason: "theme_candidate_requires_compatibility_validation" }],
      warnings: ["theme_coverage_home_country_only"], dirty_ids: [], pending_scopes: {},
      preview_only: false, executable: true, execution_mode: "theme",
    };
    const runs: unknown[] = [];
    const rollbacks: unknown[] = [];
    const errors: string[] = [];
    const unexpected: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(language => {
      if (window.top === window) localStorage.setItem("globalid-dashboard-store", JSON.stringify({
        state: { lang: language }, version: 0,
      }));
    }, lang);
    await page.route("**/api/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname.replace("/api/v1", "");
      const respond = (data: unknown) => route.fulfill({ json: { data, meta: {} } });
      if (path === "/api/panel-session") return route.fulfill({ json: { required: false, ok: true } });
      if (path === "/events/stream") return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
      if (path === "/countries") return respond([]);
      if (path === "/overview") return respond({ generated_at: candidate.created_at,
        tasks: { queued: 0, running: 0, retrying: 0, failed: 0 }, schedules: { enabled: 0, total: 0 },
        action_items: [], runtime: { services: [], heartbeat_available: true }, pipeline: [], recent_tasks: [] });
      if (path === "/publish/capabilities") return respond(capabilities);
      if (path === "/publish/pending") return respond({ surveillance: ["JP"], situation: true, literature: true,
        knowledge: [], surveillance_json_enabled: true, situation_json_enabled: true, literature_json_enabled: true });
      if (path === "/publish/usage") return respond({ status: "unknown", plan: "unknown", services: [] });
      if (path === "/publish/theme-history") return respond({ items: [{ plan_id: "verified-old", created_at: candidate.created_at,
        version: "d".repeat(64), public_verified: true, compatibility: "v1" }] });
      if (path === "/publish/plans" && request.method() === "POST") return respond(candidate);
      if (path === "/publish/rollback-plans" && request.method() === "POST") {
        rollbacks.push(request.postDataJSON());
        return respond({ ...candidate, plan_id: "rollback-plan", rollback_of: "verified-old" });
      }
      if (path.endsWith("/previews")) return respond({ pages: ["en/home", "fr/country"], missing: [] });
      if (path.includes("/preview/")) return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body><h1>Frozen theme sample</h1></body></html>",
        headers: { "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'; sandbox", "Cache-Control": "private, no-store" } });
      if (path === "/publish/runs" && request.method() === "POST") {
        runs.push(request.postDataJSON()); return respond({ task_uuid: "browser-run", status: "queued" });
      }
      if (path === "/collection/runs/browser-run") return respond({ task_uuid: "browser-run", status: "completed", stages: [], artifacts: [], publication: [] });
      if (path.endsWith("/receipt")) return respond({ authoritative_committed: true, public_verified: true, database_confirmed: true,
        content_render_count: 0, html_assembly_count: 0, template_compile_count: 0, uploaded_objects: 2, uploaded_bytes: 1254, duration_seconds: 1.5 });
      unexpected.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 404, json: { detail: "Unknown fixture endpoint" } });
    });
    await page.goto("/publish");
    await page.getByRole("combobox", { name: lang === "zh" ? "发布目的" : "Publication purpose", exact: true }).selectOption("theme");
    await page.getByRole("button", { name: lang === "zh" ? "检查变更" : "Check changes", exact: true }).click();
    await page.getByRole("button", { name: lang === "zh" ? "预览主题" : "Preview theme", exact: true }).click();
    await page.getByRole("combobox", { name: lang === "zh" ? "语言" : "Language", exact: true }).selectOption("fr");
    await page.getByRole("combobox", { name: lang === "zh" ? "页面" : "Page", exact: true }).selectOption("country");
    await expect(page.locator("iframe")).toHaveAttribute("sandbox", "");
    await expect(page.locator("iframe")).toHaveAttribute("src", "/api/v1/publish/plans/browser-plan/preview/fr/country");
    await page.screenshot({ path: testInfo.outputPath("reviewed-theme-plan.png"), fullPage: true });
    const publish = page.getByRole("button", { name: lang === "zh" ? "发布已审阅计划" : "Publish reviewed plan", exact: true });
    await publish.evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
    await expect(page.getByText(lang === "zh" ? "公开访问验证" : "Public verification", { exact: true })).toBeVisible();
    expect(runs).toEqual([{ plan_id: "browser-plan", plan_digest: candidate.plan_digest, idempotency_key: "publication:browser-plan" }]);
    await page.getByRole("button", { name: lang === "zh" ? "检查回滚" : "Check rollback", exact: true }).click();
    await expect(page.getByRole("button", { name: lang === "zh" ? "发布回滚计划" : "Publish rollback plan", exact: true })).toBeEnabled();
    expect(rollbacks).toEqual([{ kind: "theme", target_plan_id: "verified-old", job_id: "site-release" }]);
    expect(runs).toHaveLength(1);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    expect(overflow).toBe(false);
    expect(errors).toEqual([]);
    expect(unexpected).toEqual([]);
  });
}
