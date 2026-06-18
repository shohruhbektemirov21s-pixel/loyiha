/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MOCK?: string;
  readonly VITE_AUTH_BYPASS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
