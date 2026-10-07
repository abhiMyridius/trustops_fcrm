/** Display formatting only. No business arithmetic: values arrive computed from the server. */
import type { VerifyResult } from '@/api/types/analyst';
import type { ChangeType, Outcome } from '@/api/types/common';

const dateFmt = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
const dateTimeFmt = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'UTC',
});
const monthFmt = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', timeZone: 'UTC' });
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const int = new Intl.NumberFormat('en-US');

function parse(iso: string): Date | null {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = parse(iso);
  return d ? dateFmt.format(d) : iso;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = parse(iso);
  return d ? `${dateTimeFmt.format(d)} UTC` : iso;
}

export function formatMonth(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = parse(iso);
  return d ? monthFmt.format(d) : iso;
}

export function formatMoney(amount: number): string {
  return usd.format(amount);
}

export function formatInt(n: number): string {
  return int.format(n);
}

/** Server-computed decimals shown with a fixed number of places (no rounding logic of our own beyond display). */
export function formatFixed(n: number, places = 1): string {
  return n.toFixed(places);
}

export function shortHash(hash: string | null | undefined): string {
  if (!hash) return '—';
  return hash.length > 10 ? `${hash.slice(0, 4)}…${hash.slice(-4)}` : hash;
}

/**
 * The line the Verify chain banner shows: "9 records, chain verified" or "9 records, chain broken at
 * record 4". It reads the answer defensively: a field the server did not send leaves its words out
 * instead of throwing, so a changed or partial answer can never take the page down.
 */
export function chainSummary(result: Partial<VerifyResult>): string {
  const count = result.record_count;
  const records = typeof count === 'number' ? `${count} ${count === 1 ? 'record' : 'records'}, ` : '';
  if (result.ok === true) return `${records}chain verified`;
  const at = result.first_break_seq;
  return `${records}${typeof at === 'number' ? `chain broken at record ${at}` : 'chain could not be verified'}`;
}

export const CHANGE_TYPE_LABEL: Record<ChangeType, string> = {
  new_product: 'New product',
  new_feature: 'New feature',
  process_change: 'Process change',
  vendor_onboarding: 'Vendor onboarding',
  new_geography: 'New geography',
  new_segment: 'New customer segment',
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  approve: 'Approve',
  approve_with_conditions: 'Approve with conditions',
  defer: 'Defer',
  reject: 'Reject',
};

export function caseShortId(id: string): string {
  return id.replace(/^FCRM-\d{4}-/, '');
}
