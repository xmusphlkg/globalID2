import { test, expect, noHorizontalOverflow, newTask, otherChannelTask } from "./realtime-publish-fixtures";

test("independent channel health, IE blocked reason, pagination and retry/continuation records", async ({ page, state }, testInfo) => {
  await page.goto("/publish/realtime");
  await expect(page.getByRole("heading", { level: 1, name: "Realtime data publication" })).toBeVisible();
  const surveillance = page.getByRole("region", { name: "Surveillance", exact: true });
  const situation = page.getByRole("region", { name: "Situation", exact: true });
  await expect(surveillance.getByText("Action required", { exact: true })).toBeVisible();
  await expect(surveillance.getByText("Monitoring snapshot stale", { exact: true })).toBeVisible();
  await expect(surveillance.getByText("Sample SHA failed", { exact: true })).toBeVisible();
  await expect(situation.getByText("Healthy", { exact: true })).toBeVisible();
  await expect(situation.getByText("Sample SHA verified", { exact: true })).toBeVisible();
  const changes = page.getByRole("region", { name: "Pending changes · Surveillance", exact: true });
  await expect(changes.getByText("IE", { exact: true })).toBeVisible();
  await expect(changes.getByText("Publication is disabled for this country or region.", { exact: true })).toBeVisible();
  await changes.getByRole("button", { name: "Next page" }).click();
  await expect(changes.getByText("JP-20", { exact: true })).toBeVisible();
  await page.getByLabel("Change eligibility").selectOption("blocked");
  await expect(changes.getByText("IE", { exact: true })).toBeVisible();
  await expect(changes.locator("tbody tr")).toHaveCount(1);
  await expect(page.getByText("Automatic attempts exhausted", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open follow-up task" }).first()).toHaveAttribute("href", `/publish/tasks?detail=${otherChannelTask}`);
  await expect(page.getByText("2.5 sec", { exact: true }).first()).toBeVisible();
  await noHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("channel-health-and-ie.png"), fullPage: true, animations: "disabled" });
  expect(state.requests.filter(request => request.method !== "GET")).toEqual([]);
});

test("selected Situation JSON publication, independent pause/resume and read-only monitor refresh", async ({ page, state }, testInfo) => {
  await page.goto("/publish/realtime");
  await page.getByLabel("Publication channel").selectOption("situation");
  await expect(page.getByRole("button", { name: "Publish selected JSON", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Publish selected JSON", exact: true }).click();
  const manual = page.getByRole("region", { name: "Manual JSON publication", exact: true });
  await expect(manual.getByRole("status")).toContainText("Situation: Publication queued");
  await expect(manual.getByRole("link", { name: "Open task", exact: true })).toHaveAttribute("href", `/publish/tasks?detail=${newTask}`);
  expect(state.requests.filter(request => request.method === "POST")).toEqual([{ path: "/publish/realtime/situation/runs", method: "POST", body: {} }]);
  await page.getByRole("button", { name: "Pause automatic publication · Situation", exact: true }).click();
  await expect(manual.getByRole("status")).toContainText("Existing tasks continue");
  await expect(page.getByRole("button", { name: "Publish selected JSON", exact: true })).toBeEnabled();
  expect(state.overview.channels.situation.paused).toBe(true);
  expect(state.overview.channels.surveillance.paused).toBe(false);
  await noHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("situation-manual-and-paused.png"), fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Resume automatic publication · Situation", exact: true }).click();
  await expect(manual.getByRole("status")).toContainText("Automatic publication resumed");
  expect(state.overview.channels.situation.paused).toBe(false);
  const mutations = state.requests.filter(request => request.method !== "GET");
  expect(mutations).toEqual([
    { path: "/publish/realtime/situation/runs", method: "POST", body: {} },
    { path: "/publish/realtime/situation/control", method: "PATCH", body: { paused: true } },
    { path: "/publish/realtime/situation/control", method: "PATCH", body: { paused: false } },
  ]);
  const reads = state.requests.length;
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await expect.poll(() => state.requests.length).toBeGreaterThan(reads);
  expect(state.requests.filter(request => request.method !== "GET")).toEqual(mutations);
});

test("retry reservation links the other channel and hides raw error details", async ({ page, state }, testInfo) => {
  state.reservedConflict = true;
  await page.goto("/publish/realtime");
  await page.getByRole("button", { name: "Retry failed publication", exact: true }).click();
  const manual = page.getByRole("region", { name: "Manual JSON publication", exact: true });
  await expect(manual.getByRole("alert")).toContainText("Publication state has changed");
  await expect(manual.getByRole("link", { name: "Open task", exact: true })).toHaveAttribute("href", `/publish/tasks?detail=${otherChannelTask}`);
  await expect(page.getByText("SECRET_RAW_UPSTREAM_ERROR", { exact: false })).toHaveCount(0);
  await expect(page.locator('a[href^="https://unexpected.invalid"]')).toHaveCount(0);
  expect(state.requests.filter(request => request.method === "POST")).toEqual([{ path: "/publish/realtime/surveillance/retry", method: "POST", body: {} }]);
  await noHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("cross-channel-retry-conflict.png"), fullPage: true, animations: "disabled" });
});

test("Chinese navigation and global page labels remain usable on desktop and mobile", async ({ page, state, isMobile }, testInfo) => {
  await page.goto("/publish/realtime");
  await expect(page.getByRole("heading", { level: 1, name: "Realtime data publication" })).toBeVisible();
  await page.getByRole("button", { name: "Switch language", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "实时数据发布", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "国家与疾病监测", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "国际疫情态势", exact: true })).toBeVisible();
  await expect(page.getByText("全局范围", { exact: true })).toBeVisible();
  await expect(page.getByText("监测采样已陈旧", { exact: true })).toBeVisible();
  if (isMobile) await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await expect(page.getByRole("link", { name: "实时数据发布", exact: true })).toBeVisible();
  if (isMobile) await page.getByRole("button", { name: "关闭导航", exact: true }).click();
  expect(new URL(page.url()).searchParams.has("country")).toBe(false);
  await noHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("realtime-chinese.png"), fullPage: true, animations: "disabled" });
  expect(state.requests.filter(request => request.method !== "GET")).toEqual([]);
});
