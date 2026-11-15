# Single sign-on (issue #512, phase 3)

Status: draft, written before the code by the `threat-model` skill; a person
reviews it before it guides more work · Issue: #512 · SDL practice 3

## What is built

An Owner connects the company to its identity provider over OpenID Connect
(Authorization Code with PKCE). A person signs in at that provider; the
backend validates the ID token itself, resolves the person to a La Fabrique
account by `(issuer, sub)`, applies the company's membership policy and issues
the same access token the password login issues. The Owner can require SSO
for the company's members, with a break-glass password path the operator
turns on. Google and Microsoft Entra ID are presets of the generic connection
(issuer templates), not separate code paths. SAML is out of scope.

## Assets

- Accounts and company membership: who can act in which company, and as
  which role (Owner > Manager > Viewer).
- `JWT_SECRET_KEY` (signs every session) and `FERNET_SECRET_KEY` (encrypts the
  connection's client secret at rest).
- The client secret of each company's IdP application.
- The audit chain: one row per SSO sign-in attempt and per configuration
  change, and its integrity.
- Availability of sign-in (a misconfigured "require SSO" can lock a company out).

## Data flow and trust boundaries

```
Browser ─B1─ Backend /v1/auth/sso/{key}/start ──302──> IdP authorize (B8)
   ^           sets HttpOnly binding cookie
   |
IdP ──302──> Backend /v1/auth/sso/callback?code&state   (B1, browser-carried)
               ├─B8─ POST token endpoint (code + PKCE verifier + client secret)
               ├─B8─ GET discovery, JWKS (cached)
               ├── validate ID token, resolve identity, policy, audit row
               └──302──> Frontend /login/sso?code=<one-time handoff>
Browser ─B1─ POST /v1/auth/sso/exchange {code} → session cookies (as /auth/login, #524)
Owner   ─B1─ GET/PUT/DELETE /v1/companies/{id}/sso, POST …/sso/test
```

**B8 (new): backend ↔ the company's identity provider.** The IdP is chosen by
a company Owner, so its URL is input from a tenant, and what it answers is an
observation of that provider, not an instruction. Its claims establish who the
person is at that provider and nothing else: the role always comes from La
Fabrique's membership (`docs/POSITIONING.md`, rule 1: a claim and an
observation never share a field).

## Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| T1 | B8 | S | Forged or substituted ID token (wrong key, `alg: none`/HS256 confusion, token minted for another client) | M | H | Critical | New: signature checked against the issuer's JWKS with asymmetric algorithms only; `iss`, `aud` (and `azp` when several audiences), `exp`, `iat` with 60 s skew, `sub` required (`services/platform/oidc.py`, `validate_id_token`); tests for bad signature, wrong `aud`/`iss`, expiry, HS256 |
| T2 | B1 | S | Login CSRF / code injection: an attacker's `code` or `state` completes in the victim's browser | M | H | High | New: `state` random, stored hashed, single use (deleted with a row count of 1), 10-minute expiry, bound to an HttpOnly SameSite=Lax cookie set at start; `nonce` in the ID token; PKCE S256 (`services/platform/sso_flow.py`); tests for replayed, missing and other-browser state, wrong nonce |
| T3 | B8 | S | Email used as the join key lets one IdP take over an account | M | H | High | New: identity is `(issuer, sub)`; email links an existing account only once, only when `email_verified` is not false and the domain is allowed; a linked identity never moves by email (`sso_flow.resolve_identity`) |
| T15 | B8 | E | **Cross-tenant takeover** (found by the sdl-review of the first draft): any Owner can run their own IdP, list another company's domain and assert a victim's verified email; linking by email would hand them the victim's account and every company it belongs to — even after inviting the victim into their own company first | H | H | Critical | New: an existing account is linked by email only when it is a member of this connection's company **and of no other company**; auto-join never adopts an existing account (`account_exists`, `link_refused`); a domain can be listed by one company's connection only (first come); tests for an account outside the company, in another company too, and the invite-then-assert attack |
| T4 | B1 | E | Account pre-hijacking: someone registers `victim@corp` with a password before the victim, and an Owner invites that account | M | H | High | New: an account linked by email becomes SSO-only (password cleared) unless it is an Owner of the company (the break-glass path); recorded in the audit row; test |
| T5 | B8 | E | IdP-asserted role or group grants access | L | H | High | New: no claim is read for authorization; new members get the connection's default role, Viewer or Manager, never Owner (validated on save; test) |
| T6 | B8 | E | A company claims a public mail domain (`gmail.com`) and auto-joins strangers | M | M | Medium | New: public mail domains refused in `allowed_domains`; auto-join off by default; the allowed domains still only admit people the company's own IdP authenticated |
| T7 | B1 | E | A Manager, Viewer or API token changes the connection | L | H | High | New: every config route requires a signed-in human Owner (API tokens refused even with the Owner role); tests |
| T8 | B1 | I | The client secret leaks through the API, logs or audit | M | H | High | Existing: Fernet at rest (`crypto.py`). New: `serialize` returns `has_client_secret` only; the audit row records which fields changed, never values; test that no response contains the secret |
| T9 | B8 | I | SSRF: an Owner points the issuer at an internal service and reads it through "test" | M | M | Medium | New: https only, no credentials in the URL, no redirects followed, host must resolve to public addresses unless `SSO_ALLOW_PRIVATE_ISSUERS`, 1 MiB response cap, "test" reports parsed fields, never the body (`oidc.check_url`, `oidc_client.py`) |
| T10 | B1 | D | Sign-in endpoints flooded; JWKS refetch storm with unknown `kid`s | M | L | Low | New: slowapi limit on start, callback and exchange (`SSO_RATE_LIMIT`); JWKS cached an hour, refetched on an unknown `kid` at most once a minute; expired state rows purged on each start |
| T11 | B1 | D | "Require SSO" locks the company out (IdP down or misconfigured) | M | H | High | New: requiring SSO is refused until an Owner of the company has signed in through the enabled connection (after Flow's "verify before enforce"); the requirement is void while the connection is disabled; break-glass password path for Owners listed by the operator in `SSO_BREAK_GLASS_EMAILS`, audited (`docs/OPERATIONS.md`) |
| T12 | — | R | An SSO sign-in or configuration change leaves no record | M | M | Medium | New: audit row (signed and chained, `AgentService.audit_interaction`) on every completed callback with issuer, subject, email, company and outcome, on each break-glass login, and on each create/update/delete |
| T13 | B1 | I | The access token leaks through the redirect back to the SPA | L | H | Medium | New: the redirect carries a one-time handoff code (60 s, single use, stored hashed), exchanged by POST; `Referrer-Policy: strict-origin-when-cross-origin` already set (`security.py`) |
| T14 | B1 | S | Password login bypasses a company's SSO requirement | M | M | Medium | New: `/v1/auth/login` refuses, after the password check, a user who belongs to a company that requires SSO, unless break-glass applies; test |

Assume breach: if the IdP itself is compromised, its users can sign in as
anyone in its directory; the damage is bounded by La Fabrique roles (no
Owner by provisioning) and is recorded in the audit chain. If
`FERNET_SECRET_KEY` leaks, client secrets are exposed; rotate both
(`docs/OPERATIONS.md`, Rotate keys). No step lets a model decide anything.

## Tests that prove the mitigations

`backend/tests/test_sso_flow.py`, `test_sso_tokens.py`, `test_sso_admin.py`
with a fake IdP (RSA key generated in the test, discovery and JWKS served
through an `httpx.MockTransport`, no network): happy path, bad signature,
wrong `aud`/`iss`, expired token, HS256 refused, replayed / missing /
other-browser state, wrong nonce, unverified email, domain not allowed,
auto-join off, linking an invited member (and the password cleared, an
Owner's kept), an existing account outside the company or in another company
too never linked, a domain claimed twice,
SSO-required blocking password login, break-glass, the requirement refused
before an Owner signed in, secret never returned, non-Owner and API token
refused, rate limit.

## Residual risks

- Sessions already issued (updated by #524, `docs/design/SESSIONS.md`):
  turning on "require SSO" now revokes the password-started sessions of the
  members it would refuse, and removing a member here revokes theirs, at once
  (access tokens carry their family and are checked on every request).
  Removing someone **only at the IdP** still ends nothing: their session
  lasts until its absolute expiry (`REFRESH_TOKEN_ABSOLUTE_DAYS`, 7) unless an
  Owner removes them here; SCIM deprovisioning would close that. A Bearer
  access token from `"token_delivery": "bearer"` is not in a family and lives
  `ACCESS_TOKEN_EXPIRE_MINUTES` (15).
- `allowed_domains` is not verified by DNS (Flow's AD-17 makes it a verified
  claim): the first company to list a domain holds it, and with auto-join its
  IdP can create accounts for addresses there that do not exist yet; if the
  real person's company later invites such an address, it invites that
  account. T15's rules keep existing accounts out of reach; DNS verification
  of domains is the next step and should come before self-serve companies are
  widely open. **This is the top residual risk.**
- A person in several companies cannot link SSO by email (T15); they keep
  their password, unless a company requires SSO, in which case an Owner of
  the other companies must remove them first (no member-removal endpoint
  exists yet). Linking from inside a signed-in session (Flow's AD-25) is the
  follow-up.
- Once linked, an identity keeps resolving to its account even after the
  account joins other companies, so a company's IdP stays trusted for the
  people it already signed in.
- The IdP host is checked to resolve to public addresses before each fetch,
  but httpx resolves it again (DNS rebinding window); redirects are not
  followed and bodies are capped.
- `email_verified` is checked only when the IdP sends it (Entra does not);
  with Entra the tenant-pinned issuer is the guarantee.
- Users in several companies: one company requiring SSO makes that user's
  password login refused everywhere (the token is not company-scoped).
- The rate limiter's default storage is per process (`RATE_LIMIT_STORAGE_URI`).
- Login itself (`/v1/auth/login`) has no rate limit today; out of this scope.

## Open questions

1. Should a company be able to restrict sign-in to SSO for API tokens too?
   (Today tokens are company-scoped and created by Managers; unaffected.)
2. Multi-tenant Entra (`common`/`organizations`) is refused; needed?
3. SCIM deprovisioning (phase 4?). Server-side sessions exist since #524.

## Design

- **Tables** (`models/sso.py`, migration `0022`): `sso_connections` (one per
  company: slug, preset, issuer, client id, encrypted client secret, allowed
  domains, default role, auto-join, enabled, require SSO, last test),
  `sso_identities` (`(issuer, subject)` unique → user), `sso_flow_states`
  (hashed one-time `state` and handoff codes with expiry).
- **Modules** (platform context, since auth is platform's): `oidc_client.py`
  is the HTTP adapter (listed in `http_adapters`; returns data, raises
  `OidcUnreachable`/`OidcHttpError`); `oidc.py` validates discovery, caches
  JWKS and validates ID tokens with PyJWT (already a dependency; its
  `cryptography` backend is too, so nothing is added); `sso_connections.py`
  holds presets, validation and serialization; `sso_flow.py` the flow,
  identity resolution and the password policy. Routers: `routers/sso.py`
  (public), `routers/sso_admin.py` (Owner).
- **Presets:** `google` → `https://accounts.google.com`; `entra` →
  `https://login.microsoftonline.com/{tenant}/v2.0` with a tenant GUID;
  `generic` → any https issuer.

## What was taken from AixleHQ/flow (Apache-2.0, design only)

From `docs/design/federated-identity.md` and `app/services/auth/`:
identity is `(provider, subject)`, never email, and email only promotes an
assertion to an existing account under stated rules (`identity_resolver.rb`,
AD-3); every assertion is checked against the connection it claims to
satisfy, with no "try the other connections" retry (AD-13); the login `state`
shape — short-lived, single-use, the PKCE verifier and nonce kept server-side
and never in the URL, the flow bound to the browser that began it
(`auth/state.rb`); "verify before enforce" — a connection must have carried a
real sign-in by an admin before the company may depend on it (AD-7);
public-mail domains are not a trust anchor (§7.3); rate-limit every
credential-accepting endpoint (§7.6); an operator path outside the company's
policy, kept to a password (AD-19), which became the break-glass list here.
Not taken: SAML, SCIM, passkeys, server-side sessions, the per-company-entry
step-up gate, and the Rails code itself.
