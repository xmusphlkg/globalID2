import { useMemo } from "react";
import type { ChartSourceMeta } from "../../utils/chartMeta";
import SurveillanceExplorer from "./surveillance/components/SurveillanceExplorer";

interface Props {
  dataUrl?: string;
  entityIds?: string[];
  sourceMeta?: ChartSourceMeta | null;
  initialLanguage?: "en" | "zh" | "fr";
  contextKind?: "disease" | "research";
  initialSelectionIds?: string[];
}

export default function DiseaseCountryCurve({
  dataUrl,
  entityIds,
  sourceMeta,
  initialLanguage = "en",
  contextKind = "disease",
  initialSelectionIds,
}: Props) {
  const context = useMemo(
    () => ({
      id: `${contextKind}:${dataUrl ?? "empty"}:${entityIds?.join(",") ?? "all"}`,
      kind: contextKind,
      diseaseId: dataUrl?.split("/").at(-1)?.replace(".json", "").toUpperCase(),
      allowedEntityIds: entityIds,
    }),
    [dataUrl, entityIds, contextKind],
  );
  return (
    <SurveillanceExplorer
      context={context}
      dataUrl={dataUrl}
      language={initialLanguage}
      presentation={contextKind === "research" ? "compact" : "standard"}
      sourceMeta={sourceMeta}
      initialSelectionIds={initialSelectionIds}
    />
  );
}
