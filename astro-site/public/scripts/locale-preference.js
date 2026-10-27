(() => {
  const key = 'gids-locale-preference';
  const path = window.location.pathname;
  const defaultPreference = path === '/fr' || path.startsWith('/fr/')
    ? 'fr'
    : path === '/zh' || path.startsWith('/zh/')
      ? 'zh'
      : 'en';
  const normalize = (value) => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'fr-ca' || normalized === 'fr_ca') return 'fr-CA';
    if (normalized === 'fr' || normalized === 'zh' || normalized === 'en') return normalized;
    return defaultPreference;
  };
  const addCanadianFrenchOption = () => {
    document.querySelectorAll('.site-language-popover a[hreflang="fr"], .site-mobile-language-options a[hreflang="fr"]').forEach((source) => {
      const container = source.parentElement;
      if (!container || container.querySelector('[data-locale-choice="fr-CA"]')) return;
      const option = source.cloneNode(true);
      option.setAttribute('data-locale-choice', 'fr-CA');
      option.setAttribute('hreflang', 'fr-CA');
      option.setAttribute('lang', 'fr-CA');
      option.removeAttribute('aria-current');
      option.classList.remove('is-current');
      const label = option.querySelector('span');
      const description = option.querySelector('small');
      if (label) label.textContent = 'Français (Canada)';
      if (description) description.textContent = 'français canadien';
      else {
        option.replaceChildren(document.createTextNode('Français (Canada)'));
        const mobileDescription = document.createElement('small');
        mobileDescription.textContent = 'français canadien';
        option.append(mobileDescription);
      }
      container.append(option);
    });
  };
  const apply = (preference) => {
    const selected = normalize(preference);
    const contentLanguage = selected === 'fr-CA' ? 'fr' : selected;
    document.documentElement.lang = selected === 'fr-CA'
      ? 'fr-CA'
      : contentLanguage === 'zh' ? 'zh-CN' : contentLanguage;
    document.querySelectorAll('.site-language-popover a[hreflang], .site-mobile-language-options a[hreflang]').forEach((option) => {
      const preference = option.getAttribute('data-locale-choice')
        || (option.getAttribute('hreflang') === 'fr-CA' ? 'fr-CA' : option.getAttribute('hreflang') === 'zh-CN' ? 'zh' : 'en');
      const active = preference === selected;
      option.classList.toggle('is-current', active);
      if (active) option.setAttribute('aria-current', 'page');
      else option.removeAttribute('aria-current');
    });
    const currentLabel = document.querySelector('[data-language-current]');
    if (currentLabel) currentLabel.textContent = selected === 'zh' ? '中文' : selected.toUpperCase();
  };
  addCanadianFrenchOption();
  let rawStored = null;
  try { rawStored = window.localStorage.getItem(key); } catch { /* storage may be unavailable */ }
  const stored = rawStored ? normalize(rawStored) : null;
  if ((defaultPreference === 'fr' && stored === 'fr-CA') || (defaultPreference !== 'fr' && stored === defaultPreference)) {
    apply(stored);
  }
  document.querySelectorAll('.site-language-popover a[hreflang], .site-mobile-language-options a[hreflang]').forEach((option) => {
    option.addEventListener('click', () => {
      const selected = normalize(option.getAttribute('data-locale-choice') || option.getAttribute('hreflang'));
      try { window.localStorage.setItem(key, selected); } catch { /* storage may be unavailable */ }
    });
  });
})();
