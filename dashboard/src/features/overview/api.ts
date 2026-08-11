import { useQuery } from "@tanstack/react-query";

import { controlPlaneClient } from "@/generated/client";
import type { components } from "@/generated/api";

export type RuntimeService = components["schemas"]["RuntimeServiceOut"];
export type ControlPlaneActionItem = components["schemas"]["ActionItemOut"];
export type ControlPlaneOverview = components["schemas"]["ControlPlaneOverviewOut"];

export function useControlPlaneOverview() {
  return useQuery({
    queryKey: ["control-plane", "overview"],
    queryFn: async () => {
      const { data, error } = await controlPlaneClient.GET("/api/v1/overview");
      if (error) {
        throw new Error("detail" in error ? String(error.detail) : "Unable to load the control-plane overview.");
      }
      return data.data;
    },
    refetchInterval: 15_000,
    staleTime: 5_000,
  });
}
