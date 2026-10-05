/** Shared lookups for the mock projections. */
import type { ChangeType, Completion, StageStep } from '@/api/types/common';
import { STORY_TODAY, getDB } from '../db';
import { DEMO_ID, seed, type SeedCase } from '../seed';

export const FIELD_LABEL: Record<string, string> = Object.fromEntries([
  ...seed.field_set.always_required.map((f) => [f.id, f.label] as const),
  ...Object.values(seed.field_set.per_type).flatMap((fs) => fs.map((f) => [f.id, f.label] as const)),
]);

export const CONTROL_META = new Map(seed.controls.map((c) => [c.id, c]));

export function seedCase(id: string): SeedCase | undefined {
  return seed.cases.find((c) => c.id === id);
}

export function isDemo(id: string): boolean {
  return id === DEMO_ID;
}

export function createdCase(id: string) {
  return getDB().created.find((c) => c.id === id && !c.discarded);
}

export function caseExists(id: string): boolean {
  return Boolean(seedCase(id) || createdCase(id));
}

export function asTypes(xs: string[]): ChangeType[] {
  return xs as ChangeType[];
}

/** Business days after a date (Mon–Fri), used for the "decision-ready in 2 business days" target. */
export function addBusinessDays(iso: string, days: number): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  let left = days;
  while (left > 0) {
    d.setUTCDate(d.getUTCDate() + 1);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) left -= 1;
  }
  return d.toISOString().slice(0, 10);
}

export function daysBetween(a: string, b: string): number {
  return Math.round((new Date(`${b.slice(0, 10)}T00:00:00Z`).getTime() - new Date(`${a.slice(0, 10)}T00:00:00Z`).getTime()) / 86_400_000);
}

export function isApproachingDue(due: string | null): boolean {
  if (!due) return false;
  return daysBetween(STORY_TODAY, due) <= 1;
}

export function requiredFieldIds(types: string[]): string[] {
  const ids = new Set(seed.field_set.always_required.map((f) => f.id));
  for (const t of types) for (const f of seed.field_set.per_type[t] ?? []) ids.add(f.id);
  return [...ids];
}

export function completion(filled: number, required: number, alwaysFilled: number): Completion {
  return {
    filled,
    required,
    pct: required ? Math.round((filled / required) * 100) : 0,
    threshold_pct: Math.round(seed.field_set.completion_threshold * 100),
    always_required_filled: alwaysFilled,
    always_required_total: seed.field_set.always_required.length,
  };
}

export function steps(labels: [string, string][], currentIdx: number): StageStep[] {
  return labels.map(([key, label], i) => ({ key, label, state: i < currentIdx ? 'done' : i === currentIdx ? 'current' : 'upcoming' }));
}

export const OUTCOME_FROM_SEED: Record<string, 'approve' | 'approve_with_conditions' | 'defer' | 'reject'> = {
  approved: 'approve',
  approve_with_conditions: 'approve_with_conditions',
  deferred: 'defer',
  rejected: 'reject',
};

export const OUTCOME_LABEL_PAST: Record<'approve' | 'approve_with_conditions' | 'defer' | 'reject', string> = {
  approve: 'Approved',
  approve_with_conditions: 'Approved with conditions',
  defer: 'Deferred',
  reject: 'Rejected',
};

export function bandLabel(band: number): string {
  return seed.scoring_model.bands.find((b) => b.band === band)?.label ?? '';
}
