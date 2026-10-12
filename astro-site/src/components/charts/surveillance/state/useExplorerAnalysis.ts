import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { analyze } from "../analysis/engine";
import type { AnalysisConfig, Catalog, Language } from "../domain/types";
import { label } from "../presentation/labels";
import {
  initialConfig,
  restoreConfig,
  serializeConfig,
} from "./config";

interface Options {
  catalog: Catalog;
  initialTask: AnalysisConfig["task"];
  initialMeasures?: AnalysisConfig["measures"];
  initialSelectionIds?: string[];
  sourceStatus: Record<string, "loading" | "ready" | "error">;
  loadSources: (entityId: string) => void;
  language: Language;
  onInvalidShare: (message: string) => void;
  onChange: () => void;
}

/** Owns the current query and its persisted state. */
export function useExplorerAnalysis({
  catalog,
  initialTask,
  initialMeasures,
  initialSelectionIds,
  sourceStatus,
  loadSources,
  language,
  onInvalidShare,
  onChange,
}: Options) {
  const baseConfig = useMemo(
    () => ({
      ...initialConfig(catalog, initialTask, initialSelectionIds),
      ...(initialMeasures ? { measures: initialMeasures } : {}),
    }),
    [catalog.contextId],
  );
  const [config, setConfig] = useState(baseConfig);
  const hydrated = useRef(false);
  const result = useMemo(() => analyze(catalog, config), [catalog, config]);

  useEffect(() => {
    const restore = () => {
      const shared = new URL(window.location.href).searchParams.get("analysis");
      let saved: string | null = null;
      try {
        saved = sessionStorage.getItem(`surveillance:${catalog.contextId}`);
      } catch {
        // Storage can be disabled.
      }
      const serialized = shared ?? saved;
      if (serialized) {
        const restored = restoreConfig(serialized, catalog);
        if (restored) setConfig(restored);
        else if (shared) onInvalidShare(label("invalidShare", language));
      }
      hydrated.current = true;
    };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [catalog.contextId]);
  useEffect(() => {
    if (!hydrated.current) return;
    try {
      sessionStorage.setItem(
        `surveillance:${catalog.contextId}`,
        serializeConfig(catalog.contextId, config),
      );
    } catch {
      // Analysis works without storage.
    }
  }, [catalog.contextId, config]);
  useEffect(() => {
    for (const selectionId of config.selectionIds) {
      const selection = catalog.selections[selectionId];
      if (
        selection?.seriesIds.some(
          (id) => catalog.series[id].loading === "unloaded",
        ) &&
        !sourceStatus[selection.entityId]
      )
        loadSources(selection.entityId);
    }
  }, [catalog, config, sourceStatus, loadSources]);

  const propose = useCallback(
    (next: AnalysisConfig) => {
      setConfig(next);
      onChange();
    },
    [onChange],
  );
  const patch = (values: Partial<AnalysisConfig>) =>
    propose({ ...config, ...values });
  return {
    config,
    result,
    propose,
    patch,
    setConfig,
  };
}
