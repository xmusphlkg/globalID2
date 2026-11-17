import { useCallback, useLayoutEffect, useRef } from 'react';
import type { UIEvent } from 'react';

/** Preserve panel position while mobile switches between filters and results. */
export function useSituationPanelScroll<T extends HTMLElement>(visible: boolean, contextKey: string, resetOnContextChange = false) {
  const ref = useRef<T | null>(null);
  const positions = useRef(new Map<string, number>());
  const previousContext = useRef(contextKey);
  const onScroll = useCallback((event: UIEvent<T>) => {
    if (visible) positions.current.set(contextKey, event.currentTarget.scrollTop);
  }, [visible, contextKey]);

  useLayoutEffect(() => {
    if (previousContext.current !== contextKey && resetOnContextChange) positions.current.delete(contextKey);
    previousContext.current = contextKey;
    if (!visible || !ref.current) return;
    const restore = () => { if (ref.current) ref.current.scrollTop = positions.current.get(contextKey) ?? 0; };
    restore();
    const frame = requestAnimationFrame(restore);
    return () => cancelAnimationFrame(frame);
  }, [visible, contextKey, resetOnContextChange]);

  return { ref, onScroll };
}
