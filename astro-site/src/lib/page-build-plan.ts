import { readFileSync } from 'node:fs';

let plan: { selected: Set<string>; known: Set<string> } | undefined;

/** An untracked route renders normally; only verified reusable bundles skip rendering. */
export function shouldBuildPage(key: string): boolean {
  const path = process.env.GIDS_PAGE_BUILD_PLAN;
  if (!path) return true;
  if (!plan) {
    const value = JSON.parse(readFileSync(path, 'utf8'));
    if (!Array.isArray(value.selected) || !Array.isArray(value.known)) {
      throw new Error('Invalid page build plan');
    }
    plan = { selected: new Set(value.selected), known: new Set(value.known) };
  }
  return !plan.known.has(key) || plan.selected.has(key);
}
