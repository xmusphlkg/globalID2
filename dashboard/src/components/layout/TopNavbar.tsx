"use client";

import { useEffect, useMemo, useState, useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ChevronRight, Menu, PanelLeftClose, PanelLeftOpen, Search, X } from "lucide-react";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useCountries } from "@/shared/config/countries";
import { JurisdictionPicker } from "@/components/layout/JurisdictionPicker";
import { allRoutes, findRouteByPath, findSectionByPath } from "@/shared/navigation/route-registry";
import { useAppStore } from "@/stores/app-store";

export function TopNavbar({
  onMenuClick,
  sidebarCollapsed = false,
  onToggleSidebar,
}: {
  onMenuClick?: () => void;
  sidebarCollapsed?: boolean;
  onToggleSidebar?: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const route = findRouteByPath(pathname);
  const section = findSectionByPath(pathname);
  const { countryId, countryCode, lang, setLang, setCountry } = useAppStore();
  const { data: countries, isLoading } = useCountries();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [query, setQuery] = useState("");
  const paletteRef = useRef<HTMLDivElement>(null);
  const paletteTrigger = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!paletteOpen) return;
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const nodes = Array.from(paletteRef.current?.querySelectorAll<HTMLElement>('button, input, a[href]') ?? []);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", trap);
    return () => { document.removeEventListener("keydown", trap); paletteTrigger.current?.focus(); };
  }, [paletteOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        paletteTrigger.current = document.activeElement as HTMLElement | null;
        setPaletteOpen((value) => !value);
      }
      if (event.key === "Escape") setPaletteOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!countries?.length) return;
    const requestedCode = searchParams.get("country")?.toUpperCase();
    const requested = requestedCode ? countries.find((country) => country.code.toUpperCase() === requestedCode) : undefined;
    const stored = countryId ? countries.find((country) => country.id === countryId) : undefined;
    const preferred = countries.find((country) => country.code.toUpperCase() === "US");
    const selected = requested ?? stored ?? preferred ?? countries.find((country) => country.is_active) ?? countries[0];
    if (selected && (selected.id !== countryId || selected.code !== countryCode)) {
      setCountry(selected.id, selected.name_en || selected.name, selected.code);
    }
    if (selected && !requestedCode && route?.countryScope !== "none") {
      const params = new URLSearchParams(searchParams.toString());
      params.set("country", selected.code.toUpperCase());
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    }
  }, [countries, countryCode, countryId, pathname, route?.countryScope, router, searchParams, setCountry]);

  const filteredRoutes = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return allRoutes;
    return allRoutes.filter((item) => `${t(lang, item.labelKey)} ${item.label} ${item.description}`.toLowerCase().includes(normalized));
  }, [query, lang]);

  const setCountryContext = (country: NonNullable<typeof countries>[number]) => {
    setCountry(country.id, country.name_en || country.name, country.code);
    const params = new URLSearchParams(searchParams.toString());
    params.set("country", country.code.toUpperCase());
    router.replace(`${pathname}?${params.toString()}`);
  };

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-[#D9D9D6] bg-white/95 backdrop-blur">
        <div className="flex h-16 items-center gap-3 px-4 sm:px-6">
          <button type="button" onClick={onMenuClick} className="icon-button lg:hidden" aria-label={t(lang, "cc_open_nav")}>
            <Menu className="h-5 w-5" />
          </button>
          <button type="button" onClick={onToggleSidebar} className="icon-button hidden lg:inline-flex" aria-label={sidebarCollapsed ? t(lang, "cc_expand_nav") : t(lang, "cc_collapse_nav")}>
            {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </button>

          <div className="min-w-0 flex-1">
            <div className="hidden min-w-0 items-center gap-1.5 text-xs text-[#6B7280] sm:flex">
              <span className="truncate">{t(lang, section.titleKey)}</span>
              <ChevronRight className="h-3 w-3 shrink-0" />
              <span className="truncate font-medium text-[#374151]">{route ? t(lang, route.labelKey) : t(lang, "cc_center")}</span>
            </div>
            <p className="mt-0.5 truncate text-sm font-semibold text-[#1D1D1F]"><span className="sm:hidden">{t(lang, route?.labelKey ?? section.titleKey)}</span><span className="hidden sm:inline">{t(lang, section.descriptionKey)}</span></p>
          </div>

          <button type="button" onClick={(event) => { paletteTrigger.current = event.currentTarget; setPaletteOpen(true); }} className="hidden h-9 min-w-48 items-center gap-2 rounded-md border border-[#D9D9D6] bg-[#F7F7F5] px-3 text-left text-sm text-[#6B7280] transition hover:border-[#C8C8C4] sm:flex">
            <Search className="h-4 w-4" />
            <span className="flex-1">{t(lang, "cc_jump")}</span>
            <kbd className="rounded border border-[#D9D9D6] bg-white px-1.5 py-0.5 text-[10px]">⌘K</kbd>
          </button>

          <button className="cc-button shrink-0" onClick={() => setLang(lang === "en" ? "zh" : "en")} aria-label={t(lang, "language_toggle_label")}>{lang === "en" ? "中文" : "English"}</button>
          {route?.countryScope !== "none" ? (
            <JurisdictionPicker
              countries={countries ?? []}
              selectedId={countryId}
              loading={isLoading}
              lang={lang}
              onSelect={setCountryContext}
            />
          ) : (
            <span className="inline-flex rounded-md border border-[#E5E5E2] bg-[#F7F7F5] px-2.5 py-1.5 text-xs font-medium text-[#6B7280]">{t(lang, "cc_global")}</span>
          )}
        </div>
      </header>

      {paletteOpen ? (
        <div className="fixed inset-0 z-[70] flex items-start justify-center bg-black/30 px-4 pt-[12vh]" onMouseDown={() => setPaletteOpen(false)}>
          <div ref={paletteRef} role="dialog" aria-modal="true" aria-labelledby="command-palette-title" className="w-full max-w-xl overflow-hidden rounded-lg border border-[#D9D9D6] bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
            <h2 id="command-palette-title" className="sr-only">{t(lang, "cc_jump")}</h2>
            <div className="flex items-center gap-3 border-b border-[#E5E5E2] px-4">
              <Search className="h-5 w-5 text-[#6B7280]" />
              <label htmlFor="command-palette-search" className="sr-only">{t(lang, "cc_search")}</label>
              <input id="command-palette-search" name="command-palette-search" type="search" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t(lang, "cc_search")} className="h-14 flex-1 bg-transparent text-sm outline-none" />
              <button type="button" onClick={() => setPaletteOpen(false)} className="rounded p-1 text-[#6B7280] hover:bg-[#F7F7F5]" aria-label={t(lang, "cc_close_jump")}><X className="h-4 w-4" /></button>
            </div>
            <div className="max-h-[55vh] overflow-y-auto p-2">
              {filteredRoutes.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => { setPaletteOpen(false); setQuery(""); router.push(item.href); }}
                    className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left hover:bg-[#F7F7F5]"
                  >
                    <span className="flex h-8 w-8 items-center justify-center rounded-md border border-[#E5E5E2] bg-white text-[#C2410C]"><Icon className="h-4 w-4" /></span>
                    <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-[#1D1D1F]">{t(lang, item.labelKey)}</span><span className="block truncate text-xs text-[#6B7280]">{t(lang, findSectionByPath(item.href).descriptionKey)}</span></span>
                  </button>
                );
              })}
              {!filteredRoutes.length ? <p className="px-3 py-8 text-center text-sm text-[#6B7280]">{t(lang, "cc_no_tools")}</p> : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
