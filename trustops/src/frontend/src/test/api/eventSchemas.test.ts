/** The stream's event vocabulary: which query keys each backend event type invalidates. */
import { describe, expect, it } from 'vitest';
import { invalidationKeys, invalidationMap, parseStreamEvent } from '@/api/sse/eventSchemas';

const CASE = 'FCRM-2026-0151';

/** Sub-keys ('' = the whole case subtree) per event type, as the backend catalogue names them. */
const EXPECTED: Record<string, string[]> = {
  'case.created': ['', 'activity'],
  'case.updated': ['', 'activity'],
  'case.state_changed': ['', 'activity'],
  'document.stored': ['documents'],
  'document.ingested': ['documents', 'intake'],
  'document.extracted': ['documents', 'intake'],
  'intake.proposed': ['documents', 'intake', 'review'],
  'intake.proposals_ready': ['documents', 'intake', 'review'],
  'fieldset.resolved': ['', 'activity'],
  'case.assigned': ['', 'activity'],
  'gate.opened': ['', 'assessment', 'policy', 'risk', 'intake', 'review', 'handoff'],
  'gate.decided': ['', 'assessment', 'policy', 'risk', 'intake', 'review', 'handoff'],
  'score.computed': ['assessment', 'risk'],
  'score.recomputed': ['assessment', 'risk'],
  'agent.done': ['assessment', 'policy', 'risk', 'recommendation'],
  'agent.failed': ['assessment', 'policy', 'risk', 'recommendation'],
  'pack.released': ['handoff'],
  'vote.cast': ['handoff'],
  'vote.tallied': ['handoff'],
  'outcome.set': ['outcome', 'conditions', 'activity'],
  'outcome.delivered': ['outcome', 'conditions', 'activity'],
  'condition.updated': ['conditions'],
};

function caseKeys(type: string): string[] {
  return invalidationKeys(type, CASE)
    .filter((k) => k[0] === 'case')
    .map((k) => k[2] ?? '');
}

describe('invalidationMap', () => {
  for (const [type, subs] of Object.entries(EXPECTED)) {
    it(`${type} invalidates ${subs.map((s) => s || 'case').join(', ')}`, () => {
      expect(invalidationMap[type]).toEqual(subs);
      expect(caseKeys(type).sort()).toEqual([...subs].sort());
    });
  }

  it('maps the whole clarification.* and thread.* families to the inbox and clarifications', () => {
    for (const type of ['clarification.sent', 'clarification.answered', 'thread.updated', 'thread.opened', 'question.asked', 'values.changed']) {
      expect(caseKeys(type).sort(), type).toEqual(['clarifications', 'inbox']);
    }
  });

  it('refreshes the header too when the owner replies, so the Inbox count moves', () => {
    for (const type of ['thread.answered', 'question.answered', 'case.answered']) {
      expect(caseKeys(type).sort(), type).toEqual(['', 'clarifications', 'inbox']);
    }
  });

  it('refreshes the inbox for the catalogue name case.clarification_sent', () => {
    expect(caseKeys('case.clarification_sent').sort()).toEqual(['clarifications', 'inbox']);
  });

  it('refreshes the Documents tab on upload and when ingestion finishes, so a live stream ends "Reading…"', () => {
    for (const type of ['document.stored', 'intake.proposed', 'intake.proposals_ready']) {
      expect(invalidationKeys(type, CASE), type).toContainEqual(['case', CASE, 'documents']);
    }
  });

  it('builds case-scoped keys, with the bare case key for the whole subtree', () => {
    expect(invalidationKeys('case.updated', CASE)).toEqual([['case', CASE], ['case', CASE, 'activity']]);
  });

  it('keeps the harness handling: harness.*, agent.* and guard.* also refresh the monitor snapshot', () => {
    for (const type of ['harness.step', 'agent.done', 'agent.failed', 'agent.retry', 'guard.block', 'guard.permit']) {
      expect(invalidationKeys(type, CASE), type).toContainEqual(['harness', CASE]);
    }
  });

  it('does not refresh the harness for other event types', () => {
    for (const type of ['case.created', 'gate.decided', 'vote.cast', 'outcome.set']) {
      expect(invalidationKeys(type, CASE), type).not.toContainEqual(['harness', CASE]);
    }
  });

  it('invalidates nothing for a type it does not know', () => {
    expect(invalidationKeys('made.up_event', CASE)).toEqual([]);
  });

  it('never targets another case than the one the stream belongs to', () => {
    for (const type of Object.keys(invalidationMap)) {
      for (const key of invalidationKeys(type, CASE)) expect(key[1], type).toBe(CASE);
    }
  });
});

describe('parseStreamEvent', () => {
  it('accepts the envelope the backend emits', () => {
    const raw = JSON.stringify({ type: 'gate.decided', caseId: CASE, seq: 4, at: '2026-10-05T10:00:00Z', payload: { gate: 'g1' } });
    expect(parseStreamEvent(raw)).toMatchObject({ type: 'gate.decided', caseId: CASE, seq: 4 });
  });

  it('drops malformed frames without throwing', () => {
    expect(parseStreamEvent('not json')).toBeNull();
    expect(parseStreamEvent(JSON.stringify({ type: 'gate.decided' }))).toBeNull();
    expect(parseStreamEvent(JSON.stringify({ type: '', caseId: CASE, seq: 1, at: 'x' }))).toBeNull();
  });
});
