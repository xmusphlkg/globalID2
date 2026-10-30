/** Preserve every decoded query value, including repetitions and empty values.
 * URLSearchParams re-encodes once so reserved characters keep their meaning. */
export function legacyDestination(pathname: string, values: Record<string, string | string[] | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    for (const part of Array.isArray(value) ? value : value !== undefined ? [value] : []) params.append(key, part);
  }
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}
