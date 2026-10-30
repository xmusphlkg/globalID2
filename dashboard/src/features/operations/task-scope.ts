/** Collection contains source collection only; knowledge brief generation belongs to AI. */
export const taskTypesByScope = {
  collection: ["crawl_data", "sync_literature", "refresh_situation_sources", "refresh_disease_knowledge_sources"],
  publish: ["export_data"],
  ai: ["update_disease_knowledge", "generate_report", "generate_section", "review_section", "agent_workflow", "enrich_literature", "discover_literature_gaps"],
} as const;
export type TaskScope = keyof typeof taskTypesByScope;
export type ScheduleScope = "collection" | "publish";
export const scheduleKindsByScope = { collection: ["ingestion", "literature", "situation"], publish: ["release"] } as const;
export function taskBelongsToScope(type: string, scope: TaskScope): boolean {
  return (taskTypesByScope[scope] as readonly string[]).includes(type);
}
/** Return a mandatory server-side filter; unknown or cross-scope URL input never removes it. */
export function scopedTaskTypes(scope: TaskScope, requested?: string | null): string[] {
  const allowed = taskTypesByScope[scope] as readonly string[];
  const selected = [...new Set((requested ?? "").split(",").map((value) => value.trim().toLowerCase()))].filter((value) => allowed.includes(value));
  return selected.length ? selected : [...allowed];
}
export function scopedScheduleKinds(scope: ScheduleScope, requested?: string | null): string[] {
  const allowed = scheduleKindsByScope[scope] as readonly string[];
  return requested && allowed.includes(requested) ? [requested] : [...allowed];
}
export function taskScopeForType(type: string): TaskScope | null {
  return (Object.keys(taskTypesByScope) as TaskScope[]).find((scope) => taskBelongsToScope(type, scope)) ?? null;
}
export function scopedTaskHref(taskUuid: string, scope: TaskScope): string {
  const encoded = encodeURIComponent(taskUuid);
  return scope === "collection" ? `/collection/runs/${encoded}` : `/${scope === "publish" ? "publish" : "models"}/tasks?detail=${encoded}`;
}
export const COLLECTION_TASK_TYPES = taskTypesByScope.collection;
export const AI_TASK_TYPES = taskTypesByScope.ai;
export const PUBLISH_TASK_TYPES = taskTypesByScope.publish;
export function taskScopeHref(task: { task_uuid: string; task_type: string }): string {
  const scope = taskScopeForType(task.task_type);
  return scope ? scopedTaskHref(task.task_uuid, scope) : `/models/tasks?detail=${encodeURIComponent(task.task_uuid)}&historical=1`;
}
