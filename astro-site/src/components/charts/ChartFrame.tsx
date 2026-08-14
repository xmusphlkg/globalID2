import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { ChartSourceMeta } from '../../utils/chartMeta';

type RenderablePanel = ReactNode | ((state: { isFullscreen: boolean }) => ReactNode);

interface Props {
  lang: 'en' | 'zh';
  toolbar?: ReactNode;
  chart: RenderablePanel;
  table: RenderablePanel;
  legend?: ReactNode;
  notes?: ReactNode;
  sidebar?: ReactNode;
  fullscreenSidebar?: ReactNode;
  stageHeight?: number;
  sourceMeta?: ChartSourceMeta | null;
}

export default function ChartFrame({
  lang,
  toolbar,
  chart,
  table,
  legend,
  notes,
  sidebar,
  fullscreenSidebar,
  stageHeight,
  sourceMeta = null,
}: Props) {
  const [mode, setMode] = useState<'chart' | 'table'>('chart');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [canFullscreen, setCanFullscreen] = useState(false);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === shellRef.current);
    };
    const handleFullscreenError = () => {
      setIsFullscreen(false);
      setCanFullscreen(false);
    };

    setCanFullscreen(Boolean(document.fullscreenEnabled));
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    document.addEventListener('fullscreenerror', handleFullscreenError);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      document.removeEventListener('fullscreenerror', handleFullscreenError);
    };
  }, []);

  async function toggleFullscreen() {
    const shell = shellRef.current;
    if (!shell || !canFullscreen) return;

    try {
      if (document.fullscreenElement === shell) {
        await document.exitFullscreen();
        return;
      }

      if (!document.fullscreenElement) {
        await shell.requestFullscreen();
      }
    } catch {
      setCanFullscreen(false);
    }
  }

  const interactiveAside = isFullscreen
    ? (fullscreenSidebar ?? sidebar)
    : sidebar;
  const activeAside = interactiveAside ?? legend;
  const hasAside = Boolean(activeAside);
  const hasWideAside = Boolean(interactiveAside);
  const stageClassName = [
    'chart-stage',
    hasAside ? 'chart-stage-with-aside' : '',
    hasWideAside ? 'chart-stage-with-wide-aside' : '',
  ].filter(Boolean).join(' ');
  const asideClassName = `chart-aside ${hasWideAside ? 'chart-aside-wide' : ''}`;
  const stageStyle = stageHeight
    ? ({ '--chart-stage-height': `${stageHeight}px` } as CSSProperties)
    : undefined;
  const renderPanel = (panel: RenderablePanel) => (
    typeof panel === 'function' ? panel({ isFullscreen }) : panel
  );
  const fullscreenLabel = isFullscreen
    ? (lang === 'zh' ? '退出全屏' : 'Exit full-screen')
    : (lang === 'zh' ? '进入全屏' : 'Enter full-screen');

  return (
    <div ref={shellRef} className={`chart-shell panel-fullscreen ${isFullscreen ? 'chart-shell-fullscreen' : ''}`}>
      <div className="chart-frame-toolbar">
        <div className="chart-frame-toolbar-left">
          <div className="chart-view-toggle" role="tablist" aria-label={lang === 'zh' ? '切换图表视图' : 'Switch chart view'}>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'table'}
              onClick={() => setMode('table')}
              className={`chart-view-btn ${mode === 'table' ? 'chart-view-btn-active' : ''}`}
            >
              {lang === 'zh' ? '表格' : 'Table'}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'chart'}
              onClick={() => setMode('chart')}
              className={`chart-view-btn ${mode === 'chart' ? 'chart-view-btn-active' : ''}`}
            >
              {lang === 'zh' ? '图表' : 'Chart'}
            </button>
          </div>
          {toolbar && <div className="chart-frame-toolbar-main">{toolbar}</div>}
        </div>
        <div className="chart-frame-actions">
          {canFullscreen && (
            <button
              type="button"
              onClick={toggleFullscreen}
              className="chart-link-btn chart-fullscreen-btn"
              aria-label={fullscreenLabel}
              aria-pressed={isFullscreen}
              title={fullscreenLabel}
            >
              {isFullscreen ? (
                <svg viewBox="0 0 20 20" aria-hidden="true">
                  <path d="M7.25 3.5v3.75H3.5M12.75 3.5v3.75h3.75M7.25 16.5v-3.75H3.5M12.75 16.5v-3.75h3.75" />
                </svg>
              ) : (
                <svg viewBox="0 0 20 20" aria-hidden="true">
                  <path d="M7.25 3.5H3.5v3.75M12.75 3.5h3.75v3.75M7.25 16.5H3.5v-3.75M12.75 16.5h3.75v-3.75" />
                </svg>
              )}
              <span>{fullscreenLabel}</span>
            </button>
          )}
        </div>
      </div>

      {mode === 'chart' ? (
        <div className={stageClassName} style={stageStyle}>
          <div className="chart-canvas">{renderPanel(chart)}</div>
          {activeAside && <aside className={asideClassName}>{activeAside}</aside>}
        </div>
      ) : (
        <div className="data-preview-wrap">{renderPanel(table)}</div>
      )}

      {notes && (
        <div className="chart-footer-info">
          <div className="chart-note-panel">{notes}</div>
        </div>
      )}
    </div>
  );
}
