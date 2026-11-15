import { useId } from 'react';
import './gids-loading.css';

export interface GIDSLoaderProps {
  language?: 'en' | 'zh' | 'fr';
  label?: string;
  compact?: boolean;
  className?: string;
}

const OUTER_LINES = [
  [120, 24, 120, 53], [120, 24, 158, 54.18], [120, 24, 82, 54.18],
  [168, 36.86, 158, 54.18], [168, 36.86, 178.02, 86.5], [168, 36.86, 120, 53],
  [203.14, 72, 178.02, 86.5], [203.14, 72, 196, 120], [203.14, 72, 158, 54.18],
  [216, 120, 196, 120], [216, 120, 178.02, 153.5], [216, 120, 178.02, 86.5],
  [203.14, 168, 178.02, 153.5], [203.14, 168, 158, 185.82], [203.14, 168, 196, 120],
  [168, 203.14, 158, 185.82], [168, 203.14, 120, 187], [168, 203.14, 178.02, 153.5],
  [120, 216, 120, 187], [120, 216, 82, 185.82], [120, 216, 158, 185.82],
  [72, 203.14, 82, 185.82], [72, 203.14, 61.98, 153.5], [72, 203.14, 120, 187],
  [36.86, 168, 61.98, 153.5], [36.86, 168, 44, 120], [36.86, 168, 82, 185.82],
  [24, 120, 44, 120], [24, 120, 61.98, 86.5], [24, 120, 61.98, 153.5],
  [36.86, 72, 61.98, 86.5], [36.86, 72, 82, 54.18], [36.86, 72, 44, 120],
  [72, 36.86, 82, 54.18], [72, 36.86, 120, 53], [72, 36.86, 61.98, 86.5],
] as const;

const MID_LINES = [
  [120, 74, 120, 53], [120, 74, 158, 54.18], [120, 74, 82, 54.18],
  [159.84, 97, 178.02, 86.5], [159.84, 97, 196, 120], [159.84, 97, 158, 54.18],
  [159.84, 143, 178.02, 153.5], [159.84, 143, 158, 185.82], [159.84, 143, 196, 120],
  [120, 166, 120, 187], [120, 166, 82, 185.82], [120, 166, 158, 185.82],
  [80.16, 143, 61.98, 153.5], [80.16, 143, 44, 120], [80.16, 143, 82, 185.82],
  [80.16, 97, 61.98, 86.5], [80.16, 97, 82, 54.18], [80.16, 97, 44, 120],
] as const;

const OUTER_NODES = [
  [120, 24], [168, 36.86], [203.14, 72], [216, 120], [203.14, 168], [168, 203.14],
  [120, 216], [72, 203.14], [36.86, 168], [24, 120], [36.86, 72], [72, 36.86],
] as const;

const MID_NODES = [
  [120, 53, 4.08], [158, 54.18, 5.86], [178.02, 86.5, 4.08], [196, 120, 5.86],
  [178.02, 153.5, 4.08], [158, 185.82, 5.86], [120, 187, 4.08], [82, 185.82, 5.86],
  [61.98, 153.5, 4.08], [44, 120, 5.86], [61.98, 86.5, 4.08], [82, 54.18, 5.86],
] as const;

const INNER_NODES = [
  [120, 74], [159.84, 97], [159.84, 143], [120, 166], [80.16, 143], [80.16, 97],
] as const;

const CORE_HOLES = [
  [114.5, 113.5, 4.8], [125.5, 114.5, 2.6], [122, 123, 3.4], [112.5, 122.5, 1.9], [128.5, 121, 1.7],
] as const;

function LoaderMark({ gradId, maskId }: { gradId: string; maskId: string }) {
  const stroke = `url(#${gradId})`;
  return <svg className="gids-loading__mark" viewBox="0 0 240 240" fill="none" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id={gradId} x1="75" y1="16" x2="165" y2="224" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stopColor="#2CA0C4" />
        <stop offset="48%" stopColor="#1B7699" />
        <stop offset="82%" stopColor="#0F4968" />
        <stop offset="100%" stopColor="#08283E" />
      </linearGradient>
      <mask id={maskId}>
        <rect width="240" height="240" fill="white" />
        {CORE_HOLES.map(([cx, cy, r]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={r} fill="black" />)}
      </mask>
    </defs>
    <g className="gids-loading__layer-outer" stroke={stroke} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="120" cy="120" r="96" />
      <polygon points="120,24 168,36.86 203.14,72 216,120 203.14,168 168,203.14 120,216 72,203.14 36.86,168 24,120 36.86,72 72,36.86" />
      {OUTER_LINES.map(([x1, y1, x2, y2]) => <line key={`${x1}-${y1}-${x2}-${y2}`} x1={x1} y1={y1} x2={x2} y2={y2} />)}
    </g>
    <g className="gids-loading__layer-mid" stroke={stroke} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="120,53 158,54.18 178.02,86.5 196,120 178.02,153.5 158,185.82 120,187 82,185.82 61.98,153.5 44,120 61.98,86.5 82,54.18" />
      {MID_LINES.map(([x1, y1, x2, y2]) => <line key={`${x1}-${y1}-${x2}-${y2}`} x1={x1} y1={y1} x2={x2} y2={y2} />)}
    </g>
    <g className="gids-loading__layer-inner" stroke={stroke} strokeWidth="2.63" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="120,74 159.84,97 159.84,143 120,166 80.16,143 80.16,97" />
    </g>
    <g className="gids-loading__layer-outer" fill={stroke}>
      {OUTER_NODES.map(([cx, cy]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="4.32" />)}
    </g>
    <g className="gids-loading__layer-mid" fill={stroke}>
      {MID_NODES.map(([cx, cy, r]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={r} />)}
    </g>
    <g className="gids-loading__layer-inner" fill={stroke}>
      {INNER_NODES.map(([cx, cy]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="4.8" />)}
    </g>
    <g className="gids-loading__core" mask={`url(#${maskId})`}>
      {Array.from({ length: 16 }, (_, index) => {
        const major = index % 2 === 0;
        const y = major ? 89.6 : 92.4;
        return <g key={index} transform={`rotate(${index * 22.5} 120 120)`}>
          <line x1="120" y1="103" x2="120" y2={y} stroke={stroke} strokeWidth={major ? 3.2 : 2.5} strokeLinecap="round" />
          <circle cx="120" cy={y} r={major ? 3.2 : 2.55} fill={stroke} />
        </g>;
      })}
      <circle cx="120" cy="120" r="19.5" fill={stroke} />
    </g>
  </svg>;
}

/** Geometric mark with CSS motion only: no timer, image request, or fake progress. */
export default function GIDSLoader({ language = 'en', label, compact = false, className = '' }: GIDSLoaderProps) {
  const message = label ?? (language === 'zh' ? '正在读取公开数据…' : language === 'fr' ? 'Chargement des données publiques…' : 'Loading public data…');
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  return <span className={`gids-loading${compact ? ' gids-loading--compact' : ''}${className ? ` ${className}` : ''}`} role="status" aria-live="polite" aria-busy="true" data-gids-loading>
    <LoaderMark gradId={`gids-loader-grad-${uid}`} maskId={`gids-loader-mask-${uid}`} />
    <span className="gids-loading__copy"><span className="gids-loading__brand" aria-hidden="true">GIDS</span><span>{message}</span></span>
  </span>;
}
