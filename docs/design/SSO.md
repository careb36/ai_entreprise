# Single sign-on (issue #512, phase 3)

Status: draft, written before the code by the `threat-model` skill; a person
reviews it before it guides more work · Issue: #512 · SDL practice 3 ·
Amended 2026-11-18 for the DNS verification of allowed domains (T16–T24,
again written before the code) · Amended 2026-11-18 for linking an identity
from inside a signed-in session (T25–T33, written with the tests, before the
code; T31's single transaction and T33 added after the sdl-review) · The
mapping of the provider's groups to teams (issue #703) is designed in
[`SSO_GROUP_MAPPING.md`](SSO_GROUP_MAPPING.md), not built

## What is built

An Owner connects the company to its identity provider over OpenID Connect
(Authorization Code with PKCE). A person signs in at that provider; the
backend validates the ID token itself, resolves the person to a La Fabrique
account by `(issuer, sub)`, applies the company's membership policy and issues
the same access token the password login issues. The Owner can require SSO
for the company's members, with a break-glass password path the operator
turns on. Each allowed domain is proved by a DNS TXT record before SSO may
create or link accounts in it (below, "Domain verification"). A signed-in
person can also link their account to their company's provider themselves,
and unlink it (below, "Linking in a session"). Google and Microsoft Entra ID are presets of the generic connection
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
Person ─B1─ POST /v1/auth/sso/link/start {company_id}  (session cookie + CSRF)
               sets the binding cookie, answers the IdP authorize URL (B8)
IdP ──302──> Backend /v1/auth/sso/callback  (the same route; a "link" state)
               └──302──> Frontend /account?sso_link=linked | sso_link_error=<code>
Person ─B1─ GET/DELETE /v1/auth/sso/identities[/{id}]
Owner  ─B1─ GET/DELETE /v1/companies/{id}/sso/identities[/{id}]
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
| T5 | B8 | E | IdP-asserted role or group grants access | L | H | High | New: no claim is read for authorization; new members get the connection's default role, Viewer or Manager, never Owner (validated on save; test). Issue #703 will read a groups claim to set membership of SSO-managed teams only, within the company and up to a cap (`SSO_GROUP_MAPPING.md`, G1, G2; not built) |
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

| T25 | B1 | S | **Link injection** (identity fixation): an attacker starts a link from their own session and gets the victim's browser to finish it (the victim, already signed in at the IdP, may pass through it without a prompt), so the attacker's account gains the victim's IdP identity — or the reverse, the attacker's code finishing in the victim's session | M | H | High | New: the link state is single use, 10 minutes, and its binding is sha256 of a random HttpOnly cookie value joined to the session's public id; the callback refuses unless the browser's cookie and one of the state user's **live** sessions reproduce it (`sso_link._bound`), before the code is exchanged; the email must also be the account's own (T27); tests for another browser, another person's state, an ended session, replay, expiry |
| T26 | B1 | S, E | Linking is started by a forged request, a stolen Bearer token or an API token | L | H | Medium | New: `POST /v1/auth/sso/link/start` takes only a browser session cookie (CSRF checked by `auth_deps`); a Bearer access token or an API token is 403, since neither has a session to bind to; the caller must be a member of the company named; tests |
| T27 | B8 | S, E | A company's IdP asserts another person's address and subject, and a member of that company links it to take over the person's account | M | H | High | New: the IdP's email (`email_verified` not false, as at sign-in) must equal the signed-in account's email, so a person can only link an identity for their own address; its domain must be allowed and **verified by DNS** for this company (a legacy domain's grace does not count: the single-company rule of T15 is not applied here, so only the proof that the company holds the domain will do); tests for a mismatched email, an unverified email, an unverified and a legacy domain |
| T28 | B8 | E | A subject already linked to one account is re-linked to another, or one account collects several subjects at the same issuer | L | H | Medium | New: an `(issuer, subject)` linked to someone else is refused (`identity_taken`; the unique constraint backs it in a race), one already linked to this account is `already_linked`, a second subject at the same issuer is refused at start (409) and at the callback (`identity_conflict`); tests |
| T29 | B1 | D | A person unlinks their only way to sign in and is locked out (no password reset exists) | M | M | Medium | New: oneself may unlink only while a password **that no SSO requirement refuses** (break-glass counted) or another identity through an enabled connection remains (409 `last_sign_in_method`); tests for an SSO-only account and a password refused by "require SSO" |
| T30 | B1 | E | An Owner of one company unlinks (or lists) an identity of another company's connection, or a person unlinks someone else's | L | M | Medium | New: the Owner routes act only on identities whose connection is the company's and whose user is still a member (404 otherwise); a person's own route only on their identities (404); the routes are in `authorization.json`; tests |
| T31 | — | R | A link or unlink leaves no record, or a failed audit write leaves the change without its row | M | M | Medium | New: every link callback writes a signed, chained `sso_identity_link` row (observed claims, outcome, refusals included) and every unlink an `sso_identity_unlink` row (issuer, subject, by self or owner, whether another sign-in remained, sessions ended). The change and its row are one commit: the new identity, or the deletion with the Owner's session revocations (`sessions.revoke_users(…, commit=False)`), is committed only by the audit write, and rolled back if it fails; tests that make the audit write fail for a link, a self-unlink and an Owner's unlink |
| T33 | B1 | T | Two links of one account at one provider race past the `identity_conflict` check (two tabs, two states) and leave the account with two subjects there | L | M | Low | New: a unique index on `sso_identities (user_id, issuer)` (migration `0050`, which stops on existing duplicates and changes nothing, as `0045` does); the losing link's flush fails, is rolled back and ends as `identity_conflict` (or `identity_taken` if its subject was the one taken), audited, never a server error; the sign-in's linking by email likewise; tests for the index, both races and the migration |
| T32 | B1 | E | An identity the Owner removes keeps working through sessions it already started | M | M | Medium | New: an Owner's unlink revokes the person's SSO-started session families at once (`sessions.revoke_users(…, method="sso")`, reason `sso_unlinked`); test. A person's own unlink ends nothing (they are using a session) |

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

`test_sso_link_integrity.py`: an audit write that fails leaves no link,
no self-unlink and no Owner unlink (identity kept, sessions not revoked); the
database refuses a second identity per account and provider; a link and a
sign-in racing past the check end as `identity_conflict`; migration 0050
stops on an existing duplicate without changing anything, then applies.

`test_sso_link.py` (the same fake IdP; a browser session from the password
login): a member of several companies links (PKCE verifier matches the
challenge, password kept, audit row) and then signs in by SSO as that
account; a replayed, expired, other-browser, ended-session and
other-person's state refused before the provider is asked; a mismatched or
unverified email, a subject linked to someone else, a second subject at
the same issuer, an unverified and a legacy domain refused; start refused
anonymously, by Bearer, without CSRF, outside the company and without a
connection; the person's listing hides the subject; self-unlink with a
password, refused as the last way in (SSO-only, or a password "require SSO"
refuses), refused for another's identity; an Owner unlinks a member's
identity (the Manager cannot) and ends their SSO sessions, and cannot
unlink another company's.

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
- A person in several companies cannot be linked by email at sign-in (T15);
  they link from their own Account page while signed in (T25–T28), which
  needs the company's domain verified by DNS, not in its legacy grace.
- Linking in a session trusts the company's provider for the person's
  address once its domain is verified: an Owner who controls both the IdP
  and the mail domain could already reach the mailbox. The person chose to
  link; an Owner of that company can unlink (T30, T32).
- The link state binds to a live session found from the state's user, not
  to the exact session that began it: a second live session of the same
  person in the **same** browser (a new login without logging out) also
  matches. Another browser or another person never does.
- An Owner's unlink can leave the person with no way to sign in (on
  purpose: the Owner distrusts that identity); the audit row records
  `other_sign_in: false`. Unlinking the identity an Owner used to prove the
  connection does not turn "require SSO" off.
- The desktop app moves only the callback's redirect to `/login/sso` into
  its window (`frontend/electron/local-server.ts`); a link landing on
  `/account` is not handled there yet.
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

## Decisions on the open questions (2026-11-19)

The owner delegated these decisions on 2026-11-19 ("the app is corporate and must scale: decide with the highest standards"). #684 tracks them, #717 is SCIM.

1. **API tokens under SSO-only sign-in: yes.** When a company requires SSO, a new API token can be minted only from a session that signed in through SSO, so a token is never stronger than the sign-in that made it. Tokens minted before the requirement keep working until they expire or are revoked, and the access review lists them as "minted without SSO". (NIST SP 800-63B §4 and 800-63C: credential issuance bound to the authenticated session; ISO/IEC 27001:2022 A.5.17.)
2. **Multi-tenant Entra (`common`, `organizations`): still refused.** Each company's connection is pinned to its own tenant id, and a token from any other tenant is refused. This closes the cross-tenant confusion Microsoft documents for multi-tenant apps that do not validate `tid`. A future need for B2B guests would be a list of named, allowed tenants, never `common`.
3. **SCIM deprovisioning: yes, deprovisioning first** (#717). Server-side sessions (#524) mean a deprovisioned account loses every session at once. Until SCIM lands, the reviewed directory sync (#716) is the stopgap.
4. **Legacy domain grace: off for new deployments, 14 days elsewhere.** `SSO_LEGACY_DOMAIN_GRACE` defaults to `false` when the deployment has no legacy domain, which is every new one. Where it is on, `SSO_LEGACY_DOMAIN_GRACE_DAYS` becomes 14, down from 30. Unverified ownership is a standing risk, so its window should be short.
5. **A verified domain whose re-checks never get a definite answer: lapses after 14 days.** A transient failure still changes nothing on the day. After 14 days without a definite positive answer the domain becomes unverified (`lapsed_unconfirmed`), the Owners are notified (personal notifications, #683) and the change is audited. Every failed re-check is counted in `/metrics` by reason (#684). (ISO/IEC 27001:2022 A.8.16 monitoring; fail closed on prolonged uncertainty.)

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
  Each re-check (`recheck` or `conflict`) and each cycle of the loop is
  counted for `/metrics` by outcome and a fixed set of reasons, never by
  company, domain or person (`sso_metrics.py`, issue #684;
  `docs/OPERATIONS.md`, Read `/health` and `/metrics`).
  The record: `_la-fabrique-verify.<domain>` TXT
  `la-fabrique-domain-verification=<token>`: a token in a TXT record, as
  Google Workspace, Microsoft 365 and GitHub verify domains, under a prefix
  of its own (as GitHub's `_github-challenge-…`) so it never mixes with the
  apex's SPF or other records.
- **Presets:** `google` → `https://accounts.google.com`; `entra` →
  `https://login.microsoftonline.com/{tenant}/v2.0` with a tenant GUID;
  `generic` → any https issuer.

- **Linking in a session** (`sso_flow_states.kind` takes `link`, its
  `user_id` and `binding_hash` hold the person and the binding; migration
  `0050` adds the unique index `ux_sso_identities_user_issuer`, T33):
  `sso_link.py` (platform) starts a link (`start`), finishes the callback's
  link states (`complete`, after `sso_flow.take_state` consumed it and
  `sso_flow.exchange_claims` validated the token, both shared with the
  sign-in), applies the rules (`link`), unlinks (`unlink`, with
  `other_sign_in`) and lists (`mine`, `of_company`). Router:
  `routers/sso_link.py`; the callback in `routers/sso.py` dispatches on the
  state's kind. Frontend: `pages/Account.tsx` and
  `components/SsoIdentities.tsx`.

- **Group mapping** (issue #703, not built; migration `0053`): the
  threat model (G1–G22), data model, EARS rules and the owner's
  decisions of 2026-11-19 are in
  [`SSO_GROUP_MAPPING.md`](SSO_GROUP_MAPPING.md).

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

For linking in a session: AD-25 of the same document (a signed-in user links
a further identity; design only). The binding of the state to the browser
and the live session, the email-must-match rule, the verified-domain
requirement without the legacy grace and the unlink rules are ours. From
the reference agents: nothing.
