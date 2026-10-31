export interface LogField {
  label: string;
  value: string;
}

const FIELD_LINE = /^([A-Za-z][A-Za-z0-9 /_-]{0,40}):\s+(\S(?:.*\S)?)$/;

/**
 * Field cards are only for short label/value blocks such as crawl configuration.
 * Errors, JSON, stack traces, and command output stay as log text.
 */
export function logFieldRows(content: string | null | undefined): LogField[] {
  if (!content) return [];
  const lines = content
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length < 2 || lines.length > 16) return [];

  const rows: LogField[] = [];
  for (const line of lines) {
    const match = FIELD_LINE.exec(line);
    if (!match) return [];
    const value = match[2].trim();
    if (value.length > 160) return [];
    if (value.includes("://") || value.startsWith("{") || value.startsWith("[")) return [];
    if (value.includes(":")) return [];
    rows.push({ label: match[1].trim(), value });
  }
  return rows;
}

interface LogEntryShape {
  id: number;
  entry_type: string;
  title: string;
  content: string | null;
  success: boolean;
}

export function isAttentionLog(entry: Pick<LogEntryShape, "entry_type" | "success">): boolean {
  return entry.entry_type === "error" || entry.entry_type === "warning" || entry.success === false;
}

export function windowTaskLogs<T extends LogEntryShape>(
  entries: T[],
  recentLimit = 40,
): { pinned: T[]; recent: T[]; earlier: T[] } {
  const pinnedIds = new Set<number>();
  const pinned: T[] = [];
  entries.forEach((entry) => {
    if (!isAttentionLog(entry)) return;
    pinned.push(entry);
    pinnedIds.add(entry.id);
  });
  const routine = entries.filter((entry) => !pinnedIds.has(entry.id));
  if (routine.length <= recentLimit) {
    return { pinned, recent: routine, earlier: [] };
  }
  return {
    pinned,
    recent: routine.slice(-recentLimit),
    earlier: routine.slice(0, routine.length - recentLimit),
  };
}

function logSignature(entry: Pick<LogEntryShape, "title" | "content">): string {
  return `${entry.title}\n${(entry.content ?? "").replace(/\s+/g, " ").trim().slice(0, 120)}`;
}

/** Collapse only adjacent copies of the same log line. Distinct payloads stay visible. */
export function foldConsecutiveEntries<T extends Pick<LogEntryShape, "title" | "content">>(
  entries: T[],
): Array<{ entry: T; repeats: number }> {
  const groups: Array<{ entry: T; repeats: number }> = [];
  entries.forEach((entry) => {
    const previous = groups[groups.length - 1];
    if (previous && logSignature(previous.entry) === logSignature(entry)) {
      previous.repeats += 1;
      previous.entry = entry;
      return;
    }
    groups.push({ entry, repeats: 1 });
  });
  return groups;
}
