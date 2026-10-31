import { permanentRedirect } from "next/navigation";
import AllTasksView from "@/features/operations/tasks/all/view";
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  const task = Array.isArray(query.task) ? query.task[0] : query.task;
  if (task) { const params = new URLSearchParams(); for (const [key, value] of Object.entries(query)) { for (const part of Array.isArray(value) ? value : value !== undefined ? [value] : []) params.append(key, part); } permanentRedirect(`/collection/runs/${encodeURIComponent(task)}?${params}`); }
  return <AllTasksView />;
}
