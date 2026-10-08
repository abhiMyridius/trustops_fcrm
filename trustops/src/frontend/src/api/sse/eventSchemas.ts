/**
 * Case stream events (design C §1.4). The envelope is validated with zod; payloads stay opaque here
 * because the stream only drives cache invalidation. Event names bind to docs/architecture/events.
 */
import { z } from 'zod';

export const streamEventSchema = z.object({
  type: z.string().min(1),
  caseId: z.string().min(1),
  seq: z.number().int().nonnegative(),
  at: z.string(),
  payload: z.record(z.string(), z.unknown()).optional(),
});

export type StreamEvent = z.infer<typeof streamEventSchema>;

const GATE_KEYS = ['', 'assessment', 'policy', 'risk', 'intake', 'review', 'handoff'];
const SCORE_KEYS = ['assessment', 'risk'];
const AGENT_KEYS = ['assessment', 'policy', 'risk', 'recommendation'];
const OUTCOME_KEYS = ['outcome', 'conditions', 'activity'];
const CLARIFICATION_KEYS = ['inbox', 'clarifications'];

/**
 * Event type -> the case sub-keys it invalidates (`''` is the whole case subtree). Names follow the
 * backend event catalogue; the few older names below the line are kept until the stream vocabulary is
 * frozen. Unknown types invalidate nothing.
 */
export const invalidationMap: Record<string, string[]> = {
  'case.created': ['', 'activity'],
  'case.updated': ['', 'activity'],
  'case.state_changed': ['', 'activity'],
  'case.clarification_sent': CLARIFICATION_KEYS,
  'document.ingested': ['documents', 'intake'],
  'document.extracted': ['documents', 'intake'],
  'intake.proposed': ['intake', 'review'],
  'intake.proposals_ready': ['intake', 'review'],
  // The required field set was resolved (the header, the completion ring and both intake tabs change),
  // and an analyst took the case (the header names them): the whole case subtree.
  'fieldset.resolved': ['', 'activity'],
  'case.assigned': ['', 'activity'],
  'gate.opened': GATE_KEYS,
  'gate.decided': GATE_KEYS,
  'score.computed': SCORE_KEYS,
  'score.recomputed': SCORE_KEYS,
  'agent.done': AGENT_KEYS,
  'agent.failed': AGENT_KEYS,
  'pack.released': ['handoff'],
  'vote.cast': ['handoff'],
  'vote.tallied': ['handoff'],
  'outcome.set': OUTCOME_KEYS,
  'outcome.delivered': OUTCOME_KEYS,
  'condition.updated': ['conditions'],
  // Older names, no longer emitted by the backend catalogue.
  'case.stage_changed': ['', 'activity'],
  'document.updated': ['documents'],
  'intake.progress': ['documents', 'intake'],
  'proposal.ready': ['assessment', 'policy', 'risk'],
  'pack.updated': ['handoff'],
};

/**
 * Whole families of event types (`clarification.*`, `thread.*`, and since ADR-73 `question.*` and
 * `values.*`, the ad-hoc questions and the values an answer changed) that refresh the inbox screens.
 */
const PREFIX_RULES: ReadonlyArray<readonly [string, string[]]> = [
  ['clarification.', CLARIFICATION_KEYS],
  ['thread.', CLARIFICATION_KEYS],
  ['question.', CLARIFICATION_KEYS],
  ['values.', CLARIFICATION_KEYS],
];

/** `harness.*`, `agent.*` and `guard.*` events also refresh the harness monitor snapshot. */
const HARNESS_PREFIXES = ['harness.', 'agent.', 'guard.'];

/** The query keys one event invalidates for a case: its map entries, its family rules, the harness key. */
export function invalidationKeys(type: string, caseId: string): Array<readonly string[]> {
  const subs = new Set<string>(invalidationMap[type] ?? []);
  for (const [prefix, extra] of PREFIX_RULES) {
    if (type.startsWith(prefix)) extra.forEach((sub) => subs.add(sub));
  }
  const keys: Array<readonly string[]> = [...subs].map((sub) => (sub ? ['case', caseId, sub] : ['case', caseId]));
  if (HARNESS_PREFIXES.some((prefix) => type.startsWith(prefix))) keys.push(['harness', caseId]);
  return keys;
}

export function parseStreamEvent(raw: string): StreamEvent | null {
  try {
    const parsed = streamEventSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}
