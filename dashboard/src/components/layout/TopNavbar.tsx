"use client";

import { useEffect } from "react";
import { useAppStore } from "@/stores/app-store";
import { getCountryDisplayName, useCountries } from "@/lib/hooks/useCountries";
import { t } from "@/lib/i18n";
import { ApiError } from "@/lib/api";
import { Menu, Globe, Languages, ShieldCheck, AlertTriangle, PanelLeftClose, PanelLeftOpen } from "lucide-react";

export function TopNavbar({
  onMenuClick,
  sidebarCollapsed = false,
  onToggleSidebar,
}: {
  onMenuClick?: () => void;
  sidebarCollapsed?: boolean;
  onToggleSidebar?: () => void;
}) {
  const { lang, setLang, countryId, countryName, countryCode, setCountry } = useAppStore();
  const { data: countries, isLoading, error } = useCountries();

  useEffect(() => {
    if (!countries || countries.length === 0) {
      return;
    }

    if (countryId) {
      const selected = countries.find((country) => country.id === countryId);
      if (!selected) {
        return;
      }

      const displayName = getCountryDisplayName(selected, lang);
      if (countryName !== displayName || countryCode !== selected.code) {
        setCountry(selected.id, displayName, selected.code);
      }
      return;
    }

    const preferredCodes = new Set(["CN", "US", "GB", "UK"]);
    const preferred = countries.find((country) => preferredCodes.has(country.code.toUpperCase()));
    const fallback = countries.find((country) => country.is_active) ?? countries[0];
    const next = preferred ?? fallback;
    setCountry(next.id, getCountryDisplayName(next, lang), next.code);
  }, [countryCode, countryId, countryName, countries, lang, setCountry]);

  const selectorValue = countryId ? String(countryId) : "";
  const selectorDisabled = isLoading || !!error || !countries || countries.length === 0;
  const errorStatus =
    error instanceof ApiError ? `${error.status}` : error ? t(lang, "system_status_unavailable") : null;
  const countryHealthTone = error
    ? "text-rose-700 bg-rose-50 border-rose-200"
    : countries && countries.length > 0
      ? "text-emerald-700 bg-emerald-50 border-emerald-200"
      : "text-amber-700 bg-amber-50 border-amber-200";
  const countryHealthLabel = error
    ? t(lang, "system_status_country_unavailable")
    : countries && countries.length > 0
      ? t(lang, "system_status_country_ready")
      : t(lang, "system_status_country_empty");

  return (
    <header className="glass-panel sticky top-0 z-40 border-b border-tremor-border px-4 sm:px-6 lg:px-8">
      <div className="flex min-h-14 items-center gap-x-4 py-2 sm:gap-x-6">
        <button
          type="button"
          className="-m-2.5 p-2.5 text-tremor-content-subtle hover:text-tremor-content lg:hidden"
          onClick={onMenuClick}
        >
          <span className="sr-only">Open sidebar</span>
          <Menu className="h-6 w-6" aria-hidden="true" />
        </button>

        <div className="flex flex-1 gap-x-4 self-stretch lg:gap-x-6">
          <div className="flex flex-1 items-center gap-3 font-semibold text-tremor-content-strong">
            <button
              type="button"
              onClick={onToggleSidebar}
              className="hidden rounded-tremor-default border border-tremor-border bg-tremor-background p-2 text-tremor-content-subtle transition hover:bg-tremor-background-subtle hover:text-tremor-content lg:inline-flex"
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
            <div className="lg:hidden">
              <span className="text-lg">{t(lang, "brand_name")}</span>
            </div>
            <div className={`hidden rounded-tremor-default border px-2.5 py-1 text-xs font-medium lg:flex lg:items-center lg:gap-2 ${countryHealthTone}`}>
              {error ? <AlertTriangle className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}
              <span>{countryHealthLabel}</span>
              {errorStatus ? <span className="opacity-75">({errorStatus})</span> : null}
            </div>
          </div>
          <div className="flex items-center gap-3 lg:gap-4">
            <div className="hidden text-right lg:block">
              <p className="text-xs font-medium uppercase text-tremor-content-subtle">
                {t(lang, "active_country")}
              </p>
              <p className="text-sm font-semibold text-tremor-content-strong">
                {countryName || t(lang, "select_country")}
              </p>
            </div>

            <div className="flex items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2">
              <Globe className="h-4 w-4 text-tremor-content-subtle" />
              <select
                title={t(lang, "select_country")}
                aria-label={t(lang, "select_country")}
                value={selectorValue}
                disabled={selectorDisabled}
                onChange={(e) => {
                  const nextId = Number(e.target.value);
                  const c = countries?.find((country) => country.id === nextId);
                  if (c) setCountry(c.id, getCountryDisplayName(c, lang), c.code);
                }}
                className="block min-w-[150px] bg-transparent py-0 pl-0 pr-6 text-sm font-medium text-tremor-content-strong outline-none"
              >
                {isLoading ? <option value="">{t(lang, "select_country_loading")}</option> : null}
                {error ? <option value="">{t(lang, "select_country_unavailable")}</option> : null}
                {!isLoading && !error && (!countries || countries.length === 0) ? (
                  <option value="">{t(lang, "select_country_empty")}</option>
                ) : null}
                {countries?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {getCountryDisplayName(c, lang)}
                  </option>
                ))}
              </select>
            </div>

            <button
              onClick={() => setLang(lang === "en" ? "zh" : "en")}
              aria-label={t(lang, "language_toggle_label")}
              className="flex items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm font-medium text-tremor-content-strong transition hover:border-tremor-ring hover:bg-tremor-background-muted"
            >
              <Languages className="h-4 w-4" />
              {lang === "en" ? "中文" : "EN"}
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
