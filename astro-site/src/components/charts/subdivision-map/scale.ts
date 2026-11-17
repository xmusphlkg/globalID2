export interface MapScale {
  maximum: number;
  ticks: Array<{ value: number; position: number }>;
}

/** R pretty's 1/2/5/10 unit selection, with its default larger-unit biases.
 * Regional legends additionally require integer ticks (unit >= 1).
 * Reference: https://svn.r-project.org/R/trunk/src/appl/pretty.c
 */
function prettyUnit(cell: number): number {
  cell = Math.max(1, cell);
  const base = 10 ** Math.floor(Math.log10(cell));
  let unit = base;
  if (2 * base - cell < 1.5 * (cell - unit)) {
    unit = 2 * base;
    if (5 * base - cell < 2.75 * (cell - unit)) {
      unit = 5 * base;
      if (10 * base - cell < 1.5 * (cell - unit)) unit = 10 * base;
    }
  }
  return unit;
}

/** Integer pretty bounds cover the data; fractional observations stay intact. */
export function prettyBreaks(maximum: number, intervals = 5): number[] {
  if (!Number.isFinite(maximum) || maximum <= 0) return [0, 1];
  const unit = prettyUnit(maximum / Math.max(1, intervals));
  const count = Math.ceil(maximum / unit - 1e-10);
  const upper = Math.max(1, count) * unit;
  if (!Number.isFinite(upper)) return [0, maximum];
  // Correct tolerance only when it would otherwise exclude an actual value.
  const end = upper < maximum ? upper + unit : upper;
  if (!Number.isFinite(end)) return [0, maximum];
  return Array.from({ length: Math.round(end / unit) + 1 }, (_, index) => index * unit);
}

export const scaleValue = (value: number): number => Math.log1p(value);

/** Continuous log1p colours, like the seasonal heatmap. Pretty integer values
 * are chosen across the transformed range, then positioned using their exact
 * transform. Rounding a label never moves it to an incorrect colour/value.
 */
export function continuousScale(maximum: number, tickCount = 6): MapScale {
  const upper = prettyBreaks(maximum).at(-1)!;
  const extent = scaleValue(upper);
  const values = new Set([0, upper]);
  for (let index = 1; index < tickCount - 1; index++) {
    const value = prettyUnit(Math.expm1(extent * index / (tickCount - 1)));
    if (value < upper) values.add(value);
  }
  return { maximum: upper, ticks: [...values].sort((a, b) => a - b).map(value => ({ value, position: scaleValue(value) / extent })) };
}

/** The legend lives in the chart canvas so PNG exports retain its exact scale. */
export function scaleGraphic(scale: MapScale, colors: readonly string[], text: string, border: string, missingColor: string, missingLabel: string, format: (value: number) => string) {
  const width = 220;
  return [{ type: 'group', id: 'regional-scale', left: 12, bottom: 8, silent: true, children: [
    { type: 'rect', shape: { x: 0, y: 0, width, height: 10 }, style: { stroke: border, lineWidth: .6, fill: { type: 'linear', x: 0, y: 0, x2: 1, y2: 0, colorStops: colors.map((color, index) => ({ offset: index / (colors.length - 1), color })) } } },
    ...scale.ticks.flatMap((tick, index) => [
      { type: 'line', shape: { x1: tick.position * width, y1: 10, x2: tick.position * width, y2: 15 }, style: { stroke: border, lineWidth: 1 } },
      { type: 'text', style: { x: tick.position * width, y: 19, text: format(tick.value), align: index === 0 ? 'left' : index === scale.ticks.length - 1 ? 'right' : 'center', verticalAlign: 'top', fontSize: 10, fill: text } },
    ]),
    { type: 'rect', shape: { x: 0, y: 38, width: 11, height: 10 }, style: { fill: missingColor } },
    { type: 'text', style: { x: 17, y: 38, text: missingLabel, verticalAlign: 'top', fontSize: 10, fill: text } },
  ] }];
}
