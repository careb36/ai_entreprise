/**
 * Where the desktop app's windows may go (docs/design/SESSIONS.md, "Desktop
 * app"). Pure decisions, so they are tested without Electron; `main.ts` acts
 * on them.
 *
 * The main window stays on the local origin; any other web link opens in the
 * system browser. Single sign-on needs the identity provider's pages, so it
 * runs in a separate window with no preload script: it starts at the local
 * `/v1/auth/sso/<key>/start`, may visit the provider, has the provider's
 * redirect to the backend's callback loaded through the local origin instead
 * (the binding cookie is the local origin's), and ends when the backend sends
 * it to `/login/sso`, which the main window then loads.
 */

export type Decision =
  | { kind: 'allow' }
  | { kind: 'deny' }
  /** Open in the system browser. */
  | { kind: 'external'; url: string }
  /** Main window: start single sign-on in its own window at *url*. */
  | { kind: 'sso'; url: string }
  /** SSO window: load *url* (on the local origin) instead. */
  | { kind: 'load'; url: string }
  /** SSO window: close it and load *url* in the main window. */
  | { kind: 'finish'; url: string }

const SSO_START = /^\/v1\/auth\/sso\/[^/]+\/start$/
const SSO_CALLBACK = '/v1/auth/sso/callback'

function parse(url: string): URL | null {
  try {
    return new URL(url)
  } catch {
    return null
  }
}

function isWeb(target: URL): boolean {
  return target.protocol === 'https:' || target.protocol === 'http:'
}

/** A navigation (or redirect) of the main window to *url*. */
export function mainWindowNavigation(url: string, localOrigin: string): Decision {
  const target = parse(url)
  if (target === null) return { kind: 'deny' }
  if (target.origin === localOrigin) {
    return SSO_START.test(target.pathname) ? { kind: 'sso', url: target.href } : { kind: 'allow' }
  }
  return isWeb(target) ? { kind: 'external', url: target.href } : { kind: 'deny' }
}

/** A `window.open` or `target="_blank"` link from either window. */
export function newWindowRequest(url: string, localOrigin: string): Decision {
  const target = parse(url)
  if (target === null || target.origin === localOrigin || !isWeb(target)) return { kind: 'deny' }
  return { kind: 'external', url: target.href }
}

/** A navigation (or redirect) of the single sign-on window to *url*. */
export function ssoWindowNavigation(url: string, localOrigin: string): Decision {
  const target = parse(url)
  if (target === null || !isWeb(target)) return { kind: 'deny' }
  if (target.origin === localOrigin) {
    // The start and callback routes go through the local proxy; anything
    // else on the local origin is the app's /login/sso page.
    if (target.pathname.startsWith('/v1/auth/sso/')) return { kind: 'allow' }
    return { kind: 'finish', url: target.href }
  }
  // The provider sends the browser to SSO_CALLBACK_URL, the backend's own
  // address: take the same request through the local origin, where the
  // binding cookie set by the start route lives. The backend checks the
  // state against that cookie, so a forged callback gains nothing.
  if (target.pathname.endsWith(SSO_CALLBACK)) {
    return { kind: 'load', url: `${localOrigin}${SSO_CALLBACK}${target.search}` }
  }
  return { kind: 'allow' }
}
