"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ChevronRight, Globe2, Menu, PanelLeftClose, PanelLeftOpen, Search, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { getCountryDisplayName, useCountries } from "@/shared/config/countries";
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
  const { countryId, countryCode, setCountry } = useAppStore();
  const { data: countries, isLoading } = useCountries();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
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
    return allRoutes.filter((item) => `${item.label} ${item.description}`.toLowerCase().includes(normalized));
  }, [query]);

  const setCountryContext = (id: number) => {
    const country = countries?.find((item) => item.id === id);
    if (!country) return;
    setCountry(country.id, country.name_en || country.name, country.code);
    const params = new URLSearchParams(searchParams.toString());
    params.set("country", country.code.toUpperCase());
    router.replace(`${pathname}?${params.toString()}`);
  };

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-[#D9D9D6] bg-white/95 backdrop-blur">
        <div className="flex h-16 items-center gap-3 px-4 sm:px-6">
          <button type="button" onClick={onMenuClick} className="icon-button lg:hidden" aria-label="Open navigation">
            <Menu className="h-5 w-5" />
          </button>
          <button type="button" onClick={onToggleSidebar} className="icon-button hidden lg:inline-flex" aria-label={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}>
            {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </button>

          <div className="min-w-0 flex-1">
            <div className="hidden min-w-0 items-center gap-1.5 text-xs text-[#6B7280] sm:flex">
              <span className="truncate">{section.title}</span>
              <ChevronRight className="h-3 w-3 shrink-0" />
              <span className="truncate font-medium text-[#374151]">{route?.label ?? "Control Center"}</span>
            </div>
            <p className="mt-0.5 truncate text-sm font-semibold text-[#1D1D1F]"><span className="sm:hidden">{route?.label ?? section.title}</span><span className="hidden sm:inline">{route?.description ?? section.description}</span></p>
          </div>

          <button type="button" onClick={() => setPaletteOpen(true)} className="hidden h-9 min-w-48 items-center gap-2 rounded-md border border-[#D9D9D6] bg-[#F7F7F5] px-3 text-left text-sm text-[#6B7280] transition hover:border-[#C8C8C4] sm:flex">
            <Search className="h-4 w-4" />
            <span className="flex-1">Jump to...</span>
            <kbd className="rounded border border-[#D9D9D6] bg-white px-1.5 py-0.5 text-[10px]">⌘K</kbd>
          </button>

          {route?.countryScope !== "none" ? (
            <label className="flex h-9 items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-2.5 text-sm">
              <Globe2 className="h-4 w-4 text-[#6B7280]" />
              <span className="sr-only">Active country</span>
              <select
                id="active-country"
                name="active-country"
                value={countryId ?? ""}
                disabled={isLoading || !countries?.length}
                onChange={(event) => setCountryContext(Number(event.target.value))}
                className="max-w-36 bg-transparent pr-5 font-medium text-[#1D1D1F] outline-none"
              >
                {!countries?.length ? <option value="">No countries</option> : null}
                {countries?.map((country) => (
                  <option key={country.id} value={country.id}>{getCountryDisplayName(country, "en")}</option>
                ))}
              </select>
            </label>
          ) : (
            <span className="inline-flex rounded-md border border-[#E5E5E2] bg-[#F7F7F5] px-2.5 py-1.5 text-xs font-medium text-[#6B7280]">Global scope</span>
          )}
        </div>
      </header>

      {paletteOpen ? (
        <div className="fixed inset-0 z-[70] flex items-start justify-center bg-black/30 px-4 pt-[12vh]" onMouseDown={() => setPaletteOpen(false)}>
          <div role="dialog" aria-modal="true" aria-labelledby="command-palette-title" className="w-full max-w-xl overflow-hidden rounded-lg border border-[#D9D9D6] bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
            <h2 id="command-palette-title" className="sr-only">Jump to a control center workspace</h2>
            <div className="flex items-center gap-3 border-b border-[#E5E5E2] px-4">
              <Search className="h-5 w-5 text-[#6B7280]" />
              <label htmlFor="command-palette-search" className="sr-only">Search workspaces and tools</label>
              <input id="command-palette-search" name="command-palette-search" type="search" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search workspaces and tools" className="h-14 flex-1 bg-transparent text-sm outline-none" />
              <button type="button" onClick={() => setPaletteOpen(false)} className="rounded p-1 text-[#6B7280] hover:bg-[#F7F7F5]" aria-label="Close jump menu"><X className="h-4 w-4" /></button>
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
                    <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-[#1D1D1F]">{item.label}</span><span className="block truncate text-xs text-[#6B7280]">{item.description}</span></span>
                  </button>
                );
              })}
              {!filteredRoutes.length ? <p className="px-3 py-8 text-center text-sm text-[#6B7280]">No matching tools.</p> : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
