/** Display formatting. Every formatter renders null/undefined as an em dash. */

const NONE = "—";

type Maybe<T> = T | null | undefined;

/** 0.42 → "420 ms", 12.3 → "12.3 s", 245 → "4m 05s", 3720 → "1h 02m". */
export function formatDuration(seconds: Maybe<number>): string {
  if (seconds == null || !Number.isFinite(seconds)) return NONE;
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

/** Binary multiples with decimal-looking units: 1536 → "1.5 KB". */
export function formatBytes(bytes: Maybe<number>): string {
  if (bytes == null || !Number.isFinite(bytes)) return NONE;
  let value = bytes;
  let unit = 0;
  while (Math.abs(value) >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${UNITS[unit]}`;
}

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

/** Local date and time, e.g. "Oct 19, 2026, 4:05 PM". */
export function formatDateTime(iso: Maybe<string>): string {
  if (!iso) return NONE;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? NONE : dateTime.format(date);
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 7],
  ["week", 4.35],
  ["month", 12],
  ["year", Infinity],
];

/** Relative to `now`, e.g. "5 minutes ago", "yesterday". */
export function formatRelative(iso: Maybe<string>, now: Date = new Date()): string {
  if (!iso) return NONE;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return NONE;
  let value = (date.getTime() - now.getTime()) / 1000;
  for (const [unit, size] of STEPS) {
    if (Math.abs(value) < size) return relative.format(Math.round(value), unit);
    value /= size;
  }
  return NONE;
}

/** First `length` characters of a commit sha or fingerprint. */
export function shortSha(sha: Maybe<string>, length = 7): string {
  return sha ? sha.slice(0, length) : NONE;
}

/** A ratio as a percentage: 0.1234 → "12%" (digits = 0), "12.3%" (digits = 1). */
export function formatPercent(ratio: Maybe<number>, digits = 0): string {
  if (ratio == null || !Number.isFinite(ratio)) return NONE;
  return `${(ratio * 100).toFixed(digits)}%`;
}
