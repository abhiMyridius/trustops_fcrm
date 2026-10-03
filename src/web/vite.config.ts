import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv, searchForWorkspaceRoot } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const here = path.dirname(fileURLToPath(import.meta.url));
// The MSW seed is read at build time from the P7 package (never copied, never edited by hand).
const seedDir = path.resolve(here, '../packages/seed/fcrm_seed');

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, here, 'VITE_');
  const apiMode = env.VITE_API_MODE ?? 'msw';
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { '@': path.resolve(here, 'src') },
    },
    server: {
      port: 5173,
      strictPort: true,
      fs: { allow: [searchForWorkspaceRoot(here), seedDir] },
      // Live mode talks to the nginx gateway (ADR-20: gateway 8080); MSW mode needs no proxy.
      proxy:
        apiMode === 'live'
          ? { '/api': { target: 'http://localhost:8080', changeOrigin: false } }
          : undefined,
    },
    build: {
      sourcemap: true,
      chunkSizeWarningLimit: 900,
    },
  };
});
