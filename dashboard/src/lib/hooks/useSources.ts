import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface StageInfo {
  stage: string;
  task_type: string;
  status: string | null;
  task_uuid: string | null;
  task_name: string | null;
  progress: number;
  last_run: string | null;
}

export interface DataSourceFlow {
  data_source: string;
  record_count: number;
  latest_date: string | null;
  latest_task_uuid?: string | null;
  latest_task_source?: string | null;
  latest_task_status?: string | null;
  latest_task_time?: string | null;
  stages: StageInfo[];
}

export function useSourcesFlow(countryId: number | null) {
  return useQuery<DataSourceFlow[]>({
    queryKey: ["sources-flow", countryId],
    queryFn: () => apiFetch(`/sources/flow?country_id=${countryId}`),
    enabled: !!countryId,
    staleTime: 15 * 1000,
  });
}

export interface CreateCrawlTaskPayload {
  task_name: string;
  country_id: number;
  description?: string;
  priority?: string;
  input_data?: Record<string, unknown>;
}

/** Start a crawl that actually executes (not just creates a DB record). */
export interface StartCrawlPayload {
  country_id: number;
  source?: string;
  force?: boolean;
  process?: boolean;
  save_raw?: boolean;
  fill_missing?: boolean;
  priority?: string;
}

export function useStartCrawl() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: StartCrawlPayload) =>
      apiFetch("/crawl/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-flow"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

/** Execute an existing pending/failed task. */
export function useExecuteTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskUuid: string) =>
      apiFetch(`/tasks/${taskUuid}/execute`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-flow"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

/** Legacy: create a task record only (no execution). */
export function useCreateCrawlTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateCrawlTaskPayload) =>
      apiFetch("/tasks", {
        method: "POST",
        body: JSON.stringify({ task_type: "crawl_data", ...payload }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources-flow"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}
