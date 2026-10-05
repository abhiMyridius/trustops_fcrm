/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_MODE?: 'msw' | 'live';
  readonly VITE_DEMO_USERS?: 'true' | 'false';
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
