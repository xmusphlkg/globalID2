

import { t } from "@/lib/i18n";

import type { TrendMetric, TrendChart } from "../presentation";
import { createQrDataUrl } from "./qr-code";
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = src;
  });
}

function fitRect(sourceWidth: number, sourceHeight: number, targetWidth: number, targetHeight: number) {
  const ratio = Math.min(targetWidth / sourceWidth, targetHeight / sourceHeight);
  return {
    width: sourceWidth * ratio,
    height: sourceHeight * ratio,
  };
}

function downloadCanvas(canvas: HTMLCanvasElement, filename: string) {
  const link = document.createElement("a");
  link.href = canvas.toDataURL("image/png");
  link.download = filename;
  link.click();
}

export async function downloadChartCard(
    chart: TrendChart | null,
    title: string,
    subtitle: string,
    filenamePrefix: string,
    context: { lang: "en" | "zh"; currentDateRangeLabel: string; activeMetricLabel: string; trendMetric: TrendMetric },
  ) {
    const { lang, currentDateRangeLabel, activeMetricLabel, trendMetric } = context;
    if (!chart || typeof document === "undefined") return;
    const chartUrl = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#ffffff" });
    const pageUrl = typeof window === "undefined" ? "GIDS Dashboard" : window.location.href;
    const [chartImage, qrImage] = await Promise.all([loadImage(chartUrl), loadImage(createQrDataUrl(pageUrl))]);

    const canvas = document.createElement("canvas");
    canvas.width = 1600;
    canvas.height = 1120;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.fillStyle = "#17211f";
    ctx.font = "700 34px Arial, sans-serif";
    ctx.fillText(title, 56, 62);
    ctx.font = "500 22px Arial, sans-serif";
    ctx.fillStyle = "#4f5f5a";
    ctx.fillText(subtitle, 56, 98);

    const footerHeight = 184;
    const chartArea = fitRect(chartImage.width, chartImage.height, canvas.width - 112, canvas.height - footerHeight - 140);
    const chartX = (canvas.width - chartArea.width) / 2;
    const chartY = 122;
    ctx.drawImage(chartImage, chartX, chartY, chartArea.width, chartArea.height);

    const footerY = canvas.height - footerHeight;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, footerY, canvas.width, footerHeight);
    ctx.strokeStyle = "#d7ddd7";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, footerY);
    ctx.lineTo(canvas.width, footerY);
    ctx.stroke();

    ctx.drawImage(qrImage, 56, footerY + 26, 132, 132);
    ctx.fillStyle = "#17211f";
    ctx.font = "700 22px Arial, sans-serif";
    ctx.fillText("GIDS Dashboard", 220, footerY + 56);
    ctx.font = "400 18px Arial, sans-serif";
    ctx.fillStyle = "#4f5f5a";
    ctx.fillText(pageUrl.length > 82 ? `${pageUrl.slice(0, 79)}...` : pageUrl, 220, footerY + 88);
    ctx.fillText(lang === "zh" ? "扫码打开当前仪表盘视图" : "Scan to open this dashboard view", 220, footerY + 120);

    const exportedAt = new Date().toLocaleString(lang === "zh" ? "zh-CN" : "en-US");
    const detailX = 940;
    ctx.fillStyle = "#17211f";
    ctx.font = "700 20px Arial, sans-serif";
    ctx.fillText(lang === "zh" ? "导出信息" : "Export Details", detailX, footerY + 56);
    ctx.font = "400 18px Arial, sans-serif";
    ctx.fillStyle = "#4f5f5a";
    ctx.fillText(`${t(lang, "period")}: ${currentDateRangeLabel}`, detailX, footerY + 88);
    ctx.fillText(`${lang === "zh" ? "指标" : "Metric"}: ${activeMetricLabel}`, detailX, footerY + 118);
    ctx.fillText(`${lang === "zh" ? "导出时间" : "Exported"}: ${exportedAt}`, detailX, footerY + 148);

    const safeMetric = trendMetric.replace(/_/g, "-");
    downloadCanvas(canvas, `${filenamePrefix}-${safeMetric}-${new Date().toISOString().slice(0, 10)}.png`);
  }
