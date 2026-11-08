/** Never invent points or turn missing observations into zeros. */
export function curveSegments(cases: Array<number | null>, baseline: number, amplitude: number, dates?: string[]) {
  const max = cases.reduce<number>((value, point) => typeof point === 'number' && Number.isFinite(point) && point >= 0 ? Math.max(value, point) : value, 1);
  const segments: string[][] = [[]];
  const start = Date.parse(dates?.[0] ?? ''), end = Date.parse(dates?.at(-1) ?? '');
  cases.forEach((value, index) => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) { if (segments.at(-1)!.length) segments.push([]); return; }
    const date = Date.parse(dates?.[index] ?? '');
    const position = Number.isFinite(date) && end > start ? (date - start) / (end - start) : index / Math.max(1, cases.length - 1);
    segments.at(-1)!.push(`${(42 + position * 1356).toFixed(1)},${(baseline - value / max * amplitude).toFixed(1)}`);
  });
  return segments.filter(points => points.length > 1).map(points => points.join(' '));
}
