import qrcode from "./vendor/qrcode.js";

type CardLang = "en" | "zh" | "fr";

export interface PngCardInfo {
  lang: CardLang;
  title: string;
  source?: string;
  coverage?: string;
  updated?: string;
  url: string;
  /** The disease name in every language the site serves. Duplicates are dropped. */
  names?: Partial<Record<CardLang, string>>;
}

const copy = {
  en: { source: "Source", coverage: "Coverage", updated: "Updated", names: "Names", open: "Open this page" },
  zh: { source: "来源", coverage: "范围", updated: "更新", names: "名称", open: "打开本页" },
  fr: { source: "Source", coverage: "Couverture", updated: "Mise à jour", names: "Noms", open: "Ouvrir la page" },
} as const;

/** The strip grows with the text, but never past a band that dwarfs the chart. */
const MAX_LINES = 6;

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

/** CJK and full-width punctuation wrap per character; Latin scripts wrap per word. */
function isWide(char: string) {
  const code = char.codePointAt(0) ?? 0;
  return (code >= 0x2e80 && code <= 0x9fff)
    || (code >= 0xf900 && code <= 0xfaff)
    || (code >= 0xff00 && code <= 0xffef);
}

function tokenize(text: string) {
  const tokens: string[] = [];
  let current = "";
  for (const char of text) {
    if (isWide(char)) {
      if (current) tokens.push(current);
      current = "";
      tokens.push(char);
    } else if (/\s/.test(char)) {
      if (current) tokens.push(current);
      current = "";
    } else current += char;
  }
  if (current) tokens.push(current);
  return tokens;
}

function joinTokens(head: string, token: string) {
  const noSpace = isWide(head.slice(-1)) || isWide(token.slice(0, 1));
  return noSpace ? head + token : `${head} ${token}`;
}

/** Fall back to a character break when one word is wider than the column. */
function hardSplit(ctx: CanvasRenderingContext2D, value: string, maxWidth: number) {
  const rows: string[] = [];
  let row = "";
  for (const char of value) {
    if (row && ctx.measureText(row + char).width > maxWidth) {
      rows.push(row);
      row = char;
    } else row += char;
  }
  if (row) rows.push(row);
  return rows;
}

/** Wrap instead of truncating so long titles and multi-language names stay readable. */
function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number) {
  const rows: string[] = [];
  let row = "";
  for (const token of tokenize(text)) {
    const candidate = row ? joinTokens(row, token) : token;
    if (row && ctx.measureText(candidate).width > maxWidth) {
      rows.push(row);
      row = token;
    } else row = candidate;
  }
  if (row) rows.push(row);
  return rows.flatMap((line) => (ctx.measureText(line).width > maxWidth ? hardSplit(ctx, line, maxWidth) : [line]));
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

interface TextLine {
  text: string;
  font: string;
  color: string;
  height: number;
}

/**
 * Append the shared export strip used by result charts: a single logo row, the
 * description text across the remaining width, and a QR code. The strip is a
 * light print band so the file stays readable outside the site theme.
 */
export async function withAttributionCard(chartUrl: string, info: PngCardInfo) {
  const chart = await loadImage(chartUrl);
  const [mark, wordmark] = await Promise.all([
    loadImage("/logo-mark-128.png").catch(() => null),
    loadImage("/logo-1-256.webp").catch(() => null),
  ]);
  if (document.fonts?.ready) await document.fonts.ready;
  const scale = Math.max(1, Math.min(2, chart.width / 960));
  const pad = Math.round(18 * scale);
  const logoH = Math.round(26 * scale);
  const logoGap = Math.round(7 * scale);
  const rowGap = Math.round(10 * scale);
  const qrSize = Math.round(84 * scale);
  const qrCaptionH = Math.round(14 * scale);
  const qrBlockH = qrSize + Math.round(4 * scale) + qrCaptionH;

  const out = document.createElement("canvas");
  out.width = chart.width;
  out.height = chart.height;
  const ctx = out.getContext("2d");
  if (!ctx) return chartUrl;

  const textX = pad;
  const textMax = Math.max(120, out.width - qrSize - pad * 3);
  const names = orderedNames(info);
  const label = copy[info.lang];
  const meta = [
    info.coverage ? `${label.coverage}: ${info.coverage}` : "",
    info.updated ? `${label.updated}: ${info.updated}` : "",
  ]
    .filter(Boolean)
    .join("  ·  ");
  const blocks = [
    { text: info.title, size: 16, weight: 650, color: "#17304d", height: 22 },
    names.length ? { text: `${label.names}: ${names.join(" · ")}`, size: 13, weight: 600, color: "#1f6f68", height: 19 } : null,
    info.source ? { text: `${label.source}: ${info.source}`, size: 13, weight: 500, color: "#526271", height: 19 } : null,
    meta ? { text: meta, size: 13, weight: 500, color: "#526271", height: 19 } : null,
  ].filter((block): block is { text: string; size: number; weight: number; color: string; height: number } => Boolean(block));

  const lines: TextLine[] = [];
  for (const block of blocks) {
    const font = `${block.weight} ${Math.round(block.size * scale)}px "IBM Plex Sans"`;
    ctx.font = font;
    const rows = wrapText(ctx, block.text, textMax);
    const room = MAX_LINES - lines.length;
    rows.slice(0, Math.max(0, room)).forEach((row, index) => {
      const truncated = index === room - 1 && rows.length > room;
      lines.push({
        text: truncated ? fitText(ctx, `${row}…`, textMax) : row,
        font,
        color: block.color,
        height: Math.round(block.height * scale),
      });
    });
    if (lines.length >= MAX_LINES) break;
  }

  const textH = lines.reduce((sum, line) => sum + line.height, 0);
  const hasLogo = Boolean(mark || wordmark);
  const contentH = (hasLogo ? logoH + rowGap : 0) + textH;
  const footerH = Math.max(Math.round(112 * scale), pad + Math.max(contentH, qrBlockH) + pad);
  out.height = chart.height + footerH;

  ctx.fillStyle = "#fffdfa";
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(chart, 0, 0);
  const top = chart.height;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, top, out.width, footerH);
  ctx.fillStyle = "#0f766e";
  ctx.fillRect(0, top, out.width, Math.max(3, Math.round(3 * scale)));

  // Both logos sit on one row so the rest of the band belongs to the text.
  if (hasLogo) {
    let logoX = pad;
    const logoY = top + pad;
    if (mark) {
      ctx.drawImage(mark, logoX, logoY, logoH, logoH);
      logoX += logoH + logoGap;
    }
    if (wordmark) {
      const wordH = Math.round(logoH * 0.86);
      const wordW = Math.min(Math.round(wordH * (wordmark.width / wordmark.height)), Math.round(out.width * 0.22));
      ctx.drawImage(wordmark, logoX, logoY + (logoH - wordH) / 2, wordW, wordH);
    }
  }

  ctx.textBaseline = "top";
  let lineY = top + pad + (hasLogo ? logoH + rowGap : 0);
  for (const line of lines) {
    ctx.font = line.font;
    ctx.fillStyle = line.color;
    ctx.fillText(line.text, textX, lineY);
    lineY += line.height;
  }

  const qrX = out.width - pad - qrSize;
  const qrY = top + Math.round((footerH - qrBlockH) / 2);
  drawQr(ctx, cardUrl(info.url), qrX, qrY, qrSize);
  ctx.font = `600 ${Math.round(11 * scale)}px "IBM Plex Sans"`;
  ctx.fillStyle = "#657b92";
  ctx.textAlign = "center";
  ctx.fillText(fitText(ctx, label.open, qrSize + pad * 2), qrX + qrSize / 2, qrY + qrSize + Math.round(4 * scale));
  ctx.textAlign = "left";
  return out.toDataURL("image/png");
}

/** Lead with the language of the card, then list the other published names. */
function orderedNames(info: PngCardInfo) {
  const order: CardLang[] = [info.lang, "en", "zh", "fr"];
  const values = order
    .map((lang) => info.names?.[lang]?.trim() ?? "")
    .filter(Boolean);
  return values.filter((value, index) => values.findIndex((other) => other.toLowerCase() === value.toLowerCase()) === index);
}
