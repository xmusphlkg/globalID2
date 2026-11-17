import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { Option } from './explorer-state.ts';
import './search-filter.css';

interface Props {
  label: string;
  value: string;
  options: Option[];
  anyLabel: string;
  onChange: (value: string) => void;
  noResults?: string;
  includeAny?: boolean;
}

const searchable = (value: string) => value.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase().trim();

/** A committed filter value is separate from the text used to find an option. */
export default function SearchableFilter({ label, value, options, anyLabel, onChange, noResults = 'No matches', includeAny = true }: Props) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const selectedLabel = options.find(option => option.id === value)?.label ?? (value || anyLabel);
  const choices = useMemo(() => {
    const all = includeAny ? [{ id: '', label: anyLabel, count: -1 }, ...options] : options;
    const terms = searchable(query).split(/\s+/).filter(Boolean);
    return all.filter(option => terms.every(term => searchable(`${option.label} ${option.id}`).includes(term)));
  }, [options, anyLabel, query, includeAny]);

  const close = () => { setOpen(false); setQuery(''); };
  const expand = () => {
    setQuery('');
    setActive(Math.max(0, options.findIndex(option => option.id === value) + (includeAny ? 1 : 0)));
    setOpen(true);
  };
  const commit = (next: string) => {
    onChange(next);
    close();
  };

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);

  useEffect(() => {
    if (open) root.current?.querySelector(`[data-option-index="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) expand();
      else setActive(index => choices.length ? (index + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length : 0);
    } else if (open && (event.key === 'Home' || event.key === 'End')) {
      event.preventDefault();
      setActive(event.key === 'Home' ? 0 : Math.max(0, choices.length - 1));
    } else if (event.key === 'Enter' && open) {
      event.preventDefault();
      if (choices[active]) commit(choices[active].id);
    }
  };

  return (
    <div className="sit-field sit-search-field" ref={root} onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) close();
    }}>
      <label htmlFor={`${id}-input`}>{label}</label>
      <div className="sit-search-control">
        <input
          ref={input}
          id={`${id}-input`}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${id}-options`}
          aria-autocomplete="list"
          aria-activedescendant={open && choices[active] ? `${id}-option-${active}` : undefined}
          autoComplete="off"
          spellCheck={false}
          value={open ? query : selectedLabel}
          placeholder={selectedLabel}
          onFocus={expand}
          onClick={() => { if (!open) expand(); }}
          onChange={event => { setQuery(event.target.value); setActive(0); setOpen(true); }}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="sit-search-toggle"
          aria-label={label}
          aria-expanded={open}
          aria-controls={`${id}-options`}
          tabIndex={-1}
          onMouseDown={event => event.preventDefault()}
          onClick={() => {
            if (open) close();
            else { input.current?.focus(); expand(); }
          }}
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
        </button>
      </div>
      {open && (
        <div className="sit-search-popup">
          <ul id={`${id}-options`} role="listbox" aria-label={label}>
            {choices.map((option, index) => (
              <li
                key={option.id}
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={option.id === value}
                className={index === active ? 'is-active' : undefined}
                data-option-index={index}
                data-value={option.id}
                onMouseDown={event => event.preventDefault()}
                onMouseMove={() => setActive(index)}
                onClick={() => commit(option.id)}
              >
                <span>{option.label}</span>
                {option.count >= 0 && <small>{option.count}</small>}
              </li>
            ))}
          </ul>
          {!choices.length && <p className="sit-search-empty" role="status">{noResults}</p>}
        </div>
      )}
    </div>
  );
}
