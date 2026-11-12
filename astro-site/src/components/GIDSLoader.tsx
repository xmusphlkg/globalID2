import './gids-loading.css';

export interface GIDSLoaderProps {
  language?: 'en' | 'zh' | 'fr';
  label?: string;
  compact?: boolean;
  className?: string;
}

/** A CSS-only globe and travelling signal: no timer, image request or fake progress. */
export default function GIDSLoader({ language = 'en', label, compact = false, className = '' }: GIDSLoaderProps) {
  const message = label ?? (language === 'zh' ? '正在读取公开数据…' : language === 'fr' ? 'Chargement des données publiques…' : 'Loading public data…');
  return <span className={`gids-loading${compact ? ' gids-loading--compact' : ''}${className ? ` ${className}` : ''}`} role="status" aria-live="polite" aria-busy="true" data-gids-loading>
    <svg className="gids-loading__globe" width="44" height="44" viewBox="0 0 48 48" fill="none" aria-hidden="true" focusable="false">
      <circle cx="24" cy="24" r="16" className="gids-loading__outline" />
      <ellipse cx="24" cy="24" rx="7" ry="16" className="gids-loading__outline" />
      <path d="M8 24h32M11 15.5c8 4 18 4 26 0M11 32.5c8-4 18-4 26 0" className="gids-loading__outline" />
      <circle cx="24" cy="24" r="21" className="gids-loading__orbit" />
      <g className="gids-loading__signal"><circle cx="24" cy="3" r="3" fill="currentColor" /><circle cx="24" cy="3" r="5" className="gids-loading__halo" /></g>
      <circle cx="14" cy="20" r="2" className="gids-loading__node gids-loading__node--a" />
      <circle cx="30" cy="28" r="2" className="gids-loading__node gids-loading__node--b" />
    </svg>
    <span className="gids-loading__copy"><span className="gids-loading__brand" aria-hidden="true">GIDS</span><span>{message}</span></span>
  </span>;
}
