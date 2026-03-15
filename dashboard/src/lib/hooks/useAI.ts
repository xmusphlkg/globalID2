import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface StartAITaskPayload {
  country_id: number;
  report_type?: "daily" | "weekly" | "monthly" | "special";
  period_start?: string | null;
  period_end?: string | null;
  days?: number;
  enable_review?: boolean;
  send_email?: boolean;
  priority?: "low" | "normal" | "high" | "urgent";
  task_name?: string;
  description?: string;
}

export interface StartAITaskResult {
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

export function useStartAITask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: StartAITaskPayload) =>
      apiFetch("/ai/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }) as Promise<StartAITaskResult>,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}
