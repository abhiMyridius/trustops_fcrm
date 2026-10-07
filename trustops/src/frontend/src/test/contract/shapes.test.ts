/**
 * Contract ratchet for answers (the path ratchet in paths.test.ts covers routes only): what the mock
 * backend answers must carry exactly the fields the backend's OpenAPI declares, so a screen written
 * against the mock reads the same names it will meet live. Verify chain once crashed the page because
 * the web read `count` and `head_hash` while S4 answers `record_count` and `first_break_seq` (D-105).
 */
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { resetMockState } from '@/mocks/server';
import { USERS, call, tokenFor } from '../fixtures';
import { openApiSchema } from './routes';

const DEMO = 'FCRM-2026-0151';
const SEEDED = 'FCRM-2026-0118';

async function json(path: string, who: string): Promise<Record<string, unknown>> {
  const res = await call(await tokenFor(who), path);
  expect(res.status, path).toBe(200);
  return (await res.json()) as Record<string, unknown>;
}

describe('mock answers carry the fields of the backend schema', () => {
  it('VerifyResult: the demo case and a seeded case', async () => {
    resetMockState('closed');
    const schema = openApiSchema('VerifyResult');
    for (const id of [DEMO, SEEDED]) {
      const answer = await json(paths.audit.verify(id), USERS.rahul);
      expect(Object.keys(answer).sort(), id).toEqual(schema.fields);
      expect(answer.case_id).toBe(id);
      expect(typeof answer.record_count).toBe('number');
      expect(typeof answer.ok).toBe('boolean');
    }
  });

  it('VerifyResult: the committee may read it too', async () => {
    resetMockState('closed');
    const answer = await json(paths.audit.verify(DEMO), USERS.ana);
    expect(Object.keys(answer).sort()).toEqual(openApiSchema('VerifyResult').fields);
    expect(answer.record_count).toBe(67);
    expect(answer.first_break_seq).toBeNull();
  });
});
