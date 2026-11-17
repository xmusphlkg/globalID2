import type { ReportingPeriod, SituationAlertRecord } from '../../utils/situationTypes.ts';

interface Resolution {
  period: ReportingPeriod | null;
  /** A textual annual total is needed before equating total and confirmed. */
  annual: boolean;
  /** Preserve ambiguous time evidence so equal numbers cannot erase it. */
  unresolved?: string;
  invalid?: boolean;
}

const normalize = (value: string) => value.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const MONTH = '(january|february|march|april|may|june|july|august|september|october|november|december|jan\\.?|feb\\.?|mar\\.?|apr\\.?|jun\\.?|jul\\.?|aug\\.?|sep\\.?|sept\\.?|oct\\.?|nov\\.?|dec\\.?)';
// "New" is also part of place names. Require a count noun and do not cross
// reporting verbs, while retaining phrases such as "new laboratory-confirmed cases".
const INCIDENT = /\b(?:new(?:ly)?(?:\s+(?!(?:reported|reports|reporting|has|have|was|were|is|are|recorded|records|recording)\b)[a-z0-9-]+){0,3}\s+(?:cases?|deaths?|infections?|fatalities)|additional|incident|daily|weekly|monthly|this week|last week|previous update|(?:last|past) \d+ days?)\b/;
const TIME_EVIDENCE = /\b(?:20\d{2}|since|between|from|during|daily|weekly|monthly|year|last|past|previous update|january|february|march|april|may|june|july|august|september|october|november|december)\b/;

function validDay(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function validatePeriod(value: unknown): ReportingPeriod | null {
  if (!value || typeof value !== 'object') return null;
  const period = value as Partial<ReportingPeriod>;
  if ((period.kind !== 'cumulative' && period.kind !== 'incident') || !validDay(period.start)) return null;
  if (period.end !== undefined && (!validDay(period.end) || period.end < period.start)) return null;
  if (period.kind === 'incident' && !period.end) return null;
  return { kind: period.kind, start: period.start, ...(period.end ? { end: period.end } : {}) };
}

function monthDay(day: string, month: string, year: string): string | null {
  const index = MONTHS.findIndex(name => name.startsWith(month.replace('.', '').replace(/^sept$/, 'sep')));
  const value = `${year}-${String(index + 1).padStart(2, '0')}-${day.padStart(2, '0')}`;
  return index >= 0 && validDay(value) ? value : null;
}

function infer(text: string): Resolution {
  const clean = normalize(text);
  if (!clean) return { period: null, annual: false };
  const years = [...new Set(clean.match(/\b20\d{2}\b/g) ?? [])];
  const starts = [...clean.matchAll(/(?:since|from) (?:the )?(?:beginning|start) of (20\d{2})/g)].map(match => match[1]);
  const january = /\b(?:between|from|since) (?:1|01|first) (?:january|jan\.?)(?:\s|,)/.test(clean);
  const incident = INCIDENT.test(clean.replace(/\bno new\b/g, 'no further'));
  // Multiple years or mixed cumulative/new counts are not one proven annual datum.
  if ((starts.length || january) && (years.length !== 1 || starts.length > 1 || incident)) {
    return { period: null, annual: false, unresolved: clean };
  }
  if ((starts.length || january) && years.length === 1) {
    return { period: { kind: 'cumulative', start: `${years[0]}-01-01` }, annual: true };
  }
  if (incident) {
    const isoRanges = [...clean.matchAll(/\b(20\d{2}-\d{2}-\d{2})\s+(?:and|to|through|until|–|—)\s+(20\d{2}-\d{2}-\d{2})\b/g)];
    if (isoRanges.length > 1) return { period: null, annual: false, unresolved: clean };
    const isoRange = isoRanges[0];
    if (isoRange) {
      const period = validatePeriod({ kind: 'incident', start: isoRange[1], end: isoRange[2] });
      return { period, annual: false, ...(period ? {} : { unresolved: clean }) };
    }
    const ranges = [...clean.matchAll(new RegExp(`\\b(?:between|from) (\\d{1,2}) ${MONTH}(?: (20\\d{2}))? (?:and|to|through|until) (\\d{1,2}) ${MONTH} (20\\d{2})\\b`, 'g'))];
    if (ranges.length > 1) return { period: null, annual: false, unresolved: clean };
    const range = ranges[0];
    if (range) {
      const start = monthDay(range[1], range[2], range[3] || range[6]);
      const end = monthDay(range[4], range[5], range[6]);
      const period = validatePeriod({ kind: 'incident', start, end });
      return { period, annual: false, ...(period ? {} : { unresolved: clean }) };
    }
  }
  return { period: null, annual: false, ...(TIME_EVIDENCE.test(clean) || incident ? { unresolved: clean } : {}) };
}

/** Quarantine conflicting metadata from merging; individual reports remain visible. */
function consistentPeriod(period: ReportingPeriod, alert: SituationAlertRecord, annual: boolean): ReportingPeriod | null {
  if (!validDay(alert.published_date) || period.start > alert.published_date || (period.end && period.end > alert.published_date)) return null;
  if (alert.as_of) {
    if (!validDay(alert.as_of) || alert.as_of < period.start || alert.as_of > alert.published_date) return null;
    if (period.end && period.end !== alert.as_of) return null;
    if (annual && alert.as_of.slice(0, 4) !== period.start.slice(0, 4)) return null;
    if (period.kind === 'cumulative' && !period.end) return { ...period, end: alert.as_of };
  }
  return period;
}

/** Structured extraction wins; quoted time evidence wins over a conflicting summary. */
export function resolveReportingPeriod(alert: SituationAlertRecord): Resolution {
  const quoted = infer(alert.quote ?? '');
  const legacy = quoted.period || quoted.unresolved ? quoted : infer(alert.summary_en ?? '');
  const structured = alert.reporting_period;
  if (structured !== undefined && structured !== null) {
    const parsed = validatePeriod(structured);
    const period = parsed && consistentPeriod(parsed, alert, false);
    if (!period) return { period: null, annual: false, invalid: true };
    const provenAnnual = legacy.annual && legacy.period && consistentPeriod(legacy.period, alert, true);
    return { period, annual: period.kind === 'cumulative' && Boolean(provenAnnual) && legacy.period?.start === period.start };
  }
  if (legacy.period) {
    const period = consistentPeriod(legacy.period, alert, legacy.annual);
    return period ? { ...legacy, period } : { period: null, annual: false, invalid: true };
  }
  return legacy;
}
