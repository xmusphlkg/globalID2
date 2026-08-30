import { describe, expect, it } from "vitest";

import { groupJurisdictions } from "./JurisdictionPicker";
import type { Country } from "@/shared/config/countries";

const countries: Country[] = [
  {
    id: 1,
    code: "CN",
    name: "China",
    name_en: "China",
    name_zh: "中国",
    name_local: "中国",
    language: "zh-CN",
    timezone: "Asia/Shanghai",
    is_active: true,
    location_type: "country",
  },
  {
    id: 2,
    code: "CN-SH",
    name: "Shanghai, China",
    name_en: "Shanghai, China",
    name_zh: "上海市",
    name_local: "上海市",
    language: "zh-CN",
    timezone: "Asia/Shanghai",
    is_active: true,
    location_type: "subdivision",
    parent_code: "CN",
  },
  {
    id: 3,
    code: "US",
    name: "United States",
    name_en: "United States",
    name_zh: "美国",
    name_local: "United States",
    language: "en",
    timezone: "UTC",
    is_active: true,
  },
];

describe("groupJurisdictions", () => {
  it("separates countries from child jurisdictions", () => {
    const grouped = groupJurisdictions(countries, "", "en");
    expect(grouped.countries.map((country) => country.code)).toEqual(["CN", "US"]);
    expect(grouped.subdivisions.map((country) => country.code)).toEqual(["CN-SH"]);
  });

  it("searches English, Chinese, and ISO codes", () => {
    expect(groupJurisdictions(countries, "上海", "zh").subdivisions[0]?.code).toBe("CN-SH");
    expect(groupJurisdictions(countries, "cn-sh", "en").subdivisions[0]?.code).toBe("CN-SH");
    expect(groupJurisdictions(countries, "united", "en").countries[0]?.code).toBe("US");
  });

  it("prefers an exact ISO code over incidental name matches", () => {
    const grouped = groupJurisdictions(countries, "US", "en");
    expect(grouped.countries.map((country) => country.code)).toEqual(["US"]);
    expect(grouped.subdivisions).toEqual([]);
  });
});
