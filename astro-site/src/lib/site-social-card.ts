const escapeXml = (value: unknown) => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&apos;');

export function renderDefaultSocialCardSvg(): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-labelledby="title description">
  <title id="title">${escapeXml('GIDS — Global Infectious Disease Surveillance')}</title>
  <desc id="description">${escapeXml('Official-source surveillance data, trends, evidence, and reusable datasets.')}</desc>
  <rect width="1200" height="630" fill="#f4f7f5"/>
  <rect width="24" height="630" fill="#9f2d32"/>
  <circle cx="1025" cy="128" r="238" fill="#e3eeea"/>
  <circle cx="1025" cy="128" r="156" fill="none" stroke="#9dbab3" stroke-width="3"/>
  <path d="M869 128h312M1025-28v312M916 18c82 59 123 135 123 228M1134 18c-82 59-123 135-123 228" fill="none" stroke="#46796f" stroke-width="3" opacity=".72"/>
  <text x="78" y="96" font-family="Arial, sans-serif" font-size="26" font-weight="700" letter-spacing="5" fill="#9f2d32">GIDS</text>
  <text x="78" y="188" font-family="Georgia, serif" font-size="60" font-weight="700" fill="#172521">Global Infectious Disease</text>
  <text x="78" y="258" font-family="Georgia, serif" font-size="60" font-weight="700" fill="#172521">Surveillance</text>
  <text x="78" y="330" font-family="Arial, sans-serif" font-size="25" fill="#405650">Official-source public health data · trends · evidence · downloads</text>
  <text x="78" y="372" font-family="Arial, sans-serif" font-size="23" fill="#5b6d67">官方来源公共卫生数据 · 趋势 · 证据 · 下载</text>
  <g transform="translate(78 438)">
    <rect width="214" height="48" rx="24" fill="#e9f1ef" stroke="#9dbab3"/>
    <text x="107" y="31" text-anchor="middle" font-family="Arial, sans-serif" font-size="19" font-weight="600" fill="#245f54">Surveillance data</text>
    <rect x="230" width="178" height="48" rx="24" fill="#f4e9e9" stroke="#c9989b"/>
    <text x="319" y="31" text-anchor="middle" font-family="Arial, sans-serif" font-size="19" font-weight="600" fill="#8a282d">Research Radar</text>
    <rect x="426" width="194" height="48" rx="24" fill="#eef0e6" stroke="#b7b997"/>
    <text x="523" y="31" text-anchor="middle" font-family="Arial, sans-serif" font-size="19" font-weight="600" fill="#59602f">Reusable datasets</text>
  </g>
  <line x1="78" y1="536" x2="1122" y2="536" stroke="#c7d5d0"/>
  <text x="78" y="584" font-family="Arial, sans-serif" font-size="21" font-weight="600" fill="#172521">globalinfectiousdisease.com</text>
  <text x="1122" y="584" text-anchor="end" font-family="Arial, sans-serif" font-size="18" fill="#5b6d67">Traceable sources · documented limitations</text>
</svg>`;
}
