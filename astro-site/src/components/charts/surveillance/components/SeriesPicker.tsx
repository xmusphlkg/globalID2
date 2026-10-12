import { useId, useMemo, useState } from "react";
import type { AnalysisConfig, Catalog, Language } from "../domain/types";
import { label } from "../presentation/labels";
interface Props {
  catalog: Catalog;
  config: AnalysisConfig;
  lang: Language;
  onSelect: (id: string) => void;
  onBatch: (ids: string[]) => void;
  loadSources: (entityId: string) => void;
  sourceStatus: Record<string, "loading" | "ready" | "error">;
}
export default function SeriesPicker({
  catalog,
  config,
  lang,
  onSelect,
  onBatch,
  loadSources,
  sourceStatus,
}: Props) {
  const inputId = useId();
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("all");
  const [page, setPage] = useState(0);
  const entities = useMemo(
    () =>
      Object.values(catalog.entities).sort((a, b) =>
        a.labels[lang].localeCompare(b.labels[lang], lang),
      ),
    [catalog.entities, lang],
  );
  const parents = [
    ...new Set(
      entities
        .map((entity) => entity.parentId)
        .filter((id): id is string => Boolean(id)),
    ),
  ];
  const filtered = entities.filter((entity) => {
    const search = query.trim().toLowerCase();
    return (
      (!search ||
        [entity.id, ...Object.values(entity.labels)].some((value) =>
          value.toLowerCase().includes(search),
        )) &&
      (scope === "all" || scope === "national"
        ? scope === "all" || entity.geographyLevel === "country"
        : entity.parentId === scope)
    );
  });
  const selectionsByEntity = useMemo(() => {
    const map = new Map<string, (typeof catalog.selections)[string][]>();
    Object.values(catalog.selections).forEach((selection) =>
      map.set(selection.entityId, [
        ...(map.get(selection.entityId) ?? []),
        selection,
      ]),
    );
    return map;
  }, [catalog.selections]);
  const eligibleDefault = (entityId: string) => {
    const selections = selectionsByEntity.get(entityId) ?? [];
    return selections.find((selection) => selection.isDefault) ?? selections[0];
  };
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filtered.length / 30) - 1),
  );
  const option = (selection: (typeof catalog.selections)[string]) => {
    const supported = selection.seriesIds.map(
      (id) => catalog.series[id].measure,
    );
    const dimensions = Object.entries(
      catalog.series[selection.seriesIds[0]]?.dimensions ?? {},
    ).filter(([key]) => key !== "sourceDimension");
    const sourceDimension =
      catalog.series[selection.seriesIds[0]]?.dimensions.sourceDimension;
    if (!dimensions.length && sourceDimension && sourceDimension !== "all")
      dimensions.push(["dimension", sourceDimension]);
    const selected = config.selectionIds.includes(selection.id);
    const firstSeries = catalog.series[selection.seriesIds[0]];
    return (
      <label
        className={`surveillance-choice surveillance-source-choice ${selected ? "is-selected" : ""}`}
        key={selection.id}
      >
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onSelect(selection.id)}
          name={`${inputId}-series`}
          value={selection.id}
        />
        <span>
          <strong>
            {selection.isDefault
              ? label("published", lang)
              : selection.sourceLabel}
          </strong>
          <small>
            {label(firstSeries?.displayCadence ?? firstSeries?.cadence ?? "unknown", lang)}
            {firstSeries?.cadenceBasis === "date_pattern" && ` · ${label("cadenceEstimate", lang)}`}
          </small>
          {dimensions.length > 0 && (
            <small>
              {dimensions.map(([key, value]) => `${key}: ${value}`).join(" · ")}
            </small>
          )}
          {!config.measures.every((measure) => supported.includes(measure)) && (
            <small>{label("measure_unavailable", lang)}</small>
          )}
        </span>
      </label>
    );
  };
  return (
    <div className="surveillance-picker">
      <label className="sr-only" htmlFor={`${inputId}-search`}>{label("search", lang)}</label>
      <input
        id={`${inputId}-search`}
        type="search"
        placeholder={label("search", lang)}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setPage(0);
        }}
      />
      {parents.length > 0 && (
        <label>
          {label("scope", lang)}
          <select
            value={scope}
            onChange={(event) => {
              setScope(event.target.value);
              setPage(0);
            }}
          >
            <option value="all">{label("mixed", lang)}</option>
            <option value="national">{label("national", lang)}</option>
            {parents.map((parent) => (
              <option key={parent} value={parent}>
                {parent} · {label("subdivisions", lang)}
              </option>
            ))}
          </select>
        </label>
      )}
      <button
        className="surveillance-batch"
        type="button"
        disabled={!filtered.length}
        onClick={() =>
          onBatch(
            filtered.flatMap((entity) => {
              const selection = eligibleDefault(entity.id);
              return selection ? [selection.id] : [];
            }),
          )
        }
      >
        {label("selectFiltered", lang)} ({filtered.length})
      </button>
      <div className="surveillance-candidates">
        {filtered
          .slice(currentPage * 30, currentPage * 30 + 30)
          .map((entity) => {
            const selections = selectionsByEntity.get(entity.id) ?? [];
            const primary = selections.find((selection) => selection.isDefault);
            const sources = selections.filter(
              (selection) => !selection.isDefault,
            );
            return (
              <div key={entity.id} className="surveillance-entity">
                {primary ? (
                  <label className={`surveillance-entity-main ${config.selectionIds.includes(primary.id) ? "is-selected" : ""}`}>
                    <input
                      type="checkbox"
                      checked={config.selectionIds.includes(primary.id)}
                      onChange={() => onSelect(primary.id)}
                      name={`${inputId}-series`}
                      value={primary.id}
                    />
                    <span className="surveillance-entity-name" title={`${entity.labels[lang]} · ${entity.id}`}>
                      {entity.labels[lang]}
                    </span>
                    <small
                      className="surveillance-frequency"
                      title={catalog.series[primary.seriesIds[0]]?.cadenceBasis === "date_pattern" ? label("cadenceEstimateHelp", lang) : undefined}
                    >
                      {label(catalog.series[primary.seriesIds[0]]?.displayCadence ?? catalog.series[primary.seriesIds[0]]?.cadence ?? "unknown", lang)}
                      {catalog.series[primary.seriesIds[0]]?.cadenceBasis === "date_pattern" && "*"}
                    </small>
                  </label>
                ) : (
                  <div className="surveillance-entity-name" title={entity.id}>{entity.labels[lang]}</div>
                )}
                <details className="surveillance-source-details"
                  onToggle={(event) => {
                    if (event.currentTarget.open) loadSources(entity.id);
                  }}
                >
                  <summary
                    title={label("sourceOptions", lang)}
                    aria-label={`${label("sourceOptions", lang)}${sourceStatus[entity.id] === "ready" ? ` (${sources.length})` : ""}`}
                  >
                    <span className="sr-only">{label("sourceOptions", lang)}</span>
                    <span aria-hidden="true">{(sources.length > 0 || sourceStatus[entity.id] === "ready") ? sources.length : ""}</span>
                  </summary>
                  {sourceStatus[entity.id] === "loading" && (
                    <p role="status">{label("loading", lang)}</p>
                  )}
                  {sourceStatus[entity.id] === "error" && (
                    <button
                      type="button"
                      onClick={() => loadSources(entity.id)}
                    >
                      {label("retry", lang)}
                    </button>
                  )}
                  {sources.map(option)}
                  {!sources.length && sourceStatus[entity.id] === "ready" && (
                    <small>{label("noSources", lang)}</small>
                  )}
                </details>
              </div>
            );
          })}
      </div>
      {filtered.some((entity) => {
        const selection = eligibleDefault(entity.id);
        return selection && catalog.series[selection.seriesIds[0]]?.cadenceBasis === "date_pattern";
      }) && (
        <p className="surveillance-picker-note">* {label("cadenceEstimateHelp", lang)}</p>
      )}
      {filtered.length > 30 && (
        <nav className="surveillance-pager" aria-label={label("objects", lang)}>
          <button
            type="button"
            disabled={!currentPage}
            onClick={() => setPage(currentPage - 1)}
          >
            {label("back", lang)}
          </button>
          <span>
            {currentPage + 1}/{Math.ceil(filtered.length / 30)}
          </span>
          <button
            type="button"
            disabled={(currentPage + 1) * 30 >= filtered.length}
            onClick={() => setPage(currentPage + 1)}
          >
            {label("next", lang)}
          </button>
        </nav>
      )}
    </div>
  );
}
