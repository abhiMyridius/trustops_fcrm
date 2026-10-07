/**
 * ISO 8601 week keys (`2026-W40`), the only form S4's `GET audit/agent-quality?week=` accepts: the eval
 * publisher writes the key with `isocalendar()`, and any other spelling (a date such as `2026-09-28`)
 * is a 422. Date formatting only; nothing here is business arithmetic.
 */

const DAY_MS = 86_400_000;

/** The week key S4 validates against (`fcrm_audit.routes_analytics.WEEK_PATTERN`). */
export const ISO_WEEK = /^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$/;

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * The ISO week of a date, read in UTC: weeks start on Monday and week 1 is the one holding the year's
 * first Thursday, so the first days of January can belong to the last week of the year before and the
 * last days of December to week 1 of the next (2027-01-01 is 2026-W53, 2024-12-30 is 2025-W01).
 */
export function isoWeekOf(date: Date): string {
  const day = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const weekday = day.getUTCDay() || 7; // Monday 1 .. Sunday 7
  day.setUTCDate(day.getUTCDate() + 4 - weekday); // the Thursday of this ISO week decides the year
  const year = day.getUTCFullYear();
  const week = Math.ceil(((day.getTime() - Date.UTC(year, 0, 1)) / DAY_MS + 1) / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
}

/**
 * What to send as `week`: an ISO week key as it is, an ISO date turned into its week, and nothing for
 * anything else (S4 then answers the newest published week). The Agent quality tab reads this from the
 * address bar, so a hand-typed or older link can never put an invalid week on the wire.
 */
export function weekParam(raw: string | null | undefined): string | undefined {
  const text = raw?.trim().toUpperCase();
  if (!text) return undefined;
  if (ISO_WEEK.test(text)) return text;
  const parts = ISO_DATE.exec(text);
  if (!parts) return undefined;
  const [, y, m, d] = parts;
  const date = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d)));
  const real = date.getUTCFullYear() === Number(y) && date.getUTCMonth() === Number(m) - 1 && date.getUTCDate() === Number(d);
  return real ? isoWeekOf(date) : undefined;
}
