import type { TriggerCategory } from '@/types';

// Short in-table labels for trigger categories - the full names truncate awkwardly in a
// fixed-width column ("Vendor / Third-Party Onboa…"). Full names are used everywhere else;
// this is table-only, with the full name still available on hover.
export const SHORT_CATEGORY: Record<TriggerCategory, string> = {
  'Vendor / Third-Party Onboarding': 'Vendor',
  'New Product': 'Product',
  'New Feature': 'Feature',
  'Process Change': 'Process',
  'New Geography': 'Geography',
  'New Customer Segment': 'Segment',
};

export function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return 'Just now';
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return formatDate(iso);
}

export function formatDate(iso: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: 'numeric' })} · ${d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`;
}

export function summarizeTitle(description: string | undefined | null, maxWords = 8): string {
  if (!description) return 'New submission';
  const words = description.replace(/^(Launch|Onboard|Add|Introduce|Extend|Integrate|Stand up|Set up)\s+/i, '').split(/\s+/);
  const short = words.slice(0, maxWords).join(' ');
  return short.charAt(0).toUpperCase() + short.slice(1) + (words.length > maxWords ? '...' : '');
}

export function businessDaysUntil(targetIso: string | null): number | null {
  if (!targetIso) return null;
  const target = new Date(targetIso);
  const now = new Date();
  let count = 0;
  const d = new Date(now);
  const sign = target > now ? 1 : -1;
  while (d.toDateString() !== target.toDateString()) {
    d.setDate(d.getDate() + sign);
    const day = d.getDay();
    if (day !== 0 && day !== 6) count += sign;
  }
  return count;
}
