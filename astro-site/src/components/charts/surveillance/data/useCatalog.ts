import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  loadCountryDataset,
  loadCountrySourceSeries,
  invalidateCountryDataset,
} from "../../countryDataset";
import { loadDiseaseDataset } from "../../diseaseDataset";
import {
  adaptCatalog,
  mergeSources,
  type CatalogContext,
  type LegacyEntries,
} from "../adapters/catalog.ts";
import type { SourceSeriesMetadata } from "../../countryDataset";

export interface CatalogInput {
  context: CatalogContext;
  entries?: LegacyEntries;
  dataUrl?: string;
  sourceSeriesUrl?: string;
}
export function useCatalog({
  context,
  entries,
  dataUrl,
  sourceSeriesUrl,
}: CatalogInput) {
  const [retry, setRetry] = useState(0);
  const [base, setBase] = useState<{
    url: string | undefined;
    entries: LegacyEntries;
    status: "loading" | "ready" | "error";
  }>({
    url: dataUrl,
    entries: entries ?? {},
    status: entries ? "ready" : "loading",
  });
  const [details, setDetails] = useState<
    Record<string, SourceSeriesMetadata[]>
  >({});
  const [sourceStatus, setSourceStatus] = useState<
    Record<string, "loading" | "ready" | "error">
  >({});
  const generation = useRef(0);
  const inFlight = useRef(new Set<string>());
  useEffect(() => {
    generation.current++;
    inFlight.current.clear();
    setDetails({});
    setSourceStatus({});
  }, [context.id, dataUrl, sourceSeriesUrl]);
  useEffect(() => {
    if (entries) {
      setBase({ url: dataUrl, entries, status: "ready" });
      return;
    }
    if (!dataUrl) {
      setBase({ url: dataUrl, entries: {}, status: "ready" });
      return;
    }
    let cancelled = false;
    setBase({ url: dataUrl, entries: {}, status: "loading" });
    const request =
      context.kind === "country"
        ? loadCountryDataset(dataUrl).then((data) => data.disease_series ?? {})
        : loadDiseaseDataset(dataUrl).then((data) => data.country_series ?? {});
    request
      .then((result) => {
        if (!cancelled)
          setBase({ url: dataUrl, entries: result, status: "ready" });
      })
      .catch(() => {
        if (!cancelled) setBase({ url: dataUrl, entries: {}, status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [dataUrl, entries, context.kind, retry]);
  const activeEntries = entries ?? (base.url === dataUrl ? base.entries : {});
  const merged = useMemo(
    () => mergeSources(activeEntries, details),
    [activeEntries, details],
  );
  const catalog = useMemo(
    () => adaptCatalog(merged, context),
    [merged, context],
  );
  const loadSources = useCallback(
    (entityId: string) => {
      if (inFlight.current.has(entityId) || sourceStatus[entityId] === "ready")
        return;
      const version = generation.current;
      const url =
        sourceSeriesUrl ??
        (context.kind !== "country" &&
        context.kind !== "report" &&
        /^[A-Z]{2}(?:-[A-Z0-9]+)?$/.test(entityId)
          ? `/site-data/countries/${entityId.toLowerCase()}-source-series.json`
          : null);
      if (!url) return;
      inFlight.current.add(entityId);
      setSourceStatus((current) => ({ ...current, [entityId]: "loading" }));
      loadCountrySourceSeries(url)
        .then((result) => {
          if (version !== generation.current) return;
          setDetails((current) => ({
            ...current,
            ...(context.kind === "country"
              ? result
              : { [entityId]: result[context.diseaseId ?? ""] ?? [] }),
          }));
          setSourceStatus((current) => ({ ...current, [entityId]: "ready" }));
        })
        .catch(() => {
          if (version === generation.current)
            setSourceStatus((current) => ({ ...current, [entityId]: "error" }));
        })
        .finally(() => {
          if (version === generation.current) inFlight.current.delete(entityId);
        });
    },
    [sourceSeriesUrl, context.kind, context.diseaseId, sourceStatus],
  );
  const reload = useCallback(() => {
    if (dataUrl) invalidateCountryDataset(dataUrl);
    setRetry((value) => value + 1);
  }, [dataUrl]);
  return {
    catalog,
    status: entries
      ? ("ready" as const)
      : base.url === dataUrl
        ? base.status
        : ("loading" as const),
    sourceStatus,
    loadSources,
    reload,
  };
}
