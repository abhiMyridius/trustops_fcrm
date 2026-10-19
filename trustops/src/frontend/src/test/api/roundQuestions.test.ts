import { describe, expect, it } from 'vitest';
import { roundQuestionsBody } from '@/api/queries/analystQueries';
import type { DraftQuestion } from '@/api/types/analyst';

// Seen live on FCRM-2026-0152 (19 Oct): "Send round" posted the drafted questions as read, with their
// `dropped_reason`, and the API's RoundQuestion (extra keys forbidden) refused every round with a 422.
describe('roundQuestionsBody', () => {
  it('sends only the keys POST /clarifications takes', () => {
    const drafted: DraftQuestion[] = [
      { id: 'Q1', text: 'Please attach the procedure.', anchor: 'CTL-G01', keep: true, dropped_reason: null },
      { id: 'Q2', text: 'Already answered on p.6.', anchor: 'F05', keep: false, dropped_reason: 'Answered on p.6' },
    ];

    const body = roundQuestionsBody(drafted);

    expect(body).toEqual([
      { id: 'Q1', text: 'Please attach the procedure.', anchor: 'CTL-G01', keep: true },
      { id: 'Q2', text: 'Already answered on p.6.', anchor: 'F05', keep: false },
    ]);
    for (const question of body) {
      expect(Object.keys(question).sort()).toEqual(['anchor', 'id', 'keep', 'text']);
    }
  });
});
