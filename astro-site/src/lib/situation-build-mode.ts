/** Keep static route enumeration and cache output inventory in the same mode.
 * A standalone data Worker does not itself disable legacy static JSON output.
 */
export function prerenderSituationJson(env: Record<string, string | undefined> = process.env): boolean {
  return env.GIDS_PAGES_DATA_WORKER !== '1';
}
