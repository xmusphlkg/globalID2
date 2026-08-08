export function resolveFlagIso2(code: string | null | undefined): string {
  const normalized = (code ?? '').trim().toUpperCase();
  if (normalized === 'UK') return 'GB';
  if (normalized === 'TW' || normalized === 'HK') return 'CN';

  const subdivision = /^([A-Z]{2})-[A-Z0-9]{1,3}$/.exec(normalized);
  if (subdivision) return subdivision[1];

  return /^[A-Z]{2}$/.test(normalized) ? normalized : 'UN';
}

export function toFlagEmoji(countryCode: string): string {
  if (!/^[A-Z]{2}$/.test(countryCode)) return '🌐';
  return String.fromCodePoint(
    ...Array.from(countryCode).map((char) => 127397 + char.charCodeAt(0)),
  );
}
