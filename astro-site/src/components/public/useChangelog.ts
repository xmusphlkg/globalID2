import { useEffect, useState } from 'react';
import { CHANGELOG_URL, readChangelog, type ChangelogRelease } from '../../lib/changelog';
import { mainSiteBase } from '../../lib/papers';

/** Website records refresh independently of the HTML/template publication. */
export function useChangelog() {
  const [releases, setReleases] = useState<ChangelogRelease[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const update = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15_000);
      try {
        const response = await fetch(`${mainSiteBase()}${CHANGELOG_URL}`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('Website history unavailable');
        const next = readChangelog(await response.json());
        if (active) {
          setReleases(current => JSON.stringify(current) === JSON.stringify(next) ? current : next);
          setStatus('ready');
        }
      } catch {
        if (active) setStatus('error');
      } finally {
        clearTimeout(timeout);
        if (active) timer = setTimeout(update, 60_000);
      }
    };
    void update();
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [revision]);
  return { releases, status, retry: () => setRevision(value => value + 1) };
}
