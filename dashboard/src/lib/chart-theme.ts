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

export const CHART_DEFAULTS: Record<string, unknown> = {
  tooltip: {
    trigger: "axis",
    backgroundColor: "rgba(255, 255, 255, 0.97)",
    borderColor: "#d9dfd9",
    borderWidth: 1,
    textStyle: { color: "#17211f", fontSize: 12 },
    padding: [8, 12],
    extraCssText: "border-radius: 8px; box-shadow: 0 10px 24px rgba(23,33,31,0.12);",
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
    textStyle: { fontSize: 12, color: "#5f6f6a" },
    icon: "roundRect",
    itemWidth: 12,
    itemHeight: 8,
    itemGap: 16,
  },
};

/** Merge user options with theme defaults */
export function withTheme(option: Record<string, unknown>): Record<string, unknown> {
  const defaultTooltip = CHART_DEFAULTS.tooltip as Record<string, unknown>;
  const userTooltip = (option.tooltip ?? {}) as Record<string, unknown>;
  return {
    color: CHART_COLORS,
    ...CHART_DEFAULTS,
    ...option,
    tooltip: { ...defaultTooltip, ...userTooltip },
  };
}
