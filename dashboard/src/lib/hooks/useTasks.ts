import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, apiFetchWithMeta, eventStreamUrl } from "@/lib/api";

export interface TaskItem {
  id: number;
  task_uuid: string;
  task_name: string;
  task_type: string;
  status: string;
  priority: string;
  progress: number;
  country_id: number | null;
  country_code: string | null;
  country_name: string | null;
  report_id: number | null;
  description: string | null;
  last_error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  actual_duration: number | null;
  workbook_count: number;
  cancel_requested: boolean;
  cancel_requested_at: string | null;
}

export interface TaskDetail extends TaskItem {
  input_data: Record<string, unknown> | null;
  output_data: Record<string, unknown> | null;
  parent_task_id: number | null;
  workbook_entries: WorkbookEntry[];
}

export interface WorkerStatus {
  worker_process_running: boolean;
  worker_pid: number | null;
  worker_concurrency: number;
  queued_tasks: number;
  running_tasks: number;
  retrying_tasks: number;
  active_tasks: number;
  latest_created_at: string | null;
  latest_started_at: string | null;
  latest_completed_at: string | null;
}

export interface WorkbookEntry {
  id: number;
  entry_uuid: string | null;
  entry_type: string;
  title: string;
  content: string | null;
  content_type: string | null;
  prompt: string | null;
  response: string | null;
  model_used: string | null;
  tokens_used: number | null;
  cost: number | null;
  duration: number | null;
  success: boolean;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface TaskPage {
  items: TaskItem[];
  totalCount: number;
  limit: number;
  offset: number;
}

export function useTasks(
  status?: string,
  taskType?: string,
  countryCode?: string | null,
  search?: string,
  limit = 50,
) {
  return useQuery<TaskItem[]>({
    queryKey: ["tasks", status, taskType, countryCode, search, limit],
    queryFn: () => {
      const params = new URLSearchParams({ page: "1", page_size: String(limit) });
      if (status) params.set("status", status);
      if (taskType) params.set("task_type", taskType);
      if (countryCode) params.set("country_code", countryCode);
      if (search) params.set("search", search);
      return apiFetch(`/tasks?${params}`);
    },
    staleTime: 10 * 1000,
    refetchInterval: 5 * 1000,
  });
}

export function usePaginatedTasks(
  status?: string,
  taskType?: string,
  countryCode?: string | null,
  search?: string,
  limit = 50,
  offset = 0,
) {
  return useQuery<TaskPage>({
    queryKey: ["tasks", "paged", status, taskType, countryCode, search, limit, offset],
    queryFn: async () => {
      const apiPage = Math.floor(offset / limit) + 1;
      const params = new URLSearchParams({
        page: String(apiPage),
        page_size: String(limit),
      });
      if (status) params.set("status", status);
      if (taskType) params.set("task_type", taskType);
      if (countryCode) params.set("country_code", countryCode);
      if (search) params.set("search", search);

      const { data, meta } = await apiFetchWithMeta<TaskItem[]>(`/tasks?${params}`);
      const totalCount = meta.pagination?.total ?? data.length;
      const parsedLimit = meta.pagination?.page_size ?? limit;
      const parsedOffset = ((meta.pagination?.page ?? apiPage) - 1) * parsedLimit;

      return {
        items: data,
        totalCount: Number.isFinite(totalCount) ? totalCount : data.length,
        limit: Number.isFinite(parsedLimit) ? parsedLimit : limit,
        offset: Number.isFinite(parsedOffset) ? parsedOffset : offset,
      };
    },
    staleTime: 10 * 1000,
    refetchInterval: 5 * 1000,
  });
}

export function useWorkerStatus() {
  return useQuery<WorkerStatus>({
    queryKey: ["tasks", "worker-status"],
    queryFn: () => apiFetch("/tasks/worker-status"),
    staleTime: 5 * 1000,
    refetchInterval: 5 * 1000,
  });
}

export function useTaskDetail(uuid: string | null) {
  return useQuery<TaskDetail>({
    queryKey: ["task", uuid],
    queryFn: () => apiFetch(`/tasks/${uuid}`),
    enabled: !!uuid,
    staleTime: 5 * 1000,
    refetchInterval: uuid ? 5 * 1000 : false,
  });
}

export function useExecuteTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskUuid: string) =>
      apiFetch(`/tasks/${taskUuid}/retry`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: ["report-runs"] });
    },
  });
}

export function useCancelTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskUuid: string) =>
      apiFetch(`/tasks/${taskUuid}/cancel`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: ["reports"] });
      queryClient.invalidateQueries({ queryKey: ["report-runs"] });
      queryClient.invalidateQueries({ queryKey: ["ai-interactions"] });
      queryClient.invalidateQueries({ queryKey: ["ai-interactions-summary"] });
    },
  });
}

/**
 * Subscribe to cross-process task updates via the control-plane SSE stream.
 * Automatically invalidates the tasks query cache on each message.
 */
interface TaskEventStreamOptions {
  extraQueryKeys?: ReadonlyArray<readonly unknown[]>;
}

const EMPTY_EXTRA_QUERY_KEYS: ReadonlyArray<readonly unknown[]> = [];

export function useTaskEventStream(options: TaskEventStreamOptions = {}) {
  const queryClient = useQueryClient();
  const extraQueryKeys = options.extraQueryKeys ?? EMPTY_EXTRA_QUERY_KEYS;
  const extraQueryKeysSignature = JSON.stringify(extraQueryKeys);

  useEffect(() => {
    const stream = new EventSource(eventStreamUrl());
    const refresh = () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      extraQueryKeys.forEach((queryKey) => {
        queryClient.invalidateQueries({ queryKey: [...queryKey] });
      });
    };

    ["task.created", "task.claimed", "task.started", "task.progress", "task.status", "task.cancel_requested", "task.cancelled", "task.failed", "task.completed"].forEach(
      (eventName) => stream.addEventListener(eventName, refresh),
    );

    return () => {
      stream.close();
    };
  }, [extraQueryKeysSignature, queryClient]);
}
