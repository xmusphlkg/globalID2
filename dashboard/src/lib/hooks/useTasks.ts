import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, wsUrl } from "@/lib/api";

export interface TaskItem {
  id: number;
  task_uuid: string;
  task_name: string;
  task_type: string;
  status: string;
  priority: string;
  progress: number;
  country_id: number | null;
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

export function useTasks(
  status?: string,
  taskType?: string,
  countryId?: number | null,
  search?: string,
  limit = 50,
) {
  return useQuery<TaskItem[]>({
    queryKey: ["tasks", status, taskType, countryId, search, limit],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit) });
      if (status) params.set("status", status);
      if (taskType) params.set("task_type", taskType);
      if (countryId) params.set("country_id", String(countryId));
      if (search) params.set("search", search);
      return apiFetch(`/tasks?${params}`);
    },
    staleTime: 10 * 1000,
  });
}

export function useTaskDetail(uuid: string | null) {
  return useQuery<TaskDetail>({
    queryKey: ["task", uuid],
    queryFn: () => apiFetch(`/tasks/${uuid}`),
    enabled: !!uuid,
    staleTime: 5 * 1000,
  });
}

export function useExecuteTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (taskUuid: string) =>
      apiFetch(`/tasks/${taskUuid}/execute`, { method: "POST" }),
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
 * Subscribe to real-time task updates via WebSocket.
 * Automatically invalidates the tasks query cache on each message.
 */
interface TaskWebSocketOptions {
  extraQueryKeys?: ReadonlyArray<readonly unknown[]>;
  pingIntervalMs?: number;
}

export function useTaskWebSocket(options: TaskWebSocketOptions = {}) {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const pingTimerRef = useRef<number | null>(null);
  const { extraQueryKeys = [], pingIntervalMs = 15000 } = options;

  useEffect(() => {
    let disposed = false;

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const clearPingTimer = () => {
      if (pingTimerRef.current !== null) {
        window.clearInterval(pingTimerRef.current);
        pingTimerRef.current = null;
      }
    };

    const connect = () => {
      const ws = new WebSocket(wsUrl("/tasks/ws"));
      wsRef.current = ws;

      ws.onopen = () => {
        if (disposed) {
          ws.close();
          return;
        }

        clearPingTimer();
        pingTimerRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send("ping");
          }
        }, pingIntervalMs);
      };

      ws.onmessage = () => {
        // Invalidate so TanStack Query refetches.
        queryClient.invalidateQueries({ queryKey: ["tasks"] });
        queryClient.invalidateQueries({ queryKey: ["task"] });
        extraQueryKeys.forEach((queryKey) => {
          queryClient.invalidateQueries({ queryKey: [...queryKey] });
        });
      };

      ws.onclose = () => {
        clearPingTimer();
        if (wsRef.current === ws) {
          wsRef.current = null;
        }

        if (disposed) {
          return;
        }

        clearReconnectTimer();
        reconnectTimerRef.current = window.setTimeout(() => {
          reconnectTimerRef.current = null;
          if (!disposed) {
            connect();
          }
        }, 3000);
      };
    };

    connect();

    return () => {
      disposed = true;
      clearReconnectTimer();
      clearPingTimer();

      const ws = wsRef.current;
      wsRef.current = null;

      if (!ws) {
        return;
      }

      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
        return;
      }

      if (ws.readyState === WebSocket.CONNECTING) {
        ws.addEventListener(
          "open",
          () => {
            ws.close();
          },
          { once: true },
        );
      }
    };
  }, [extraQueryKeys, pingIntervalMs, queryClient]);
}
