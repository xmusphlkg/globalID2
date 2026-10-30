import { taskScopeForType } from "@/features/operations/task-scope";
import { legacyDestination } from "./legacy-query";
type Values = Record<string,string|string[]|undefined>;
export function legacyTaskDestination(values: Values) {
  const tasks = Array.isArray(values.task) ? values.task : [values.task];
  if (tasks.some(Boolean)) return legacyDestination("/collection/runs",values);
  const requested = (Array.isArray(values.task_type) ? values.task_type : [values.task_type]).flatMap(value => (value ?? "").split(",")).map(value => value.trim().toLowerCase()).filter(Boolean);
  const scopes = new Set(requested.map(taskScopeForType));
  const scope = scopes.size === 1 ? [...scopes][0] : null;
  const kinds = Array.isArray(values.kind) ? values.kind : [values.kind];
  const path = scope === "publish" || (!requested.length && kinds.includes("release")) ? "/publish/tasks" : scope === "ai" ? "/models/tasks" : "/collection/runs";
  return legacyDestination(path,values);
}
export function legacyScheduleDestination(values: Values) {
  const kind = Array.isArray(values.kind) ? values.kind[0] : values.kind;
  return legacyDestination(kind === "release" ? "/publish/schedules" : "/collection/schedules",values);
}
