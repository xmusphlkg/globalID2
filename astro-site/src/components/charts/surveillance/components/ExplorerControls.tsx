import {
  type AnalysisConfig,
  type AnalysisResult,
  type Catalog,
  type DateWindow,
  type Language,
  type Measure,
} from "../domain/types";
import { label } from "../presentation/labels";

interface MeasureProps {
  config: AnalysisConfig;
  catalog: Catalog;
  lang: Language;
  patch: (values: Partial<AnalysisConfig>) => void;
}

export function MeasureSelect({ config, catalog, lang, patch }: MeasureProps) {
  const selectedMeasure = config.measures.length > 1 ? "both" : config.measures[0];
  const selectionIds = config.selectionIds.length
    ? config.selectionIds
    : Object.keys(catalog.selections);
  const availableMeasures = new Set(
    selectionIds.flatMap((id) =>
      (catalog.selections[id]?.seriesIds ?? [])
        .map((seriesId) => catalog.series[seriesId])
        .filter((series) => series?.observations.some((point) => point.value !== null))
        .map((series) => series.measure),
    ),
  );
  return (
    <label className="chart-measure">
      {label("metric", lang)}
      <select
        aria-label={label("metric", lang)}
        value={selectedMeasure}
        onChange={(event) =>
          patch({
            measures:
              event.target.value === "both"
                ? ["cases", "deaths"]
                : [event.target.value as Measure],
          })
        }
      >
        {(
          [
            "cases",
            "deaths",
            "incidence_rates",
            "source_value",
            "both",
          ] as const
        ).map((metric) => {
          const available = metric === "both"
            ? availableMeasures.has("cases") && availableMeasures.has("deaths")
            : availableMeasures.has(metric);
          return (
            <option value={metric} key={metric} disabled={!available}>
              {label(metric, lang)}{available ? "" : ` · ${label("unavailableOption", lang)}`}
            </option>
          );
        })}
      </select>
    </label>
  );
}

interface PrimaryProps {
  expanded: boolean;
  config: AnalysisConfig;
  result: AnalysisResult;
  catalog: Catalog;
  lang: Language;
  patch: (values: Partial<AnalysisConfig>) => void;
  chooseWindow: (months: number | null) => void;
  dateValue: DateWindow | null;
  setDate: (field: "startDate" | "endDate", value: string) => void;
}
export function PrimaryControls({
  expanded,
  config,
  result,
  catalog,
  lang,
  patch,
  chooseWindow,
  dateValue,
  setDate,
}: PrimaryProps) {
  return (
    <>
      <div className="surveillance-time">
        <span>{label("time", lang)}</span>
        {[6, 12, 36].map((months) => (
          <button
            type="button"
            key={months}
            onClick={() => chooseWindow(months)}
          >
            {months === 36 ? "3Y" : `${months}M`}
          </button>
        ))}
        <button type="button" onClick={() => chooseWindow(null)}>
          {label("all", lang)}
        </button>
        <label>
          {label("start", lang)}
          <input
            aria-label={label("start", lang)}
            type="date"
            value={dateValue?.startDate ?? ""}
            onChange={(event) => setDate("startDate", event.target.value)}
          />
        </label>
        <label>
          {label("end", lang)}
          <input
            aria-label={label("end", lang)}
            type="date"
            value={dateValue?.endDate ?? ""}
            onChange={(event) => setDate("endDate", event.target.value)}
          />
        </label>
        {result.commonWindow &&
          config.compareWindow === "common" &&
          config.selectionIds.length > 1 && (
            <small>
              {label("common", lang)}: {result.commonWindow.startDate} →{" "}
              {result.commonWindow.endDate}
            </small>
          )}
      </div>
      <div
        className="surveillance-selected"
        aria-label={label("selected", lang)}
      >
        {config.selectionIds.map((id) => {
          const selection = catalog.selections[id];
          return (
            <button
              type="button"
              key={id}
              onClick={() =>
                patch({
                  selectionIds: config.selectionIds.filter((key) => key !== id),
                  aggregationGroups: config.aggregationGroups
                    .map((group) => ({
                      ...group,
                      selectionIds: group.selectionIds.filter(
                        (member) => member !== id,
                      ),
                    }))
                    .filter((group) => group.selectionIds.length >= 2),
                })
              }
              aria-label={`${label("remove", lang)} ${catalog.entities[selection?.entityId]?.labels[lang] ?? id}`}
            >
              {catalog.entities[selection?.entityId]?.labels[lang] ?? id}
              {selection?.sourceLabel ? ` · ${selection.sourceLabel}` : ""}{" "}
              <span aria-hidden="true">×</span>
            </button>
          );
        })}
        {expanded && config.selectionIds.length > 0 && (
          <button
            type="button"
            onClick={() => patch({ selectionIds: [], aggregationGroups: [] })}
          >
            {label("clear", lang)}
          </button>
        )}
      </div>
    </>
  );
}
