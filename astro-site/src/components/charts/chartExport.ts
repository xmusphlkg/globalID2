export interface PngMarker {
  x: number;
  y: number;
  color: string;
  radius?: number;
}

export function chartSurfaceColor(): string {
  return document.documentElement.getAttribute("data-theme") === "light" ? "#fffdfa" : "#142839";
}

function opaqueBackground(element: HTMLElement): string {
  const color = getComputedStyle(element).backgroundColor;
  if (!color || color === "transparent" || color === "rgba(0, 0, 0, 0)") return chartSurfaceColor();
  return color;
}

export function downloadDataUrl(filename: string, dataUrl: string) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
}

function paintSource(
  source: HTMLCanvasElement,
  background: string,
  drawMarkers?: (ctx: CanvasRenderingContext2D, scaleX: number, scaleY: number) => void,
): string | null {
  const out = document.createElement("canvas");
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(source, 0, 0);
  if (drawMarkers && source.clientWidth > 0 && source.clientHeight > 0) {
    drawMarkers(ctx, source.width / source.clientWidth, source.height / source.clientHeight);
  }
  return out.toDataURL("image/png");
}

/**
 * Export every painted canvas inside `root` as one PNG.
 * Marker coordinates are CSS pixels relative to `root`.
 */
export function pngFromCanvases(root: HTMLElement, markers: PngMarker[] = []): string | null {
  const canvases = [...root.querySelectorAll("canvas")].filter(
    (canvas): canvas is HTMLCanvasElement => canvas.width > 0 && canvas.height > 0,
  );
  if (!canvases.length) return null;
  const background = opaqueBackground(root);
  if (canvases.length === 1) {
    const canvas = canvases[0];
    const canvasRect = canvas.getBoundingClientRect();
    const rootRect = root.getBoundingClientRect();
    return paintSource(canvas, background, markers.length
      ? (ctx, scaleX, scaleY) => {
          const originX = canvasRect.left - rootRect.left;
          const originY = canvasRect.top - rootRect.top;
          for (const marker of markers) {
            ctx.beginPath();
            ctx.fillStyle = marker.color;
            ctx.arc(
              (marker.x - originX) * scaleX,
              (marker.y - originY) * scaleY,
              (marker.radius ?? 5) * scaleX,
              0,
              Math.PI * 2,
            );
            ctx.fill();
          }
        }
      : undefined);
  }

  const rootRect = root.getBoundingClientRect();
  const scale = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.round(rootRect.width * scale));
  const height = Math.max(1, Math.round(rootRect.height * scale));
  const out = document.createElement("canvas");
  out.width = width;
  out.height = height;
  const ctx = out.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);
  for (const canvas of canvases) {
    const rect = canvas.getBoundingClientRect();
    ctx.drawImage(
      canvas,
      (rect.left - rootRect.left) * scale,
      (rect.top - rootRect.top) * scale,
      rect.width * scale,
      rect.height * scale,
    );
  }
  for (const marker of markers) {
    ctx.beginPath();
    ctx.fillStyle = marker.color;
    ctx.arc(marker.x * scale, marker.y * scale, (marker.radius ?? 5) * scale, 0, Math.PI * 2);
    ctx.fill();
  }
  return out.toDataURL("image/png");
}
