const SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
let scriptReady;

function loadTurnstile() {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (scriptReady) return scriptReady;
  scriptReady = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    const fail = () => {
      clearTimeout(timer);
      script.remove();
      scriptReady = undefined;
      reject(new Error('turnstile_unavailable'));
    };
    const timer = setTimeout(fail, 15_000);
    script.src = SCRIPT_URL;
    script.async = true;
    script.addEventListener('error', fail, { once: true });
    script.addEventListener('load', () => {
      if (!window.turnstile) return fail();
      clearTimeout(timer);
      resolve(window.turnstile);
    }, { once: true });
    document.head.append(script);
  });
  return scriptReady;
}

/** Each same-page submission consumes its own token and resets its own widget. */
export function createTurnstileGuard(container, sitekey, locale, messages) {
  const target = document.createElement('div');
  const status = document.createElement('p');
  const retry = document.createElement('button');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  retry.type = 'button';
  retry.className = 'subscribe-modal-secondary';
  retry.textContent = messages.turnstileRetry;
  retry.hidden = true;
  container.replaceChildren(target, status, retry);
  container.hidden = false;
  let token = '';
  let widgetId;
  let disposed = false;

  function pending() {
    token = '';
    status.textContent = messages.turnstilePending;
    retry.hidden = true;
  }
  function failed() {
    token = '';
    status.textContent = messages.turnstileFailed;
    retry.hidden = false;
  }
  async function render() {
    pending();
    try {
      const api = await loadTurnstile();
      if (disposed) return;
      widgetId = api.render(target, {
        sitekey, action: 'subscribe', language: locale === 'zh' ? 'zh-cn' : locale,
        size: container.clientWidth < 300 ? 'compact' : 'flexible', 'response-field': false,
        callback(value) { token = value; status.textContent = messages.turnstileVerified; retry.hidden = true; },
        'expired-callback'() { token = ''; status.textContent = messages.turnstileRequired; },
        'timeout-callback': failed,
        'error-callback'() { failed(); return true; },
      });
    } catch { if (!disposed) failed(); }
  }
  function reset() {
    if (disposed) return;
    pending();
    if (widgetId !== undefined) {
      try { window.turnstile.reset(widgetId); } catch { failed(); }
    } else { void render(); }
  }
  retry.addEventListener('click', reset);
  void render();
  return {
    token: () => token,
    reset,
    remove() {
      disposed = true;
      token = '';
      if (widgetId !== undefined) window.turnstile?.remove(widgetId);
      container.replaceChildren();
      container.hidden = true;
    },
  };
}
