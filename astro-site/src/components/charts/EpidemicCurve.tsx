import { useMemo } from "react";
import type { ChartSourceMeta } from "../../utils/chartMeta";
import type { CountryDatasetSeriesEntry } from "./countryDataset";
import SurveillanceExplorer from "./surveillance/components/SurveillanceExplorer";

interface Props {
  series?: Record<string, CountryDatasetSeriesEntry>;
  dataUrl?: string;
  sourceSeriesUrl?: string;
  title?: string;
  entityIds?: string[];
  initialSelectionIds?: string[];
  sourceMeta?: ChartSourceMeta | null;
  initialLanguage?: "en" | "zh" | "fr";
}

/** Country-page compatibility entry. All analysis lives in surveillance/. */
export default function EpidemicCurve({
  series,
  dataUrl,
  sourceSeriesUrl,
  title,
  entityIds,
  initialSelectionIds,
  initialLanguage = "en",
  sourceMeta,
}: Props) {
  const context = useMemo(
    () => ({
      id: `country:${dataUrl ?? "embedded"}`,
      kind: "country" as const,
      geographyId: dataUrl
        ?.split("/")
        .at(-1)
        ?.replace(".json", "")
        .toUpperCase(),
      allowedEntityIds: entityIds,
    }),
    [dataUrl, entityIds],
  );
  return (
    <SurveillanceExplorer
      context={context}
      entries={series}
      dataUrl={dataUrl}
      sourceSeriesUrl={sourceSeriesUrl}
      title={title}
      language={initialLanguage}
      sourceMeta={sourceMeta}
      initialSelectionIds={initialSelectionIds}
    />
  );
}
