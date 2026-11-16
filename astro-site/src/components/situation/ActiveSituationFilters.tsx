import type { Locale } from '../../utils/situationTypes.ts';
import type { Option } from './explorer-state.ts';
import { DEFAULT_FILTERS, type Filters } from './model.ts';
import { COPY, HOST_FILTER_LABEL } from './labels.ts';
import './situation-controls.css';

interface Props {
  locale: Locale;
  filters: Filters;
  options: { diseases: Option[]; countries: Option[]; sources: Option[]; hosts: Option[]; windows: Option[] };
  onRemove: (key: keyof Filters) => void;
  onReset: () => void;
}

const TEXT = {
  en: { heading: 'Selected filters', remove: (field: string, value: string) => `Remove ${field}: ${value}` },
  zh: { heading: '已选条件', remove: (field: string, value: string) => `移除${field}：${value}` },
  fr: { heading: 'Filtres sélectionnés', remove: (field: string, value: string) => `Retirer ${field} : ${value}` },
};

export default function ActiveSituationFilters({ locale, filters, options, onRemove, onReset }: Props) {
  const copy = COPY[locale];
  const text = TEXT[locale];
  const fields: Array<{ key: keyof Filters; label: string; options: Option[] }> = [
    { key: 'disease', label: copy.disease, options: options.diseases },
    { key: 'country', label: copy.country, options: options.countries },
    { key: 'source', label: copy.source, options: options.sources },
    { key: 'host', label: HOST_FILTER_LABEL[locale], options: options.hosts },
    { key: 'window', label: copy.window, options: options.windows },
  ];
  const selected = fields.filter(field => (filters[field.key] ?? '') !== (DEFAULT_FILTERS[field.key] ?? ''));
  if (!selected.length) return null;
  return (
    <div className="sit-active-filters" role="group" aria-label={text.heading}>
      <ul>
        {selected.map(field => {
          const value = field.options.find(option => option.id === filters[field.key])?.label ?? filters[field.key] ?? '';
          return (
            <li key={field.key}>
              <button type="button" className="sit-filter-chip" data-filter={field.key} onClick={() => onRemove(field.key)} aria-label={text.remove(field.label, value)} title={text.remove(field.label, value)}>
                <span><span className="sit-filter-chip-label">{field.label}:</span> {value}</span>
                <span className="sit-filter-chip-close" aria-hidden="true">×</span>
              </button>
            </li>
          );
        })}
      </ul>
      <button type="button" className="sit-text-btn sit-filter-clear" onClick={onReset}>{copy.reset}</button>
    </div>
  );
}
