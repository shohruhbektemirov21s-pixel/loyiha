/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MOCK?: string;
  readonly VITE_AUTH_BYPASS?: string;
  // "false" => this deploy has no physical camera (e.g. the cloud host), so the
  // console hides the live-camera capture UI and only offers image upload.
  readonly VITE_ENABLE_CAMERA?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
