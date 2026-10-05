/** Node MSW server for vitest: the same handlers the browser uses, so tests exercise real HTTP paths. */
import { setupServer } from 'msw/node';
import { resetForTests, type ScenarioKey } from './db';
import { handlers } from './handlers';
import { applyScenario } from './scenarios';

export const server = setupServer(...handlers);

export function resetMockState(key: ScenarioKey = 'fresh'): void {
  resetForTests(key, applyScenario);
}
