/** Shared ECharts color palette and default options */
export const CHART_COLORS = [
  "#0f766e", // teal
  "#14b8a6", // aqua teal
  "#0b7285", // deep cyan
  "#d97706", // amber
  "#22c55e", // green
  "#f97316", // orange
  "#2563eb", // blue
  "#e11d48", // rose
  "#7c3aed", // violet accent
  "#475569", // slate
];

export const CHART_TOKENS = {
  gridLine: "#d8e2df",
  axisText: "#5b7077",
  text: "#1d2d36",
  neutral: "#6f8188",
  neutralSoft: "rgba(91, 112, 119, 0.22)",
  primary: "#0f766e",
  primarySoft: "rgba(15, 118, 110, 0.12)",
  info: "#0b7285",
  infoSoft: "rgba(11, 114, 133, 0.12)",
  success: "#0d9488",
  successSoft: "rgba(13, 148, 136, 0.12)",
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
    borderColor: "#d8e2df",
    borderWidth: 1,
    textStyle: { color: "#1d2d36", fontSize: 12 },
    padding: [8, 12],
    extraCssText: "border-radius: 10px; box-shadow: 0 10px 24px rgba(17,34,39,0.12);",
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
    textStyle: { fontSize: 12, color: "#5b7077" },
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
