"use client";

import { useEffect, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { eventStreamUrl } from "@/lib/api";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60 * 1000,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

function getQueryClient() {
  if (typeof window === "undefined") return makeQueryClient();
  if (!browserQueryClient) browserQueryClient = makeQueryClient();
  return browserQueryClient;
}

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(getQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <ControlPlaneEventBridge />
      {children}
    </QueryClientProvider>
  );
}

function ControlPlaneEventBridge() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const source = new EventSource(eventStreamUrl());
    const refresh = () => {
      queryClient.invalidateQueries({ queryKey: ["control-plane"] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["worker-status"] });
    };
    for (const type of ["task.status", "task.claimed", "task.cancel_requested", "runtime.started", "runtime.stopped"]) {
      source.addEventListener(type, refresh);
    }
    return () => source.close();
  }, [queryClient]);

  return null;
}
