/**
 * The words a person sees for an API error. S1 answers 404 in two ways: the plain `not_found` (a missing
 * case, or one that is not yours: the same words, no hints) and `no_run_recorded` (the case exists and
 * no run has recorded anything yet: information, not a fault). The web tells them apart by the problem
 * `type` and nothing else (D-106).
 */
import { describe, expect, it } from 'vitest';
import { ApiError, NO_RUN_MESSAGE, isNoRunRecorded, isNotReady, signInMessage, userMessage } from '@/api/errors';

const BASE = 'https://trustops.local/errors/';

function failure(status: number, code: string, detail?: string): ApiError {
  return new ApiError(status, { type: `${BASE}${code}`, title: code, status, detail });
}

describe('isNoRunRecorded', () => {
  it('recognises the 404 whose type ends in /no_run_recorded', () => {
    expect(isNoRunRecorded(failure(404, 'no_run_recorded', 'no harness run is recorded for case X'))).toBe(true);
  });

  it('does not take the plain not_found, or the same code on another status, for it', () => {
    expect(isNoRunRecorded(failure(404, 'not_found', 'no such case X'))).toBe(false);
    expect(isNoRunRecorded(failure(409, 'no_run_recorded'))).toBe(false);
    expect(isNoRunRecorded(failure(404, 'also_no_run_recorded_yet'))).toBe(false);
  });

  it('is false for anything that is not an ApiError', () => {
    expect(isNoRunRecorded(new Error('no_run_recorded'))).toBe(false);
    expect(isNoRunRecorded(undefined)).toBe(false);
    expect(isNoRunRecorded(null)).toBe(false);
    expect(isNoRunRecorded({ status: 404, problem: { type: `${BASE}no_run_recorded` } })).toBe(false);
  });

  it('is false for an answer that carries no problem type at all', () => {
    expect(isNoRunRecorded(new ApiError(404, { type: 'about:blank', title: 'Not Found', status: 404 }))).toBe(false);
  });
});

describe('isNotReady', () => {
  it('recognises the 409 not_ready and nothing else', () => {
    expect(isNotReady(failure(409, 'not_ready', 'the narrative has not drafted the recommendation yet'))).toBe(true);
    expect(isNotReady(failure(409, 'conflict', 'the stored recommendation does not have its expected shape'))).toBe(false);
    expect(isNotReady(failure(404, 'not_ready'))).toBe(false);
    expect(isNotReady(new Error('not_ready'))).toBe(false);
  });
});

describe('userMessage', () => {
  it('keeps the same words for a missing case and a case that is not yours', () => {
    expect(userMessage(failure(404, 'not_found', 'no such case X'))).toBe('This case is not in your view.');
    expect(userMessage(failure(404, 'not_found', 'some other detail'))).toBe('This case is not in your view.');
  });

  it('says there is no run yet, in calm words, for no_run_recorded', () => {
    const message = userMessage(failure(404, 'no_run_recorded'));
    expect(message).toBe(NO_RUN_MESSAGE);
    expect(message).toMatch(/^No run is recorded for this case yet/);
    expect(message).not.toMatch(/not in your view/i);
  });

  it('keeps the rest of the vocabulary', () => {
    expect(userMessage(failure(401, 'unauthorized'))).toBe('Your session has ended. Sign in again.');
    expect(userMessage(failure(403, 'forbidden'))).toBe('Not available for your role.');
    expect(userMessage(failure(409, 'not_ready', 'the narrative has not drafted the recommendation yet'))).toBe('the narrative has not drafted the recommendation yet');
    expect(userMessage(failure(409, 'conflict'))).toBe('Someone changed this first. Reload to see the latest.');
    expect(userMessage(failure(422, 'validation_failed', 'Some detail'))).toBe('Some detail');
    expect(userMessage(failure(500, 'internal_error', 'An unexpected error occurred.'))).toBe('An unexpected error occurred.');
    expect(userMessage(new Error('boom'))).toBe('boom');
    expect(userMessage('nope')).toBe('Something went wrong.');
  });
});

describe('signInMessage (D-108)', () => {
  it("shows identity's own words for a refused sign-in, not 'session ended'", () => {
    expect(signInMessage(failure(401, 'unauthorized', 'Unknown user or wrong password.'))).toBe('Unknown user or wrong password.');
    expect(signInMessage(failure(401, 'unauthorized', 'Unknown user or wrong password.'))).not.toMatch(/session/i);
  });

  it('has words of its own when the 401 carries no detail', () => {
    expect(signInMessage(failure(401, 'unauthorized'))).toBe('Unknown user or wrong password.');
  });

  it('leaves every other failure to userMessage, and a 401 elsewhere still means the session ended', () => {
    expect(signInMessage(failure(429, 'too_many_requests', 'Too many sign-in attempts.'))).toBe('Too many sign-in attempts.');
    expect(signInMessage(failure(503, 'upstream_unavailable', 'The database is unavailable.'))).toBe('The database is unavailable.');
    expect(signInMessage(new Error('Failed to fetch'))).toBe('Failed to fetch');
    expect(userMessage(failure(401, 'unauthorized', 'Unknown user or wrong password.'))).toBe('Your session has ended. Sign in again.');
  });
});
