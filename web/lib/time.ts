// Date helpers built on Intl only. The API speaks UTC ("…Z"); we render in the
// user's `tz` preference, falling back to the browser's zone.

export function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function fmt(tz: string, opts: Intl.DateTimeFormatOptions, locale?: string): Intl.DateTimeFormat {
  const key = `${locale ?? ""}|${tz}|${JSON.stringify(opts)}`;
  let f = fmtCache.get(key);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat(locale, { ...opts, timeZone: tz });
    } catch {
      f = new Intl.DateTimeFormat(locale, { ...opts, timeZone: "UTC" });
    }
    fmtCache.set(key, f);
  }
  return f;
}

export function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Fri, 4 Dec 2026, 22:00" */
export function formatDateTime(iso: string | null | undefined, tz: string): string {
  const d = parse(iso);
  if (!d) return "—";
  return fmt(tz, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(d);
}

/** "4 Dec, 22:00" (year added when it isn't the current one) */
export function formatShort(iso: string | null | undefined, tz: string, now = new Date()): string {
  const d = parse(iso);
  if (!d) return "—";
  const sameYear = dayParts(d, tz).y === dayParts(now, tz).y;
  return fmt(tz, {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(d);
}

/** "4 Dec 2026" */
export function formatDate(iso: string | null | undefined, tz: string): string {
  const d = parse(iso);
  if (!d) return "—";
  return fmt(tz, { day: "numeric", month: "short", year: "numeric" }).format(d);
}

export function formatTime(iso: string | null | undefined, tz: string): string {
  const d = parse(iso);
  if (!d) return "";
  return fmt(tz, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d);
}

/** "in 3d 4h", "in 45m", "2h 10m ago", "now" */
export function relative(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parse(iso);
  if (!d) return "";
  let secs = Math.round((d.getTime() - now.getTime()) / 1000);
  const past = secs < 0;
  secs = Math.abs(secs);
  if (secs < 60) return "now";
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  let s: string;
  if (days >= 60) s = `${Math.round(days / 30)}mo`;
  else if (days > 0) s = hours ? `${days}d ${hours}h` : `${days}d`;
  else if (hours > 0) s = mins ? `${hours}h ${mins}m` : `${hours}h`;
  else s = `${mins}m`;
  return past ? `${s} ago` : `in ${s}`;
}

export type Urgency = "past" | "critical" | "soon" | "later";

/** < 24h → critical, < 7d → soon. */
export function urgency(iso: string | null | undefined, now: Date = new Date()): Urgency {
  const d = parse(iso);
  if (!d) return "later";
  const h = (d.getTime() - now.getTime()) / 3_600_000;
  if (h < 0) return "past";
  if (h < 24) return "critical";
  if (h < 24 * 7) return "soon";
  return "later";
}

export interface DayParts {
  y: number;
  m: number; // 1-12
  d: number;
  hh: number;
  mm: number;
}

export function dayParts(date: Date, tz: string): DayParts {
  const parts = fmt(
    tz,
    { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" },
    "en-US",
  ).formatToParts(date);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return { y: get("year"), m: get("month"), d: get("day"), hh: get("hour") % 24, mm: get("minute") };
}

/** "YYYY-MM-DD" for the calendar day `iso` falls on in `tz`. */
export function dayKey(isoOrDate: string | Date, tz: string): string {
  const d = typeof isoOrDate === "string" ? parse(isoOrDate) : isoOrDate;
  if (!d) return "";
  const p = dayParts(d, tz);
  return ymd(p.y, p.m, p.d);
}

export function ymd(y: number, m: number, d: number): string {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** Offset of `tz` from UTC at instant `ms`, in ms. */
function tzOffset(ms: number, tz: string): number {
  const p = dayParts(new Date(ms), tz);
  const asUtc = Date.UTC(p.y, p.m - 1, p.d, p.hh, p.mm);
  return asUtc - Math.floor(ms / 60000) * 60000;
}

/** Wall-clock time in `tz` → UTC ISO string with Z. */
export function zonedToUtcIso(y: number, m: number, d: number, hh: number, mm: number, tz: string): string {
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  let ms = guess - tzOffset(guess, tz);
  const off2 = tzOffset(ms, tz);
  ms = guess - off2;
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** "<input type=date>" value → 23:59 that day in `tz`, as UTC ISO. */
export function endOfDayIso(dateValue: string, tz: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateValue);
  if (!m) return null;
  return zonedToUtcIso(Number(m[1]), Number(m[2]), Number(m[3]), 23, 59, tz);
}

export function monthLabel(y: number, m: number): string {
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(y, m - 1, 15)),
  );
}

export function weekdayLabels(): string[] {
  // Monday-first, matching ISO weeks.
  const f = new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" });
  return Array.from({ length: 7 }, (_, i) => f.format(new Date(Date.UTC(2024, 0, 1 + i))));
}

export function listTimeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  try {
    const zones = intl.supportedValuesOf?.("timeZone");
    if (zones && zones.length) return zones.includes("UTC") ? zones : ["UTC", ...zones];
  } catch {
    /* older browsers */
  }
  return ["UTC", browserTz()];
}
