import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config.ts';

export default defineConfig((env) =>
  mergeConfig(viteConfig(env), {
    test: {
      environment: 'jsdom',
      // MSW is the vitest backend: the specs run against the in-process mock, never a gateway.
      env: { VITE_API_MODE: 'msw' },
      globals: false,
      setupFiles: ['./src/test/setup.ts'],
      // Specs live under src/test, mirroring the source path; never beside the component (ADR-21 spirit).
      include: ['src/test/**/*.test.{ts,tsx}'],
      css: false,
      testTimeout: 20000,
    },
  }),
);
