import { describe, expect, it } from "vitest";

import { getSourceDisplayLabel, getSourceOptionsForCountry } from "./source-labels";

describe("Colombia source labels", () => {
  it("keeps historical and successor datasets distinct", () => {
    expect(getSourceDisplayLabel("co_ins_sivigila_historical", "en", "CO"))
      .toContain("Historical");
    expect(getSourceDisplayLabel("co_ins_sivigila_current", "en", "CO"))
      .toContain("2023 onward");
    expect(getSourceDisplayLabel("co_ins_bes_weekly", "en", "CO"))
      .toContain("BES Epidemiological Bulletins");
    expect(getSourceDisplayLabel("all", "zh", "CO")).toBe("全部来源");
    expect(getSourceOptionsForCountry("CO", "en").map((option) => option.value))
      .toEqual(["co_ins_sivigila_historical", "co_ins_sivigila_current", "co_ins_bes_weekly"]);
  });
});
