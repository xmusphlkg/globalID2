/** Initialise one published download catalogue without changing its URL contract. */
export function initializeDownloadCatalogue(locale, copy) {

  const search = document.querySelector(`#download-search-${locale}`);
  const sort = document.querySelector(`#download-sort-${locale}`);
  const tbody = document.querySelector('[data-download-row]')?.closest('tbody');
  const rows = Array.from(document.querySelectorAll('[data-download-row]'));
  const buttons = Array.from(document.querySelectorAll('[data-download-kind]'));
  const count = document.querySelector(`#download-count-${locale}`);
  const empty = document.querySelector(`#downloads-empty-${locale}`);
  const more = document.querySelector(`#downloads-more-${locale}`);
  const validKinds = new Set(['all', 'country', 'disease']);
  const validSorts = new Set(['featured', 'name', 'records', 'kind']);
  const initialUrl = new URL(window.location.href);
  let kind = validKinds.has(initialUrl.searchParams.get('type'))
    ? initialUrl.searchParams.get('type')
    : 'all';
  let sortMode = validSorts.has(initialUrl.searchParams.get('sort'))
    ? initialUrl.searchParams.get('sort')
    : 'featured';
  const pageSize = window.matchMedia('(max-width: 760px)').matches ? 12 : 30;
  let limit = pageSize;

  function normalize(value) {
    return String(value || '').trim().toLowerCase();
  }

  function orderedRows() {
    const ordered = [...rows];
    ordered.sort((a, b) => {
      if (sortMode === 'name') {
        return String(a.dataset.name || '').localeCompare(String(b.dataset.name || ''));
      }
      if (sortMode === 'records') {
        return Number(b.dataset.count || 0) - Number(a.dataset.count || 0);
      }
      if (sortMode === 'kind') {
        const kindCompare = String(a.dataset.kind || '').localeCompare(String(b.dataset.kind || ''));
        if (kindCompare) return kindCompare;
        return String(a.dataset.name || '').localeCompare(String(b.dataset.name || ''));
      }
      return Number(a.dataset.index || 0) - Number(b.dataset.index || 0);
    });
    return ordered;
  }

  function syncControls() {
    if (sort) sort.value = sortMode;
    buttons.forEach((button) => {
      const active = button.dataset.downloadKind === kind;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
  }

  function syncUrl() {
    const next = new URL(window.location.href);
    const query = normalize(search?.value);
    if (query) next.searchParams.set('q', query);
    else next.searchParams.delete('q');
    if (kind === 'all') next.searchParams.delete('type');
    else next.searchParams.set('type', kind);
    if (sortMode === 'featured') next.searchParams.delete('sort');
    else next.searchParams.set('sort', sortMode);
    window.history.replaceState({}, '', next);
  }

  function update() {
    const query = normalize(search?.value);
    let matches = 0;
    let visible = 0;
    const ordered = orderedRows();
    ordered.forEach((row) => tbody?.append(row));
    ordered.forEach((row) => {
      const match =
        (kind === 'all' || row.dataset.kind === kind) &&
        (!query || row.dataset.search?.includes(query));
      if (match) matches++;
      row.hidden = !match || visible >= limit;
      if (!row.hidden) visible++;
    });
    if (count) count.textContent = `${copy.showing} ${visible} ${copy.of} ${matches} ${copy.dataset}`;
    if (empty) empty.hidden = matches !== 0;
    if (more) {
      more.hidden = visible >= matches;
      more.textContent = `${copy.more} ${Math.min(pageSize, matches - visible)} ${copy.dataset}`;
    }
    syncUrl();
  }

  search?.addEventListener('input', () => {
    limit = pageSize;
    update();
  });
  sort?.addEventListener('change', () => {
    sortMode = validSorts.has(sort.value) ? sort.value : 'featured';
    limit = pageSize;
    update();
  });
  buttons.forEach((button) =>
    button.addEventListener('click', () => {
      kind = button.dataset.downloadKind || 'all';
      limit = pageSize;
      syncControls();
      update();
    }),
  );
  more?.addEventListener('click', () => {
    limit += pageSize;
    update();
  });

  if (search) search.value = initialUrl.searchParams.get('q') || '';
  syncControls();
  update();

}
