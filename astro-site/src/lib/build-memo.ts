/** Cache derived indexes only inside one immutable static build. Dev requests
 * and direct callers keep observing input changes; failed computations retry. */
export function memoizeForBuild<T>(create: () => T, enabled = () => process.env.GIDS_STATIC_RENDER === '1'): () => T {
  let ready = false;
  let value: T;
  return () => {
    if (!enabled()) { ready = false; return create(); }
    if (!ready) { value = create(); ready = true; }
    return value;
  };
}
