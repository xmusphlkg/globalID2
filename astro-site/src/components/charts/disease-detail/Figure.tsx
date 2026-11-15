import React, { useEffect, useRef, useState } from 'react';
import ChartResultActions from '../ChartResultActions';
import { pngFromCanvases } from '../chartExport';
import { ui } from './presentation.ts';

// ─── Figure wrapper ───────────────────────────────────────────────────────────

export function Figure({ number, caption, children, theme, lang, actions = true, pngFilename = 'gids-figure.png' }: {
  number: number;
  caption: string;
  children: React.ReactNode;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh' | 'fr';
  actions?: boolean;
  pngFilename?: string;
}) {
  const isLight = theme === 'light';
  const figureRef = useRef<HTMLElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [canFullscreen, setCanFullscreen] = useState(false);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === figureRef.current);
    };

    setCanFullscreen(Boolean(document.fullscreenEnabled));
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  async function toggleFullscreen() {
    const figure = figureRef.current;
    if (!figure || !canFullscreen) return;

    if (document.fullscreenElement === figure) {
      await document.exitFullscreen();
      return;
    }

    if (!document.fullscreenElement) {
      await figure.requestFullscreen();
    }
  }

  return (
    <figure
      ref={figureRef}
      style={{
        background: isLight ? '#ffffff' : 'rgba(15,23,42,0.5)',
        border: `1px solid ${isLight ? '#e2e8f0' : '#1e293b'}`,
      }}
      data-chart-title={caption}
      className={`panel-fullscreen p-4 md:p-6 ${isFullscreen ? 'comparison-shell-fullscreen' : ''}`}
    >
      {actions && (
        <div className="chart-result-actions-fill mb-3">
          <ChartResultActions
            lang={lang}
            shellRef={figureRef}
            fullscreen={{ active: isFullscreen, enabled: canFullscreen, toggle: toggleFullscreen }}
            pngFilename={pngFilename}
            getPng={() => (plotRef.current ? pngFromCanvases(plotRef.current) : null)}
          />
        </div>
      )}
      <div className="mb-3" ref={plotRef}>
        {children}
      </div>
      <figcaption
        style={{ color: isLight ? '#64748b' : '#94a3b8', fontSize: '12px', lineHeight: '1.5', borderTop: `1px solid ${isLight ? '#f1f5f9' : '#1e293b'}` }}
        className="text-center mt-2 pt-3"
      >
        <span style={{ fontWeight: 700 }}>{ui(lang, `Figure ${number}.`, `图 ${number}。`, `Figure ${number}.`)}</span> {caption}
      </figcaption>
    </figure>
  );
}
