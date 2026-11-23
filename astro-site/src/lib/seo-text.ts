const SEO_DESCRIPTION_LIMIT = 160;
const SEO_TITLE_LIMIT = 70;

export function clampSeoDescription(value: unknown, limit = SEO_DESCRIPTION_LIMIT): string {
  const text = normalizeSeoText(value);
  if (text.length <= limit) return text;
  const boundary = text.lastIndexOf(' ', limit - 1);
  return `${text.slice(0, boundary > 40 ? boundary : limit - 1).trimEnd()}…`;
}

export function clampSeoTitle(value: unknown, limit = SEO_TITLE_LIMIT): string {
  const text = normalizeSeoText(value);
  if (text.length <= limit) return text;
  const punctuationBoundary = Math.max(
    text.lastIndexOf(':', limit - 1),
    text.lastIndexOf('—', limit - 1),
    text.lastIndexOf('-', limit - 1),
  );
  const wordBoundary = text.lastIndexOf(' ', limit - 1);
  const boundary = punctuationBoundary >= 36 ? punctuationBoundary : wordBoundary;
  return `${text.slice(0, boundary >= 36 ? boundary : limit - 1).trimEnd()}…`;
}

/** Shorten the subject while retaining the complete place and site suffix. */
export function clampSeoTitleWithSuffix(subject: unknown, suffix: unknown, limit = SEO_TITLE_LIMIT): string {
  const text = normalizeSeoText(subject);
  const ending = normalizeSeoText(suffix);
  if (!ending) return clampSeoTitle(text, limit);
  const subjectLimit = limit - ending.length - 1;
  if (subjectLimit < 2) throw new RangeError('SEO title suffix leaves no room for its subject');
  return `${clampSeoTitle(text, subjectLimit)} ${ending}`;
}

export function normalizeSeoText(value: unknown): string {
  return typeof value === 'string' ? value.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim() : '';
}
