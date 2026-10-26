import { useCallback, useEffect, useId, useRef, useState } from "react";
import { analyze, selectedSeries } from "../analysis/engine";
import { useCatalog, type CatalogInput } from "../data/useCatalog";
import {
  type AnalysisConfig,
  type Catalog,
  type Language,
  type Measure,
} from "../domain/types";
import { trailingWindow, validDate, windowFor } from "../domain/calendar";
import {
  retainAggregationGroups,
  serializeConfig,
} from "../state/config";
import { useExplorerAnalysis } from "../state/useExplorerAnalysis";
import { label, lineName } from "../presentation/labels";
import ChartResultActions from "../../ChartResultActions";
import SeriesPicker from "./SeriesPicker";
import { MeasureSelect, PrimaryControls } from "./ExplorerControls";
import SurveillancePlot, { seriesColor } from "./SurveillancePlot";
import {
  DataPanel,
  Diagnostics,
  MethodsPanel,
  SummaryPanel,
} from "./ResultPanels";
import "./surveillance.css";
import type { ChartSourceMeta } from "../../../../utils/chartMeta";

interface Props extends CatalogInput {
  language?: Language;
  initialTask?: AnalysisConfig["task"];
  initialMeasures?: Measure[];
  initialSelectionIds?: string[];
  presentation?: "compact" | "standard" | "workspace";
  title?: string;
  sourceMeta?: ChartSourceMeta | null;
  reportComparison?: boolean;
  /** Hide the data button when this page has no published-file dialog. */
  showData?: boolean;
}
export default function SurveillanceExplorer(props: Props) {
  const data = useCatalog(props);
  const lang = props.language ?? "en";
  if (data.status === "loading")
    return (
      <div className="surveillance-loading" role="status" aria-busy="true">
        {label("loading", lang)}
      </div>
    );
  if (data.status === "error")
    return (
      <div className="surveillance-empty" role="alert">
        {label("loadError", lang)}{" "}
        <button type="button" onClick={data.reload}>
          {label("retry", lang)}
        </button>
      </div>
    );
  return (
    <ExplorerWorkspace
      key={props.context.id}
      {...props}
      catalog={data.catalog}
      sourceStatus={data.sourceStatus}
      loadSources={data.loadSources}
    />
  );
}
interface WorkspaceProps extends Props {
  catalog: Catalog;
  sourceStatus: Record<string, "loading" | "ready" | "error">;
  loadSources: (entityId: string) => void;
}
function ExplorerWorkspace({
  catalog,
  context,
  language: lang = "en",
  initialTask = "trend",
  initialMeasures,
  initialSelectionIds,
  presentation = "standard",
  title,
  sourceStatus,
  loadSources,
  sourceMeta,
  reportComparison = false,
  showData = true,
}: WorkspaceProps) {
  const [panelPage, setPanelPage] = useState(0);
  const [notice, setNotice] = useState("");
  const {
    setConfig,
    config,
    result,
    propose,
    patch,
  } = useExplorerAnalysis({
    catalog,
    initialTask,
    initialMeasures,
    initialSelectionIds,
    sourceStatus,
    loadSources,
    language: lang,
    onInvalidShare: setNotice,
    onChange: () => setPanelPage(0),
  });
  const [tab, setTab] = useState<"chart" | "table">("chart");
  const [expanded, setExpanded] = useState(presentation !== "compact");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [canFullscreen, setCanFullscreen] = useState(false);
  const [shareLink, setShareLink] = useState("");
  const shell = useRef<HTMLDivElement>(null),
    dialog = useRef<HTMLDialogElement>(null),
    qualityDetails = useRef<HTMLDetailsElement>(null);
  const controlId = useId();
  const panelIds = [...new Set(result.lines.map((line) => line.panelId))];
  const panelCount = panelIds.length;
  const currentPanelPage = Math.min(
    panelPage,
    Math.max(0, Math.ceil(panelCount / 4) - 1),
  );
  const currentPanelIds = panelIds.slice(currentPanelPage * 4, currentPanelPage * 4 + 4);
  useEffect(() => {
    const changed = () =>
      setFullscreen(document.fullscreenElement === shell.current);
    setCanFullscreen(Boolean(document.fullscreenEnabled));
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  useEffect(() => {
    if (pickerOpen && dialog.current && !dialog.current.open)
      dialog.current.showModal();
    else if (!pickerOpen) dialog.current?.close();
  }, [pickerOpen]);
  const select = (id: string) => {
    const requested = config;
    const adding = !requested.selectionIds.includes(id);
    const compare = adding && requested.task === "trend" && requested.selectionIds.length > 0;
    const nextIds = requested.selectionIds.includes(id)
      ? requested.selectionIds.filter((value) => value !== id)
      : requested.task === "outbreak"
        ? [id]
        : [...requested.selectionIds, id];
    propose({
      ...requested,
      task: compare ? "burden" : requested.task,
      selectionIds: nextIds,
      aggregationGroups: retainAggregationGroups(
        requested.aggregationGroups,
        nextIds,
      ),
    });
  };
  const onWindow = useCallback(
    (window: AnalysisConfig["window"]) =>
      setConfig({ ...config, window }),
    [config, setConfig],
  );
  const picker = (
    <SeriesPicker
      catalog={catalog}
      config={config}
      lang={lang}
      onSelect={select}
      onBatch={(ids) =>
        propose(
          {
            ...config,
            task: ids.length > 1 ? "burden" : config.task,
            selectionIds: ids,
            layout: "auto",
            aggregationGroups: retainAggregationGroups(
              config.aggregationGroups,
              ids,
            ),
          },
        )
      }
      loadSources={loadSources}
      sourceStatus={sourceStatus}
    />
  );
  const chooseWindow = (months: number | null) => {
    const full =
      config.task === "seasonality"
        ? windowFor(
            selectedSeries(catalog, config).flatMap((series) =>
              series.observations.map((point) => point.period.label),
            ),
          )
        : analyze(catalog, { ...config, window: null }).domain;
    if (full) patch({ window: months ? trailingWindow(full, months) : null });
  };
  const share = () => {
    const url = new URL(window.location.href);
    url.searchParams.set(
      "analysis",
      serializeConfig(catalog.contextId, config),
    );
    setShareLink(url.toString());
    return url.toString();
  };
  const toggleFullscreen = async () => {
    if (!canFullscreen) return;
    try {
      if (document.fullscreenElement === shell.current) {
        await document.exitFullscreen();
      } else if (!document.fullscreenElement && shell.current?.requestFullscreen) {
        setExpanded(true);
        await shell.current.requestFullscreen();
      }
    } catch {
      setCanFullscreen(false);
    }
  };
  const dateValue =
    config.task === "seasonality"
      ? (config.window ??
        windowFor(
          selectedSeries(catalog, config).flatMap((series) =>
            series.observations.map((point) => point.period.label),
          ),
        ))
      : (config.window ?? result.domain);
  const setDate = (field: "startDate" | "endDate", value: string) => {
    if (!validDate(value) || !dateValue) return;
    const next = { ...dateValue, [field]: value };
    if (next.startDate <= next.endDate) patch({ window: next });
  };
  return (
    <div
      className={`surveillance-explorer panel-fullscreen ${expanded ? "is-expanded" : "is-compact"} ${fullscreen ? "is-fullscreen" : ""}`}
      ref={shell}
      data-testid="surveillance-explorer"
      data-context={catalog.contextId}
      data-result-id={result.id}
      data-chart-title={title || undefined}
      data-source-label={sourceMeta?.label || undefined}
      data-source-coverage={sourceMeta?.coverage || undefined}
      data-source-updated={sourceMeta?.updatedAt || undefined}
    >
      <div className="surveillance-heading">
        {(title || presentation === "compact") && <strong>{title ?? label("title", lang)}</strong>}
        <div className="surveillance-actions">
          {expanded && (
            <MeasureSelect config={config} catalog={catalog} lang={lang} patch={patch} />
          )}
          {presentation === "compact" && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
            >
              {label("advanced", lang)}
            </button>
          )}
          {expanded && (
            <button
              className="surveillance-mobile-picker"
              type="button"
              onClick={() => setPickerOpen(true)}
            >
              {label("objects", lang)}
            </button>
          )}
          <ChartResultActions
            lang={lang}
            shellRef={shell}
            fullscreen={{ active: fullscreen, enabled: canFullscreen, toggle: toggleFullscreen }}
            onShare={share}
            showShareLink={false}
            pngFilename={`gids-surveillance-${result.id}.png`}
            pngSelector=".surveillance-plot"
            showData={showData}
          />
        </div>
      </div>
      {context.kind === "report" && (
        <p className="surveillance-note">
          {label(reportComparison ? "reportSnapshotComparison" : catalog.snapshot ? "reportSnapshot" : "reportLatest", lang)}
        </p>
      )}
      <PrimaryControls
        expanded={expanded}
        config={config}
        result={result}
        catalog={catalog}
        lang={lang}
        patch={patch}
        chooseWindow={chooseWindow}
        dateValue={dateValue}
        setDate={setDate}
      />
      {notice && (
        <p role="status" className="surveillance-note">
          {notice}
        </p>
      )}
      {shareLink && (
        <label className="surveillance-share">
          {label("shareManual", lang)}
          <input
            readOnly
            value={shareLink}
            onFocus={(event) => event.target.select()}
          />
        </label>
      )}
      <div className="surveillance-workspace">
        {expanded && (
          <aside
            className="surveillance-picker-desktop"
            aria-label={label("objects", lang)}
          >
            {picker}
          </aside>
        )}
        <div className="surveillance-result">
          <div
            className="surveillance-tabs"
            role="tablist"
            aria-label={label("title", lang)}
            onKeyDown={(event) => {
              const tabs = ["chart", "table"] as const;
              const index = tabs.indexOf(tab);
              const next =
                event.key === "ArrowRight"
                  ? tabs[(index + 1) % tabs.length]
                  : event.key === "ArrowLeft"
                    ? tabs[(index + tabs.length - 1) % tabs.length]
                    : event.key === "Home"
                      ? tabs[0]
                      : event.key === "End"
                        ? tabs.at(-1)
                        : null;
              if (!next) return;
              event.preventDefault();
              setTab(next);
              document.getElementById(`${controlId}-${next}`)?.focus();
            }}
          >
            {(["chart", "table"] as const).map(
              (value) => (
                <button
                  type="button"
                  role="tab"
                  id={`${controlId}-${value}`}
                  aria-controls={`${controlId}-panel`}
                  aria-selected={tab === value}
                  tabIndex={tab === value ? 0 : -1}
                  onClick={() => setTab(value)}
                  key={value}
                >
                  {label(value, lang)}
                </button>
              ),
            )}
          </div>
          {result.diagnostics.length > 0 && (
            <div className="surveillance-status" role="status">
              {label(
                result.diagnostics.find(
                  (item) =>
                    item.severity === "error" || item.severity === "warning",
                )?.code ?? result.diagnostics[0].code,
                lang,
              )}
              <button
                type="button"
                onClick={() => {
                  if (qualityDetails.current) qualityDetails.current.open = true;
                  qualityDetails.current?.scrollIntoView({ block: "nearest" });
                }}
              >
                {label("quality", lang)}
              </button>
              {result.diagnostics.some(
                (item) => item.code === "empty_window",
              ) && (
                <button type="button" onClick={() => chooseWindow(null)}>
                  {label("resetTime", lang)}
                </button>
              )}
            </div>
          )}
          <div
            id={`${controlId}-panel`}
            role="tabpanel"
            aria-labelledby={`${controlId}-${tab}`}
          >
            {tab === "chart" && (
              <>
                <SurveillancePlot
                  result={result}
                  catalog={catalog}
                  lang={lang}
                  panelPage={currentPanelPage}
                  onWindow={onWindow}
                />
                {panelCount > 4 && (
                  <nav className="surveillance-pager">
                    <button
                      type="button"
                      disabled={!currentPanelPage}
                      onClick={() => setPanelPage(currentPanelPage - 1)}
                    >
                      {label("back", lang)}
                    </button>
                    <span>
                      {currentPanelPage + 1}/{Math.ceil(panelCount / 4)}
                    </span>
                    <button
                      type="button"
                      disabled={(currentPanelPage + 1) * 4 >= panelCount}
                      onClick={() => setPanelPage(currentPanelPage + 1)}
                    >
                      {label("next", lang)}
                    </button>
                  </nav>
                )}
                <div className="surveillance-legend">
                  {result.lines.filter((line) => currentPanelIds.includes(line.panelId)).map((line) => (
                    <span
                      key={line.id}
                      className="surveillance-legend-item"
                    >
                      <span
                        className="surveillance-swatch"
                        style={{
                          backgroundColor: seriesColor(line.id),
                        }}
                      />
                      {lineName(line, catalog, lang)}
                    </span>
                  ))}
                </div>
                <SummaryPanel result={result} catalog={catalog} lang={lang} />
              </>
            )}
            {tab === "table" && (
              <DataPanel
                result={result}
                catalog={catalog}
                lang={lang}
                scope="window"
              />
            )}
          </div>
          {result.diagnostics.length > 0 && (
            <details className="surveillance-settings surveillance-quality-details" ref={qualityDetails}>
              <summary>{label("quality", lang)} ({result.diagnostics.length})</summary>
              <Diagnostics
                diagnostics={result.diagnostics}
                catalog={catalog}
                lang={lang}
              />
            </details>
          )}
          <details className="surveillance-settings surveillance-method-details">
            <summary>{label("methods", lang)}</summary>
            <MethodsPanel result={result} catalog={catalog} lang={lang} />
          </details>
        </div>
      </div>
      {sourceMeta && (
        <p className="surveillance-note">
          {sourceMeta.href && /^https?:\/\//i.test(sourceMeta.href) ? (
            <a href={sourceMeta.href} target="_blank" rel="noreferrer">
              {sourceMeta.label}
            </a>
          ) : (
            sourceMeta.label
          )}
          {sourceMeta.coverage ? ` · ${sourceMeta.coverage}` : ""}
          {sourceMeta.updatedAt ? ` · ${sourceMeta.updatedAt}` : ""}
        </p>
      )}
      <dialog
        className="surveillance-picker-dialog"
        ref={dialog}
        onCancel={() => setPickerOpen(false)}
        onClose={() => setPickerOpen(false)}
      >
        <div className="surveillance-heading">
          <strong>{label("objects", lang)}</strong>
          <button
            type="button"
            onClick={() => setPickerOpen(false)}
            aria-label={label("cancel", lang)}
          >
            ×
          </button>
        </div>
        {pickerOpen && picker}
      </dialog>
    </div>
  );
}
