/** Shared ECharts color palette and default options */
export const CHART_COLORS = [
  "#0f6b62", // brand green
  "#2563eb", // blue
  "#d97706", // amber
  "#16a34a", // green
  "#c24139", // red
  "#7c3aed", // violet
  "#0b7285", // cyan
  "#f97316", // orange
  "#64748b", // slate
  "#be185d", // magenta
];

export const CHART_TOKENS = {
  gridLine: "#d9dfd9",
  axisText: "#5f6f6a",
  text: "#17211f",
  neutral: "#697873",
  neutralSoft: "rgba(95, 111, 106, 0.2)",
  primary: "#0f6b62",
  primarySoft: "rgba(15, 107, 98, 0.12)",
  info: "#2563eb",
  infoSoft: "rgba(37, 99, 235, 0.12)",
  success: "#16a34a",
  successSoft: "rgba(22, 163, 74, 0.12)",
  warning: "#d97706",
  warningSoft: "rgba(217, 119, 6, 0.14)",
  destructive: "#c24139",
  destructiveSoft: "rgba(194, 65, 57, 0.12)",
  accentBlue: "#2563eb",
} as const;

export type ChartTheme = "light" | "dark";

function chartDefaults(theme: ChartTheme): Record<string, unknown> {
  const isDark = theme === "dark";
  const text = isDark ? "#cbd5e1" : "#17211f";
  const axisText = isDark ? "#94a3b8" : "#5f6f6a";
  const gridLine = isDark ? "#1e293b" : "#d9dfd9";
  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: isDark ? "rgba(15, 23, 42, 0.97)" : "rgba(255, 255, 255, 0.97)",
      borderColor: gridLine,
      borderWidth: 1,
      textStyle: { color: text, fontSize: 12 },
      padding: [8, 12],
      extraCssText: `border-radius: 8px; box-shadow: 0 10px 24px ${isDark ? "rgba(0,0,0,0.28)" : "rgba(23,33,31,0.12)"};`,
    },
    grid: {
      left: 60,
      right: 24,
      bottom: 50,
      top: 40,
      containLabel: true,
    },
    legend: {
      top: 0,
      textStyle: { fontSize: 12, color: axisText },
      icon: "roundRect",
      itemWidth: 12,
      itemHeight: 8,
      itemGap: 16,
    },
    textStyle: { color: text },
  };
}

function remapForDark(value: unknown): unknown {
  const replacements: Record<string, string> = {
    "#5d6978": "#94a3b8",
    "#5f6f6a": "#94a3b8",
    "#263647": "#cbd5e1",
    "#17211f": "#cbd5e1",
    "#e7ebf0": "#1e293b",
    "#d7dde5": "#334155",
    "#d9dfd9": "#1e293b",
    "#f7fafc": "#17283a",
    "rgba(255,255,255,0.28)": "rgba(255,255,255,0.06)",
  };
  if (typeof value === "string") return replacements[value] || value;
  if (Array.isArray(value)) return value.map(remapForDark);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, remapForDark(item)]),
    );
  }
  return value;
}

/** Merge user options with theme defaults */
export function withTheme(option: Record<string, unknown>, theme: ChartTheme = "light"): Record<string, unknown> {
  const defaults = chartDefaults(theme);
  const defaultTooltip = defaults.tooltip as Record<string, unknown>;
  const userTooltip = (option.tooltip ?? {}) as Record<string, unknown>;
  const merged = {
    color: CHART_COLORS,
    ...defaults,
    ...option,
    tooltip: { ...defaultTooltip, ...userTooltip },
  };
  return theme === "dark" ? (remapForDark(merged) as Record<string, unknown>) : merged;
}
