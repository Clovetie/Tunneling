export function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

let counter = 0;
export function uid(prefix = "id") {
  counter += 1;
  return `${prefix}_${Date.now().toString(36)}${counter.toString(36)}${Math.random()
    .toString(36)
    .slice(2, 6)}`;
}

export const TZ = "Asia/Bangkok";

export function now() {
  return new Date();
}

/** ISO string offset from now by `minutes`. */
export function iso(minutesFromNow: number) {
  return new Date(Date.now() + minutesFromNow * 60_000).toISOString();
}

export function hoursFromNow(h: number) {
  return iso(h * 60);
}

export function daysFromNow(d: number) {
  return iso(d * 24 * 60);
}

/** Local date string (YYYY-MM-DD) of a Date in the operator timezone. */
export function localDateKey(d: Date = new Date(), tz = TZ) {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: tz,
  }).format(d);
}

function wallParts(date: Date, tz: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return {
    y: get("year"),
    m: get("month"),
    d: get("day"),
    h: get("hour") % 24,
    min: get("minute"),
    s: get("second"),
  };
}

function tzOffsetMs(date: Date, tz: string) {
  const p = wallParts(date, tz);
  const asUtc = Date.UTC(p.y, p.m - 1, p.d, p.h, p.min, p.s);
  return asUtc - date.getTime();
}

/**
 * A wall-clock time in the operator's timezone, shifted by `dayOffset` days.
 * Two-pass offset resolution so it is correct on both sides of a DST edge and
 * independent of the machine's own local timezone (the sandbox is usually UTC).
 */
export function atLocalHour(dayOffset: number, hour: number, minute = 0, tz = TZ) {
  const nowWall = wallParts(new Date(), tz);
  const naive = Date.UTC(nowWall.y, nowWall.m - 1, nowWall.d + dayOffset, hour, minute, 0, 0);
  const firstOffset = tzOffsetMs(new Date(naive), tz);
  let stamp = naive - firstOffset;
  const secondOffset = tzOffsetMs(new Date(stamp), tz);
  if (secondOffset !== firstOffset) stamp = naive - secondOffset;
  return new Date(stamp).toISOString();
}

export function fmtTime(iso: string, tz = TZ) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: tz,
  }).format(new Date(iso));
}

export function fmtDate(iso: string, tz = TZ) {
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: tz,
  }).format(new Date(iso));
}

export function fmtLongDate(d: Date = new Date(), tz = TZ) {
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: tz,
  }).format(d);
}

export function fmtClock(d: Date = new Date(), tz = TZ) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: tz,
  }).format(d);
}

export function dayKey(iso: string, tz = TZ) {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: tz,
  }).format(new Date(iso));
}

export function relative(iso: string | null) {
  if (!iso) return "—";
  const delta = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(delta);
  const mins = Math.round(abs / 60_000);
  const past = delta < 0;
  const phrase = (() => {
    if (mins < 1) return "moments";
    if (mins < 60) return `${mins}m`;
    if (mins < 60 * 24) return `${Math.round(mins / 60)}h`;
    const days = Math.round(mins / (60 * 24));
    if (days < 30) return `${days}d`;
    return `${Math.round(days / 30)}mo`;
  })();
  return past ? `${phrase} ago` : `in ${phrase}`;
}

export function minutesUntil(iso: string) {
  return Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
}

export function greeting(tz = TZ) {
  const h = Number(
    new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hour12: false, timeZone: tz }).format(
      new Date(),
    ),
  );
  if (h < 5) return "Still up";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export function pct(n: number) {
  return `${Math.round(n)}%`;
}

export function clamp(n: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, n));
}

export function sleep(ms: number) {
  return new Promise<void>((r) => setTimeout(r, ms));
}

export function groupBy<T, K extends string>(items: T[], key: (item: T) => K) {
  return items.reduce<Record<string, T[]>>((acc, item) => {
    const k = key(item);
    (acc[k] ||= []).push(item);
    return acc;
  }, {});
}

export const priorityRank: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export function sortByPriority<T extends { priority: string }>(items: T[]) {
  return [...items].sort(
    (a, b) => (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9),
  );
}
