import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DeliveryStatus } from "./delivery-status";

afterEach(cleanup);

it("distinguishes pilot acceptance from actual delivery feedback", () => {
  render(<DeliveryStatus isZh options={{ pilot_mode: true, lists: [
    { code: "reports", name_en: "Reports", name_zh: "报告", default_frequency: "weekly", available: true },
    { code: "alerts", name_en: "Alerts", name_zh: "提醒", default_frequency: "instant", available: false },
  ], locales: [], frequencies: [], filters: { country: [], disease: [] } }} stats={{
    campaign_deliveries_last_7_days: { sent: 5, delivered: 2, unknown: 3 },
    provider_feedback_last_7_days: { delivered: 2, bounced: 1 },
  }} />);
  expect(screen.getByText("管理员试运行")).toBeInTheDocument();
  expect(screen.getByText("报告: 开放")).toBeInTheDocument();
  expect(screen.getByText("提醒: 未开放")).toBeInTheDocument();
  expect(within(screen.getByText("群发已接收").parentElement!).getByText("7")).toBeInTheDocument();
  expect(within(screen.getByText("群发结果待核实").parentElement!).getByText("3")).toBeInTheDocument();
  expect(within(screen.getByText("服务商送达回执").parentElement!).getByText("2")).toBeInTheDocument();
  expect(screen.getByText("1 / 0")).toBeInTheDocument();
});

it("does not mistake missing data for full delivery or zero events", () => {
  render(<DeliveryStatus isZh={false} />);
  expect(screen.getByText("Delivery mode unknown")).toBeInTheDocument();
  expect(screen.getAllByText("—")).toHaveLength(5);
  expect(screen.queryByText("Full delivery")).not.toBeInTheDocument();
});
