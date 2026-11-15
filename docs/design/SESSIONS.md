# Browser sessions (issue #524)

Status: draft, written before the code by the `threat-model` skill; a person
reviews it before it guides more work · Issue: #524 (refs #512, #522) · SDL
practice 3

## What is built

The browser no longer holds a credential JavaScript can read. Password login
(`POST /v1/auth/login`) and the SSO handoff exchange
(`POST /v1/auth/sso/exchange`) set three cookies instead of returning a token:
a short access token (`fabrique_session`, `HttpOnly`), a refresh token
(`fabrique_refresh`, `HttpOnly`, path `/v1/auth`) and a CSRF token
(`fabrique_csrf`, readable by the page). The refresh token is one of a
**family** stored server-side (hashed); each use rotates it, a reused one
revokes the family, and the family dies at an absolute expiry. Bearer callers
(API tokens, scripts) are unchanged; a non-browser client can still ask login
for a Bearer access token with its password.

## Assets

- A person's session: whoever holds it acts with every company role the
  person has.
- `JWT_SECRET_KEY` (signs access tokens and keys the stored digests).
- The refresh-token families (`session_families`, `refresh_tokens`): only
  HMAC-SHA256 digests (under `JWT_SECRET_KEY`) of the browser's values.
- Availability of sign-in (a bad rotation rule logs everyone out).

## Data flow and trust boundaries

```
Browser ─B1─ POST /v1/auth/login {email, password}        ─┐
Browser ─B1─ POST /v1/auth/sso/exchange {code}            ─┤ Set-Cookie: fabrique_session (HttpOnly, Path=/)
                                                           │            fabrique_refresh (HttpOnly, Path=/v1/auth)
                                                           │            fabrique_csrf   (readable, Path=/)
                                                           └ body: {expires_at, csrf_token, ...}; no token
Browser ─B1─ any route, cookie + X-Fabrique-CSRF on unsafe methods
               └ auth_deps: Bearer first; else cookie JWT → family live? → CSRF matches?
Browser ─B1─ POST /v1/auth/refresh (cookie + CSRF) → rotate; reuse → revoke family
Browser ─B1─ POST /v1/auth/logout  (cookie + CSRF) → revoke family, clear cookies
Browser ─B1─ GET  /v1/auth/csrf    → the CSRF cookie's value (a page on another host)
Owner   ─B1─ PUT …/sso require_sso, DELETE /v1/company/{id}/members/{user} → revoke families
Script  ─B1─ Authorization: Bearer <API token | login token> (unchanged, no CSRF)
```

No new boundary: everything crosses B1.

## Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| S1 | B1 | I | A script in the page (XSS through rendered agent output, a compromised dependency) reads the token and uses it elsewhere | M | H | High | New: the access and refresh tokens are `HttpOnly` cookies and never in a response body for the browser flow; the frontend persists no credential (test); a Bearer token is issued only for a password, never from a cookie, so a script cannot mint a portable token from a live session |
| S2 | B1 | S | CSRF: another site makes the browser send an unsafe request with the cookie | M | H | High | New: cookies `SameSite=Strict`; unsafe methods authenticated by cookie also need `X-Fabrique-CSRF` equal to the `fabrique_csrf` cookie **and** to the digest signed into the access token (signed double submit, so a cookie planted from a sibling host does not pass); CORS credentials only with explicit origins (S6); tests |
| S3 | B1 | S | A stolen refresh token is used in parallel with its owner | L | H | Medium | New: rotation on every use; a token used twice revokes its whole family, including the thief's new token (test); absolute expiry `REFRESH_TOKEN_ABSOLUTE_DAYS` |
| S4 | B1 | D | Two tabs refresh at once and the second use reads as reuse, logging the person out | H | L | Medium | New: a reuse within `REFRESH_REUSE_GRACE_SECONDS` of its rotation, while its successor is unused, answers 409 without issuing anything and without revoking; the frontend retries its request (the cookie jar already holds the new pair) and refreshes once per tab at a time; any other reuse revokes |
| S5 | B1 | E | A session outlives the reason it was allowed: SSO becomes required, or a member is removed | M | M | Medium | New: turning `require_sso` on revokes the password-issued families of the company's members who are not on the break-glass list; removing a member revokes that person's families; access tokens carry their family id and are refused once it is revoked (checked on every request) |
| S6 | B1 | I | `allow_credentials` with a wildcard origin lets any site read authenticated responses (Starlette echoes the origin when cookies are sent) | M | H | High | New: credentials are allowed only when `CORS_ORIGINS` lists explicit origins; `*` turns credentials off with a warning (test) |
| S7 | B1 | I | A cookie sent over plain HTTP | L | H | Medium | New: `Secure` by default; `SESSION_COOKIE_SECURE=false` only for `http://` development, with a startup warning whenever it is off |
| S8 | B1 | I | The refresh table is read (backup, SQL injection elsewhere) and its values replayed | L | H | Medium | New: only HMAC-SHA256 under `JWT_SECRET_KEY` of 32 random bytes is stored (a copy is useless without the key); the CSRF value likewise |
| S9 | B1 | D | The refresh endpoint flooded or used to probe tokens | M | L | Low | New: `REFRESH_RATE_LIMIT` (slowapi, per address); 256-bit tokens; families past their absolute expiry purged at each sign-in |
| S10 | — | R | No record of when a session was revoked and why | M | L | Low | New: the family row keeps `revoked_at` and `revoked_reason` (`logout`, `reuse`, `sso_required`, `member_removed`); member removal also writes an audit row |
| S11 | B1 | S | Session fixation: a family started by an attacker carried into the victim's sign-in | L | M | Low | New: every login and SSO exchange starts a new family and new cookies; nothing is adopted from cookies already present (after Flow's "rotate the session on every authentication") |
| S12 | B1 | E | Bearer automation broken, or a request with both credentials treated as a cookie request and so exposed to CSRF rules or the other way round | M | M | Medium | New: `Authorization: Bearer` wins whenever present (an invalid Bearer is 401 even with a good cookie); API tokens and Bearer access tokens skip the CSRF check exactly as before (tests) |

Assume breach: with code running in the page, an attacker can still act
**through** the browser while it is open (any same-origin request carries the
cookie and can read the CSRF value); what changes is that nothing it can read
works from elsewhere, and logout or revocation ends it at once. If
`JWT_SECRET_KEY` leaks, an attacker can mint access tokens without a family
claim, which the server accepts as Bearer tokens for at most their lifetime,
and could compute the stored digests; rotating the key ends every access token
and every family at once, since the refresh tokens' digests are keyed with it
(`docs/OPERATIONS.md`, Rotate keys, which also gives the statement that ends
every family without rotating). No step lets a model decide anything.

## Tests that prove the mitigations

`backend/tests/test_sessions.py` (cookie flags, no token in the login body,
rotation, reuse revoking the family, the grace window, absolute expiry,
logout, CSRF required on unsafe cookie requests and not on Bearer, a request
with both treated as Bearer, revocation on `require_sso` and member removal,
the SSO exchange setting the cookies), `test_cors_credentials.py`, and
`frontend/src/store/useAuthStore.test.ts` (nothing credential-like in
`localStorage` or `sessionStorage`, 401 → refresh → retry, logout).

## Residual risks

- A Bearer access token from `POST /v1/auth/login` with
  `"token_delivery": "bearer"` has no family: it lives
  `ACCESS_TOKEN_EXPIRE_MINUTES` (15 by default) and is not ended by logout or
  revocation. Automation should use API tokens, which are revocable.
- Revoking a person's families on member removal signs them out of every
  company, since sessions are not company-scoped.
- XSS can still act in the open tab (see assume breach). The CSP and output
  encoding stay the controls for that.
- The rate limiter's storage is per process by default
  (`RATE_LIMIT_STORAGE_URI`); `/v1/auth/login` has no limit of its own yet.
- The desktop app's own residual risks are under Desktop app, below.
- A person invited into a company that already requires SSO keeps a
  password-started session until it expires or is revoked; only turning the
  requirement on revokes.
- Cookies are not `__Host-` prefixed (development over plain HTTP must keep
  working); the signed double submit covers the cookie-tossing case for CSRF.

## Open questions

1. A per-session list in the UI ("sign out everywhere", device list)? The
   families make it possible; not built.
2. A page served from another host than the API cannot read the CSRF
   cookie and, after a reload, learns the value from `GET /v1/auth/csrf`
   when a 403 carrying `X-Fabrique-CSRF: required` asks for it. Is one site
   (the same registrable domain) a deployment requirement worth enforcing at
   startup?

## Design

- **Tables** (`models/session.py`, migration `0023`): `session_families`
  (public id, user, method `password`|`sso`, CSRF digest, created, absolute
  expiry, last used, revoked at and reason) and `refresh_tokens` (family,
  sha256 of the token, created, used at). Logic in
  `services/platform/sessions.py`; cookies and the CSRF check in
  `backend/app/session_cookies.py` and `auth_deps.py`.
- **Access token claims** gain `sid` (the family's public id) and `csrf` (the
  CSRF digest) for the browser flow; a token without `sid` is a Bearer token.

## Desktop app

Status: draft, written with the `threat-model` skill; a person reviews it ·
SDL practice 3

**What is built.** The packaged Electron app used to load the frontend from
`file://`, which is not the API's site, so the `SameSite=Strict` cookies were
never sent and sign-in did not hold. Its main process now starts a small HTTP
server (`frontend/electron/local-server.ts`, Node's `http` only, no new
dependency) bound to `127.0.0.1` on a port the system picks at each launch. It
serves the built frontend (`dist/`, `index.html` for the app's routes) and
forwards `/v1/*` to the backend the app is configured with (`BACKEND_URL`,
default `http://localhost:8000`), then the window loads
`http://127.0.0.1:<port>/`. The page and the API share that origin: the
cookies are first-party, the page reads `fabrique_csrf` for the double
submit, and the desktop build calls the API on its own origin
(`vite build --mode electron` sets `VITE_API_SAME_ORIGIN`, so `API_BASE` is
empty). The Electron dev mode is unchanged (the Vite dev server on localhost
and the backend on localhost are the same site).

- **Cookies.** `Set-Cookie` comes back with its `Domain` removed, so the
  cookie binds to `127.0.0.1`; `Path`, `HttpOnly`, `SameSite`, `Max-Age` and
  `Secure` are kept. Electron's Chromium treats `http://127.0.0.1` as a secure
  context and stores `Secure` cookies from it (checked in the smoke run
  below), so nothing changes on the backend: `SESSION_COOKIE_SECURE` stays on.
- **Who may call it.** A request whose `Host` is not exactly
  `127.0.0.1:<port>` is refused (DNS rebinding); so is one whose `Origin` is
  any other origin, one whose `Sec-Fetch-Site` is `cross-site` or `same-site`
  (another port of 127.0.0.1 is the same site, and `SameSite=Strict` would let
  its requests carry the cookies), and an unsafe method without the local
  `Origin`. The target must be origin-form (`/path`), never `http://other/…`
  or `//other/…`. The forwarded request drops `Origin`, `Referer`,
  `Forwarded`, `X-Forwarded-*`, `Sec-Fetch-*` and hop-by-hop headers and sets
  `Host` to the backend's; the response drops `Access-Control-*` and
  `Strict-Transport-Security`, so the server never grants another origin a
  read even when the backend's `CORS_ORIGINS` would.
- **No open proxy.** Only `/v1` and `/v1/*` are forwarded, always to the one
  configured backend; everything else is a file under `dist/` (GET and HEAD
  only; a decoded `..`, a backslash or NUL is refused, and the resolved path
  must stay under `dist/`).
- **Headers on the page.** `Content-Security-Policy` (`script-src 'self'`
  without `unsafe-inline`, `connect-src 'self'`, `object-src 'none'`,
  `frame-ancestors 'none'`, `base-uri` and `form-action 'self'`; the `<meta>`
  policy in `index.html` also applies), `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy`, `Cross-Origin-Opener-Policy: same-origin` and
  `Cross-Origin-Resource-Policy: same-origin`, as `SECURITY.md` §3.3 asks.
- **Windows.** `contextIsolation`, `sandbox`, no `nodeIntegration`, never
  `webSecurity: false`. The main window stays on the local origin; any other
  web link opens in the system browser, and no new Electron window opens
  (`frontend/electron/navigation.ts`). IPC answers only a page on the local
  origin.
- **Single sign-on.** The login page's navigation to
  `/v1/auth/sso/<key>/start` opens a separate window with no preload script.
  It goes through the local server (the binding cookie binds to the local
  origin), visits the identity provider, and the provider's redirect to the
  backend's callback (`SSO_CALLBACK_URL`, any host, path
  `/v1/auth/sso/callback`) is loaded through the local origin instead, with
  the same query; the backend checks `state` against the binding cookie, so a
  forged callback gains nothing. The backend's redirect to
  `SSO_FRONTEND_URL/login/sso` is rewritten by the server to the local
  `/login/sso` (only on the `/v1/auth/sso/*` routes; a `Location` on the
  backend's own origin is moved to the local origin too), and the SSO window
  hands that URL to the main window and closes. No `SSO_FRONTEND_URL` change
  is needed for the desktop app.

**New boundary B10: other software on the user's computer ↔ the desktop
app's local server.** Any local process, and any web page in any browser on
the machine, can send requests to a port on 127.0.0.1.

```
Electron main window ─(http://127.0.0.1:<port>, same origin)─▶ local server ─B1─▶ backend
                                                        ▲
Other local processes, pages in other browsers ──B10────┘
SSO window ─▶ local /v1/auth/sso/<key>/start ─▶ provider (B8) ─▶ local /v1/auth/sso/callback ─▶ main window /login/sso
```

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| D1 | B10 | S | A page in another browser or another local server makes requests to the port that carry the app's session | L | H | Medium | The cookies live in Electron's own cookie store, never another browser's; inside the app, `Origin` must be the local origin if present and on every unsafe method, and `Sec-Fetch-Site` must be `same-origin` or `none` (another port of 127.0.0.1 is same-site); tests |
| D2 | B10 | S/T | DNS rebinding: a site whose name resolves to 127.0.0.1 talks to the port as its own origin | L | H | Medium | `Host` must be exactly `127.0.0.1:<port>`; tests |
| D3 | B10 | E | The server used as an open proxy to reach other hosts or other backend paths | L | M | Low | Only `/v1/*`, only to the configured backend, origin-form targets only; tests |
| D4 | B10 | I | The server reflects the backend's CORS grant to another origin | L | H | Medium | `Origin` is not forwarded, `Access-Control-*` is dropped from responses; tests |
| D5 | B10 | I | Path traversal reads files beside the build | L | M | Low | Decoded `..`, backslash and NUL refused; resolved path must stay under `dist/`; tests |
| D6 | B10 | S | Another program takes the port first | L | L | Low | The system picks a free port at each launch, so the app never assumes one; the server listens before the window loads |
| D7 | B8 | E | The identity provider's pages reach the app's privileged API (`window.electronAPI`) | L | H | Medium | The SSO window has no preload script; IPC refuses a sender not on the local origin (test); the main window never leaves the local origin |
| D8 | B8 | S | A forged callback is loaded through the local origin | L | M | Low | The callback carries the provider's `state`, checked by the backend against the binding cookie set at start (`services/platform/sso_flow.py`) |
| D9 | B1 | I | `Secure` cookies refused on `http://127.0.0.1`, pushing an operator to turn `SESSION_COOKIE_SECURE` off globally | L | M | Low | Electron's Chromium accepts them (smoke run); the backend setting is not touched |
| D10 | B1 | I | `BACKEND_URL` on plain http to another machine sends the cookies across the network in clear | M | H | High | New (from the sdl-review of this change): the server refuses to start unless `BACKEND_URL` is https, or http on localhost, 127.0.0.0/8 or `[::1]`; the app shows the reason and quits; test |

Assume breach: a process running as the user can read Electron's cookie
store from the profile on disk whatever the proxy does; the session's
15-minute access token and revocable refresh family (above) bound that. The
server holds no credential and adds no authority: a request through it is a
request to the backend with whatever cookies the app's own page holds.

**Tests.** `frontend/electron/local-server.test.ts`,
`frontend/electron/navigation.test.ts`,
`frontend/electron/ipc-handlers.test.ts` (`npm run test:run`), and
`src/viteConfig.test.ts` for the same-origin build. A smoke run on 2026-11-15
under Xvfb launched the packaged-mode app (`dist/` from
`npm run build -- --mode electron`, Electron 45) against a real backend on
Postgres: sign-in through the login form, the three cookies stored for
`127.0.0.1` with `Secure`, `HttpOnly` and `SameSite=Strict` kept, a reload
still signed in (`GET /v1/auth/me` 200), `POST /v1/auth/refresh` 200 with the
CSRF header and 403 without; an SSO start for an unknown company went through
the SSO window and landed the main window on the local
`/login/sso?error=unknown_connection`; `window.open` to an external page
opened no window. A full sign-in at a real identity provider was not run.

**Residual risks.** The callback is recognised by its path on any host, so an
`SSO_CALLBACK_URL` with another path breaks desktop SSO (the default and
documented path work). A local process running as the user can read the
cookie store (assume breach, above). The SSO window shows the provider's
pages inside the app, not the system browser, so a person sees no address bar
there.

## What was taken

From AixleHQ/flow (Apache-2.0, design only), `docs/design/federated-identity.md`
§4.2 and AD-6: a server-side session record is what makes revocation and
deprovisioning possible at all; the session identifier rotates on every
successful authentication; membership is re-validated against live rows on
every request rather than from a cache (here: the family is checked on every
cookie request). From OAuth 2.0 Security BCP (RFC 9700 §4.14): refresh-token
rotation with reuse detection that revokes the family. From the OWASP CSRF
Cheat Sheet: the signed double-submit cookie and the custom header. Not
taken: Flow's appended proofs and step-up gate, device lists, the Rails code.

For the desktop app: from Electron's security checklist (design only), the
limits on navigation and new windows, context isolation and the sandbox, and
checking the IPC sender; from Jupyter Server's local server (design only),
refusing any `Host` but the expected one against DNS rebinding; from the
Fetch Metadata guidance, refusing what `Sec-Fetch-Site` marks as sent by
another site.
