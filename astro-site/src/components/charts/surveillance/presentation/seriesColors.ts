import { fingerprint } from "../domain/types";

const palette = [
  "#0072b2", "#d55e00", "#009e73", "#cc79a7", "#e69f00",
  "#56b4e9", "#7f3c8d", "#777777", "#bc5090", "#4e79a7",
];

export function seriesColor(id: string) {
  return palette[parseInt(fingerprint(id), 36) % palette.length];
}

// Catalog order gives each region a persistent color across filters and shared links.
export function regionColor(index: number) {
  if (index < palette.length) return palette[index];
  const hue = Math.round((index * 137.508) % 360);
  return `hsl(${hue}, 58%, ${index % 2 ? 45 : 58}%)`;
}

export function axisColors(dark: boolean) {
  return { ink: dark ? "#a4b1c1" : "#556070", grid: dark ? "#344a64" : "#d9d2c7" };
}
