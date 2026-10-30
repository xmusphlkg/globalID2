import { afterEach, expect, it } from "vitest";
import { useAppStore } from "./app-store";
afterEach(() => useAppStore.setState({ lang: "en" }));
it("switches and persists the selected interface language", () => {
  useAppStore.getState().setLang("zh");
  expect(useAppStore.getState().lang).toBe("zh");
  expect(JSON.parse(localStorage.getItem("globalid-dashboard-store")!).state.lang).toBe("zh");
  useAppStore.getState().setLang("en");
  expect(useAppStore.getState().lang).toBe("en");
});
