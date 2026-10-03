/**
 * Vitest setup: jest-dom matchers, the MSW node server with the same handlers the browser uses,
 * a fresh mock state per test, and the jsdom gaps (matchMedia, ResizeObserver, scrollIntoView).
 */
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest';
import { setAccessToken } from '@/api/client';
import { resetMockState, server } from '@/mocks/server';

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver = ResizeObserverStub;
}

if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined;
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

beforeEach(() => {
  resetMockState('fresh');
  setAccessToken(null);
  document.cookie = 'fcrm_refresh=; Max-Age=0; Path=/';
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => server.close());
