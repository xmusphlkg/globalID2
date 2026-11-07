"use client";

import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  PendingEligibility, RealtimeActionResult, RealtimeChannel, RealtimeControlResult,
  RealtimeOverview, RealtimePage, RealtimePending, RealtimeRun,
} from "./types";

const queryRoot = ["realtime-publish"] as const;
const base = "/publish/realtime";
const polling = {
  refetchInterval: 15_000,
  retry: (count: number, error: Error) => !(error instanceof ApiError && error.status < 500) && count < 1,
};

export function useRealtimeOverview() {
  return useQuery({ queryKey: [...queryRoot, "overview"], queryFn: () => apiFetch<RealtimeOverview>(`${base}/overview`), ...polling });
}

export function useRealtimePending(channel: RealtimeChannel, page: number, eligibility: PendingEligibility) {
  const search = new URLSearchParams({ page: String(page), page_size: "20", eligibility });
  return useQuery({
    queryKey: [...queryRoot, channel, "pending", page, eligibility],
    queryFn: () => apiFetch<RealtimePage<RealtimePending>>(`${base}/${channel}/pending?${search}`),
    ...polling,
  });
}

export function useRealtimeRuns(channel: RealtimeChannel, page: number, status: string) {
  const search = new URLSearchParams({ page: String(page), page_size: "20" });
  if (status) search.set("status", status);
  return useQuery({
    queryKey: [...queryRoot, channel, "runs", page, status],
    queryFn: () => apiFetch<RealtimePage<RealtimeRun>>(`${base}/${channel}/runs?${search}`),
    ...polling,
  });
}

export type RealtimeAction =
  | { channel: RealtimeChannel; kind: "publish" | "retry" }
  | { channel: RealtimeChannel; kind: "control"; paused: boolean };

export function useRealtimeAction() {
  const client = useQueryClient();
  const lock = useRef(false);
  const mutation = useMutation<RealtimeActionResult | RealtimeControlResult, Error, RealtimeAction>({
    mutationFn: (action: RealtimeAction) => action.kind === "control"
      ? apiFetch<RealtimeControlResult>(`${base}/${action.channel}/control`, { method: "PATCH", body: JSON.stringify({ paused: action.paused }) })
      : apiFetch<RealtimeActionResult>(`${base}/${action.channel}/${action.kind === "retry" ? "retry" : "runs"}`, { method: "POST", body: JSON.stringify({}) }),
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: queryRoot }),
        client.invalidateQueries({ queryKey: ["tasks"] }),
        client.invalidateQueries({ queryKey: ["/collection/runs"] }),
      ]);
    },
  });
  async function submit(action: RealtimeAction) {
    if (lock.current) return;
    lock.current = true;
    try {
      return await mutation.mutateAsync(action);
    } finally {
      lock.current = false;
    }
  }
  return { ...mutation, submit };
}
