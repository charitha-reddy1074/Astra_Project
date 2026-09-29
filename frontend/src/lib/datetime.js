/**
 * Date/time formatting — single source of truth for how the platform renders time.
 *
 * Two problems this solves:
 *
 * 1. The API serialises `datetime.utcnow()`, which is a NAIVE datetime, so the
 *    JSON has no timezone marker: "2026-07-27T05:15:04.602373". `new Date(...)`
 *    on that string treats it as LOCAL time, so a 10:45 IST event rendered as
 *    05:15 AM — off by the UTC offset. `parseUtc` pins those strings to UTC.
 *
 * 2. The platform is operated from India, so timestamps are displayed in
 *    Asia/Kolkata rather than whatever timezone the viewer's laptop is set to.
 *    Change DISPLAY_TZ here to move the whole app.
 */

export const DISPLAY_TZ = "Asia/Kolkata";
export const DISPLAY_TZ_LABEL = "IST";

/**
 * Parse an API timestamp into a Date.
 *
 * Timestamps that already carry an offset ("…Z", "…+05:30") are respected.
 * Bare/naive ones are interpreted as UTC, which is what the backend actually
 * stores — not as the viewer's local time, which is what JS would assume.
 */
export function parseUtc(value) {
  if (!value) return null;
  if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
  const s = String(value).trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(s);
  const d = new Date(hasZone ? s : `${s.replace(" ", "T")}Z`);
  return isNaN(d.getTime()) ? null : d;
}

const fmt = (opts) => new Intl.DateTimeFormat("en-GB", { timeZone: DISPLAY_TZ, ...opts });

/** "27 Jul 2026" */
export function formatDate(value) {
  const d = parseUtc(value);
  if (!d) return "—";
  return fmt({ day: "2-digit", month: "short", year: "numeric" }).format(d);
}

/** "10:45 am" */
export function formatTime(value) {
  const d = parseUtc(value);
  if (!d) return "—";
  return fmt({ hour: "2-digit", minute: "2-digit", hour12: true }).format(d).toLowerCase();
}

/** "27 Jul 2026, 10:45 am" */
export function formatDateTime(value) {
  const d = parseUtc(value);
  if (!d) return "—";
  return `${formatDate(d)}, ${formatTime(d)}`;
}

/** Split form, for table cells that stack the date over the time. */
export function formatDateParts(value) {
  const d = parseUtc(value);
  if (!d) return { date: "—", time: "" };
  return { date: formatDate(d), time: formatTime(d) };
}

/** "just now" · "12 min ago" · "3 h ago" · "5 d ago", then an absolute date. */
export function formatRelative(value) {
  const d = parseUtc(value);
  if (!d) return "—";
  const secs = Math.round((Date.now() - d.getTime()) / 1000);
  if (secs < 0) return formatDateTime(d);
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days} d ago`;
  return formatDate(d);
}

/**
 * Turn a date-only input value ("2026-07-27" from <input type="date">) into the
 * UTC instant that bounds that IST day, so From/To filters mean an IST day
 * rather than a UTC one.
 */
export function istDayBoundsToUtcIso(dateStr, edge = "start") {
  if (!dateStr) return null;
  // IST is a fixed UTC+05:30 — no DST — so a literal offset is safe and exact.
  const iso = edge === "end" ? `${dateStr}T23:59:59.999+05:30` : `${dateStr}T00:00:00.000+05:30`;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d.toISOString();
}
