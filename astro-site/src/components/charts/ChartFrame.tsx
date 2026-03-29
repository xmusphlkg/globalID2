import React, { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ChartSourceMeta } from '../../utils/chartMeta';

type RenderablePanel = ReactNode | ((state: { isFullscreen: boolean }) => ReactNode);

interface Props {
  lang: 'en' | 'zh';
  toolbar?: ReactNode;
  note?: ReactNode;
  chart: RenderablePanel;
  table: ReactNode;
  legend?: ReactNode;
  fullscreenSidebar?: ReactNode;
  sourceMeta?: ChartSourceMeta | null;
}

export default function ChartFrame({
  lang,
  toolbar,
  note,
  chart,
  table,
  legend,
  fullscreenSidebar,
  sourceMeta = null,
}: Props) {
  const [mode, setMode] = useState<'chart' | 'table'>('chart');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === shellRef.current);
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  async function toggleFullscreen() {
    const shell = shellRef.current;
    if (!shell) return;

    if (document.fullscreenElement === shell) {
      await document.exitFullscreen();
      return;
    }

    if (!document.fullscreenElement) {
      await shell.requestFullscreen();
    }
  }

  const activeAside = isFullscreen && fullscreenSidebar ? fullscreenSidebar : legend;
  const stageClassName = `chart-stage ${isFullscreen && fullscreenSidebar ? 'chart-stage-with-wide-aside' : ''}`;
  const asideClassName = `chart-aside ${isFullscreen && fullscreenSidebar ? 'chart-aside-wide' : ''}`;
  const renderedChart = typeof chart === 'function' ? chart({ isFullscreen }) : chart;
  const renderedSources = sourceMeta?.sources?.length
    ? sourceMeta.sources
    : sourceMeta?.label
      ? [{ label: sourceMeta.label, href: sourceMeta.href }]
      : [];

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
          <button type="button" onClick={toggleFullscreen} className="chart-link-btn">
            {isFullscreen
              ? (lang === 'zh' ? '退出全屏' : 'Exit full-screen')
              : (lang === 'zh' ? '进入全屏' : 'Enter full-screen')}
          </button>
        </div>
      </div>

      {note && <div className="chart-note">{note}</div>}

      {mode === 'chart' ? (
        <div className={stageClassName}>
          <div className="chart-canvas">{renderedChart}</div>
          {activeAside && <aside className={asideClassName}>{activeAside}</aside>}
        </div>
      ) : (
        <div className="data-preview-wrap">{table}</div>
      )}

      {sourceMeta && (
        <div className="chart-source-block">
          <div className="chart-source-row">
            <span className="chart-source-label">{lang === 'zh' ? '数据来源' : 'Data source'}</span>
            <span>
              {renderedSources.map((source, index) => (
                <React.Fragment key={`${source.label}-${source.href ?? index}`}>
                  {index > 0 && '; '}
                  {source.href ? (
                    <a href={source.href} target="_blank" rel="noreferrer" className="chart-source-link">
                      {source.label}
                    </a>
                  ) : (
                    <span>{source.label}</span>
                  )}
                </React.Fragment>
              ))}
            </span>
          </div>
          {sourceMeta.coverage && (
            <div className="chart-source-row">
              <span className="chart-source-label">{lang === 'zh' ? '时间范围' : 'Coverage'}</span>
              <span>{sourceMeta.coverage}</span>
            </div>
          )}
          {sourceMeta.updatedAt && (
            <div className="chart-source-row">
              <span className="chart-source-label">{lang === 'zh' ? '更新时间' : 'Updated'}</span>
              <span>{sourceMeta.updatedAt}</span>
            </div>
          )}
          {sourceMeta.rowCount != null && (
            <div className="chart-source-row">
              <span className="chart-source-label">{lang === 'zh' ? '记录数' : 'Rows'}</span>
              <span>{sourceMeta.rowCount.toLocaleString()}</span>
            </div>
          )}
          {sourceMeta.note && (
            <div className="chart-source-row">
              <span className="chart-source-label">{lang === 'zh' ? '说明' : 'Note'}</span>
              <span>{sourceMeta.note}</span>
            </div>
          )}
          {sourceMeta.downloadHref && (
            <div className="chart-source-actions">
              <a href={sourceMeta.downloadHref} target="_blank" rel="noreferrer" className="chart-link-btn">
                {lang === 'zh' ? '下载底层数据' : 'Download data'}
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
