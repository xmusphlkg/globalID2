(() => {
  const loader = document.currentScript;
  if (!(loader instanceof HTMLScriptElement)) return;

  const measurementId = String(loader.dataset.measurementId || '').trim().toUpperCase();
  if (!/^G-[A-Z0-9]{6,20}$/.test(measurementId)) return;

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }
  window.gtag = gtag;
  gtag('js', new Date());
  gtag('config', measurementId);

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element
      ? event.target.closest('[data-seo-event],[data-file-download]')
      : null;
    if (!target) return;
    gtag('event', target.getAttribute('data-seo-event') || 'dataset_download');
  });

  let started = false;
  const loadAnalytics = () => {
    if (started) return;
    started = true;
    const script = document.createElement('script');
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${measurementId}`;
    document.head.appendChild(script);
  };
  const deferAnalytics = () => {
    window.setTimeout(() => {
      if ('requestIdleCallback' in window) {
        window.requestIdleCallback(loadAnalytics, { timeout: 2000 });
      } else {
        loadAnalytics();
      }
    }, 2000);
  };

  if (document.readyState === 'complete') {
    deferAnalytics();
  } else {
    window.addEventListener('load', deferAnalytics, { once: true });
  }
})();
