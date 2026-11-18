# Single sign-on (issue #512, phase 3)

Status: draft, written before the code by the `threat-model` skill; a person
reviews it before it guides more work · Issue: #512 · SDL practice 3 ·
Amended 2026-11-18 for the DNS verification of allowed domains (T16–T24,
again written before the code)

## What is built

An Owner connects the company to its identity provider over OpenID Connect
(Authorization Code with PKCE). A person signs in at that provider; the
backend validates the ID token itself, resolves the person to a La Fabrique
account by `(issuer, sub)`, applies the company's membership policy and issues
the same access token the password login issues. The Owner can require SSO
for the company's members, with a break-glass password path the operator
turns on. Each allowed domain is proved by a DNS TXT record before SSO may
create or link accounts in it (below, "Domain verification"). Google and Microsoft Entra ID are presets of the generic connection
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
- Which company holds an email domain: the right, through its IdP, to create
  and link accounts for addresses there.

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
Owner   ─B1─ GET …/sso/domains, POST …/sso/domains/{domain}/check
               └─B14─ TXT _la-fabrique-verify.<domain> (system or named resolver)
Re-check loop (backend, one replica) ─B14─ the same lookup every 24 h
```

**B8 (new): backend ↔ the company's identity provider.** The IdP is chosen by
a company Owner, so its URL is input from a tenant, and what it answers is an
observation of that provider, not an instruction. Its claims establish who the
person is at that provider and nothing else: the role always comes from La
Fabrique's membership (`docs/POSITIONING.md`, rule 1: a claim and an
observation never share a field).

**B14 (new): backend ↔ DNS.** What a resolver answers for a name the Owner
chose is an observation of DNS at that moment, through resolvers the backend
does not control, over UDP without DNSSEC validation by the backend. It can be
absent, stale or (on path) forged. Listing a domain is the Owner's claim; the
check rows are the observation; the status is derived only from the latter.

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
| T16 | B8 | E | An Owner lists a domain the company does not hold (the former top residual risk): with auto-join their IdP creates accounts for addresses there that do not exist yet, and an invitation of such an address later invites the squatter's account | H | H | Critical | New: SSO creates or links an account only when the email's domain is verified by DNS for this company, or is a legacy domain in its grace (`sso_domains.usable`, `sso_flow.resolve_identity`, refusal `domain_unverified`); tests for create and link refused, then allowed once verified |
| T17 | B14 | S | A forged DNS answer (on-path attacker or poisoned cache between the backend and its resolver, a compromised resolver) verifies a domain the company does not hold | L | H | High | New: the expected value carries a 256-bit token the backend issued, so a forgery must target this name, this token and the moment of the check; every answer is recorded (`sso_domain_checks.answer`); `SSO_DNS_NAMESERVERS` names a resolver the operator trusts (a validating one). Residual: no DNSSEC validation in the backend |
| T18 | B1 | T | The Owner supplies or replays the token, or reuses another company's published token | M | H | High | New: the token is generated by the backend (`secrets.token_urlsafe(32)`) per company and domain, stored, never read from a request; a domain removed and re-added gets a new one; the comparison is exact and constant-time; tests that tokens differ between companies and that a guessed value does not verify |
| T19 | B1 | D | A squatter lists a domain first to keep the real company out (the old first-come rule) | M | M | Medium | New: an unverified listing holds nothing; only a domain another company **verified** refuses the listing (409); the login page's domain lookup prefers the connection that may use the domain; tests |
| T20 | B14 | E | A domain changes hands (sold, expired and re-registered) and the previous company's verification lingers | M | H | High | New: every domain is looked up again each `SSO_DOMAIN_RECHECK_HOURS` (24) and a definite negative (no such name, no matching string) unverifies it, audited; a newer proof by another company first re-checks the older claim (`trigger: conflict`) and takes over only when it is gone; tests. Residual: the window up to one re-check interval |
| T21 | B14 | D | DNS failure, a lapsed record or the upgrade itself locks people out | M | H | High | New: a transient failure (timeout, SERVFAIL, no resolver) changes nothing; only creating and linking are gated, so an identity already linked keeps signing in and "require SSO" is unaffected; domains listed before this change are `legacy` and keep working for `SSO_LEGACY_DOMAIN_GRACE_DAYS` (30) while `SSO_LEGACY_DOMAIN_GRACE` is on (default), unless another company verified the domain; tests |
| T22 | B14 | I, D | The check is used to make the backend query arbitrary names (internal zones, a DNS side channel) or to flood resolvers | L | M | Low | New: only the company's own listed domains (validated, no public mail provider) at the fixed `_la-fabrique-verify.` prefix are looked up, any other name is a 404 before any lookup; a signed-in Owner only; `SSO_RATE_LIMIT` on the check; the answer bounded (20 strings of 1024 characters) and the resolver's errors reduced to a class name; the re-check takes at most 200 domains per 15 minutes; tests |
| T23 | — | R | A domain becomes verified or unverified with no record of why | M | M | Medium | New: each lookup is an append-only `sso_domain_checks` row (time, trigger, requester, outcome, the strings seen, kept when the domain is removed); each status change writes a signed, chained audit row (`sso_domain_verified`, `sso_domain_unverified`) |
| T24 | — | T | The Owner's claim and DNS's observation share a field (`docs/POSITIONING.md`, rule 1) | L | M | Low | New: `allowed_domains` stays the claim; `sso_domains.status` is set only by `sso_domains.check` from the backend's own lookup; the observations are their own table |

Assume breach: if the resolver the backend asks is compromised, it can
verify any listed domain for whoever listed it (T17) — but only for a company
whose Owner listed it and whose IdP then asserts addresses there, and every
answer it gave is in `sso_domain_checks`. If the IdP itself is compromised, its users can sign in as
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
too never linked, a domain verified by another company refused,
SSO-required blocking password login, break-glass, the requirement refused
before an Owner signed in, secret never returned, non-Owner and API token
refused, rate limit.

`test_sso_domains.py`, `test_sso_domain_sign_in.py` and `test_sso_dns_client.py`
with a fake DNS (`sso_fake_dns.py` replaces the adapter's `txt_records`; the
adapter's own tests replace dnspython's resolver; no network): tokens issued
per company and domain, readable by its Owner only; a published token
verifies and the observation and audit row are recorded; no such name, a
wrong value or no TXT do not verify; a transient failure keeps the status;
only listed domains are looked up; the re-check unverifies a withdrawn record,
verifies a published one and can be turned off; a verified domain cannot be
listed elsewhere and an unverified listing holds nothing; a newer proof
re-checks the older claim; create and link refused for an unverified domain,
a linked identity still signs in; legacy grace on, off, over and void once
another company verified; the domain lookup on the login page; migration
0049's backfill.

## Residual risks

- Sessions already issued (updated by #524, `docs/design/SESSIONS.md`):
  turning on "require SSO" now revokes the password-started sessions of the
  members it would refuse, and removing a member here
  (`DELETE /v1/company/{id}/members/{user_id}`, Owner) revokes theirs, at once
  (access tokens carry their family and are checked on every request).
  Removing someone **only at the IdP** still ends nothing: their session
  lasts until its absolute expiry (`REFRESH_TOKEN_ABSOLUTE_DAYS`, 7) unless an
  Owner removes them here; SCIM deprovisioning would close that. A Bearer
  access token from `"token_delivery": "bearer"` is not in a family and lives
  `ACCESS_TOKEN_EXPIRE_MINUTES` (15).
- Domain verification (T16–T24) trusts the resolver's answer: the backend does
  not validate DNSSEC itself. An operator who needs that names a validating
  resolver in `SSO_DNS_NAMESERVERS`.
- A domain that changes hands stays verified for its previous company until
  the next re-check (at most `SSO_DOMAIN_RECHECK_HOURS` plus 15 minutes), or
  until the new holder proves it, which re-checks the old claim at once.
- During the grace period (`SSO_LEGACY_DOMAIN_GRACE`, 30 days from the
  upgrade by default) a legacy domain carries the risk T16 describes, as it
  did before; the Owner verifies it in the meantime, and turning the grace off
  ends it at once.
- An identity linked while its domain was verified keeps signing in after the
  record lapses (on purpose, T21); removing the member is how an Owner ends
  that.
- A verified domain covers that exact domain only: `eu.acme.com` is listed
  and verified on its own.
- The DNS adapter (`services/platform/dns_client.py`) is not in
  `http_adapters`: `scripts/check_architecture.py` knows only HTTP libraries,
  so nothing yet stops another module importing `dns` directly. Adding `dns`
  to its list (a protected path) is the follow-up.
- A person in several companies cannot link SSO by email (T15); they keep
  their password, unless a company requires SSO, in which case an Owner of
  the other companies removes them first
  (`DELETE /v1/company/{id}/members/{user_id}`). Linking from inside a
  signed-in session (Flow's AD-25) is the follow-up.
- Once linked, an identity keeps resolving to its account even after the
  account joins other companies, so a company's IdP stays trusted for the
  people it already signed in.
- The IdP host is checked to resolve to public addresses and the request goes
  to that checked address with the host in `Host` and TLS SNI
  (`services/platform/egress.py`, `pin`), so a DNS answer that changes between
  the check and the connection cannot steer it; redirects are not followed
  and bodies are capped.
- `email_verified` is checked only when the IdP sends it (Entra does not);
  with Entra the tenant-pinned issuer is the guarantee.
- Users in several companies: one company requiring SSO makes that user's
  password login refused everywhere (the token is not company-scoped).
- The rate limiters' default storage is per process
  (`RATE_LIMIT_STORAGE_URI`), and so is the per-account login backoff.
  Password login itself is limited per address (`LOGIN_RATE_LIMIT`) and per
  account (`services/platform/login_throttle.py`).

## Open questions

1. Should a company be able to restrict sign-in to SSO for API tokens too?
   (Today tokens are company-scoped and created by Managers; unaffected.)
2. Multi-tenant Entra (`common`/`organizations`) is refused; needed?
3. SCIM deprovisioning (phase 4?). Server-side sessions exist since #524.
4. Should the grace for legacy domains default to off for new deployments
   (nothing is legacy there anyway), and should its 30 days be shorter?
5. Should a verified domain whose re-checks keep failing transiently (no
   definite answer for days) lapse after a maximum age?

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
- **Domain verification** (migration `0049`): `sso_domains` (one row per
  company and listed domain: the backend's token, `legacy`, the status, when
  it was verified and last checked, the last outcome) and `sso_domain_checks`
  (each lookup, append-only, no foreign key so it outlives the domain).
  `sso_domains.py` (platform) issues tokens on save (`sync`), checks
  (`check`: `verified`, `no_match`, `not_found`, `unavailable`,
  `verified_elsewhere`), answers `usable` for the flow, re-checks
  (`recheck_due`, the `run_rechecks` loop under the `sso-domain-recheck`
  lock) and serializes. `dns_client.py` is the resolver adapter (dnspython,
  ISC licence, since the standard library resolves only addresses): it
  returns the TXT strings and raises `DnsNameNotFound` or `DnsUnavailable`,
  never dnspython's exceptions. Router: `routers/sso_domains.py` (Owner).
  The record: `_la-fabrique-verify.<domain>` TXT
  `la-fabrique-domain-verification=<token>`: a token in a TXT record, as
  Google Workspace, Microsoft 365 and GitHub verify domains, under a prefix
  of its own (as GitHub's `_github-challenge-…`) so it never mixes with the
  apex's SPF or other records.
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

For the domain verification, from the same document: AD-17, a claimed domain
is a verified claim before it is trusted (design only, as this document's
first draft already cited it). The record's shape, the re-check, the grace
for legacy domains and the rule that a newer proof re-checks the older claim
are ours. From the reference agents named in `CLAUDE.md`: nothing; none of
them verifies a tenant's email domain.
