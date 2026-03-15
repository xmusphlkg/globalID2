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

export function useStartAITask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: StartAITaskPayload) =>
      apiFetch("/ai/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}
