import { useMemo, useState } from "react";
import type {
  AnalysisResult,
  Catalog,
  Diagnostic,
  Language,
} from "../domain/types";
import { dataRows, type DataScope } from "../presentation/export";
import { label, lineName, number } from "../presentation/labels";

export function Diagnostics({
  diagnostics,
  catalog,
  lang,
}: {
  diagnostics: Diagnostic[];
  catalog: Catalog;
  lang: Language;
}) {
  return (
    <ul className="surveillance-diagnostics">
      {diagnostics.map((diagnostic, index) => {
        const names = [
          ...new Set(
            diagnostic.seriesIds.map((id) => {
              const selection =
                catalog.selections[catalog.series[id]?.selectionId ?? id];
              return selection
                ? `${catalog.entities[selection.entityId]?.labels[lang] ?? selection.entityId}${selection.sourceLabel ? ` · ${selection.sourceLabel}` : ""}`
                : id;
            }),
          ),
        ];
        return (
          <li
            key={`${diagnostic.code}-${index}`}
            data-severity={diagnostic.severity}
          >
            <span>
              {label(diagnostic.code, lang)}{" "}
              {diagnostic.detail ? label(diagnostic.detail, lang) : ""}
            </span>
            <small>{names.join(" / ")}</small>
          </li>
        );
      })}
    </ul>
  );
}
export function SummaryPanel({
  result,
  catalog,
  lang,
}: {
  result: AnalysisResult;
  catalog: Catalog;
  lang: Language;
}) {
  return (
    <div
      className="surveillance-table-wrap"
      role="region"
      aria-label={label("summary", lang)}
      tabIndex={0}
    >
      <table className="surveillance-table">
        <caption>{label("summary", lang)}</caption>
        <thead>
          <tr>
            <th>{label("name", lang)}</th>
            <th>{label("latest", lang)}</th>
            <th>{label("total", lang)}</th>
            <th>{label("observed", lang)}</th>
            <th>{label("missing", lang)}</th>
          </tr>
        </thead>
        <tbody>
          {result.summaries.map((summary) => {
            const line = result.lines.find(
              (item) => item.id === summary.lineId,
            )!;
            return (
              <tr key={summary.lineId}>
                <th scope="row">
                  {lineName(line, catalog, lang)}
                  <small>{label(line.unit, lang)}</small>
                </th>
                <td>
                  {number(summary.latest?.value, lang)}
                  <small>{summary.latest?.period.label ?? "—"}</small>
                </td>
                <td>{number(summary.total, lang)}</td>
                <td>
                  {summary.observed}
                  {summary.expectedPeriods != null
                    ? ` / ${summary.expectedPeriods}`
                    : ""}
                </td>
                <td>{summary.missing}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="surveillance-note">{label("latestNote", lang)}</p>
    </div>
  );
}
export function DataPanel({
  result,
  catalog,
  lang,
  scope,
}: {
  result: AnalysisResult;
  catalog: Catalog;
  lang: Language;
  scope: DataScope;
}) {
  const [page, setPage] = useState(0);
  const rows = useMemo(
    () => dataRows(result, catalog, lang, scope),
    [result, catalog, lang, scope],
  );
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(rows.length / 100) - 1),
  );
  return (
    <div>
      <div
        className="surveillance-table-wrap"
        role="region"
        aria-label={label("table", lang)}
        tabIndex={0}
      >
        <table className="surveillance-table" data-testid="surveillance-data">
          <caption>
            {label(scope === "window" ? "currentWindow" : scope === "raw" ? "rawData" : "allDerived", lang)} · {rows.length} · {label("version", lang)}: {result.catalogVersion}
          </caption>
          <thead>
            <tr>
              {["name", "date", "value", ...(result.config.task === "change" ? ["baseline"] : []), "unit", "status"].map((key) => (
                <th key={key}>{label(key, lang)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows
              .slice(currentPage * 100, currentPage * 100 + 100)
              .map((row, index) => (
                <tr key={`${row.seriesId}-${row.date}-${index}`}>
                  <th scope="row">{row.series}</th>
                  <td>{row.date}</td>
                  <td>{number(row.value, lang)}</td>
                  {result.config.task === "change" && (
                    <td>
                      {row.baselineDate ?? "—"}
                      {row.baselineValue != null
                        ? ` · ${number(row.baselineValue, lang)}`
                        : ""}
                    </td>
                  )}
                  <td>{label(row.unit, lang)}</td>
                  <td>
                    {row.quality
                      ? row.quality
                          .split(";")
                          .map((status) => label(status, lang))
                          .join(", ")
                      : "—"}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {rows.length > 100 && (
        <nav className="surveillance-pager">
          <button
            type="button"
            disabled={!currentPage}
            onClick={() => setPage(currentPage - 1)}
          >
            {label("back", lang)}
          </button>
          <span>
            {currentPage + 1}/{Math.ceil(rows.length / 100)}
          </span>
          <button
            type="button"
            disabled={(currentPage + 1) * 100 >= rows.length}
            onClick={() => setPage(currentPage + 1)}
          >
            {label("next", lang)}
          </button>
        </nav>
      )}
    </div>
  );
}
export function MethodsPanel({
  result,
  catalog,
  lang,
}: {
  result: AnalysisResult;
  catalog: Catalog;
  lang: Language;
}) {
  return (
    <div className="surveillance-methods">
      <p>
        {result.config.task === "change"
          ? label("changeMethod", lang)
          : result.config.task === "shape"
          ? label("shapeMethod", lang)
          : result.config.task === "historical" ||
              (result.config.task === "trend" && result.config.showReference)
            ? label("referenceMethod", lang)
            : label("latestNote", lang)}
      </p>
      {result.config.measures.includes("incidence_rates") && (
        <p>{label("ratesMethod", lang)}</p>
      )}
      {result.config.aggregationGroups.length > 0 && (
        <>
          <p>{label("aggregateMethod", lang)}</p>
          <ul>
            {result.config.aggregationGroups.map((group) => (
              <li key={group.id}>
                {group.selectionIds
                  .map(
                    (id) =>
                      catalog.entities[catalog.selections[id]?.entityId]
                        ?.labels[lang] ?? id,
                  )
                  .join(" + ")}
              </li>
            ))}
          </ul>
        </>
      )}
      <p>
        {label("version", lang)}: <code>{result.catalogVersion}</code> ·{" "}
        <code>{result.methodVersion}</code>
        {catalog.snapshot ? ` · ${label("snapshot", lang)}` : ""}
      </p>
      {catalog.sourceVersion && (
        <p>
          {label("sourceVersion", lang)}: <code>{catalog.sourceVersion}</code>
        </p>
      )}
      {catalog.recordedAtAsOf && (
        <div className="surveillance-recorded-snapshot" role="note">
          <p>
            {label("recordedAtAsOf", lang)}: <time dateTime={catalog.recordedAtAsOf}>{catalog.recordedAtAsOf}</time>
          </p>
          <p>{label("recordedSnapshotNote", lang)}</p>
          {catalog.recordedHistoryComplete === false && (
            <p role="status">{label("recordedHistoryIncomplete", lang)}</p>
          )}
        </div>
      )}
      <dl>
        {result.config.selectionIds
          .flatMap((id) => catalog.selections[id]?.seriesIds ?? [])
          .filter((id) =>
            result.config.measures.includes(catalog.series[id].measure),
          )
          .map((id) => {
            const series = catalog.series[id];
            return (
              <div key={id}>
                <dt>
                  {catalog.entities[series.entityId].labels[lang]} ·{" "}
                  {label(series.measure, lang)}
                </dt>
                <dd>
                  {series.provenance.label} · {label(series.displayCadence ?? series.cadence, lang)}{series.cadenceBasis === "date_pattern" ? ` · ${label("cadenceEstimate", lang)}` : ""} ·{" "}
                  {series.timeBasis ?? label("unknown", lang)} ·{" "}
                  {series.reportingBasis ?? label("unknown", lang)}
                  <small>
                    {series.definitionId ?? label("unknown", lang)} ·{" "}
                    {series.provenance.policy ?? label("unknown", lang)}
                    {series.provenance.snapshotVersion && (
                      <> · {label("sourceVersion", lang)}: {series.provenance.snapshotVersion}</>
                    )}
                  </small>
                  {series.provenance.url &&
                    /^https?:\/\//i.test(series.provenance.url) && (
                      <a
                        href={series.provenance.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {label("sourceOptions", lang)}
                      </a>
                    )}
                </dd>
              </div>
            );
          })}
      </dl>
    </div>
  );
}
