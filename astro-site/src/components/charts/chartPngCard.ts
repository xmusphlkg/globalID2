import qrcode from "./vendor/qrcode.js";

type CardLang = "en" | "zh" | "fr";

export interface PngCardInfo {
  lang: CardLang;
  title: string;
  source?: string;
  coverage?: string;
  updated?: string;
  url: string;
}

const copy = {
  en: { source: "Source", coverage: "Coverage", updated: "Updated", open: "Open this page" },
  zh: { source: "来源", coverage: "范围", updated: "更新", open: "打开本页" },
  fr: { source: "Source", coverage: "Couverture", updated: "Mise à jour", open: "Ouvrir la page" },
} as const;

function loadImage(src: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(src));
    image.src = src;
  });
}

function fitText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let value = text;
  while (value.length > 1 && ctx.measureText(`${value}…`).width > maxWidth) value = value.slice(0, -1);
  return `${value}…`;
}

/** Keep the code scannable when a share link carries a long analysis payload. */
export function cardUrl(href: string) {
  const url = new URL(href);
  url.hash = "";
  if (url.search.length > 180) {
    const next = new URLSearchParams();
    for (const key of ["d", "c", "u", "season_metric", "season_span", "map_metric", "research_chart"]) {
      const value = url.searchParams.get(key);
      if (value) next.set(key, value);
    }
    url.search = next.toString();
  }
  return url.toString();
}

function drawQr(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, size: number) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  const count = qr.getModuleCount();
  const cell = size / count;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(x, y, size, size);
  ctx.fillStyle = "#17304d";
  for (let row = 0; row < count; row += 1) {
    for (let col = 0; col < count; col += 1) {
      if (!qr.isDark(row, col)) continue;
      ctx.fillRect(x + col * cell, y + row * cell, Math.ceil(cell), Math.ceil(cell));
    }
  }
}

/**
 * Append the shared export strip used by result charts: logo, data lines, and a QR code.
 * The strip is a light print band so the file stays readable outside the site theme.
 */
export async function withAttributionCard(chartUrl: string, info: PngCardInfo) {
  const chart = await loadImage(chartUrl);
  const [mark, wordmark] = await Promise.all([
    loadImage("/logo-mark-128.png").catch(() => null),
    loadImage("/logo-1-256.webp").catch(() => null),
  ]);
  if (document.fonts?.ready) await document.fonts.ready;
  const scale = Math.max(1, Math.min(2, chart.width / 960));
  const footerH = Math.round(132 * scale);
  const pad = Math.round(18 * scale);
  const out = document.createElement("canvas");
  out.width = chart.width;
  out.height = chart.height + footerH;
  const ctx = out.getContext("2d");
  if (!ctx) return chartUrl;
  ctx.fillStyle = "#fffdfa";
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(chart, 0, 0);
  const top = chart.height;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, top, out.width, footerH);
  ctx.fillStyle = "#0f766e";
  ctx.fillRect(0, top, out.width, Math.max(3, Math.round(3 * scale)));

  const markSize = Math.round(72 * scale);
  const markX = pad;
  const markY = top + (footerH - markSize) / 2;
  if (mark) ctx.drawImage(mark, markX, markY, markSize, markSize);

  let textX = markX + (mark ? markSize + pad : 0);
  if (wordmark) {
    const wordH = Math.round(34 * scale);
    const wordW = Math.min(Math.round(wordH * (wordmark.width / wordmark.height)), Math.round(out.width * 0.28));
    ctx.drawImage(wordmark, textX, top + pad, wordW, wordH);
    textX += wordW + pad;
  }

  const qrSize = Math.round(84 * scale);
  const qrX = out.width - pad - qrSize;
  const qrY = top + Math.round(16 * scale);
  const textMax = Math.max(80, qrX - textX - pad);
  const text = copy[info.lang];
  const lines = [
    info.title,
    info.source ? `${text.source}: ${info.source}` : "",
    [info.coverage ? `${text.coverage}: ${info.coverage}` : "", info.updated ? `${text.updated}: ${info.updated}` : ""]
      .filter(Boolean)
      .join("  ·  "),
  ].filter(Boolean);

  ctx.textBaseline = "top";
  ctx.fillStyle = "#17304d";
  let lineY = wordmark ? top + pad + Math.round(40 * scale) : top + pad + Math.round(8 * scale);
  lines.forEach((line, index) => {
    ctx.font = `${index === 0 ? 650 : 500} ${Math.round((index === 0 ? 16 : 13) * scale)}px "IBM Plex Sans"`;
    ctx.fillStyle = index === 0 ? "#17304d" : "#526271";
    ctx.fillText(fitText(ctx, line, textMax), textX, lineY);
    lineY += Math.round((index === 0 ? 24 : 20) * scale);
  });

  drawQr(ctx, cardUrl(info.url), qrX, qrY, qrSize);
  ctx.font = `600 ${Math.round(11 * scale)}px "IBM Plex Sans"`;
  ctx.fillStyle = "#657b92";
  ctx.textAlign = "center";
  ctx.fillText(fitText(ctx, text.open, qrSize + pad * 2), qrX + qrSize / 2, qrY + qrSize + Math.round(4 * scale));
  ctx.textAlign = "left";
  return out.toDataURL("image/png");
}
