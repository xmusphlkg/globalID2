import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useAppStore } from "@/stores/app-store";
import { usePaginatedTasks, useTaskDetail, useTaskEventStream } from "@/features/operations/tasks/api";
import { useAIInteractions, useAIInteractionSummary } from "@/features/ai/api";
import { uiText } from "../messages";

export function useInteractionContext() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { countryCode, countryName, lang } = useAppStore();
  const labels = useMemo(() => uiText(lang), [lang]);
  const searchParamsString = searchParams.toString();
  const initialTaskUuid = (searchParams.get("task") ?? searchParams.get("task_uuid") ?? "").trim();
  const initialUuid = (searchParams.get("uuid") ?? searchParams.get("report_uuid") ?? "").trim();
  const initialDisease = (searchParams.get("disease") ?? "").trim();

  const [agentFilter, setAgentFilter] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [taskUuidFilter, setTaskUuidFilter] = useState(initialTaskUuid);
  const [reportUuidFilter, setReportUuidFilter] = useState(initialUuid);
  const [diseaseFilter, setDiseaseFilter] = useState(initialDisease);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const activeTaskUuid = taskUuidFilter.trim();
  const activeReportUuid = reportUuidFilter.trim();
  const hasTaskScope = Boolean(activeTaskUuid || activeReportUuid);

  useEffect(() => {
    const params = new URLSearchParams(searchParamsString);
    const nextTaskUuid = (params.get("task") ?? params.get("task_uuid") ?? "").trim();
    const nextUuid = (params.get("uuid") ?? params.get("report_uuid") ?? "").trim();
    const nextDisease = (params.get("disease") ?? "").trim();
    if (nextTaskUuid !== taskUuidFilter) {
      setTaskUuidFilter(nextTaskUuid);
    }
    if (nextUuid !== reportUuidFilter) {
      setReportUuidFilter(nextUuid);
    }
    if (nextDisease !== diseaseFilter) {
      setDiseaseFilter(nextDisease);
    }
  }, [diseaseFilter, reportUuidFilter, searchParamsString, taskUuidFilter]);

  useEffect(() => {
    const params = new URLSearchParams(searchParamsString);
    const currentTaskUuid = (params.get("task") ?? "").trim();
    const currentUuid = (params.get("uuid") ?? "").trim();
    const currentDisease = (params.get("disease") ?? "").trim();
    const nextTaskUuid = taskUuidFilter.trim();
    const nextUuid = reportUuidFilter.trim();
    const nextDisease = diseaseFilter.trim();

    if (nextTaskUuid === currentTaskUuid && nextUuid === currentUuid && nextDisease === currentDisease && !params.get("report_uuid") && !params.get("task_uuid")) {
      return;
    }

    if (nextTaskUuid) {
      params.set("task", nextTaskUuid);
    } else {
      params.delete("task");
    }
    if (nextUuid) {
      params.set("uuid", nextUuid);
    } else {
      params.delete("uuid");
    }
    if (nextDisease) {
      params.set("disease", nextDisease);
    } else {
      params.delete("disease");
    }
    params.delete("report_uuid");
    params.delete("task_uuid");

    const nextQuery = params.toString();
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
  }, [diseaseFilter, pathname, reportUuidFilter, router, searchParamsString, taskUuidFilter]);

  const interactionLimit = hasTaskScope ? 1000 : 200;
  const filters = {
    countryCode: countryCode || undefined,
    taskUuid: activeTaskUuid || undefined,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    disease: diseaseFilter.trim() || undefined,
    reportUuid: activeReportUuid || undefined,
    limit: interactionLimit,
  };

  useTaskEventStream({
    extraQueryKeys: [["ai-interactions"], ["ai-interactions-summary"], ["reports"], ["report-runs"]],
  });

  const liveRefreshMs = hasTaskScope ? 3000 : 5000;

  const { data: taskDetail, isFetching: taskDetailLoading } = useTaskDetail(activeTaskUuid || null);
  const { data: recentKnowledgeTaskPage } = usePaginatedTasks(
    undefined,
    "update_disease_knowledge",
    countryCode || undefined,
    diseaseFilter.trim() || undefined,
    8,
    0,
  );

  const { data: interactions, isLoading, isError, error } = useAIInteractions(filters, {
    refetchIntervalMs: liveRefreshMs,
  });
  const { data: summary } = useAIInteractionSummary({
    countryCode: countryCode || undefined,
    taskUuid: activeTaskUuid || undefined,
    agent: agentFilter || undefined,
    model: modelFilter || undefined,
    disease: diseaseFilter.trim() || undefined,
    reportUuid: activeReportUuid || undefined,
  }, {
    refetchIntervalMs: liveRefreshMs,
  });

  return { countryCode, countryName, lang, labels, agentFilter, setAgentFilter, modelFilter, setModelFilter, taskUuidFilter, setTaskUuidFilter, reportUuidFilter, setReportUuidFilter, diseaseFilter, setDiseaseFilter, expandedId, setExpandedId, activeTaskUuid, activeReportUuid, hasTaskScope, taskDetail, taskDetailLoading, recentKnowledgeTaskPage, interactions, isLoading, isError, error, summary };
}
