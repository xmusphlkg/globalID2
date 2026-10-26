import { useEffect, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { downloadDataUrl, pngFromCanvases } from "./chartExport";
import "./chartActions.css";

export type ChartLang = "en" | "zh" | "fr";

interface FullscreenControl {
  active: boolean;
  enabled: boolean;
  toggle: () => void | Promise<void>;
}

interface Props {
  lang: ChartLang;
  shellRef?: RefObject<HTMLElement | null>;
  fullscreen?: FullscreenControl;
  /** Return the URL that should be copied. Defaults to the current page URL. */
  onShare?: () => string | Promise<string | void> | void;
  /** When the parent already shows the copied link, skip the second field. */
  showShareLink?: boolean;
  pngFilename: string;
  /** CSS selector, inside the shell, of the area to export. Defaults to the shell. */
  pngSelector?: string;
  getPng?: () => string | null | undefined;
  pngDisabled?: boolean;
  /** When the page has no published-file dialog, omit the data button. */
  showData?: boolean;
  /** Link to the page that owns the download dialog for this chart. */
  dataHref?: string;
  leading?: ReactNode;
}

const text = {
  en: {
    enter: "Enter full-screen",
    exit: "Exit full-screen",
    share: "Share configuration",
    png: "Download PNG",
    data: "Download data",
    copied: "Link copied. Opening it recalculates with the latest available data.",
    manual: "Copy this link",
    pngWaiting: "The chart is not ready to export.",
  },
  zh: {
    enter: "进入全屏",
    exit: "退出全屏",
    share: "分享配置",
    png: "下载 PNG",
    data: "下载数据",
    copied: "链接已复制。打开后会按最新可用数据重新计算。",
    manual: "复制此链接",
    pngWaiting: "图表尚未准备好，暂时不能导出。",
  },
  fr: {
    enter: "Plein écran",
    exit: "Quitter le plein écran",
    share: "Partager la configuration",
    png: "Télécharger PNG",
    data: "Télécharger les données",
    copied: "Lien copié. Il sera recalculé avec les données disponibles les plus récentes.",
    manual: "Copier ce lien",
    pngWaiting: "Le graphique n’est pas prêt à être exporté.",
  },
} as const;

function EnterIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M7.25 3.5H3.5v3.75M12.75 3.5h3.75v3.75M7.25 16.5H3.5v-3.75M12.75 16.5h3.75v-3.75" />
    </svg>
  );
}

function ExitIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M7.25 3.5v3.75H3.5M12.75 3.5v3.75h3.75M7.25 16.5v-3.75H3.5M12.75 16.5v-3.75h3.75" />
    </svg>
  );
}

function readAttr(shell: HTMLElement | null | undefined, name: string) {
  return shell?.getAttribute(name)?.trim()
    || shell?.closest(`[${name}]`)?.getAttribute(name)?.trim()
    || "";
}

export default function ChartResultActions({
  lang,
  shellRef,
  fullscreen,
  onShare,
  showShareLink = true,
  pngFilename,
  pngSelector,
  getPng,
  pngDisabled = false,
  showData = true,
  dataHref,
  leading,
}: Props) {
  const copy = text[lang];
  const [ownedFullscreen, setOwnedFullscreen] = useState(false);
  const [canFullscreen, setCanFullscreen] = useState(false);
  const [shareLink, setShareLink] = useState("");
  const [copied, setCopied] = useState(false);
  const [pngBusy, setPngBusy] = useState(false);
  const [dataReady, setDataReady] = useState(showData);
  const active = fullscreen ? fullscreen.active : ownedFullscreen;
  const enabled = fullscreen ? fullscreen.enabled : canFullscreen;
  const dataLink = dataHref?.trim() ?? "";

  useEffect(() => {
    if (fullscreen) return;
    const changed = () => setOwnedFullscreen(document.fullscreenElement === shellRef?.current);
    setCanFullscreen(Boolean(document.fullscreenEnabled));
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, [fullscreen, shellRef]);

  useEffect(() => {
    if (!showData) {
      setDataReady(false);
      return;
    }
    setDataReady(Boolean(document.querySelector("[data-download-confirm-dialog]")));
  }, [showData]);

  async function toggleFullscreen() {
    if (fullscreen) {
      await fullscreen.toggle();
      return;
    }
    const shell = shellRef?.current;
    if (!shell || !canFullscreen) return;
    try {
      if (document.fullscreenElement === shell) await document.exitFullscreen();
      else if (!document.fullscreenElement) await shell.requestFullscreen();
    } catch {
      setCanFullscreen(false);
    }
  }

  async function share() {
    const result = onShare ? await onShare() : window.location.href;
    const url = typeof result === "string" && result ? result : window.location.href;
    if (showShareLink) setShareLink(url);
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false);
      if (showShareLink) setShareLink(url);
    }
  }

  async function exportPng() {
    if (pngBusy || pngDisabled) return;
    const custom = getPng?.();
    const shell = shellRef?.current;
    const target = shell
      ? pngSelector
        ? (shell.querySelector(pngSelector) as HTMLElement | null) ?? shell
        : shell
      : null;
    const chartUrl = custom || (target ? pngFromCanvases(target) : null);
    if (!chartUrl) return;
    setPngBusy(true);
    try {
      const heading = shell?.closest("section")?.querySelector("h2, h3")?.textContent?.replace(/\s+/g, " ").trim()
        || document.querySelector("h1")?.textContent?.replace(/\s+/g, " ").trim()
        || document.title;
      const { withAttributionCard } = await import("./chartPngCard");
      const card = await withAttributionCard(chartUrl, {
        lang,
        title: readAttr(shell, "data-chart-title") || heading,
        source: readAttr(shell, "data-source-label"),
        coverage: readAttr(shell, "data-source-coverage"),
        updated: readAttr(shell, "data-source-updated"),
        url: window.location.href,
      });
      downloadDataUrl(pngFilename.endsWith(".png") ? pngFilename : `${pngFilename}.png`, card);
    } finally {
      setPngBusy(false);
    }
  }

  function openData() {
    const dialog = document.querySelector("[data-download-confirm-dialog]");
    if (!(dialog instanceof HTMLDialogElement) || dialog.open) return;
    for (const selector of ["[data-download-time-filter]", "[data-download-format-filter]"]) {
      const select = dialog.querySelector(selector);
      if (!(select instanceof HTMLSelectElement) || select.value === "all") continue;
      select.value = "all";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    try {
      dialog.showModal();
    } catch {
      // Another modal is already open.
    }
  }

  const fullscreenLabel = active ? copy.exit : copy.enter;

  return (
    <div className="chart-result-actions-wrap">
      <div className="chart-result-actions" data-testid="chart-result-actions">
        {leading}
        {enabled && (
          <button
            type="button"
            className="chart-link-btn chart-fullscreen-btn"
            onClick={toggleFullscreen}
            aria-label={fullscreenLabel}
            aria-pressed={active}
            title={fullscreenLabel}
          >
            {active ? <ExitIcon /> : <EnterIcon />}
            <span>{fullscreenLabel}</span>
          </button>
        )}
        <button type="button" className="chart-link-btn" onClick={share}>
          {copy.share}
        </button>
        <button
          type="button"
          className="chart-link-btn"
          onClick={exportPng}
          disabled={pngDisabled || pngBusy}
          title={pngDisabled ? copy.pngWaiting : copy.png}
        >
          {copy.png}
        </button>
        {(dataReady || dataLink) && (
          dataLink
            ? <a className="chart-link-btn" href={dataLink}>{copy.data}</a>
            : <button type="button" className="chart-link-btn" onClick={openData}>
                {copy.data}
              </button>
        )}
      </div>
      {showShareLink && shareLink && (
        <label className="chart-share-link">
          {copied ? copy.copied : copy.manual}
          <input readOnly value={shareLink} onFocus={(event) => event.target.select()} />
        </label>
      )}
    </div>
  );
}
