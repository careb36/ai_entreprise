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
- The packaged desktop app loads the frontend from `file://`; a page there
  is not same-site with the API, so `SameSite=Strict` cookies are not sent
  and sign-in does not hold (the Electron dev mode, served from the Vite dev
  server on localhost, works). Serving the packaged app from a local http
  origin or a privileged custom scheme is the follow-up.
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
