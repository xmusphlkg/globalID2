import './gids-loading.css';

export interface GIDSLoaderProps {
  language?: 'en' | 'zh' | 'fr';
  label?: string;
  compact?: boolean;
  className?: string;
}

export default function GIDSLoader({ language = 'en', label, compact = false, className = '' }: GIDSLoaderProps) {
  const message = label ?? (language === 'zh' ? '正在读取公开数据…' : language === 'fr' ? 'Chargement des données publiques…' : 'Loading public data…');
  return <span className={`gids-loading${compact ? ' gids-loading--compact' : ''}${className ? ` ${className}` : ''}`} role="status" aria-live="polite" aria-busy="true" data-gids-loading>
    <img className="gids-loading__mark" src="/brand/gids-loader.svg" alt="" width={compact ? 40 : 160} height={compact ? 40 : 160} />
    <span className="gids-loading__copy"><span className="gids-loading__brand" aria-hidden="true">GIDS</span><span>{message}</span></span>
  </span>;
}
