"use client";

import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from "@headlessui/react";
import { Check, ChevronsUpDown, Globe2, MapPin, Search } from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import {
  getCountryDisplayName,
  getCountrySearchText,
  isCountrySubdivision,
  type Country,
} from "@/shared/config/countries";
import { cn } from "@/lib/utils";

export interface JurisdictionGroups {
  countries: Country[];
  subdivisions: Country[];
}

export function groupJurisdictions(
  countries: Country[],
  query: string,
  lang: "en" | "zh",
): JurisdictionGroups {
  const normalized = query.trim().toLocaleLowerCase();
  const exactCode = countries.find(
    (country) => country.code.toLocaleLowerCase() === normalized,
  )?.code;
  const matches = (country: Country) =>
    !normalized || (exactCode ? country.code === exactCode : getCountrySearchText(country).includes(normalized));
  const byName = (left: Country, right: Country) =>
    getCountryDisplayName(left, lang).localeCompare(getCountryDisplayName(right, lang), lang === "zh" ? "zh-CN" : "en");

  return {
    countries: countries.filter((country) => !isCountrySubdivision(country) && matches(country)).sort(byName),
    subdivisions: countries.filter((country) => isCountrySubdivision(country) && matches(country)).sort(byName),
  };
}

function JurisdictionOption({ country, lang }: { country: Country; lang: "en" | "zh" }) {
  const subdivision = isCountrySubdivision(country);
  const displayName = getCountryDisplayName(country, lang);
  return (
    <ComboboxOption
      value={country}
      aria-label={`${displayName} (${country.code})`}
      data-testid={`jurisdiction-option-${country.code}`}
      className="group flex cursor-default items-center gap-3 rounded-md px-3 py-2.5 text-sm outline-none data-[focus]:bg-[#FFF3E8]"
    >
      <span
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-md border",
          subdivision
            ? "border-orange-200 bg-orange-50 text-[#B54708]"
            : "border-[#E5E5E2] bg-[#F7F7F5] text-[#52606F]",
        )}
      >
        {subdivision ? <MapPin className="h-4 w-4" /> : <Globe2 className="h-4 w-4" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-[#1D1D1F]">
          {displayName}
        </span>
        <span className="block truncate text-xs text-[#6B7280]">
          {subdivision
            ? `${lang === "zh" ? "省 / 州级地区" : "Province / state-level region"} · ${country.code}`
            : country.code}
        </span>
      </span>
      <Check className="h-4 w-4 shrink-0 text-[#B54708] opacity-0 group-data-[selected]:opacity-100" />
    </ComboboxOption>
  );
}

export function JurisdictionPicker({
  countries,
  selectedId,
  loading,
  lang,
  onSelect,
}: {
  countries: Country[];
  selectedId: number | null;
  loading: boolean;
  lang: "en" | "zh";
  onSelect: (country: Country) => void;
}) {
  const [query, setQuery] = useState("");
  const selected = countries.find((country) => country.id === selectedId) ?? null;
  const groups = useMemo(
    () => groupJurisdictions(countries, query, lang),
    [countries, lang, query],
  );
  const resultCount = groups.countries.length + groups.subdivisions.length;
  const subdivisionSections = useMemo(() => {
    const parents = new Map(countries.map((country) => [country.code.toUpperCase(), country]));
    const sections = new Map<string, { parentName: string; items: Country[] }>();
    for (const country of groups.subdivisions) {
      const parentCode = (country.parent_code || country.code.split("-", 1)[0]).toUpperCase();
      const parent = parents.get(parentCode);
      const parentName = parent ? getCountryDisplayName(parent, lang) : parentCode;
      const section = sections.get(parentCode) ?? { parentName, items: [] };
      section.items.push(country);
      sections.set(parentCode, section);
    }
    return Array.from(sections.entries()).sort((left, right) => left[1].parentName.localeCompare(right[1].parentName));
  }, [countries, groups.subdivisions, lang]);

  return (
    <Combobox
      value={selected}
      by="id"
      disabled={loading || countries.length === 0}
      onChange={(country) => {
        if (country) onSelect(country);
      }}
      onClose={() => setQuery("")}
    >
      {({ open }) => (
        <div className="relative">
          <ComboboxButton
            aria-label={lang === "zh" ? "当前国家或地区" : "Active country or region"}
            className="flex h-10 max-w-[220px] items-center gap-2 rounded-md border border-[#D9D9D6] bg-white px-2.5 text-left transition hover:border-[#B8B8B4] hover:bg-[#FAFAF9] disabled:cursor-not-allowed disabled:opacity-60 sm:min-w-[184px]"
          >
            {selected && isCountrySubdivision(selected) ? (
              <MapPin className="h-4 w-4 shrink-0 text-[#B54708]" />
            ) : (
              <Globe2 className="h-4 w-4 shrink-0 text-[#52606F]" />
            )}
            <span className="min-w-0 flex-1">
              <span className="hidden text-[10px] font-semibold uppercase tracking-[0.08em] text-[#7A858F] sm:block">
                {lang === "zh" ? "国家 / 地区" : "Country / region"}
              </span>
              <span className="block truncate text-sm font-semibold text-[#1D1D1F]">
                {selected
                  ? getCountryDisplayName(selected, lang)
                  : loading
                    ? (lang === "zh" ? "加载中" : "Loading")
                    : (lang === "zh" ? "暂无地区" : "No regions")}
              </span>
            </span>
            <ChevronsUpDown className="h-4 w-4 shrink-0 text-[#6B7280]" />
          </ComboboxButton>

          {open ? (
            <div className="absolute right-0 top-full z-[80] mt-2 w-[min(92vw,420px)] overflow-hidden rounded-lg border border-[#D9D9D6] bg-white shadow-2xl">
              <div className="border-b border-[#E5E5E2] bg-white p-3">
                <div className="flex items-center gap-2 rounded-md border border-[#CFCFCA] bg-[#F7F7F5] px-3 focus-within:border-[#175CD3] focus-within:ring-2 focus-within:ring-blue-100">
                  <Search className="h-4 w-4 shrink-0 text-[#6B7280]" />
                  <ComboboxInput
                    autoFocus
                    aria-label={lang === "zh" ? "搜索国家、地区或代码" : "Search countries, regions, or codes"}
                    placeholder={lang === "zh" ? "搜索名称或代码，例如上海、CN-SH" : "Search name or code, e.g. Shanghai, CN-SH"}
                    displayValue={() => query}
                    onChange={(event) => setQuery(event.target.value)}
                    className="h-10 min-w-0 flex-1 bg-transparent text-sm text-[#1D1D1F] outline-none placeholder:text-[#7A858F]"
                  />
                  <kbd className="hidden rounded border border-[#D9D9D6] bg-white px-1.5 py-0.5 text-[10px] text-[#6B7280] sm:inline">esc</kbd>
                </div>
              </div>

              <ComboboxOptions static className="max-h-[min(62vh,520px)] overflow-y-auto p-2 outline-none">
                {groups.countries.length > 0 ? (
                  <>
                    <div className="sticky top-0 z-10 flex items-center justify-between bg-white/95 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#6B7280] backdrop-blur">
                      <span>{lang === "zh" ? "国家和独立地区" : "Countries & regions"}</span>
                      <span>{groups.countries.length}</span>
                    </div>
                    {groups.countries.map((country) => (
                      <JurisdictionOption key={country.id} country={country} lang={lang} />
                    ))}
                  </>
                ) : null}

                {subdivisionSections.map(([parentCode, section]) => (
                  <Fragment key={parentCode}>
                    <div className="sticky top-0 z-10 mt-1 flex items-center justify-between border-t border-[#ECECEA] bg-white/95 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#6B7280] backdrop-blur">
                      <span>{section.parentName} · {lang === "zh" ? "省 / 州级地区" : "Province / state-level"}</span>
                      <span>{section.items.length}</span>
                    </div>
                    {section.items.map((country) => (
                      <JurisdictionOption key={country.id} country={country} lang={lang} />
                    ))}
                  </Fragment>
                ))}

                {resultCount === 0 ? (
                  <div className="px-4 py-10 text-center">
                    <Search className="mx-auto h-5 w-5 text-[#9AA1A9]" />
                    <p className="mt-2 text-sm font-medium text-[#374151]">
                      {lang === "zh" ? "没有匹配的国家或地区" : "No matching country or region"}
                    </p>
                    <p className="mt-1 text-xs text-[#6B7280]">
                      {lang === "zh" ? "可搜索中文、英文或 ISO 代码" : "Search by name or ISO code"}
                    </p>
                  </div>
                ) : null}
              </ComboboxOptions>

              <div className="flex items-center justify-between border-t border-[#E5E5E2] bg-[#F7F7F5] px-4 py-2 text-xs text-[#6B7280]">
                <span>{lang === "zh" ? `${countries.length} 个可用地区` : `${countries.length} available jurisdictions`}</span>
                <span>{lang === "zh" ? "↑↓ 选择 · Enter 确认" : "↑↓ select · Enter confirm"}</span>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </Combobox>
  );
}
