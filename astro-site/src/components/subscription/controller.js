import { subscriptionMessages as copy } from './messages';
import { requestedSubscriptionTopic, findSubscriptionTopic, subscriptionPayload } from './form-model.js';

export function initializeSubscription(subscriptionCountryNames) {
const form = document.getElementById('subscription-form');
if (!form) return;

const apiBase = form.getAttribute('data-api-base') || 'https://subscriptions.globalinfectiousdisease.com';
const optionsUrl = form.getAttribute('data-options-url') || `${apiBase}/api/subscriptions/options`;
const emailInput = document.getElementById('subscriber-email');
const submitButton = document.getElementById('subscription-submit');
const statusEl = document.getElementById('subscription-status');
const loadingEl = document.getElementById('subscription-loading');
const pilotNotice = document.getElementById('subscription-pilot-notice');
const modal = document.getElementById('subscription-modal');
const modalMessage = document.getElementById('subscription-modal-message');
const resendButton = document.getElementById('subscription-resend');
const resendStatus = document.getElementById('subscription-resend-status');
const allCountries = document.getElementById('all-countries');
const localeOptions = document.getElementById('locale-options');
const listOptions = document.getElementById('list-options');
const countryOptions = document.getElementById('country-options');
const diseaseOptions = document.getElementById('disease-options');
const diseaseSearch = document.getElementById('disease-search');
const researchTopicOptions = document.getElementById('research-topic-options');
const studyTypeOptions = document.getElementById('study-type-options');
const peerReviewStatusOptions = document.getElementById('peer-review-status-options');
const { requestedCountry, requestedDisease } = requestedSubscriptionTopic(window.location.search);
const optionState = {
  locales: [],
  frequencies: [],
  lists: [],
  countries: [],
  diseases: [],
  researchTopics: [],
  studyTypes: [],
  peerReviewStatuses: [],
};
let lastSubmittedPayload = null;
const selectedDiseases = new Set();


function currentLang() {
  const pageLocale = form.getAttribute('data-page-locale');
  return pageLocale === 'zh' || pageLocale === 'fr' ? pageLocale : 'en';
}

function setStatus(type, message) {
  if (!statusEl) return;
  statusEl.hidden = type === 'pending';
  if (loadingEl) {
    loadingEl.hidden = type !== 'pending';
    const text = loadingEl.querySelector('.gids-loading__copy > span:last-child');
    if (text) text.textContent = message;
  }
  statusEl.dataset.type = type;
  statusEl.textContent = message;
}

function setSubmitting(isSubmitting) {
  if (!submitButton) return;
  submitButton.disabled = isSubmitting;
  submitButton.setAttribute('aria-busy', String(isSubmitting));
}

function showModal(message) {
  if (!modal) return;
  if (modalMessage) modalMessage.textContent = message;
  if (resendStatus) resendStatus.textContent = '';
  modal.hidden = false;
  document.documentElement.classList.add('subscribe-modal-open');
  resendButton?.focus();
}

function closeModal() {
  if (!modal) return;
  modal.hidden = true;
  document.documentElement.classList.remove('subscribe-modal-open');
}

modal?.querySelectorAll('[data-modal-close]').forEach((element) => {
  element.addEventListener('click', closeModal);
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && modal && !modal.hidden) closeModal();
});

function labelFor(item, lang = currentLang()) {
  return lang === 'zh'
    ? (item.label_zh || item.name_zh || item.label_en || item.name_en || item.value || item.code)
    : lang === 'fr'
      ? (item.label_fr || item.name_fr || item.label_en || item.name_en || item.label_zh || item.name_zh || item.value || item.code)
      : (item.label_en || item.name_en || item.label_zh || item.name_zh || item.value || item.code);
}

function countryLabelFor(item) {
  const code = String(item.value || item.code || '').toUpperCase();
  if (subscriptionCountryNames[code]) return subscriptionCountryNames[code];
  if (/^[A-Z]{2}$/.test(code)) {
    try { return new Intl.DisplayNames([currentLang() === 'zh' ? 'zh-CN' : currentLang()], { type: 'region' }).of(code) || labelFor(item); } catch {}
  }
  return labelFor(item);
}

function descriptionFor(item, lang = currentLang()) {
  return lang === 'zh'
    ? (item.description_zh || item.description_en || '')
    : lang === 'fr'
      ? (item.description_fr || item.description_en || item.description_zh || '')
      : (item.description_en || item.description_zh || '');
}

function checkedValues(name) {
  return new Set(Array.from(form.querySelectorAll(`input[name="${name}"]:checked`)).map((input) => input.value));
}

function renderRadioOptions(container, name, items, defaultValue) {
  if (!container) return;
  const selected = form.querySelector(`input[name="${name}"]:checked`)?.value || defaultValue;
  container.replaceChildren();
  items.forEach((item) => {
    const label = document.createElement('label');
    const input = document.createElement('input');
    const span = document.createElement('span');
    input.type = 'radio';
    input.name = name;
    input.value = item.value;
    input.defaultChecked = item.value === defaultValue;
    input.checked = item.value === selected;
    span.textContent = labelFor(item);
    label.append(input, span);
    container.append(label);
  });
}

function renderListOptions() {
  if (!listOptions) return;
  const selected = checkedValues('list_codes');
  const hasSelection = selected.size > 0;
  listOptions.replaceChildren();
  optionState.lists.forEach((item, index) => {
    const label = document.createElement('label');
    const input = document.createElement('input');
    const body = document.createElement('span');
    const title = document.createElement('strong');
    const detail = document.createElement('small');
    const schedule = document.createElement('small');
    label.className = 'subscribe-check';
    input.type = 'checkbox';
    input.name = 'list_codes';
    input.value = item.code;
    input.disabled = item.available === false;
    input.defaultChecked = !input.disabled && (index === 0 || item.code === 'reports');
    input.checked = !input.disabled && (hasSelection ? selected.has(item.code) : input.defaultChecked);
    title.textContent = labelFor(item);
    detail.textContent = descriptionFor(item);
    const frequency = item.code === 'alerts' ? 'instant' : 'weekly';
    schedule.textContent = input.disabled ? copy[currentLang()].unavailable : copy[currentLang()][frequency];
    body.append(title, detail, schedule);
    label.append(input, body);
    listOptions.append(label);
  });
}

function renderChipOptions(container, name, items, disabled = false, selected = checkedValues(name)) {
  if (!container) return;
  container.replaceChildren();
  items.forEach((item) => {
    const label = document.createElement('label');
    const input = document.createElement('input');
    const span = document.createElement('span');
    label.className = 'subscribe-chip';
    input.type = 'checkbox';
    input.name = name;
    input.value = item.value;
    input.disabled = disabled;
    input.checked = selected.has(item.value);
    span.textContent = name === 'countries' ? countryLabelFor(item) : labelFor(item);
    label.append(input, span);
    container.append(label);
  });
}

function renderDiseaseOptions() {
  const query = String(diseaseSearch?.value || '').trim().toLowerCase();
  const selectedFirst = optionState.diseases.filter((item) => selectedDiseases.has(item.value));
  const visible = query
    ? optionState.diseases.filter((item) => {
        const haystack = `${item.value} ${item.code || ''} ${item.slug || ''} ${item.disease_id || ''} ${item.label_en || ''} ${item.label_zh || ''} ${item.label_fr || ''}`.toLowerCase();
        return haystack.includes(query);
      })
    : [...selectedFirst, ...optionState.diseases.filter((item) => !selectedDiseases.has(item.value))]
        .slice(0, Math.max(24, selectedDiseases.size));
  renderChipOptions(diseaseOptions, 'diseases', visible, false, selectedDiseases);
}

function renderAllOptions() {
  if (diseaseSearch) {
  diseaseSearch.setAttribute('placeholder', currentLang() === 'zh' ? '搜索疾病' : currentLang() === 'fr' ? 'Rechercher une maladie' : 'Search diseases');
  }
  const emailLocale = optionState.locales.some((item) => item.value === currentLang()) ? currentLang() : 'en';
  renderRadioOptions(localeOptions, 'locale', optionState.locales, emailLocale);
  renderListOptions();
  renderChipOptions(countryOptions, 'countries', optionState.countries, Boolean(allCountries?.checked));
  renderDiseaseOptions();
  renderChipOptions(researchTopicOptions, 'research_topics', optionState.researchTopics);
  renderChipOptions(studyTypeOptions, 'study_types', optionState.studyTypes);
  renderChipOptions(peerReviewStatusOptions, 'peer_review_statuses', optionState.peerReviewStatuses);
}

function applyRequestedTopic() {
  const { exactCountry, country, disease } = findSubscriptionTopic(optionState, requestedCountry, requestedDisease);
  if (country && allCountries) {
    allCountries.checked = false;
    renderChipOptions(countryOptions, 'countries', optionState.countries, false);
    const input = Array.from(form.querySelectorAll('input[name="countries"]')).find((node) => node.value === country.value);
    if (input) input.checked = true;
    if (!exactCountry) setStatus('info', copy[currentLang()].regionFallback);
  }
  if (disease) {
    selectedDiseases.add(disease.value);
    if (diseaseSearch) diseaseSearch.value = labelFor(disease).toLowerCase();
    renderDiseaseOptions();
  }
}

async function loadOptions() {
  const messages = copy[currentLang()];
  setSubmitting(true);
  setStatus('pending', messages.optionsLoading);
  try {
    const response = await fetch(optionsUrl, {
      headers: { accept: 'application/json' },
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.error) {
      throw new Error(result.error || 'options_failed');
    }
    optionState.locales = Array.isArray(result.locales) ? result.locales : [];
    optionState.frequencies = Array.isArray(result.frequencies) ? result.frequencies : [];
    optionState.lists = Array.isArray(result.lists) ? result.lists : [];
    optionState.countries = Array.isArray(result.filters?.country) ? result.filters.country : [];
    optionState.diseases = Array.isArray(result.filters?.disease) ? result.filters.disease : [];
    optionState.researchTopics = Array.isArray(result.filters?.research_topic) ? result.filters.research_topic : [];
    optionState.studyTypes = Array.isArray(result.filters?.study_type) ? result.filters.study_type : [];
    optionState.peerReviewStatuses = Array.isArray(result.filters?.peer_review_status) ? result.filters.peer_review_status : [];
    if (pilotNotice) {
      pilotNotice.hidden = result.pilot_mode !== true;
      if (result.pilot_mode === true) pilotNotice.textContent = messages.pilotOnly;
    }
    renderAllOptions();
    applyRequestedTopic();
    if (statusEl && statusEl.dataset.type === 'pending') statusEl.hidden = true;
    if (loadingEl) loadingEl.hidden = true;
    setSubmitting(false);
  } catch (error) {
    setStatus('error', messages.optionsFailed);
    setSubmitting(true);
  }
}

function syncCountryInputs() {
  const all = Boolean(allCountries && allCountries.checked);
  form.querySelectorAll('input[name="countries"]').forEach((input) => {
    input.disabled = all;
    if (all) input.checked = false;
  });
}

allCountries?.addEventListener('change', syncCountryInputs);
diseaseSearch?.addEventListener('input', renderDiseaseOptions);
diseaseOptions?.addEventListener('change', (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.name !== 'diseases') return;
  if (input.checked) selectedDiseases.add(input.value);
  else selectedDiseases.delete(input.value);
});
loadOptions();

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const lang = currentLang();
  const messages = copy[lang];
  const data = new FormData(form);
  const email = String(data.get('email') || '').trim();
  const consent = data.get('consent') === 'on';
  const listCodes = data.getAll('list_codes').map(String);

  if (!email || !emailInput?.checkValidity()) {
    setStatus('error', messages.missingEmail);
    emailInput?.focus();
    return;
  }

  if (listCodes.length === 0) {
    setStatus('error', messages.missingList);
    return;
  }

  if (!consent) {
    setStatus('error', messages.missingConsent);
    return;
  }

    const payload = subscriptionPayload(data, lang, Boolean(allCountries?.checked), selectedDiseases);

  setSubmitting(true);
  setStatus('pending', messages.submitting);
  window.gtag?.('event', 'subscription_start');

  try {
    const response = await fetch(`${apiBase}/api/subscriptions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));

    if (!response.ok || result.error) {
      setStatus('error', result.error === 'rate_limited' ? messages.rateLimited : result.error === 'subscription_pilot_only' ? messages.pilotOnly : (result.error || messages.failed));
      return;
    }

    lastSubmittedPayload = payload;
    const emailStatus = result?.email?.status || result?.notification?.status || '';
    const emailReason = result?.email?.reason || result?.notification?.reason || '';
    if (emailStatus === 'failed' || (emailStatus === 'skipped' && emailReason !== 'confirmation_rate_limited')) {
      setStatus('error', messages.confirmationFailed);
      return;
    }
    const successMessage = emailReason === 'confirmation_rate_limited'
      ? messages.emailRateLimited
      : emailStatus === 'sent'
      ? messages.emailSent
      : emailStatus === 'queued'
      ? messages.emailQueued
      : messages.received;
    setStatus('success', successMessage);
    window.gtag?.('event', 'subscription_request_submitted');
    showModal(successMessage);
    form.reset();
    selectedDiseases.clear();
    if (diseaseSearch) diseaseSearch.value = '';
    renderAllOptions();
  } catch (error) {
    setStatus('error', messages.network);
  } finally {
    setSubmitting(false);
  }
});

resendButton?.addEventListener('click', async () => {
  const lang = currentLang();
  const messages = copy[lang];
  if (!lastSubmittedPayload) {
    if (resendStatus) resendStatus.textContent = messages.resendUnavailable;
    return;
  }

  resendButton.disabled = true;
  if (resendStatus) resendStatus.textContent = messages.resendSending;

  try {
    const response = await fetch(`${apiBase}/api/subscriptions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...lastSubmittedPayload, source: 'website-subscribe-page-resend' }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.error) {
      if (resendStatus) resendStatus.textContent = result.error === 'rate_limited' ? messages.rateLimited : result.error === 'subscription_pilot_only' ? messages.pilotOnly : (result.error || messages.failed);
      return;
    }
    const emailStatus = result?.email?.status || result?.notification?.status || '';
    const emailReason = result?.email?.reason || result?.notification?.reason || '';
    if (resendStatus) {
      resendStatus.textContent = emailReason === 'confirmation_rate_limited'
        ? messages.emailRateLimited
        : emailStatus === 'pending'
        ? messages.received
        : emailStatus === 'sent'
        ? messages.resendDone
        : emailStatus === 'queued'
        ? messages.emailQueued
        : messages.resendUnavailable;
    }
  } catch (error) {
    if (resendStatus) resendStatus.textContent = messages.network;
  } finally {
    resendButton.disabled = false;
  }
});
}
