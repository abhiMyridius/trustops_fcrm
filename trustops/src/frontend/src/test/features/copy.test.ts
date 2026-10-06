/**
 * The screens read live data, so no user-facing string may claim a mock. The MSW backend (src/mocks)
 * and the login page's mock-only scenario switch may name the mock: they only exist under
 * `npm run dev:msw`. This scans the screen and component sources for the mock-era phrases that once
 * shipped to the live build.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '../..');
const SCREEN_DIRS = ['features', 'components', 'app'];
const MOCK_ERA = /mock mode|replay fixtures?|live case in mock/i;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

describe('screen copy', () => {
  it('never says "mock mode" or "replay fixtures"', () => {
    const offenders = SCREEN_DIRS.flatMap((dir) => sources(join(SRC, dir))).flatMap((file) =>
      readFileSync(file, 'utf8')
        .split('\n')
        .flatMap((line, i) => (MOCK_ERA.test(line) ? [`${file.slice(SRC.length + 1)}:${i + 1}: ${line.trim()}`] : [])),
    );
    expect(offenders).toEqual([]);
  });
});
