/** Query normalization and payload construction share the existing API contract. */
export function requestedSubscriptionTopic(search) {
  const params = new URLSearchParams(search);
  return {
    requestedCountry: params.get('country')?.trim().toUpperCase().slice(0, 12) || '',
    requestedDisease: params.get('disease')?.trim().toLowerCase().slice(0, 80) || '',
  };
}

export function findSubscriptionTopic(optionState, requestedCountry, requestedDisease) {
  const findCountry = (code) => optionState.countries.find((item) => [item.value, item.code].some((value) => String(value || '').toUpperCase() === code));
  const exactCountry = findCountry(requestedCountry);
  const country = exactCountry || (requestedCountry.includes('-') ? findCountry(requestedCountry.split('-')[0]) : null);
  const disease = optionState.diseases.find((item) => [item.value, item.code, item.slug, item.disease_id].some((value) => String(value || '').toLowerCase() === requestedDisease));
  return { exactCountry, country, disease };
}

export function subscriptionPayload(data, lang, allCountries, selectedDiseases) {
  const selectedCountries = allCountries
    ? []
    : data.getAll('countries').map(String);
  const checkedDiseases = Array.from(selectedDiseases);
  const checkedResearchTopics = data.getAll('research_topics').map(String);
  const checkedStudyTypes = data.getAll('study_types').map(String);
  const checkedPeerReviewStatuses = data.getAll('peer_review_statuses').map(String);

  const payload = {
    email: String(data.get('email') || '').trim(),
    locale: String(data.get('locale') || lang),
    consent: true,
    list_frequencies: Object.fromEntries(data.getAll('list_codes').map(String).map((code) => [code, code === 'alerts' ? 'instant' : 'weekly'])),
    list_codes: data.getAll('list_codes').map(String),
    countries: selectedCountries,
    diseases: Array.from(new Set(checkedDiseases)),
    research_topics: Array.from(new Set(checkedResearchTopics)),
    study_types: Array.from(new Set(checkedStudyTypes)),
    peer_review_statuses: Array.from(new Set(checkedPeerReviewStatuses)),
    source: 'website-subscribe-page',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  };

  return payload;
}
