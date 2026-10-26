import { useMemo } from "react";
import type { Language } from "../domain/types";
import {
  buildReportComparison,
  type ReportCurveDisease,
  type ReportCurveSeries,
} from "../adapters/reportComparison";
import SurveillanceExplorer from "./SurveillanceExplorer";

interface Props {
  series: ReportCurveSeries;
  latestSeries?: ReportCurveSeries | null;
  disease: ReportCurveDisease;
  report: {
    id: number;
    country_code: string;
    period_start: string;
    period_end: string;
    snapshot_version?: string | null;
  };
  lang: Language;
  showDeaths: boolean;
}

export default function ReportSurveillanceCurve({
  series,
  latestSeries,
  disease,
  report,
  lang,
  showDeaths,
}: Props) {
  const comparison = useMemo(
    () =>
      buildReportComparison({
        series,
        latestSeries,
        disease,
        periodStart: report.period_start,
        periodEnd: report.period_end,
      }),
    [series, latestSeries, disease, report.period_start, report.period_end],
  );
  const context = useMemo(
    () => ({
      id: `report:${report.country_code}:${report.id}:${disease.disease_id}`,
      kind: "report" as const,
      diseaseId: disease.disease_id,
      geographyId: report.country_code.toUpperCase(),
      snapshot: comparison.hasSnapshot,
      sourceVersion: series.snapshot_version,
    }),
    [report.id, report.country_code, disease.disease_id, comparison.hasSnapshot, series.snapshot_version],
  );
  return (
    <SurveillanceExplorer
      context={context}
      entries={comparison.entries}
      language={lang}
      initialTask={comparison.initialTask}
      initialSelectionIds={comparison.initialSelectionIds}
      initialMeasures={showDeaths ? ["cases", "deaths"] : ["cases"]}
      presentation="compact"
      reportComparison={comparison.compareLatest}
      title={
        lang === "zh"
          ? `${disease.name_zh || disease.name_en}疫情曲线`
          : lang === "fr"
            ? `Courbe épidémique : ${disease.name_fr || disease.name_en}`
            : `${disease.name_en} epidemic curve`
      }
    />
  );
}
