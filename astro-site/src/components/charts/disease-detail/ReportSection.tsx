import { marked } from 'marked';
import { type DiseaseSection } from './types.ts';
import { ui } from './presentation.ts';

// ─── Section renderer ─────────────────────────────────────────────────────────

export function localizedText(value: Partial<Record<'zh' | 'en' | 'fr', string>> | undefined, lang: 'en' | 'zh' | 'fr', fallback: string) {
  if (value?.[lang]) return value[lang] as string;
  if (fallback) return fallback;
  return ui(lang, 'Translation pending.', '翻译待补充。', 'Traduction française en attente.');
}

export function markdownHtml(value: string) {
  return marked.parse(value || '', { async: false, breaks: true, gfm: true }) as string;
}

export function SectionBlock({ section, theme, lang }: {
  section: DiseaseSection;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh' | 'fr';
}) {
  const isLight = theme === 'light';
  const borderColor = isLight ? '#e2e8f0' : '#1e293b';
  const accentColor = {
    summary:        isLight ? '#2563eb' : '#60a5fa',
    highlights:     isLight ? '#0d9488' : '#2dd4bf',
    key_findings:   isLight ? '#7c3aed' : '#a78bfa',
    trend_analysis: isLight ? '#0891b2' : '#38bdf8',
  }[section.section_type] ?? (isLight ? '#475569' : '#64748b');

  const labels: Record<string, { en: string; zh: string; fr: string; icon: string }> = {
    summary:        { en: 'Summary',        zh: '摘要',   fr: 'Résumé', icon: '◈' },
    highlights:     { en: 'Highlights',     zh: '要点',   fr: 'Points clés', icon: '◆' },
    key_findings:   { en: 'Key Findings',   zh: '关键发现', fr: 'Conclusions clés', icon: '◉' },
    trend_analysis: { en: 'Trend Analysis', zh: '趋势分析', fr: 'Analyse de tendance', icon: '◎' },
  };
  const meta = labels[section.section_type] ?? { en: section.section_type, zh: section.section_type, fr: section.section_type, icon: '○' };
  const content = localizedText(section.content_i18n, lang, lang === 'fr' ? '' : section.content);
  const contentHtml = section.content_html_i18n?.[lang]
    || (lang === 'fr' ? markdownHtml(content) : section.content_html || markdownHtml(content));

  return (
    <div
      style={{
        borderLeft: `3px solid ${accentColor}`,
        background: isLight ? '#fafbfd' : 'rgba(15,23,42,0.6)',
        borderRadius: 0,
        borderStyle: 'solid',
        borderWidth: '1px',
        borderColor,
        borderLeftWidth: '3px',
        borderLeftColor: accentColor,
      }}
      className="p-6 md:p-8"
    >
      <div className="flex items-center gap-3 mb-4">
        <span style={{ color: accentColor, fontSize: '16px', fontWeight: 700 }}>{meta.icon}</span>
        <h2 style={{ color: accentColor }}
          className="text-xs font-bold uppercase tracking-[0.15em]">
          {meta[lang]}
        </h2>
      </div>
      {contentHtml ? (
        <div
          className="prose prose-sm max-w-none journal-markdown"
          style={{
            color: isLight ? '#334155' : '#cbd5e1',
            lineHeight: '1.8',
            fontSize: '14px',
          }}
          dangerouslySetInnerHTML={{ __html: contentHtml }}
        />
      ) : (
        <p className="text-sm leading-relaxed whitespace-pre-wrap"
          style={{ color: isLight ? '#475569' : '#94a3b8' }}>
          {content}
        </p>
      )}
    </div>
  );
}
