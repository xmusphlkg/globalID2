/** Completed tasks have finished even when older servers stored an intermediate value. */
export function displayedTaskProgress(status: string, progress: number): number;
export function displayedTaskProgress(status: string, progress: number | null): number | null;
export function displayedTaskProgress(status: string, progress: number | null): number | null {
  return status.toLowerCase() === "completed" ? 100 : progress;
}

export function normalizeTaskProgress<T extends { status: string; progress: number }>(task: T): T {
  return { ...task, progress: displayedTaskProgress(task.status, task.progress) };
}
