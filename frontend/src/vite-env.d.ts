/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The API's base URL for the web build; default `http://localhost:8000`. */
  readonly VITE_API_URL?: string
  /** `'true'` in the desktop build (`--mode electron`): call the API on the page's own origin. */
  readonly VITE_API_SAME_ORIGIN?: string
}
