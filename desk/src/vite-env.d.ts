/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The public demo's login, as "user / password". Only the hosted demo sets it; a site
   * you run yourself leaves it unset and the sign-in page says nothing. */
  readonly VITE_DEMO_LOGIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
