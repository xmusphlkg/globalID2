import { describe, expect, it } from "vitest";
import { defaultForm, toForm, defaultFillMissingForCountry, defaultIncludeCurrentMonthForCountry, defaultRevisionWindowForCountry, findCountryTimezone } from "./form-model";
import { intervalMinutesToFields, intervalFieldsToMinutes, scheduleLabel } from "./schedule-model";
import type { AutomationJob, CountrySourceConfig } from "./api";
import type { Country } from "@/shared/config/countries";

describe("automation scheduling compatibility", () => {
  it("round-trips hour, minute and fractional intervals and rejects invalid form values", () => {
    for (const minutes of [15, 60, 90, 180, 0.5]) {
      const fields = intervalMinutesToFields(minutes);
      expect(intervalFieldsToMinutes(fields.value, fields.unit)).toBe(minutes);
    }
    for (const value of ["", "0", "-1", "Infinity", "oops"]) expect(intervalFieldsToMinutes(value, "hours")).toBeNull();
    expect(scheduleLabel({ daily_time: "08:00", timezone: "Europe/London" }, "en")).toBe("Daily at 08:00 (Europe/London)");
    expect(findCountryTimezone([{ code: "GB", timezone: "Europe/London" } as Country], "GB")).toBe("Europe/London");
  });

  it("preserves explicit false settings and source revision policy precedence", () => {
    const form = toForm({ ...defaultForm, enabled: false, process: false, save_raw: false, fill_missing: false, revision_window_months: 0, retry_threshold: 0, interval_minutes: 0 } as AutomationJob);
    expect(form).toMatchObject({ enabled: false, process: false, save_raw: false, fill_missing: false, revision_window_months: 0, retry_threshold: 0, interval_minutes: 0 });
    const config = { default_fill_missing: false, source_policy: { default_include_current_month: true, default_revision_window: 8, default_revision_window_months: 4 } } as CountrySourceConfig;
    expect(defaultFillMissingForCountry(config)).toBe(false);
    expect(defaultIncludeCurrentMonthForCountry(config)).toBe(true);
    expect(defaultRevisionWindowForCountry(config)).toBe(8);
    expect(defaultRevisionWindowForCountry()).toBe(3);
    expect(defaultRevisionWindowForCountry({ source_policy: { default_revision_window: 0 } } as CountrySourceConfig)).toBe(1);
  });
});
