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

/** Event type -> the case sub-keys it invalidates. Unknown types invalidate nothing. */
export const invalidationMap: Record<string, string[]> = {
  'case.updated': ['', 'activity'],
  'case.stage_changed': ['', 'activity'],
  'document.updated': ['documents'],
  'intake.progress': ['documents', 'intake'],
  'intake.proposals_ready': ['intake', 'review'],
  'proposal.ready': ['assessment', 'policy', 'risk'],
  'score.computed': ['assessment', 'risk'],
  'thread.updated': ['inbox', 'clarifications'],
  'clarification.sent': ['inbox', 'clarifications'],
  'pack.updated': ['handoff'],
  'vote.cast': ['handoff'],
  'outcome.delivered': ['outcome', 'conditions', 'activity'],
  'condition.updated': ['conditions'],
};

export function parseStreamEvent(raw: string): StreamEvent | null {
  try {
    const parsed = streamEventSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}
