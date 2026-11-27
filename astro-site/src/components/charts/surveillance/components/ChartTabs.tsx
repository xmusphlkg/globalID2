interface Props<T extends string> {
  id: string;
  label: string;
  tabs: readonly T[];
  title: (tab: T) => string;
  value: T;
  onChange: (tab: T) => void;
}

export default function ChartTabs<T extends string>({ id, label, tabs, title, value, onChange }: Props<T>) {
  return <div className="surveillance-tabs" role="tablist" aria-label={label} onKeyDown={event => {
    const index = tabs.indexOf(value);
    const target = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index + tabs.length - 1) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    onChange(tabs[target]);
    document.getElementById(`${id}-${tabs[target]}`)?.focus();
  }}>
    {tabs.map(tab => <button type="button" role="tab" key={tab} id={`${id}-${tab}`}
      aria-selected={tab === value} aria-controls={`${id}-panel`} tabIndex={tab === value ? 0 : -1}
      onClick={() => onChange(tab)}>{title(tab)}</button>)}
  </div>;
}
