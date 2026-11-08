export const visibleText = (html) => html
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
  .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ')
  .replace(/<[^>]+>/g, ' ')
  .replaceAll('&amp;', '&')
  .replaceAll('&#39;', "'")
  .replaceAll('&quot;', '"')
  .replace(/\s+/g, ' ')
  .trim();

export const pendingTranslationMarkers = ['Traduction française en attente', 'Translation pending.'];
export function visibleTranslationPlaceholders(html) {
  // Stripping tags inserts spaces; the entity decoder cannot create either
  // initial word. Most translated pages need no full text extraction.
  if (!html.includes('Traduction') && !html.includes('Translation')) return [];
  const text = visibleText(html);
  return pendingTranslationMarkers.filter(marker => text.includes(marker));
}
