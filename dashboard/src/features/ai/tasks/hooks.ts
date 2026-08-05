"use client";

import { useMemo, useState, type FormEvent } from "react";
import {
  useDiseaseKnowledgeCatalogue,
  useStartAITask,
  useStartDiseaseKnowledgeTasks,
  type StartDiseaseKnowledgeTaskResult,
} from "@/features/ai/api";
import { useSettings } from "@/features/admin/api";

export type Language = "en" | "zh";
export type Priority = "low" | "normal" | "high" | "urgent";

export function useCreateAITaskForm({
  countryId,
  onClose,
}: {
  countryId: number;
  onClose: () => void;
}) {
  const [reportType, setReportType] = useState<"daily" | "weekly" | "monthly" | "special">("monthly");
  const [reportLanguage, setReportLanguage] = useState<Language>("en");
  const [reportLayout, setReportLayout] = useState<"analytical_v3" | "structured" | "legacy">("analytical_v3");
  const [analysisDepth, setAnalysisDepth] = useState<"deep" | "deterministic">("deep");
  const [qualityThreshold, setQualityThreshold] = useState(0.85);
  const [priority, setPriority] = useState<Priority>("normal");
  const [days, setDays] = useState(365);
  const [enableReview, setEnableReview] = useState(true);
  const [sendEmail, setSendEmail] = useState(false);
  const [reuseFromFailed, setReuseFromFailed] = useState(true);
  const [taskName, setTaskName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [createdTaskUuid, setCreatedTaskUuid] = useState<string | null>(null);
  const { mutate: startAITask, isPending, isSuccess } = useStartAITask();
  const { data: settings } = useSettings();

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setCreatedTaskUuid(null);

    startAITask(
      {
        country_id: countryId,
        report_type: reportType,
        language: reportLanguage,
        days,
        report_layout: reportLayout,
        analysis_depth: analysisDepth,
        quality_threshold: qualityThreshold,
        enable_review: enableReview,
        send_email: sendEmail,
        reuse_from_failed: reuseFromFailed,
        priority,
        task_name: taskName.trim() || undefined,
        description: description.trim() || undefined,
      },
      {
        onSuccess: (result) => {
          setCreatedTaskUuid(result.task_uuid);
          setTimeout(onClose, 1200);
        },
        onError: (submitError: unknown) => {
          setError(submitError instanceof Error ? submitError.message : String(submitError));
        },
      },
    );
  };

  return {
    reportType, setReportType,
    reportLanguage, setReportLanguage,
    reportLayout, setReportLayout,
    analysisDepth, setAnalysisDepth,
    qualityThreshold, setQualityThreshold,
    priority, setPriority,
    days, setDays,
    enableReview, setEnableReview,
    sendEmail, setSendEmail,
    reuseFromFailed, setReuseFromFailed,
    taskName, setTaskName,
    description, setDescription,
    error, createdTaskUuid,
    isPending, isSuccess, settings,
    handleSubmit,
  };
}

export function useDiseaseKnowledgeTaskForm({
  lang,
  onClose,
}: {
  lang: Language;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>(["who", "wikidata", "wikipedia"]);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [generator, setGenerator] = useState<"ai" | "auto">("ai");
  const [priority, setPriority] = useState<Priority>("normal");
  const [taskName, setTaskName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [successResult, setSuccessResult] = useState<StartDiseaseKnowledgeTaskResult | null>(null);
  const { data: catalogue, isLoading } = useDiseaseKnowledgeCatalogue();
  const { mutate: startTasks, isPending, isSuccess } = useStartDiseaseKnowledgeTasks();

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return catalogue ?? [];
    return (catalogue ?? []).filter((item) =>
      [item.disease_id, item.name_en, item.name_zh, item.category, item.description, item.slug]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle)),
    );
  }, [catalogue, search]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const toggleDisease = (diseaseId: string) => {
    setSelectedIds((current) =>
      current.includes(diseaseId) ? current.filter((id) => id !== diseaseId) : [...current, diseaseId],
    );
  };
  const toggleSource = (source: string) => {
    setSelectedSources((current) =>
      current.includes(source) ? current.filter((item) => item !== source) : [...current, source],
    );
  };
  const selectVisible = () => {
    setSelectedIds((current) => {
      const next = new Set(current);
      filtered.forEach((item) => next.add(item.disease_id));
      return Array.from(next);
    });
  };
  const clearSelection = () => setSelectedIds([]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSuccessResult(null);
    if (selectedIds.length === 0) {
      setError(lang === "zh" ? "请至少选择一个疾病。" : "Select at least one disease.");
      return;
    }
    startTasks(
      {
        disease_ids: selectedIds,
        source: selectedSources,
        force: forceRefresh,
        generator,
        priority,
        task_name: taskName.trim() || undefined,
        description: description.trim() || undefined,
      },
      {
        onSuccess: (result) => {
          setSuccessResult(result);
          window.setTimeout(onClose, 1800);
        },
        onError: (submitError: unknown) => {
          setError(submitError instanceof Error ? submitError.message : String(submitError));
        },
      },
    );
  };

  return {
    search, setSearch,
    selectedIds, selectedSources,
    forceRefresh, setForceRefresh,
    generator, setGenerator,
    priority, setPriority,
    taskName, setTaskName,
    description, setDescription,
    error, successResult,
    catalogue, isLoading, isPending, isSuccess,
    filtered, selectedSet,
    selectedCount: selectedIds.length,
    visibleSelectedCount: filtered.filter((item) => selectedSet.has(item.disease_id)).length,
    toggleDisease, toggleSource, selectVisible, clearSelection, handleSubmit,
  };
}
