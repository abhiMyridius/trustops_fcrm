import { describe, expect, it } from 'vitest';
import { ISO_WEEK, isoWeekOf, weekParam } from '@/lib/isoWeek';

const utc = (iso: string) => new Date(`${iso}T12:00:00Z`);

describe('isoWeekOf', () => {
  it('names the ISO week of an ordinary date', () => {
    expect(isoWeekOf(utc('2026-09-28'))).toBe('2026-W40'); // a Monday: the first day of its week
    expect(isoWeekOf(utc('2026-10-04'))).toBe('2026-W40'); // the Sunday that ends it
    expect(isoWeekOf(utc('2026-10-05'))).toBe('2026-W41');
    expect(isoWeekOf(utc('2026-10-07'))).toBe('2026-W41');
  });

  it('puts the last days of December in week 1 of the next year when the week holds its Thursday', () => {
    expect(isoWeekOf(utc('2024-12-29'))).toBe('2024-W52');
    expect(isoWeekOf(utc('2024-12-30'))).toBe('2025-W01');
    expect(isoWeekOf(utc('2024-12-31'))).toBe('2025-W01');
    expect(isoWeekOf(utc('2025-12-29'))).toBe('2026-W01');
  });

  it('puts the first days of January in the last week of the year before when it holds the Thursday', () => {
    expect(isoWeekOf(utc('2021-01-01'))).toBe('2020-W53');
    expect(isoWeekOf(utc('2021-01-03'))).toBe('2020-W53');
    expect(isoWeekOf(utc('2021-01-04'))).toBe('2021-W01');
    expect(isoWeekOf(utc('2027-01-01'))).toBe('2026-W53');
    expect(isoWeekOf(utc('2027-01-03'))).toBe('2026-W53');
    expect(isoWeekOf(utc('2027-01-04'))).toBe('2027-W01');
  });

  it('knows a 53-week year (2026 starts on a Thursday) and a year that starts on a Thursday of week 1', () => {
    expect(isoWeekOf(utc('2026-01-01'))).toBe('2026-W01');
    expect(isoWeekOf(utc('2026-12-31'))).toBe('2026-W53');
    expect(isoWeekOf(utc('2015-12-31'))).toBe('2015-W53');
  });

  it('reads the date in UTC, whatever the machine zone', () => {
    expect(isoWeekOf(new Date('2026-09-28T00:00:00Z'))).toBe('2026-W40');
    expect(isoWeekOf(new Date('2026-09-27T23:59:59Z'))).toBe('2026-W39');
  });

  it('always answers a key S4 accepts', () => {
    for (let day = 0; day < 4 * 366; day += 1) {
      const key = isoWeekOf(new Date(Date.UTC(2024, 0, 1 + day)));
      expect(ISO_WEEK.test(key), key).toBe(true);
    }
  });
});

describe('weekParam', () => {
  it('sends nothing when no week was asked for, so S4 answers the newest', () => {
    expect(weekParam(null)).toBeUndefined();
    expect(weekParam(undefined)).toBeUndefined();
    expect(weekParam('')).toBeUndefined();
    expect(weekParam('   ')).toBeUndefined();
  });

  it('passes a valid ISO week through', () => {
    expect(weekParam('2026-W40')).toBe('2026-W40');
    expect(weekParam('2026-w05')).toBe('2026-W05');
    expect(weekParam('2026-W53')).toBe('2026-W53');
  });

  it('turns an ISO date, the form that used to be sent, into its week', () => {
    expect(weekParam('2026-09-28')).toBe('2026-W40');
    expect(weekParam('2027-01-01')).toBe('2026-W53');
    expect(weekParam('2024-12-30')).toBe('2025-W01');
  });

  it('sends nothing for anything it cannot read, never a value S4 would refuse', () => {
    for (const bad of ['2026-W00', '2026-W54', '2026-W5', 'last week', '2026-02-30', '2026-13-01', '28/09/2026', '2026-09-28T00:00:00Z']) {
      expect(weekParam(bad), bad).toBeUndefined();
    }
  });
});
