"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useAppStore } from "@/stores/app-store";
import { getCountryDisplayName, useCountries } from "@/shared/config/countries";
import { type CountryScope, findRouteByPath, navigationSections } from "@/shared/navigation/route-registry";
import { t } from "@/lib/i18n";
import { ApiError } from "@/lib/api";
import { Menu, Globe, Languages, ShieldCheck, AlertTriangle, PanelLeftClose, PanelLeftOpen } from "lucide-react";

function scopeLabel(lang: "en" | "zh", scope: CountryScope) {
  if (scope === "required") return t(lang, "country_scope_required");
  if (scope === "optional") return t(lang, "country_scope_optional");
  return t(lang, "country_scope_global");
}

function scopeHint(lang: "en" | "zh", scope: CountryScope) {
  if (scope === "required") return t(lang, "country_scope_required_hint");
  if (scope === "optional") return t(lang, "country_scope_optional_hint");
  return t(lang, "country_scope_global_hint");
}

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
  const pathname = usePathname();
  const currentRoute = findRouteByPath(pathname);
  const currentSection = currentRoute
    ? navigationSections.find((section) => section.id === currentRoute.section)
    : null;
  const CurrentIcon = currentRoute?.icon;
  const countryScope = currentRoute?.countryScope ?? "none";
  const showCountrySelector = countryScope !== "none";

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
      <div className="flex min-h-[68px] items-center gap-x-4 py-2 sm:gap-x-6">
        <button
          type="button"
          className="icon-button lg:hidden"
          onClick={onMenuClick}
        >
          <span className="sr-only">Open sidebar</span>
          <Menu className="h-5 w-5" aria-hidden="true" />
        </button>

        <div className="flex flex-1 gap-x-4 self-stretch lg:gap-x-6">
          <div className="flex min-w-0 flex-1 items-center gap-3 font-semibold text-tremor-content-strong">
            <button
              type="button"
              onClick={onToggleSidebar}
              className="icon-button hidden lg:inline-flex"
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
            <div className="flex min-w-0 items-center gap-3">
              {CurrentIcon ? (
                <span className="hidden h-10 w-10 shrink-0 items-center justify-center rounded-tremor-default border border-tremor-border bg-tremor-background text-tremor-brand sm:flex">
                  <CurrentIcon className="h-5 w-5" />
                </span>
              ) : null}
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate text-base font-semibold text-tremor-content-strong">
                    {currentRoute ? t(lang, currentRoute.labelKey) : t(lang, "brand_name")}
                  </p>
                  {currentSection ? (
                    <span className="hidden rounded-tremor-default bg-tremor-background-muted px-2 py-0.5 text-[11px] font-semibold uppercase text-tremor-content-subtle sm:inline-flex">
                      {t(lang, currentSection.titleKey)}
                    </span>
                  ) : null}
                </div>
                <p className="hidden truncate text-xs font-normal text-tremor-content-subtle sm:block">
                  {showCountrySelector
                    ? `${scopeLabel(lang, countryScope)} · ${countryName || t(lang, "select_country")}`
                    : scopeHint(lang, countryScope)}
                </p>
              </div>
            </div>
            {showCountrySelector ? (
              <div className={`hidden rounded-tremor-default border px-2.5 py-1 text-xs font-medium xl:flex xl:items-center xl:gap-2 ${countryHealthTone}`}>
              {error ? <AlertTriangle className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}
              <span>{countryHealthLabel}</span>
              {errorStatus ? <span className="opacity-75">({errorStatus})</span> : null}
              </div>
            ) : null}
          </div>
          <div className="flex items-center gap-2 lg:gap-3">
            {showCountrySelector ? (
              <>
                <div className="hidden text-right lg:block">
                  <p className="text-[11px] font-semibold uppercase text-tremor-content-subtle">
                    {t(lang, "active_country")}
                  </p>
                  <p className="text-sm font-semibold text-tremor-content-strong">
                    {countryName || t(lang, "select_country")}
                  </p>
                </div>

                <div className="flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 shadow-[0_1px_2px_rgba(23,33,31,0.04)]">
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
                    className="block max-w-[46vw] bg-transparent py-0 pl-0 pr-6 text-sm font-medium text-tremor-content-strong outline-none sm:min-w-[150px]"
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
              </>
            ) : (
              <div className="hidden h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong shadow-[0_1px_2px_rgba(23,33,31,0.04)] sm:flex">
                <Globe className="h-4 w-4 text-tremor-content-subtle" />
                {t(lang, "country_scope_global")}
              </div>
            )}

            <button
              onClick={() => setLang(lang === "en" ? "zh" : "en")}
              aria-label={t(lang, "language_toggle_label")}
              className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content-strong shadow-[0_1px_2px_rgba(23,33,31,0.04)] transition hover:border-tremor-ring hover:bg-tremor-background-muted"
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
