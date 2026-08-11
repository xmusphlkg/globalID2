"use client";

import { useAppStore } from "@/stores/app-store";
import { t } from "@/lib/i18n";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge as UiStatusBadge } from "@/components/ui/StatusBadge";
import { AlertTriangle, BookOpen, FlaskConical, ShieldCheck } from "lucide-react";
import { CataloguePanel } from "./components/catalogue-panel";
import { DetailPanel } from "./components/detail-panel";
import { useKnowledgePage } from "./hooks/use-knowledge-page";

export default function KnowledgePage() {
  const { lang } = useAppStore();
  const state = useKnowledgePage(lang);
  const {
    totalDiseases, fullProfiles, partialProfiles, blockedProfiles, selectedCount, visibleEntries, isLoading,
  } = state;

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={t(lang, "knowledge_base")}
        description={t(lang, "knowledge_base_subtitle")}
        meta={
          <>
            <UiStatusBadge tone="primary">
              {lang === "zh" ? `已选 ${selectedCount}` : `${selectedCount} selected`}
            </UiStatusBadge>
            <UiStatusBadge tone={partialProfiles > 0 ? "warning" : "success"}>
              {lang === "zh" ? `部分画像 ${partialProfiles}` : `${partialProfiles} partial`}
            </UiStatusBadge>
            <UiStatusBadge tone={blockedProfiles > 0 ? "danger" : "success"}>
              {lang === "zh" ? `阻断 ${blockedProfiles}` : `${blockedProfiles} blocked`}
            </UiStatusBadge>
            <UiStatusBadge>
              {isLoading ? (lang === "zh" ? "加载中" : "Loading") : lang === "zh" ? `可见 ${visibleEntries.length}` : `${visibleEntries.length} visible`}
            </UiStatusBadge>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          label={lang === "zh" ? "监测疾病" : "Tracked diseases"}
          value={isLoading ? "—" : totalDiseases}
          icon={<BookOpen className="h-4 w-4" />}
          tone="primary"
        />
        <MetricTile
          label={lang === "zh" ? "完整画像" : "Full profiles"}
          value={isLoading ? "—" : fullProfiles}
          icon={<ShieldCheck className="h-4 w-4" />}
          tone="success"
        />
        <MetricTile
          label={lang === "zh" ? "部分画像" : "Partial profiles"}
          value={isLoading ? "—" : partialProfiles}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone="warning"
        />
        <MetricTile
          label={lang === "zh" ? "证据阻断" : "Blocked profiles"}
          value={isLoading ? "—" : blockedProfiles}
          icon={<FlaskConical className="h-4 w-4" />}
          tone="danger"
        />
      </div>

      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,1fr)_420px]">
        <CataloguePanel lang={lang} state={state} />
        <DetailPanel lang={lang} state={state} />
      </div>
    </div>
  );
}
