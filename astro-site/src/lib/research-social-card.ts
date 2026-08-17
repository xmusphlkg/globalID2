export interface ResearchSocialCardInput {
  title: string;
  studyType?: string | null;
  countries?: string[];
  diseases?: string[];
  implication?: string | null;
  doi?: string | null;
}

const escapeXml = (value: unknown) => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&apos;');

export function wrapSocialCardText(value: string, maxCharacters: number, maxLines: number): string[] {
  const words = value.trim().split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length <= maxCharacters || !current) {
      current = candidate;
      continue;
    }
    lines.push(current);
    current = word;
    if (lines.length === maxLines) break;
  }
  if (lines.length < maxLines && current) lines.push(current);
  const consumed = lines.join(' ').length;
  if (lines.length === maxLines && consumed < value.trim().length) {
    lines[maxLines - 1] = `${lines[maxLines - 1].replace(/[.,;:!?]?$/, '')}…`;
  }
  return lines.slice(0, maxLines);
}

export function renderResearchSocialCardSvg(input: ResearchSocialCardInput): string {
  const titleLines = wrapSocialCardText(input.title, 48, 3);
  const implicationLines = wrapSocialCardText(input.implication ?? '', 82, 2);
  const chips = [
    input.studyType,
    ...(input.diseases ?? []).slice(0, 2),
    ...(input.countries ?? []).slice(0, 2),
  ].filter(Boolean).slice(0, 5) as string[];
  let chipX = 72;
  const chipMarkup = chips.map((label) => {
    const width = Math.min(250, Math.max(92, label.length * 9 + 28));
    const markup = `<g transform="translate(${chipX} 446)"><rect width="${width}" height="34" rx="17" fill="#eef5f3" stroke="#9dbab3"/><text x="14" y="22" font-family="Arial, sans-serif" font-size="15" font-weight="600" fill="#245f54">${escapeXml(label)}</text></g>`;
    chipX += width + 12;
    return markup;
  }).join('');

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#f5f7f5"/>
  <rect x="0" y="0" width="22" height="630" fill="#9f2d32"/>
  <circle cx="1080" cy="98" r="154" fill="#e5efec"/>
  <circle cx="1080" cy="98" r="104" fill="none" stroke="#9dbab3" stroke-width="2"/>
  <path d="M1010 98h140M1080 28v140M1031 50c38 28 60 68 60 116M1129 50c-38 28-60 68-60 116" fill="none" stroke="#46796f" stroke-width="2" opacity=".7"/>
  <text x="72" y="80" font-family="Arial, sans-serif" font-size="22" font-weight="700" letter-spacing="3" fill="#9f2d32">GIDS RESEARCH RADAR</text>
  <text x="72" y="118" font-family="Arial, sans-serif" font-size="18" fill="#5d6b67">New infectious-disease evidence · 新近传染病证据</text>
  ${titleLines.map((line, index) => `<text x="72" y="${190 + index * 58}" font-family="Georgia, serif" font-size="46" font-weight="700" fill="#172521">${escapeXml(line)}</text>`).join('')}
  ${implicationLines.map((line, index) => `<text x="72" y="${386 + index * 26}" font-family="Arial, sans-serif" font-size="19" fill="#4a5c57">${escapeXml(line)}</text>`).join('')}
  ${chipMarkup}
  <line x1="72" y1="520" x2="1128" y2="520" stroke="#cbd6d2"/>
  <text x="72" y="563" font-family="Arial, sans-serif" font-size="18" font-weight="600" fill="#172521">${escapeXml(input.doi ? `DOI ${input.doi}` : 'Evidence summary on GIDS')}</text>
  <text x="72" y="594" font-family="Arial, sans-serif" font-size="17" fill="#5d6b67">globalinfectiousdisease.com/research</text>
  <text x="1128" y="582" text-anchor="end" font-family="Georgia, serif" font-size="42" font-weight="700" fill="#9f2d32">GIDS</text>
</svg>`;
}

