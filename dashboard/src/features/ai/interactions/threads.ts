import type { AIInteractionItem } from "@/features/ai/api";

export interface RunThread {
  taskUuid: string | null;
  taskName: string | null;
  taskStatus: string | null;
  runId: number;
  runUuid: string | null;
  reportUuid: string;
  reportTitle: string;
  reportStatus: string | null;
  runStatus: string | null;
  runModel: string | null;
  runProvider: string | null;
  runTemperature: number | null;
  sectionType: string | null;
  sectionTitle: string | null;
  diseaseName: string | null;
  qualityOverall: number | null;
  totalTokens: number;
  startedAt: string | null;
  endedAt: string | null;
  messages: AIInteractionItem[];
}

export function groupInteractionThreads(interactions: AIInteractionItem[] | null | undefined): RunThread[] {
  const groups = new Map<number, AIInteractionItem[]>();

  (interactions ?? []).forEach((item) => {
    const arr = groups.get(item.run_id) ?? [];
    arr.push(item);
    groups.set(item.run_id, arr);
  });

  return Array.from(groups.entries()).map(([runId, messages]) => {
    const ordered = [...messages].sort((a, b) => {
      const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0;
      const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0;
      return ta - tb;
    });

    const first = ordered[0];
    const last = ordered[ordered.length - 1];
    const totalTokens = ordered.reduce((acc, row) => acc + row.total_tokens, 0);

    return {
      taskUuid: first?.task_uuid ?? null,
      taskName: first?.task_name ?? null,
      taskStatus: first?.task_status ?? null,
      runId,
      runUuid: first?.run_uuid ?? null,
      reportUuid: first?.report_uuid ?? "",
      reportTitle: first?.report_title ?? "",
      reportStatus: first?.report_status ?? null,
      runStatus: first?.run_status ?? null,
      runModel: first?.run_model ?? first?.model ?? null,
      runProvider: first?.run_provider ?? first?.provider ?? null,
      runTemperature: first?.run_temperature ?? null,
      sectionType: first?.section_type ?? null,
      sectionTitle: first?.section_title ?? null,
      diseaseName: first?.disease_name ?? null,
      qualityOverall: first?.quality_overall ?? null,
      totalTokens,
      startedAt: first?.timestamp ?? null,
      endedAt: last?.timestamp ?? null,
      messages: ordered,
    };
  }).sort((a, b) => {
    const ta = a.endedAt ? new Date(a.endedAt).getTime() : 0;
    const tb = b.endedAt ? new Date(b.endedAt).getTime() : 0;
    return tb - ta;
  });
}
