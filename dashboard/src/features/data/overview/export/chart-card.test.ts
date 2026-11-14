import { afterEach, describe, expect, it, vi } from "vitest";
import type { TrendChart } from "../presentation";
import { downloadChartCard } from "./chart-card";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("dashboard chart export", () => {
  it("keeps the card size, metric filename, title, date range and QR footer", async () => {
    const context = { fillRect: vi.fn(), fillText: vi.fn(), drawImage: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn() };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
    let exportedCanvas: HTMLCanvasElement | undefined;
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(function (this: HTMLCanvasElement) { exportedCanvas = this; return "data:image/png,local-test"; });
    let filename = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { filename = this.download; });
    vi.stubGlobal("Image", class {
      width = 1200; height = 600; onload?: () => void;
      set src(_value: string) { queueMicrotask(() => this.onload?.()); }
    });
    const chart = { getDataURL: vi.fn(() => "data:image/png,chart-test") };
    await downloadChartCard(chart as unknown as TrendChart, "GIDS Trend", "Canada · Influenza", "gids-trend", { lang: "en", currentDateRangeLabel: "2026-01-01 -> 2026-11-14", activeMetricLabel: "Incidence", trendMetric: "incidence_rate" });
    expect(chart.getDataURL).toHaveBeenCalledWith({ type: "png", pixelRatio: 2, backgroundColor: "#ffffff" });
    expect([exportedCanvas?.width, exportedCanvas?.height]).toEqual([1600, 1120]);
    expect(filename).toMatch(/^gids-trend-incidence-rate-\d{4}-\d{2}-\d{2}\.png$/);
    expect(context.fillText).toHaveBeenCalledWith("GIDS Trend", 56, 62);
    expect(context.fillText).toHaveBeenCalledWith(expect.stringContaining("2026-01-01 -> 2026-11-14"), 940, 1024);
    expect(context.drawImage).toHaveBeenCalledTimes(2);
    expect(context.drawImage).toHaveBeenLastCalledWith(expect.anything(), 56, 962, 132, 132);
  });
});
