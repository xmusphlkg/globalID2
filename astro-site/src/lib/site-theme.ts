/** Templates whose layouts consume the independent v1 theme tokens. */
export function isThemePilotPage(pathname: string): boolean {
  return /^(?:\/(?:zh|fr))?\/$/.test(pathname)
    || /^(?:\/(?:zh|fr))?\/compare\/$/.test(pathname)
    || /^(?:\/(?:zh|fr))?\/countries\/[^/]+\/$/.test(pathname);
}

export function themeStylesheetHref(pathname: string, configured?: string): string | null {
  if (!isThemePilotPage(pathname) || !configured?.trim()) return null;
  const value = configured.trim();
  const local = value.startsWith('/') && !value.startsWith('//');
  let url: URL;
  try { url = new URL(value, 'https://theme-preview.invalid'); }
  catch { throw new Error('Invalid PUBLIC_GIDS_THEME_V1_URL'); }
  if ((!local && (!value.startsWith('https://') || url.protocol !== 'https:')) || url.username || url.password || url.search || url.hash
    || !/\/theme\/(?:compat\/v1\/theme|versions\/sha256\/[a-f0-9]{64})\.css$/.test(url.pathname)
    || /[\\\s]/.test(value)) {
    throw new Error('Theme v1 requires a fixed HTTPS or same-origin compatibility/immutable CSS URL');
  }
  return local ? url.pathname : url.href;
}
