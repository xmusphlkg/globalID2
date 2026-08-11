import { Suspense } from "react";
import SchedulesView from "@/features/operations/schedules/view";

export default function Page() {
  return <Suspense fallback={<div className="p-8 text-sm text-[#6B7280]">Loading schedules…</div>}><SchedulesView /></Suspense>;
}
