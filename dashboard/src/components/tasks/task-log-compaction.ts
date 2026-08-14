import type { WorkbookEntry } from "@/lib/hooks/useTasks";

const OUTPUT_TITLE = /^(.*) Output #(\d+)$/;
const TERMINAL_SUFFIXES = ["Completed", "Timed Out", "Failed", "Cancelled"] as const;

interface OutputGroup {
  entries: WorkbookEntry[];
  terminal: boolean;
}

export interface CompactedTaskLogEntries {
  entries: WorkbookEntry[];
  hiddenCount: number;
}

/**
 * Fold repetitive command-output chunks while retaining lifecycle entries and
 * the most useful diagnostic output for a command that is still running.
 */
export function compactTaskLogEntries(
  entries: WorkbookEntry[],
): CompactedTaskLogEntries {
  const outputBases = new Set<string>();
  entries.forEach((entry) => {
    const match = OUTPUT_TITLE.exec(entry.title);
    if (match) outputBases.add(match[1]);
  });

  if (outputBases.size === 0) {
    return { entries, hiddenCount: 0 };
  }

  const lifecycle = new Map<string, { base: string; kind: "start" | "terminal" }>();
  outputBases.forEach((base) => {
    lifecycle.set(`${base} Started`, { base, kind: "start" });
    TERMINAL_SUFFIXES.forEach((suffix) => {
      lifecycle.set(`${base} ${suffix}`, { base, kind: "terminal" });
    });
  });

  const generations = new Map<string, number>();
  const entryGroups = new Map<WorkbookEntry, string>();
  const groups = new Map<string, OutputGroup>();

  entries.forEach((entry) => {
    const lifecycleEvent = lifecycle.get(entry.title);
    if (lifecycleEvent?.kind === "start") {
      generations.set(
        lifecycleEvent.base,
        (generations.get(lifecycleEvent.base) ?? 0) + 1,
      );
      return;
    }

    const outputMatch = OUTPUT_TITLE.exec(entry.title);
    if (outputMatch) {
      const base = outputMatch[1];
      const groupKey = `${base}\u0000${generations.get(base) ?? 0}`;
      const group = groups.get(groupKey) ?? { entries: [], terminal: false };
      group.entries.push(entry);
      groups.set(groupKey, group);
      entryGroups.set(entry, groupKey);
      return;
    }

    if (lifecycleEvent?.kind === "terminal") {
      const groupKey = `${lifecycleEvent.base}\u0000${generations.get(lifecycleEvent.base) ?? 0}`;
      const group = groups.get(groupKey) ?? { entries: [], terminal: false };
      group.terminal = true;
      groups.set(groupKey, group);
    }
  });

  const visibleOutputEntries = new Set<WorkbookEntry>();
  groups.forEach((group) => {
    if (group.entries.length <= 1) {
      group.entries.forEach((entry) => visibleOutputEntries.add(entry));
      return;
    }
    if (!group.terminal) {
      visibleOutputEntries.add(group.entries[group.entries.length - 1]);
    }
  });

  const compactedEntries = entries.filter((entry) => {
    if (!entryGroups.has(entry)) return true;
    return visibleOutputEntries.has(entry);
  });
  return {
    entries: compactedEntries,
    hiddenCount: entries.length - compactedEntries.length,
  };
}
