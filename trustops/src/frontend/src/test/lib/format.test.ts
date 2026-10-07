import { describe, expect, it } from 'vitest';
import { chainSummary, shortHash } from '@/lib/format';

describe('chainSummary', () => {
  it('says how many records and that the chain verified', () => {
    expect(chainSummary({ case_id: 'c', record_count: 9, ok: true, first_break_seq: null })).toBe('9 records, chain verified');
  });

  it('uses the singular for one record', () => {
    expect(chainSummary({ case_id: 'c', record_count: 1, ok: true, first_break_seq: null })).toBe('1 record, chain verified');
  });

  it('names the first broken record', () => {
    expect(chainSummary({ case_id: 'c', record_count: 9, ok: false, first_break_seq: 4 })).toBe('9 records, chain broken at record 4');
  });

  it('never throws on a missing field and never claims a verified chain without ok', () => {
    expect(chainSummary({})).toBe('chain could not be verified');
    expect(chainSummary({ ok: true })).toBe('chain verified');
    expect(chainSummary({ record_count: 3, ok: false })).toBe('3 records, chain could not be verified');
    expect(chainSummary({ record_count: 0, ok: true })).toBe('0 records, chain verified');
  });
});

describe('shortHash', () => {
  it('shortens a long hash and leaves a short one', () => {
    expect(shortHash('f04d1c2b9a8e871a')).toBe('f04d…871a');
    expect(shortHash('abc')).toBe('abc');
  });

  it('does not throw on a missing hash', () => {
    expect(shortHash(undefined)).toBe('—');
    expect(shortHash(null)).toBe('—');
    expect(shortHash('')).toBe('—');
  });
});
