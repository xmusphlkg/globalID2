import { useEffect, useId, useRef, type ReactNode } from 'react';
import { label } from '../presentation/labels';
import type { Language } from '../domain/types';

interface Props {
  lang: Language;
  from?: string;
  to?: string;
  comparison?: string;
  children: ReactNode;
}

export default function TimeRangePanel({ lang, from, to, comparison, children }: Props) {
  const id = useId();
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 761px)');
    const useDefault = () => { if (ref.current) ref.current.open = desktop.matches; };
    useDefault();
    desktop.addEventListener('change', useDefault);
    return () => desktop.removeEventListener('change', useDefault);
  }, []);
  const year = from?.slice(0, 4);
  const fullYear = from === `${year}-01-01` && to === `${year}-12-31`;
  const annualLabel = { en: 'Full year', zh: '全年', fr: 'Année entière' }[lang];
  return <div className="chart-time-field">
    <strong id={id}>{label('time', lang)}</strong>
    <details ref={ref} className="chart-time-panel">
      <summary aria-labelledby={`${id} ${id}-value`}>
        <span id={`${id}-value`}>{fullYear ? `${year} ${annualLabel}` : <><time>{from || '—'}</time> – <time>{to || '—'}</time></>}{comparison && ` · ${comparison}`}</span>
      </summary>
      {children}
    </details>
  </div>;
}
