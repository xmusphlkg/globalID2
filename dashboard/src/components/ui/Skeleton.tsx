import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <span className={cn("block animate-pulse rounded bg-[#E5E5E2]", className)} aria-hidden="true" />;
}
