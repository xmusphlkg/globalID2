type Geography = { code?: string; name_en?: string; name_zh?: string; name_fr?: string };
type Signal = {
  signal_id?: string;
  disease_id?: string;
  disease_name_en?: string;
  relation_level?: string;
  geographies?: Geography[];
  data_through?: string;
};

// Summaries merge repeated countries, while each source event stays available
// inside its evidence relationship. Historical context must never become exact.
export function groupResearchSignals<T extends Signal>(signals: T[]) {
  const groups = new Map<string, {
    disease: T; relation: string; signals: T[]; places: Map<string, Geography>; latest: string;
  }>();
  for (const signal of signals) {
    const relation = signal.relation_level ?? 'disease_context';
    const key = JSON.stringify([signal.disease_id ?? signal.disease_name_en, relation]);
    if (!groups.has(key)) groups.set(key, { disease: signal, relation, signals: [], places: new Map(), latest: '' });
    const group = groups.get(key)!;
    if (!signal.signal_id || !group.signals.some(item => item.signal_id === signal.signal_id)) group.signals.push(signal);
    for (const place of signal.geographies ?? []) {
      const placeKey = place.code?.trim().toUpperCase() || place.name_en?.trim();
      if (placeKey) group.places.set(placeKey, { ...group.places.get(placeKey), ...place });
    }
    if (signal.data_through && signal.data_through > group.latest) group.latest = signal.data_through;
  }
  const priority = (relation: string) => relation === 'exact_disease_geography' ? 0 : relation === 'historical_disease_geography_context' ? 1 : 2;
  return [...groups.values()].sort((a, b) => priority(a.relation) - priority(b.relation));
}
