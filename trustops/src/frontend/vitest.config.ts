import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config.ts';

export default defineConfig((env) =>
  mergeConfig(viteConfig(env), {
    test: {
      environment: 'jsdom',
      globals: false,
      setupFiles: ['./src/test/setup.ts'],
      // Specs live under src/test, mirroring the source path; never beside the component (ADR-21 spirit).
      include: ['src/test/**/*.test.{ts,tsx}'],
      css: false,
      testTimeout: 20000,
    },
  }),
);
