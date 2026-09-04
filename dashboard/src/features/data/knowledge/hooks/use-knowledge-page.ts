import { useEffect, useMemo, useRef, useState } from "react";
import type { Color } from "@/components/ui/tremor";
import {
  type StartDiseaseKnowledgeTaskResult,
  useDiseaseKnowledgeCatalogue,
  useDiseaseKnowledgeDetail,
  useStartDiseaseKnowledgeTasks,
} from "@/features/ai/api";
import { useTaskEventStream } from "@/features/operations/tasks/api";

export type KnowledgeStatusFilter = "all" | "published" | "automating" | "requires_review" | "blocked";
export type KnowledgeDisplayFilter = "all" | "full" | "partial" | "blocked";
export type RefreshPriority = "low" | "normal" | "high" | "urgent";
export type RefreshGenerator = "ai" | "auto";
export type SourceGroup = "who" | "search" | "wikidata" | "wikipedia" | "pubmed";
export type DetailTab = "briefs" | "sources" | "meta";

export const SOURCE_GROUPS: Array<{
  value: SourceGroup;
  label: string;
  note: string;
  color: Color;
}> = [
  { value: "who", label: "WHO", note: "health topics, fact sheets, and outbreak news", color: "teal" },
  { value: "search", label: "Search discovery", note: "trusted web discovery across CDC, NIH, WHO, BMJ, and Wikipedia", color: "indigo" },
  { value: "wikidata", label: "Wikidata", note: "structured identifiers and aliases", color: "violet" },
  { value: "wikipedia", label: "Wikipedia", note: "article text and section structure", color: "sky" },
  { value: "pubmed", label: "PubMed", note: "review article abstracts for supplementary knowledge", color: "rose" },
];

export function useKnowledgePage(lang: "en" | "zh") {
  const { data: catalogue, isLoading, isFetching } = useDiseaseKnowledgeCatalogue();
  const detailPanelRef = useRef<HTMLDivElement | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<KnowledgeStatusFilter>("all");
  const [displayFilter, setDisplayFilter] = useState<KnowledgeDisplayFilter>("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedDiseaseId, setSelectedDiseaseId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("briefs");
  const [briefLanguage, setBriefLanguage] = useState<string>(lang);
  const [refreshSources, setRefreshSources] = useState<SourceGroup[]>(["who", "search", "wikidata", "wikipedia", "pubmed"]);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [generator, setGenerator] = useState<RefreshGenerator>("auto");
  const [priority, setPriority] = useState<RefreshPriority>("normal");
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshResult, setRefreshResult] = useState<StartDiseaseKnowledgeTaskResult | null>(null);

  useTaskEventStream({
    extraQueryKeys: [
      ["ai", "disease-knowledge", "catalogue"],
      ["ai", "disease-knowledge", "detail"],
    ],
  });

  const { data: detail, isFetching: detailLoading } = useDiseaseKnowledgeDetail(selectedDiseaseId);
  const { mutate: startTasks, isPending: refreshPending } = useStartDiseaseKnowledgeTasks();

  const visibleEntries = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = catalogue ?? [];
    const modeRank: Record<string, number> = { blocked: 0, partial: 1, full: 2 };
    return rows.filter((item) => {
      if (statusFilter !== "all" && item.knowledge_status !== statusFilter) return false;
      if (displayFilter !== "all" && item.knowledge_display_mode !== displayFilter) return false;
      if (!needle) return true;
      return [item.disease_id, item.name_en, item.name_zh, item.category, item.icd_10, item.icd_11, item.description, item.slug]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    }).sort((a, b) => {
      const modeDifference = (modeRank[a.knowledge_display_mode] ?? 3) - (modeRank[b.knowledge_display_mode] ?? 3);
      return modeDifference !== 0 ? modeDifference : a.disease_id.localeCompare(b.disease_id);
    });
  }, [catalogue, displayFilter, search, statusFilter]);

  useEffect(() => {
    if (selectedDiseaseId && visibleEntries.length > 0 && !visibleEntries.some((item) => item.disease_id === selectedDiseaseId)) {
      setSelectedDiseaseId(null);
    }
  }, [selectedDiseaseId, visibleEntries]);

  useEffect(() => {
    setDetailTab("briefs");
    setBriefLanguage(lang);
  }, [lang, selectedDiseaseId]);

  useEffect(() => {
    const languages = Array.isArray(detail?.briefs) ? detail.briefs.map((brief) => brief.language).filter(Boolean) : [];
    if (languages.length > 0 && !languages.includes(briefLanguage)) {
      setBriefLanguage(languages.includes(lang) ? lang : languages[0]);
    }
  }, [briefLanguage, detail?.briefs, lang]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedDisease = useMemo(
    () => visibleEntries.find((item) => item.disease_id === selectedDiseaseId) ?? catalogue?.find((item) => item.disease_id === selectedDiseaseId) ?? null,
    [catalogue, selectedDiseaseId, visibleEntries],
  );
  const detailBriefs = Array.isArray(detail?.briefs) ? detail.briefs : [];
  const detailSources = Array.isArray(detail?.sources) ? detail.sources : [];
  const availableBriefLanguages = detailBriefs.map((brief) => brief.language).filter(Boolean);
  const selectedBrief = detailBriefs.find((brief) => brief.language === briefLanguage) ?? detailBriefs[0] ?? null;
  const totalDiseases = catalogue?.length ?? 0;
  const fullProfiles = catalogue?.filter((item) => item.knowledge_display_mode === "full").length ?? 0;
  const partialProfiles = catalogue?.filter((item) => item.knowledge_display_mode === "partial").length ?? 0;
  const blockedProfiles = catalogue?.filter((item) => item.knowledge_display_mode === "blocked").length ?? 0;
  const visibleAllSelected = visibleEntries.length > 0 && visibleEntries.every((item) => selectedSet.has(item.disease_id));
  const taskLogsHref = selectedDiseaseId
    ? `/ai/tasks?task_type=update_disease_knowledge&search=${encodeURIComponent(selectedDiseaseId)}`
    : "/ai/tasks?task_type=update_disease_knowledge";
  const detailTabs: Array<{ key: DetailTab; label: string; count: number }> = [
    { key: "briefs", label: lang === "zh" ? "简介" : "Briefs", count: detailBriefs.length },
    { key: "sources", label: lang === "zh" ? "来源" : "Sources", count: detailSources.length },
    { key: "meta", label: lang === "zh" ? "元信息" : "Meta", count: selectedDisease ? 1 : 0 },
  ];

  const toggleSourceGroup = (value: SourceGroup) => setRefreshSources((current) =>
    current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  const toggleDiseaseSelection = (diseaseId: string) => setSelectedIds((current) =>
    current.includes(diseaseId) ? current.filter((id) => id !== diseaseId) : [...current, diseaseId]);
  const toggleVisibleSelection = () => setSelectedIds((current) => {
    const next = new Set(current);
    if (visibleAllSelected) visibleEntries.forEach((item) => next.delete(item.disease_id));
    else visibleEntries.forEach((item) => next.add(item.disease_id));
    return Array.from(next);
  });

  const refreshDiseases = (diseaseIds: string[]) => {
    if (diseaseIds.length === 0) return;
    setRefreshError(null);
    setRefreshResult(null);
    startTasks(
      { disease_ids: diseaseIds, source: refreshSources, force: forceRefresh, generator, priority },
      {
        onSuccess: setRefreshResult,
        onError: (err: unknown) => setRefreshError(err instanceof Error ? err.message : String(err)),
      },
    );
  };

  const queryKnowledgeBrief = (diseaseId: string) => {
    setDetailTab("briefs");
    setSelectedDiseaseId(diseaseId);
    if (window.matchMedia("(max-width: 1023px)").matches) {
      window.requestAnimationFrame(() => detailPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  };

  return {
    catalogue, isLoading, isFetching, detail, detailLoading, detailPanelRef,
    search, setSearch, statusFilter, setStatusFilter, displayFilter, setDisplayFilter,
    selectedIds, selectedSet, selectedDiseaseId, selectedDisease, setSelectedIds,
    detailTab, setDetailTab, briefLanguage, setBriefLanguage, refreshSources,
    forceRefresh, setForceRefresh, generator, setGenerator, priority, setPriority,
    refreshError, refreshResult, refreshPending, visibleEntries, detailBriefs, detailSources,
    availableBriefLanguages, selectedBrief, totalDiseases, fullProfiles, partialProfiles, blockedProfiles,
    taskLogsHref, detailTabs,
    selectedCount: selectedIds.length,
    visibleSelectedCount: visibleEntries.filter((item) => selectedSet.has(item.disease_id)).length,
    visibleAllSelected,
    toggleSourceGroup, toggleDiseaseSelection, toggleVisibleSelection,
    clearSelection: () => setSelectedIds([]),
    handleBatchRefresh: () => refreshDiseases(selectedIds),
    handleSingleRefresh: () => selectedDiseaseId && refreshDiseases([selectedDiseaseId]),
    queryKnowledgeBrief,
  };
}

export type KnowledgePageState = ReturnType<typeof useKnowledgePage>;
