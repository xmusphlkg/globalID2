import { useEffect } from 'react';
import type { ChangeKind } from '../../lib/changelog';
import GIDSLoader from '../GIDSLoader';
import { useChangelog } from './useChangelog';
import './changelog.css';

export default function Changelog({ locale }: { locale: 'en' | 'zh' | 'fr' }) {
  const zh = locale === 'zh';
  const fr = locale === 'fr';
  const prefix = locale === 'en' ? '' : `/${locale}`;
  const { releases: changelogReleases, status, retry } = useChangelog();
  const frenchUi: Record<string, string> = {
    Changelog: 'Journal des modifications', 'Changelog | GIDS': 'Journal des modifications | GIDS', 'Product updates': 'Mises à jour du produit', 'What’s new in GIDS': 'Nouveautés de GIDS',
    'Public release history across capabilities, refinements, and fixes.': 'Historique public des fonctionnalités, améliorations et corrections.', 'Changelog summary': 'Résumé des modifications', releases: 'versions', 'documented changes': 'changements documentés', 'first published': 'première publication', 'Release navigation': 'Navigation des versions', 'Current release': 'Version actuelle', 'Get future updates': 'Recevoir les prochaines mises à jour', 'Choose the topics you care about.': 'Choisissez les sujets qui vous intéressent.', 'Release history': 'Historique des versions', Latest: 'Dernière version', 'Jump to a version': 'Accéder à une version', 'Link to this release': 'Lien vers cette version',
    'Follow every GIDS release, including new capabilities, improvements, fixes, and release dates.': 'Suivez chaque version de GIDS : nouveautés, améliorations, corrections et dates de publication.', 'Link to version': 'Lien vers la version',
  };
  const pick = <T,>(en: T, zhValue: T) => {
    if (!fr || typeof en !== 'string') return zh ? zhValue : en;
    const translated = en.startsWith('Link to version ')
      ? `Lien vers la version ${en.slice('Link to version '.length)}`
      : frenchUi[en];
    if (!translated) throw new Error(`Missing French changelog UI translation: ${en}`);
    return translated as T;
  };

  const latestRelease = changelogReleases[0];
  const changeCount = changelogReleases.reduce(
    (total, release) => total + release.sections.reduce((sectionTotal, section) => sectionTotal + section.items.length, 0),
    0,
  );

  const kindMeta: Record<ChangeKind, { marker: string; className: string }> = {
    new: { marker: '+', className: 'changelog-kind-new' },
    improved: { marker: '↑', className: 'changelog-kind-improved' },
    fixed: { marker: '✓', className: 'changelog-kind-fixed' },
  };

  function formatDate(date: string, locale: 'en-US' | 'zh-CN' | 'fr-FR') {
    return new Date(`${date}T00:00:00Z`).toLocaleDateString(locale, {
      year: 'numeric',
      month: locale === 'zh-CN' ? 'long' : 'short',
      day: 'numeric',
      timeZone: 'UTC',
    });
  }

  useEffect(() => {
    const openTargetRelease = () => {
      let id: string;
      try { id = decodeURIComponent(window.location.hash.slice(1)); } catch { return; }
      if (!id) return;
      const release = document.getElementById(id);
      const details = release?.querySelector('details');
      if (details instanceof HTMLDetailsElement) {
        details.open = true;
        release?.scrollIntoView({ block: 'start' });
      }
    };
    openTargetRelease();
    window.addEventListener('hashchange', openTargetRelease);
    return () => window.removeEventListener('hashchange', openTargetRelease);
  }, [changelogReleases]);
  return (
    <div className="changelog-page">
      <section className="changelog-hero">
        <div className="changelog-shell changelog-hero-inner">
          <div className="changelog-eyebrow">
            <span className="changelog-eyebrow-dot" aria-hidden="true"></span>
            <span>{pick('Product updates', '产品更新')}</span>
          </div>
          <h1>{pick('What’s new in GIDS', 'GIDS 版本更新')}</h1>
          <p className="changelog-hero-copy">{pick('Public release history across capabilities, refinements, and fixes.', '公开版本的新增能力、体验优化与问题修复。')}</p>
          <div className="changelog-summary" aria-label={pick('Changelog summary', '更新记录摘要')}>
            <div>
              <strong>{status === 'loading' ? '—' : changelogReleases.length}</strong>
              <span>{pick('releases', '个版本')}</span>
            </div>
            <span className="changelog-summary-divider" aria-hidden="true"></span>
            <div>
              <strong>{status === 'loading' ? '—' : changeCount}</strong>
              <span>{pick('documented changes', '项更新记录')}</span>
            </div>
            <span className="changelog-summary-divider" aria-hidden="true"></span>
            <div>
              <strong>2026</strong>
              <span>{pick('first published', '首次发布')}</span>
            </div>
          </div>
        </div>
      </section>

      <div className="changelog-shell changelog-layout">
        <aside className="changelog-sidebar" aria-label={pick('Release navigation', '版本导航')}>
          {latestRelease && <div className="changelog-latest-card">
            <span className="changelog-card-kicker">{pick('Current release', '当前版本')}</span>
            <strong>v{latestRelease.version}</strong>
            <time dateTime={latestRelease.date}>
              <span>{formatDate(latestRelease.date, zh ? 'zh-CN' : fr ? 'fr-FR' : 'en-US')}</span>
            </time>
          </div>}

          <nav className="changelog-version-nav" aria-label={pick('Jump to a version', '跳转至版本')}>
            <p>{pick('Jump to a version', '跳转至版本')}</p>
            <div>{changelogReleases.map(release => <a key={release.version} href={`#v${release.version}`}>v{release.version}</a>)}</div>
          </nav>

          <a className="changelog-subscribe-link" href={`${prefix}/subscribe/`}>
            <span>
              <strong>{pick('Get future updates', '订阅后续更新')}</strong>
              <small>{pick('Choose the topics you care about.', '只接收你关注的主题。')}</small>
            </span>
            <span aria-hidden="true">→</span>
          </a>
        </aside>

        <section className="changelog-timeline" aria-label={pick('Release history', '版本历史')}>
          {status === 'loading' && <GIDSLoader language={locale} compact />}
          {status === 'error' && <p role="status">{zh ? '版本记录暂不可用，已读取的记录仍可查看。' : fr ? 'Historique temporairement indisponible. Les versions déjà chargées restent visibles.' : 'Release history is temporarily unavailable. Previously loaded versions remain visible.'} <button className="focus-ring" type="button" onClick={retry}>{zh ? '重试' : fr ? 'Réessayer' : 'Retry'}</button></p>}
          {changelogReleases.map((release, index) => (
            <article key={release.version} className="changelog-release" id={`v${release.version}`}>
              <div className="changelog-rail" aria-hidden="true">
                <span className={`changelog-node ${index === 0 ? 'changelog-node-latest' : ''}`}></span>
              </div>

              <details className="changelog-release-body" open={index === 0}>
                <summary className="changelog-release-summary-row">
                <span><strong>v{release.version}</strong><span>{fr ? release.titleFr : pick(release.titleEn, release.titleZh)}</span></span>
                  <time dateTime={release.date}><span>{formatDate(release.date, zh ? 'zh-CN' : fr ? 'fr-FR' : 'en-US')}</span></time>
                </summary>
                <header className="changelog-release-header">
                  <div className="changelog-release-version-row">
                    <span className="changelog-version">v{release.version}</span>
                    {index === 0 && <span className="changelog-latest-pill">{pick('Latest', '最新版本')}</span>}
                  </div>
                  <time dateTime={release.date}>
                    <span>{formatDate(release.date, zh ? 'zh-CN' : fr ? 'fr-FR' : 'en-US')}</span>
                  </time>
                </header>

                <h2>{fr ? release.titleFr : pick(release.titleEn, release.titleZh)}</h2>
                <p className="changelog-release-summary">{fr ? release.summaryFr : pick(release.summaryEn, release.summaryZh)}</p>

                <div className="changelog-change-groups">
                  {release.sections.map((section) => {
                    const meta = kindMeta[section.kind];
                    return (
                      <section key={section.kind} className="changelog-change-group">
                        <div className={`changelog-kind ${meta.className}`}>
                          <span className="changelog-kind-marker" aria-hidden="true">{meta.marker}</span>
                          <h3>{fr ? section.labelFr : pick(section.labelEn, section.labelZh)}</h3>
                        </div>
                        <ul>
                          {section.items.map((item, itemIndex) => (
                            <li key={itemIndex}>{fr ? item.fr : pick(item.en, item.zh)}</li>
                          ))}
                        </ul>
                      </section>
                    );
                  })}
                </div>

                <a className="changelog-release-anchor" href={`#v${release.version}`} aria-label={pick(`Link to version ${release.version}`, `链接到版本 ${release.version}`)}>
                  <span aria-hidden="true">#</span>
                  <span>{pick('Link to this release', '链接到此版本')}</span>
                </a>
              </details>
            </article>
          ))}
        </section>
      </div>
    </div>
  );
}
