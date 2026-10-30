"use client";
import { useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import type { components } from "@/generated/api";
import { apiFetch } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useAppStore } from "@/stores/app-store";
import { taskScopeHref, taskScopeForType } from "@/features/operations/task-scope";
import { RunView } from "./collection";

/** Resolve historical task bookmarks by their actual business responsibility. */
export function RunRoute({ taskUuid }: { taskUuid: string }) {
  const { lang } = useAppStore();
  const router = useRouter();
  const search = useSearchParams();
  const path = `/collection/runs/${encodeURIComponent(taskUuid)}`;
  const query = useQuery({ queryKey: [path], queryFn: () => apiFetch<components["schemas"]["RunOut"]>(path), staleTime:5000, retry:false });
  const task = query.data;
  const destination = task && taskScopeForType(task.task_type) !== "collection" ? taskScopeHref(task) : null;
  useEffect(() => {
    if (!destination) return;
    const target = new URL(destination,"http://localhost");
    const params = new URLSearchParams(search.toString());
    target.searchParams.forEach((value,key) => params.set(key,value));
    router.replace(`${target.pathname}?${params.toString()}`);
  }, [destination,router,search]);
  if (destination) return <div role="status" className="cc-panel"><p>{t(lang,"cc_loading")}</p><Link className="cc-button" href={destination}>{t(lang,"wf_open")}</Link></div>;
  if (query.isError) return <div role="alert" className="cc-panel"><p>{t(lang,"cc_load_failed")}</p><p className="cc-muted">{query.error.message}</p><button className="cc-button" onClick={() => query.refetch()}>{t(lang,"cc_retry_load")}</button></div>;
  if (!task) return <p role="status" className="cc-panel">{t(lang,"cc_loading")}</p>;
  return <RunView taskUuid={taskUuid} />;
}
