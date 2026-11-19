# SECURITY.md — La Fabrique Multi-Agent Platform

**Version:** 1.1
**Owner:** the repository owner (accepts risk); every contributor and agent meets it
**Applies to:** FastAPI control plane, React/Vite frontend, LangGraph agents, the workspace daemon and NATS, PostgreSQL, Neo4j, the LLM proxy, Kubernetes deployments
**Last reviewed:** 2026-11-15 — checked against the code and against Microsoft SDL practices with the `sdl-program` skill (`.claude/skills/sdl-program/`), issue #522. 2026-11-18 — §5.4 and §17 (the exception register, the security metrics and the training notes) brought up to date by a programme reviewer with the same skill; the other sections were not re-checked on that date

**How to read this document.** Each section mixes *what is built* (it cites
the file that does it) and *requirements and targets* (rules every change is
held to, some of which the code does not meet yet). Where the code falls
short of a rule, the section says so under "Where the code stands" or marks
the item **planned**, with the issue that tracks it. A control described
without a file is a target, not a claim that it exists.

---

## Reporting a vulnerability

Report a vulnerability privately through this repository's **Security** tab
→ **Advisories** → **Report a vulnerability** (GitHub private vulnerability
reporting). Do **not** open a public issue, pull request or discussion for a
suspected vulnerability. Include the affected component, the version or
commit, the impact and the steps to reproduce.

Private vulnerability reporting must be enabled in the repository's settings
(Settings → Code security → Private vulnerability reporting); the repository
owner turns it on. Until it is enabled the button does not appear, and a
reporter should open a public issue that says only "security report, please
contact me", with no detail.

What happens next is §9: triage against the severity table in §9.2, a fix on
`main`, and a release (§15).

---

## 1. Purpose and Scope

This document defines the security requirements, secure coding rules, and review process for the La Fabrique multi-agent orchestration platform. It is binding for all agents, services, and pull requests that touch:

- FastAPI APIs and middleware
- React/Vite frontend and static asset delivery
- LangGraph agent graphs, prompts, and tool definitions
- PostgreSQL and Neo4j persistence layers
- The workspace daemon (`vscode-daemon/`), its sandbox and command policy, and the NATS bus between it and the backend
- Kubernetes manifests (`deployment/k8s/`; there are no Helm charts), container images, and CI/CD pipelines

The goal is to ensure the platform is secure by default, auditable, and resilient against both classic web/API attacks and LLM-specific risks such as prompt injection and tool-call abuse.

---

## 2. Threat Model Overview

Primary trust boundaries:

1. **End user ↔ Frontend** — browser-based attacks, token theft, XSS, CSRF, supply chain.
2. **Frontend ↔ FastAPI** — authentication, authorization, injection, rate limiting, TLS.
3. **FastAPI ↔ LangGraph Agents** — prompt injection, unsafe tool selection, data exfiltration.
4. **Agents ↔ External Tools/APIs** — tool-call abuse, excessive permissions, secret leakage.
5. **Services ↔ PostgreSQL/Neo4j** — database compromise, unauthorized queries, data leakage.
6. **CI/CD ↔ Kubernetes/Registry** — malicious images, leaked secrets, supply chain compromise.
7. **Backend ↔ NATS ↔ workspace daemon ↔ the user's disk** — anything that can publish a request can ask the daemon to write files and run commands on the machine that holds the projects (§8.3, §8.6).
8. **Backend ↔ a company's identity provider** — forged or replayed ID tokens, login CSRF, one company's provider asserting another company's people, SSRF through the issuer URL (§3.1, `docs/design/SSO.md`); and **backend ↔ DNS** (B14), where the TXT record proving a company's allowed domain is read: a forged or stale answer, a domain that changed hands, lookups of names the company does not list.
9. **Backend ↔ Slack and Microsoft Teams** — a public inbound endpoint that can accept a held delivery: forged or replayed interactions, a chat user acting as someone else, a role read from the payload, a stale click deciding a changed task, leaked bot tokens or signing secrets. Each request is verified by the platform's HMAC (Slack v0 with a five-minute timestamp; the Teams outgoing webhook's HMAC with the activity's timestamp), its id is stored once, the chat user must be linked by a one-time code to a La Fabrique account, the role comes from that account's membership exactly as on the web, and the decision is the web's own function, audited with `via` (`docs/design/CHAT_APPROVALS.md`).
10. **Other software on the user's computer ↔ the desktop app's local server** — the packaged Electron app serves its page and forwards `/v1/*` to the backend from `http://127.0.0.1:<port>` so the session cookies are first-party; any local process or browser page can reach that port. It refuses a foreign `Host` (DNS rebinding), another `Origin`, a request `Sec-Fetch-Site` marks as from another site and an unsafe method without its own `Origin`, forwards only `/v1/*` to the configured backend, never reflects a CORS grant, and keeps the identity provider's pages away from the app's IPC (`docs/design/SESSIONS.md`, Desktop app).

10. **Backend ↔ an RFC 3161 timestamping authority and the customer's anchor endpoints** — the audit chain is keyed by the factory's own key, so its head is anchored outside (issue #542, `docs/design/AUDIT_ANCHORS.md`): a token from the authority the Owner chose is checked against the certificate the Owner pinned, never against what the answer says; a customer-held Ed25519 key signs heads the factory cannot forge; a webhook's receipt is a claim. Threats: an insider re-signing the chain with `AUDIT_SIGNING_KEY`, a fake authority, a malformed token, SSRF through the authority's URL, a receipt taken as proof. An auditor verifies with the authority's published certificate and the customer's own public key, not with the factory's copies.

Key threat actors: malicious end users, compromised external content, malicious model output, insider developers, CI/CD attackers.

Reference frameworks:
- OWASP Top 10 for LLM Applications
- OWASP ASVS 4.0
- OWASP Kubernetes Top 10
- NIST 800-218 SSDF

---

## 3. Authentication and Authorization

### 3.1 Identity: what is built

La Fabrique authenticates people itself, by password, or through a company's
OpenID Connect provider (single sign-on, below).

- **Passwords** are hashed with `bcrypt` directly, cost 12
  (`backend/app/passwords.py`). A password chosen now has at least
  `PASSWORD_MIN_LENGTH` characters (12; WSTG-v42-ATHN-07) and at most
  bcrypt's 72 bytes, refused rather than truncated
  (`passwords.check_new_password`, called from `backend/app/schemas.py`); the
  rule is not applied at sign-in, so an account with an older, shorter
  password is not locked out. A successful login re-hashes a stored hash
  whose cost is below the current one (`backend/app/routers/auth.py`).
- **One account per address.** Emails are stripped and lower-cased wherever
  they are compared (`services/contracts/emails.py`: registration, sign-in,
  invitations, lookups); migration 0045 normalised the stored rows (stopping,
  never merging, on a collision) and a unique index on `lower(email)` keeps
  it.
- **Guessing and enumeration** (issue #541; WSTG-v42-ATHN-03, IDNT-04).
  `POST /v1/auth/login` is limited per client address (`LOGIN_RATE_LIMIT`,
  10/minute) and per account: after `LOGIN_FAILURES_BEFORE_BACKOFF` (5)
  failures within `LOGIN_FAILURE_WINDOW_SECONDS` the account's password
  sign-in is locked for 2 s, then twice as long after each further failure, at
  most `LOGIN_BACKOFF_MAX_SECONDS` (15 minutes); a locked account answers 429
  with `Retry-After` before its password is checked, and a success clears the
  record (`services/platform/login_throttle.py`, keyed by the sha256 of the
  email whether or not an account exists; at most 10,000 records, and making
  room drops one without a running lock first, so guesses spread over other
  addresses cannot evict the attacked account's lock). `POST /v1/auth/register` is limited
  per address (`REGISTER_RATE_LIMIT`, 5/minute) and answers the same body for
  a new and an existing email, after a bcrypt hash and an insert either way
  (the unique index refuses the taken one), leaving the existing account
  untouched. Login answers an unknown email, an account
  without a password and a wrong password alike, after a bcrypt check against
  a throwaway hash for the first two (`passwords.burn_verify`). Behind a
  reverse proxy the per-address limits key on the client only when the proxy
  is listed in `TRUSTED_PROXY_IPS` (empty by default: `X-Forwarded-For` is
  ignored); then on the right-most address of that header that is not a
  listed proxy (`security.TrustedProxyMiddleware`, `docs/OPERATIONS.md`).
- **Invitations** (WSTG-v42-IDNT-04). `POST /v1/company/invite` answers 202
  with the same body whether or not the address has an account (it used to
  answer 404 "User not found", so anyone who created a company could test
  addresses) and adds nobody: it records a pending invitation with an expiry
  (`INVITATION_TTL_HOURS`) and a single-use token kept only as its sha256.
  Only the invitee, signed in with that address, accepts or declines it;
  acceptance alone creates the membership. A Manager invites Viewers and
  Managers, only an Owner invites an Owner, never with an API token; invite,
  accept, decline and revoke are audited (`services/platform/invitations.py`).
- **Access tokens.** A JWT signed with **HS256** and the shared secret
  `JWT_SECRET_KEY` (`backend/app/auth_tokens.py`, `backend/app/config.py`).
  Claims: `sub`, `email`, `iat`, `exp`, `typ: access`, `iss` (`JWT_ISSUER`,
  `la-fabrique`), `aud` (`JWT_AUDIENCE`, `la-fabrique-api`), and for a
  browser session `sid` (its family) and `csrf` (the digest of its CSRF
  token). It lives `ACCESS_TOKEN_EXPIRE_MINUTES`, **15 minutes** by default.
  Validation checks the signature, the single configured algorithm (which must
  be HS256, HS384 or HS512: `none` or an asymmetric name refuses to sign or
  verify), `exp`, `iss`, `aud`, and that `sub`, `exp`, `typ`, `iss` and `aud`
  are present and `typ` is `access`; a token with a `sid` is refused once its
  family is revoked or expired, on every request. The Glassroom ticket has
  its own audience (`<JWT_AUDIENCE>:glassroom`), so neither passes for the
  other (issue #541, WSTG-SESS-10 at latest@87cf4a4). Tokens issued before
  `iss` and `aud` existed are refused: a browser renews its access token from
  the refresh cookie without noticing, a script's 15-minute Bearer token logs
  in again. There is no `nbf` claim. A `JWT_SECRET_KEY` shorter than the
  hash (32 bytes for HS256, 48 for HS384, 64 for HS512; RFC 7518 §3.2)
  counts as unset, so login answers 503 rather than sign with a key that can
  be guessed offline from one token (`auth_tokens.MIN_KEY_BYTES`).
- **Browser sessions** (issue #524; design and threat model in
  `docs/design/SESSIONS.md`). Password login and the SSO exchange set three
  cookies and return no token (`backend/app/session_cookies.py`):
  `fabrique_session` (the access token; `HttpOnly; Secure; SameSite=Strict;
  Path=/`), `fabrique_refresh` (the refresh token; `HttpOnly; Secure;
  SameSite=Strict; Path=/v1/auth`) and `fabrique_csrf` (readable by the page).
  Refresh tokens are 32 random bytes, stored only as HMAC-SHA256 under
  `JWT_SECRET_KEY` in a family (`session_families`, `refresh_tokens`,
  `services/platform/sessions.py`); `POST /v1/auth/refresh` rotates the token
  exactly once, a token used twice revokes its family (except two tabs racing
  within `REFRESH_REUSE_GRACE_SECONDS`, answered 409 with nothing issued), a
  family ends `REFRESH_TOKEN_ABSOLUTE_DAYS` (7) after sign-in, and the route is
  rate-limited (`REFRESH_RATE_LIMIT`). `POST /v1/auth/logout` revokes the
  family and clears the cookies. `Secure` is relaxed only by
  `SESSION_COOKIE_SECURE=false`, for plain-http development, with a startup
  warning.
- **CSRF.** The cookies are `SameSite=Strict`, and an unsafe method
  authenticated by the cookie must also send `X-Fabrique-CSRF` equal to the
  `fabrique_csrf` cookie and to the digest signed into the access token (a
  signed double submit: a cookie planted from a sibling host does not pass);
  refresh and logout check it against the family. CORS allows credentials only
  with the explicit origins of `CORS_ORIGINS`; `*` turns credentials off
  (`backend/app/security.py`).
- **Transport of the token.** `Authorization: Bearer` first
  (`backend/app/auth_deps.py`, `HTTPBearer`): an API token, or a Bearer access
  token a script gets from `POST /v1/auth/login` with `"token_delivery":
  "bearer"` (no family, no refresh, the password required). A Bearer request
  is never CSRF-checked, and a request with both a Bearer header and the
  cookie is treated as Bearer. Otherwise the `fabrique_session` cookie. The
  Glassroom stream, which a browser `EventSource` cannot give a header, takes
  a five-minute ticket for one sprint instead (`create_stream_ticket`, `typ:
  glassroom`, issued on the cookie; membership is checked again when it is
  used).
- **Without `JWT_SECRET_KEY`** login answers 503 and the backend warns at
  startup (`backend/app/lifecycle.py`).
- **API tokens** for external callers (`POST /v1/auth/tokens`,
  `backend/app/services/platform/api_tokens.py`): one company per token, a
  role (`Viewer` by default, `Manager` at most, never above its creator's,
  when minted and on every use: a token above its creator's current effective
  role, or whose creator left the company, is a 401 audited
  `api_token_refused` with the reason; the creator is matched by account
  id, so a renamed or reused email address changes nothing),
  an expiry (90 days by default, at most 365), revocable. Only the token's
  sha256 is stored; the raw value (`fab_` + 32 random bytes) is shown once.
  A request made with one acts as `api-token:<id>`, so audit rows name it.
- **Rotation and revocation.** Changing `JWT_SECRET_KEY` ends every session
  (access tokens no longer verify; refresh tokens, stored as HMAC under the
  key, match nothing) and stream ticket; API tokens keep working
  (`docs/OPERATIONS.md`, Rotate keys). Turning "require SSO" on revokes the
  password-started families of the members it would refuse, and removing a
  member (`DELETE /v1/company/{id}/members/{user_id}`) revokes theirs.

- **Single sign-on** (issue #512, phase 3; design and threat model in
  `docs/design/SSO.md`). An Owner configures one OpenID Connect connection
  per company (`/v1/companies/{id}/sso`, `backend/app/routers/sso_admin.py`);
  Google and Microsoft Entra ID are presets of the same mechanism. The client
  secret is encrypted with `FERNET_SECRET_KEY` and never returned. Sign-in
  (`backend/app/routers/sso.py`, `services/platform/sso_flow.py`) is
  Authorization Code with PKCE, a one-time `state` bound to the browser by an
  `HttpOnly` cookie, and a nonce. The backend validates the ID token itself
  (`services/platform/oidc.py`): signature with asymmetric algorithms only,
  `iss`, `aud`, `exp`/`iat` with `SSO_CLOCK_SKEW_SECONDS` of leeway, the nonce,
  and `email_verified` when present. The person is identified by
  `(issuer, sub)`; the provider's claims establish identity only, and the role
  always comes from La Fabrique's membership. A provider is trusted only for
  its own company: an existing account is linked by email only when it
  belongs to that company alone, auto-join never adopts an existing account,
  one company at most lists a given domain, and public mail domains are
  refused. The provider is reached only through
  `services/platform/oidc_client.py` (https, public addresses re-checked
  before each fetch unless `SSO_ALLOW_PRIVATE_ISSUERS`, no redirects, a 1 MiB
  cap). A one-time, 60-second code is exchanged for the same browser session
  the password login starts. An Owner can require SSO for the company's members
  once the connection is enabled and an Owner has signed in through it;
  Owners listed in `SSO_BREAK_GLASS_EMAILS` keep their password, and each
  such sign-in is audited. Every sign-in and every configuration change
  writes an audit row (never the secret); the public routes are limited by
  `SSO_RATE_LIMIT`.
  Allowed domains are verified by DNS: the backend issues a random token per
  company and domain, the Owner publishes it as a TXT record at
  `_la-fabrique-verify.<domain>`, and SSO creates or links accounts only in a
  verified domain (or a domain listed before the upgrade, during the grace
  set by `SSO_LEGACY_DOMAIN_GRACE`). Each lookup, by the Owner or the daily
  re-check, is recorded with the time and the strings seen, and each status
  change is audited; a domain verified by one company cannot be listed by
  another (`services/platform/sso_domains.py`, `dns_client.py`).
- **Known gaps.** The DNS verification trusts the resolver's answer (no
  DNSSEC validation by the backend; `SSO_DNS_NAMESERVERS` can name a
  validating resolver), a domain that changes hands stays verified for its
  previous company until the next re-check, and legacy domains carry the old
  risk until their grace ends; someone removed only at the provider keeps a session
  until it reaches its absolute expiry or an Owner removes them here; a Bearer
  access token from `"token_delivery": "bearer"` has no family, so logout and
  revocation do not reach it (it lives `ACCESS_TOKEN_EXPIRE_MINUTES`); the
  login limits and the per-account backoff are per process (several replicas
  each keep their own), and someone who knows an address can keep its owner's
  password sign-in locked while they keep failing (SSO and open sessions are
  unaffected); registration is open and unverified, so the answer of the
  following login still tells a new address from a taken one (closing that
  needs a confirmation mail, not built).

### 3.2 API Authorization: what is built

- **Enforcement is on by default.** `AUTH_REQUIRED` defaults to `true`
  (`backend/app/config.py`); with it, a protected route without a valid token
  answers 401 (`backend/app/auth_policy.py`, `current_user_if_required`,
  `require_authenticated`). `AUTH_REQUIRED=false` is for a local demo only:
  a token, when sent, is still resolved, but none is required.
- **Roles per company**, ranked **Owner > Manager > Viewer**
  (`ROLE_RANK` in `backend/app/services/platform/teams.py`, re-exported by
  `backend/app/auth_policy.py`). Every company-scoped route
  calls `require_company_role(session, user, company_id, minimum)`: a caller
  who is not a member gets 403, a member below the minimum gets 403. The role
  checked is the member's **effective role**: their own membership role raised
  by the roles of the teams they belong to in that company
  (`services/platform/teams.py`, `docs/design/TEAMS_AND_USAGE.md`). A team's
  role is Viewer or Manager, never Owner; a team never makes anyone a member
  and never reaches another company; teams are created, changed and joined
  only by a signed-in human Owner (never an API token), and every change is
  audited. An API token carries its own company and role and is held to the
  same check; it is minted at most at its creator's effective role.
  Creating a company makes its creator the Owner; inviting a member requires
  Owner. Help-desk administrators are a separate, deployment-wide list
  (`SUPPORT_ADMIN_EMAILS`, `is_support_admin`).
- **Segregation of duties** (issue #543,
  `docs/design/SEGREGATION_OF_DUTIES.md`). A role says what a person may do;
  this says what they may not do to their own work. The account that started
  a sprint does not accept its held deliveries (review, board or chat) or merge its runs
  or its integration, and the person who drafted a specification does not
  approve it (`services/platform/sod.py`, called at each approval point with
  requesters read from records, never a request body). A refusal is a 403 and
  a committed `sod_refused` audit row; an API token acts for its creator. The
  rule is on by default for high-risk work; turning it off or widening it is
  an Owner's audited choice. A company where one person can approve gets an
  explicit, audited single-person exception with a reason, never a silent
  bypass, and it lapses when a second person can approve. The report
  (`GET /v1/companies/{id}/sod/report`) lists every approval with requester
  and approver, from the records.
- **Access review** (PCI DSS 4.0 Req. 7.2.4, structure only). Every 90 days
  by default an Owner confirms or removes each member (own, team-derived and
  effective role, SSO and chat links, last observed activity) and each
  working API token (`/v1/companies/{id}/access-review`); inactive accounts
  are flagged, a review past its 14-day grace is flagged missed and recorded
  late, and removals apply at once. The service accounts inventory
  (`/v1/companies/{id}/service-accounts`) names every credential that acts
  without a person, with owner, scope and age, never a value.
- **Every route declares who may call it** (issue #541; WSTG-v42-ATHZ-02,
  ATHZ-04; WSTG-APIT-02, APIT-04 at latest@87cf4a4), in
  `docs/architecture/authorization.json`: `public`, `signed`,
  `authenticated`, `support_admin` or the minimum company role.
  `backend/tests/test_authorization_matrix.py` fails on a route missing from
  it (deny by default) and calls every route as an anonymous caller, a
  Viewer, a Manager and an Owner of one company and the Owner of another,
  expecting 401, 403 below the minimum, 403 or 404 across companies and the
  minimum let through.
- **Public routes** are that file's `public_routes`, and a change to them is
  a security decision: `/health`, `/health/deep`, `/metrics` (keep it on the
  internal network), registration, login, refresh, logout and the CSRF echo,
  the SSO start and exchange, `/v1/capabilities`, `/v1/models` and the model
  catalogue, `/v1/standards`, support bug reporting and chat, and the retired
  `/v1/approve_task` (410); and, refused without their signature, ticket or
  state, the GitHub webhook, the Slack and Teams endpoints, the SSO callback
  and the Glassroom stream (`docs/contexts/platform.md`, Auth and
  multi-tenancy). `POST /v1/agents/generate_prompt` was public by omission
  until #541; it now needs a signed-in caller.
- **Request bodies** are `schemas.RequestModel`s: an undeclared privileged
  field (`company_id`, `role`, `human_role`, `owner_id`, `is_admin`...) is
  refused with 422 instead of being dropped, and the sign-in and membership
  bodies refuse every unknown field (issue #541, WSTG-INJT-20 at
  latest@87cf4a4; `backend/tests/test_mass_assignment.py`).
- **Company scoping.** Data is namespaced by `company_id`; audit writes and
  memory stores take the company from the caller's membership, never from the
  request's payload.
- **Rate limiting.** `slowapi` on the routes that declare a limit (login,
  registration, refresh, SSO, the chat endpoints), per client address
  (`backend/app/security.py`); `RATE_LIMIT_DEFAULT` is configured but no
  middleware applies it to the other routes.
- **The New company form's git check** (`POST /v1/workspace/git_check`,
  issue #745; `services/release/folder_check.py`; OWASP ASVS V4 and V11,
  NIST SP 800-53 AC-6 and AU-2). It runs before its caller belongs to the
  company being created, and with "Check remote" makes the daemon fetch one
  branch of the folder's remote with the person's git credentials (capped by
  `FORGE_POLICY_MAX`). So: on a deployment with more than one account, only
  an account that already holds a role in some company (an API token always
  does) or the deployment's first account may call it (403 otherwise); each
  account is limited to `GIT_CHECK_RATE_LIMIT` (default `10/minute`); each
  "Check remote" is audited as `folder_remote_checked` with the account and
  the folder, never a URL; and the answer holds only branch names, the
  clean and in-progress flags, the folder's remote URL without credentials
  and the ahead/behind counts: no commit ids, no repository path, and an
  unreadable remote as a code, never the remote's own words. No git command
  of the daemon ever prompts (`vscode-daemon/git_env.py`): SSH runs the
  person's own command with `-o BatchMode=yes` right after the program,
  before their options, so a `-o BatchMode=no` of theirs (ssh keeps the
  first value) cannot turn a refusal into a prompt the daemon, with no
  terminal, could only wait out.
- **Known gap.** `GET /v1/workspace/browse` and `/inspect` are open to any
  signed-in person (the New company folder picker runs before the person
  belongs to a company), and registration is open: on a deployment shared by
  people who do not trust each other, any account can list folders on the
  daemon's machine. Restrict registration (a reverse proxy, SSO only) there
  until the picker is tied to a company or an operator role.

Requirements that still hold for every change: deny by default (a new route
is protected unless it is added to the public list on purpose); route-level
authorization in a dependency, never only in the UI; no default `admin` or
fallback credential.

**Where the code stands, and what is planned:**

- Agents act as the company's workers, not as the person who started the
  sprint: a run does not carry its caller's identity. What a person may do is
  decided at the route that starts or changes the work (Manager to start a
  sprint, Owner to delete one), and the agents' reach is bounded by the
  daemon's sandbox and command policy (§8.3), not by the caller's role.
- An API token minted while authentication was off has no recorded creator,
  so the cap on its creator's current role does not apply to it; the access
  review lists it with no owner (`docs/design/TEAMS_AND_USAGE.md`, T5).
- Emergency changes on their own path (a reason, an after-the-fact approval
  by another person within a deadline, listed in the report) are designed,
  not built (`docs/design/SEGREGATION_OF_DUTIES.md`, Next). The Manager agent
  approving under a policy its requester wrote is listed in the report as an
  agent's approval, not refused.
- Service-to-service identity is **not mTLS**. The backend and the daemon
  each have their own NATS user, with TLS and optional client certificates
  (§8.6); the backend and the LLM proxy share `LLM_PROXY_INTERNAL_TOKEN`
  (`docs/OPERATIONS.md`, Rotate keys). mTLS or a service-mesh identity
  between all services is a target.

### 3.3 Frontend Exposure

**Where the code stands.** The backend's `SecurityHeadersMiddleware`
(`backend/app/security.py`, `SECURITY_HEADERS_ENABLED`; issue #541,
WSTG-CONF-14 and CONF-12 at latest@87cf4a4, WSTG-v42-CLNT-09 and ATHN-06)
sends on every answer, errors and the unhandled-error 500 included:
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none';
base-uri 'none'; form-action 'none'` (the API renders no page; the
documentation gets a policy that lets Swagger UI and ReDoc load, with
`frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `X-XSS-Protection: 0` (the legacy auditor off),
`Strict-Transport-Security`, `Referrer-Policy: no-referrer`,
`Cross-Origin-Opener-Policy: same-origin` and a `Permissions-Policy` that
turns off geolocation, microphone and camera; and `Cache-Control: no-store`
on every answer but the documentation, unless the route set its own (the
Glassroom stream's `no-cache`), so no authenticated answer stays in a cache.
`/docs`, `/redoc` and `/openapi.json` answer 404 unless
`API_DOCS_ENABLED=true` (WSTG-v42-CONF-05): off by default, turned on by
`docker-compose.yml` for local development only. A request that fails
validation answers 422 with each error's `type`, `loc` and `msg`, never the
value sent (FastAPI's `input` and `ctx` are dropped: the value can be a
password or a key; `exception_handlers.validation_errors`, WSTG-v42-ERRH-01).
The repository ships no production server for the web frontend (the compose
service runs the Vite dev server; the built `dist/` is served by whatever the
operator puts in front). The Vite dev server and `vite preview` send
`Content-Security-Policy: frame-ancestors 'none'; object-src 'none';
base-uri 'self'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin` and
the `Permissions-Policy` (`frontend/vite.config.ts`, `WEB_PAGE_HEADERS`,
checked against a running dev server by `frontend/vite.config.test.ts`), so
no other site can frame the page where approvals and undo are clicked;
scripts stay under the `<meta>` policy of `index.html`, which the dev
server's inline preamble needs. Behind any other server, the frontend's
headers are the ingress's job, with the list below. The desktop app's local
server sends its own stricter set (`frontend/electron/local-server.ts`). CORS allows only `CORS_ORIGINS`, with
credentials only when they are explicit origins. The frontend holds no
credential (#524): the access and refresh tokens are `HttpOnly` cookies
(§3.1), and `frontend/src/store/useAuthStore.ts` persists only the profile,
the memberships and when the session ends (`frontend/src/store/useAuthStore.test.ts`
checks that nothing credential-like reaches `localStorage` or
`sessionStorage`). The CSRF value is readable by design and kept in memory
only. A script running in the page can still act through the open session;
it can no longer read a token to use elsewhere.

The rules:

- Protect all frontend routes with authentication guards; that is UX only, not security.
- Store tokens in **memory only** or secure, `HttpOnly`, `SameSite=Lax/Strict` cookies. Never in `localStorage` or `sessionStorage`.
- Configure Vite and ingress with:
  - `Content-Security-Policy` without `unsafe-inline` for scripts
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - `Permissions-Policy` limited to required features
  - `Cross-Origin-Opener-Policy: same-origin`
- CORS: allow only known frontend origins. Never use `*` with credentials.
- Build-time environment variables must **not** contain secrets. They are embedded in the bundle.

---

## 4. Secret Handling

### 4.1 Rules

- **No secrets in source code, Dockerfiles, Helm values, or frontend bundles.**
- Use an external secret manager: HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, or Kubernetes External Secrets Operator.
- Kubernetes Secrets must be encrypted at rest and mounted as files or environment variables only where needed. Prefer CSI drivers over plain `env`.
- Never log secrets, tokens, API keys, or full request/response payloads.
- Apply least privilege to service accounts and API keys.
- Rotate credentials automatically. Maximum lifetime for static credentials: **90 days**.
- Prefer workload identity / IRSA / Workload Identity Federation over static cloud keys.

### 4.2 Development

- `.env` files are ignored by git and must never be committed.
- Use a pre-commit hook and CI check for secrets: `gitleaks`, `trufflehog`, or `detect-secrets`.
- Example placeholders: `${DB_PASSWORD}` from secret manager. No default fallback in production.

### 4.3 Detection

- CI scans for secrets and high-entropy strings.
- Alerts on secret exposure trigger the incident response process.

**Where the code stands.** gitleaks (pinned, downloaded with a checksum,
`.gitleaks.toml`) runs over each pull request's own commits
(`.github/workflows/security-scan.yml`, job `secrets-pr`) and over the full
history in the weekly baseline (`.github/workflows/security-baseline.yml`).
The pre-commit hooks (`.pre-commit-config.yaml`) refuse a committed `.env`
and run gitleaks over the staged changes with the same `.gitleaks.toml`
(pinned to the commit of the version CI downloads); a hook is skipped by
`git commit --no-verify`, which is why CI scans the pull request again. Stored secrets (provider keys, MCP secrets,
run secrets, project credentials) are encrypted with `FERNET_SECRET_KEY`;
without it they are stored with a `plaintext:` prefix and the backend warns
at startup (`backend/app/config.py`, `docs/CONFIGURATION.md`). Secrets live
in `.env`, not in a secret manager.

---

## 5. Dependency and Supply Chain Security

### 5.1 Lockfiles and Pinning

- Use exact pinned dependencies:
  - Python: `requirements.txt` hashes or `poetry.lock`
  - Node: `package-lock.json` with `npm ci`
- Commit lockfiles to the repository.
- Do not use `latest` or floating tags in production image builds.

### 5.2 Scanning

- CI must run on every PR:
  - `pip-audit` or `pipenv check` for Python
  - `npm audit --omit=dev` for Node
  - `trivy fs` or `grype` for OS and language packages
- Container images scanned with `trivy image` or `grype` before push and before deploy.
- Generate an **SBOM** for each production image (CycloneDX/SPDX).
- Dependency update automation: Renovate or Dependabot enabled with security updates.

**Next: a weekly DAST of the running API (issue #541, item 12; not built).**
There is no dynamic scan yet. The job is not written because the image could
not be pinned by digest and the job run on the self-hosted runner from where
#541 was done; the plan, so the next pull request is mechanical:

1. A `dast` job in `security-baseline.yml` (weekly, `workflow_dispatch`), on
   `[self-hosted, linux, x64, lafabrique]`, `permissions: contents: read`,
   `timeout-minutes: 30`.
2. Start the backend the way the e2e does, not the compose stack (the e2e is
   the pattern that already runs on that runner): the runner's Postgres with a
   database of its own (`factory_dast`, dropped at the end), `alembic upgrade
   head`, uvicorn on a free port with `AUTH_REQUIRED=true`, a random
   `JWT_SECRET_KEY`, `API_DOCS_ENABLED=true` (the scan reads
   `/openapi.json`) and NATS absent (the routes that need it answer 503).
3. Seed one user and company and log in with `"token_delivery": "bearer"`; pass
   the token to ZAP as `ZAP_AUTH_HEADER=Authorization` and
   `ZAP_AUTH_HEADER_VALUE="Bearer …"` so the scan reaches the Viewer routes.
4. `docker run --rm --network host -v "$RUNNER_TEMP/zap:/zap/wrk"
   ghcr.io/zaproxy/zaproxy@sha256:<digest of the stable tag> zap-api-scan.py
   -t http://127.0.0.1:$PORT/openapi.json -f openapi -c rules.tsv -J
   report.json -r report.html`, the digest written in the workflow with the
   tag in a comment (`backend/tests/test_workflow_pinning.py` holds `uses:` to
   shas; the image reference is held to a digest by review until the test
   learns `docker run`), and `.github/zap/rules.tsv` mapping each alert to
   `FAIL`, `WARN` or `IGNORE` with its reason.
5. Upload `report.json` and `report.html` as an artifact
   (`actions/upload-artifact` by sha); a `FAIL` alert fails the job, which
   does not gate pull requests, like the rest of the baseline.
6. `if: always()` cleanup: stop uvicorn, drop the database, remove the
   container and `$RUNNER_TEMP/zap` (the runner is not ephemeral and hosts
   the daemon).

### 5.3 Supply Chain

- Use signed commits and signed tags.
- Container images signed with `cosign`; verify signature in admission control.
- Build from pinned, minimal base images. Do not pull `latest` base images.
- Restrict CI/CD pipeline permissions. Approval required for changes to `.github/workflows` or deploy files.

**Where the code stands** (§5.2 and §5.3). Lockfiles are committed
(`backend/poetry.lock`, `llm-proxy/poetry.lock`, `frontend/package-lock.json`,
and `vscode-daemon/requirements.txt`, compiled with every sha256 from
`vscode-daemon/requirements.in` and installed with `--require-hashes` by the
daemon's Dockerfile and both start scripts) and Dependabot covers pip, npm,
docker, docker-compose and GitHub Actions (`.github/dependabot.yml`). Every
pull request runs, in `.github/workflows/security-scan.yml`: Trivy over the
files (SARIF, not gating); a dependency audit that fails on a known
vulnerability — `pip-audit` over the hashed export of `backend/poetry.lock`
and `llm-proxy/poetry.lock` and over the daemon's lock, and
`npm audit --omit=dev --audit-level=high` over `frontend/package-lock.json`,
with exceptions only in `.github/pip-audit-ignore.txt` (each with a reason and
a review date that fails the job once passed); a licence scan
(`scripts/licence_scan.py`) that fails on a runtime dependency of the four
components whose licence is not on the allowlist (MIT, BSD-2/3-Clause,
Apache-2.0, ISC, MPL-2.0, PSF-2.0, Python-2.0, Unlicense, CC0-1.0, 0BSD,
BlueOak-1.0.0) or is unknown, with exceptions only in
`.github/licence-exceptions.txt` (same rule: a reason and a review date);
gitleaks over the pull request's commits; and Semgrep failing on the findings
the pull request adds. The gating Trivy and the full Semgrep and gitleaks
scans run weekly (`.github/workflows/security-baseline.yml`). Every
third-party action is pinned by commit sha, and no workflow names a
`docker://` image (the repository's Actions policy refuses one before the run
starts, so tools come from pinned, checksummed release downloads), checked by
`backend/tests/test_workflow_pinning.py` (the SLSA
generator is the one exception: it runs only from a release tag). A release
attaches a CycloneDX SBOM of each component's lock (`release.yml`), and
`images.yml` signs each published image by digest with keyless cosign (GitHub
OIDC, Fulcio, Rekor) and attaches its CycloneDX SBOM as a signed attestation
(`docs/OPERATIONS.md`, Releasing); neither has run on a real tag yet, and no
cluster verifies the signature at admission. A SLSA provenance workflow
exists (`generator-generic-ossf-slsa3-publish.yml`, on a created GitHub
Release or by hand), but it hashes images it builds itself rather than the
ones `images.yml` publishes, and a Release created by `release.yml` with the
workflow's own token does not start it. Commits and tags are not required to
be signed. `.github/CODEOWNERS` names an owner for CI, the locks, the daemon,
the bus, authentication, migrations and the architecture rules, but GitHub
enforces it only once "Require review from Code Owners" is on for `main`
(#182); on 2026-11-18 the repository's settings disagree on whether it is,
and the intended state is an open owner decision (§17.1). The self-hosted runner is not ephemeral and shares its machine with
the daemon; the decision record is `docs/design/SELF_HOSTED_RUNNER.md`.

### 5.4 Open advisories and their triage

*Read on 2026-11-18 at commit `8ddbe9f`; the commands are in §17.2. The
previous snapshot (2026-10-29, commit `6f343f7`: 64 open Dependabot alerts,
1 of them shipped to the browser) is in this file's history.*

**What could be read.** `npm audit --omit=dev` (the surface a browser
receives) and `npm audit` with the build tooling both report 0 advisories over
`frontend/package-lock.json`. The dependency audit on `main`
(`security-scan.yml`, job `dependencies`: `pip-audit` over the backend, proxy
and daemon locks, `npm audit --omit=dev --audit-level=high`) passed on the
same commit, and `.github/pip-audit-ignore.txt` holds no entry. The weekly
Trivy scan of 2026-11-16 found no critical or high finding.

**What could not.** The Dependabot alerts API answered 403 to the session that
read these numbers, so the number of open Dependabot alerts is **not known**
on this date. Code scanning is not enabled on the repository (the API says
so), so the Trivy SARIF that every pull request produces is not stored
anywhere it can be counted (§17.1, entry E-08).

**What changed since the snapshot.** The advisories the snapshot discussed
are closed by upgrades rather than by exceptions:

- `react-router-dom` is at 7.18.5 (`frontend/package.json`), the version that
  patches GHSA-wrjc-x8rr-h8h6 and GHSA-337j-9hxr-rhxg. `safeReturnPath()` in
  `frontend/src/lib/returnPath.ts` stays as defence in depth on every
  user-controlled destination; it is no longer an exception (§17.1, E-12).
- `pytest` is at 9.2.0 in `backend/poetry.lock`, past the 9.0.3 fix of
  GHSA-6w46-j5rx-g56g.
- `vite` 8.3.8, `vitest` 5.1.0 and `electron` 45.3.1 are in the lockfile, the
  majors the snapshot said were held back.

**Build-only versus shipped.** The distinction still holds for whatever is
reported next: `vitest`, `vite`, `esbuild` and `electron` do not appear in the
`dist/` bundle (a Vite SPA built to static assets); the `frontend` service in
`docker-compose.yml` runs the dev server and is a development container, not
a deployment artifact. An advisory there matters for a compromised build
agent, not for a visitor, and is triaged by that surface.

**Majors still held back.** Each `ignore` in `.github/dependabot.yml` is an
entry of the exception register (§17.1), with whether its reason still holds.

---

## 6. Container and Kubernetes Hardening

### 6.1 Container Images

- Use **distroless** or minimal Alpine images. No shells, compilers, or debug tools in production.
- Run as **non-root** user with a fixed UID, ideally > 10000.
- Set `readOnlyRootFilesystem: true` except for volumes.
- Drop all Linux capabilities; add only explicitly required ones.
- Define `resources.requests` and `resources.limits`.
- Avoid mounting `docker.sock` or host paths.

### 6.2 Kubernetes Deployments

- Enable Pod Security Standards: `restricted` for app namespaces.
- Network policies: default deny, then allow only required ingress/egress:
  - Frontend → API
  - API → PostgreSQL/Neo4j
  - API → agent tool endpoints
- Use encrypted storage classes for PostgreSQL/Neo4j volumes.
- Enable Kubernetes audit logging; ship to SIEM.
- Disable auto-mount of service account tokens unless needed.
- Lock down RBAC: each workload gets its own ServiceAccount with minimal permissions.
- Admission policies: block privileged pods, host networking, unknown registries.

### 6.3 Where the code stands

These are targets the images and manifests do not meet yet. The five
Dockerfiles (`backend/`, `backend/janitor/`, `frontend/`, `llm-proxy/`,
`vscode-daemon/`) start from `python:3.12-slim` or `node:26-alpine` by tag,
not by digest, and none sets a `USER`, so the containers run as root.
`deployment/k8s/backend.yaml` is a single Deployment and Service with the
image `fabrique-backend:latest`, no `securityContext` and no NetworkPolicy.
The compose stack is what the project runs and tests (`docker-compose.yml`,
`docs/OPERATIONS.md`, Deploy). The containers that run agent-written code
are a different matter: the daemon's sandbox containers drop every
capability, forbid privilege escalation and run as the host user (§8.3).

---

## 7. Data Privacy and Database Security

### 7.1 PostgreSQL and Neo4j

- Use **least privilege database users**. The API must not use superuser accounts.
- Separate schemas/databases per service or tenant where applicable.
- Enforce TLS between services and databases. Never connect with plaintext credentials.
- Enable audit logging for database connections and privileged operations.

### 7.2 Data Handling

- Data minimization: store only what is required for the product.
- Classify data: public, internal, confidential, regulated.
- For **PII/sensitive fields**, use application-level encryption (e.g., `pgcrypto`, envelope encryption) or rely on disk encryption plus strict access controls.
- Anonymize/pseudonymize in non-production environments.
- Define retention policy: delete or archive data when no longer needed.
- Honor GDPR/CCPA deletion and export requests. Build data subject request APIs with audit trail.
- Do not include sensitive data in logs, traces, or error messages.
- Backups must be encrypted and access-restricted. Test restore procedures.

### 7.3 Neo4j-Specific

- Disable Neo4j Browser in production if not required.
- Use built-in role-based access control; avoid `neo4j` admin for application connections.
- Constrain Cypher queries. Use parameterized queries and avoid string concatenation.
- For graph data containing relationships between entities, consider row-level security via custom procedures or separate databases per tenant.

---

## 8. LLM and Agent Security

### 8.1 Prompt Injection

Treat **all external content** as potentially malicious. This includes documents, web pages, emails, and output from other agents.

Core rules:

- **Model output is untrusted.** Never execute or evaluate model output as code, SQL, Cypher, or shell commands directly.
- Separate **instructions from data**. Use delimiters and make clear that content inside data sections is not instructions.
- Do not let agents fetch arbitrary URLs unless the target is on an allowlist and responses are treated as untrusted.
- Use a **system prompt** that explicitly forbids following instructions embedded in user data.
- Implement guardrails: input filtering for known injection patterns, output filtering for forbidden content, and a second LLM classifier for sensitive actions if risk demands.
- For high-impact actions, require **human approval** before execution.

### 8.2 Tool-Call Abuse

- **Tool allowlist per agent.** An agent can only call tools explicitly assigned to its role.
- Each tool call must be validated:
  - Input schema validation (types, lengths, enums, constraints)
  - Authentication context check: does the calling agent/user have permission?
  - Rate limiting per tool, per agent, per user
  - Timeouts and maximum result sizes
- Do not give agent tools broad powers. Prefer **narrow, scoped tools**:
  - `read_only` instead of `write`
  - one resource instead of `*`
- Use **scoped credentials** for each tool. Never share the agent’s main identity with third-party APIs.
- Log every tool call: agent ID, user ID, tool name, input signature/hash, timestamp, result status. This is critical for audit and abuse detection.
- For tools that modify state, require idempotency keys and maintain an audit trail.
- Never expose raw database connection or Kubernetes API as a tool.

### 8.3 Workspace Command Execution: the Sandbox and the Command Policy

The workspace daemon (`vscode-daemon/`) is the only component that runs
commands on the computer that holds the user's projects. It runs natively,
as the user who started it (`./scripts/start-daemon.sh`), and answers
`workspace.execute` through `execute_shell_command` in
`vscode-daemon/shell_exec.py`, in this order:

1. **The command policy** (`vscode-daemon/command_policy.py`, issue #357)
   decides whether the command runs at all.
2. **The sandbox** (`vscode-daemon/sandbox.py`) decides where it runs. A
   request without a `sandbox` is read as `host` (issue #525), so the
   daemon's floor below applies to every command.
3. Only when that leaves it on the host does it run in the host shell
   (`shell_exec.run_on_host`), with the daemon's privileges.

**The sandbox is the boundary.** Modes (`DELIVERY_SANDBOX` for the
deployment, or a company's own, `GET`/`PUT /v1/companies/{id}/sandbox`,
Viewer / Manager; `docs/CONFIGURATION.md`):

- `host` (the default): the command runs as the daemon's user, with its
  files, network and environment. Nothing is isolated.
- `container`: an ephemeral Docker or Podman container per run with only the
  task's worktree mounted at `/workspace`, network off unless
  `DELIVERY_SANDBOX_NETWORK`, CPU, memory and process limits, every
  capability dropped (`--cap-drop ALL`), `no-new-privileges`, the host
  user's uid, and only the environment variables the company lists by name
  (values from the daemon's machine, never from the request).
- `bwrap`: bubblewrap namespaces on Linux, system directories read-only, the
  worktree read-write, network unshared unless opened, a fresh `/tmp`, the
  same environment rule; no CPU or memory limit, and the report says so.

The daemon reports the mode it applied (`test_run.sandbox`) and never falls
back to the host on its own: a mode that cannot run fails the run with the
reason, and the tests read as unverified, not passed. The Verifier records
the report as the `sandbox` check (`backend/app/services/workspace/sandbox.py`);
a host run is a finding, never a refutation. The person running the daemon
can refuse the host: `FABRIQUE_SANDBOX_REQUIRED=container` (or `bwrap`) runs
a `host` request, and one that names no sandbox, in that mode instead and
says so (`forced_by_daemon`); when that mode cannot run, the command is
refused with the reason, never run on the host. Without the floor a request
that names no sandbox runs on the host as before and its reply is unchanged.
`backend/tests/test_sandbox_floor.py` also checks that nothing else in the
daemon builds a host shell command: only `shell_exec` (after `normalize`) and
the application check's host path (after `normalize`). The daemon's other
subprocesses are fixed argv it chose itself (`git` with hooks disabled, the
container runtime, `node --check` on a copy, `gh` / `glab api`), never a
command from a request.

Requests that carry a `sandbox`: the delivery test run
(`services/loop/delivery_checks.py`), the Developer's test tool
(`services/tools/developer_tools.py`), the lint after an edit
(`services/tools/edit_lint.py`), the dependency install
(`services/workspace/dependency_install.py`), a console producer's run
(`services/producers/console_producer.py`), a console assistant's
consultation and the application check (`vscode-daemon/app_run.py`).

**Console assistants** (`services/producers/console_assistants.py`,
`assistant_sandbox.py`; issue #525) send the company's sandbox with purpose
`assistant`: its mode, the network as `DELIVERY_SANDBOX_NETWORK` sets it (off
by default), no dependency cache, and only the variable *names* in the
company's `sandbox_env` and `console_env` (the provider's key is listed in
`console_env`; its value stays on the daemon's machine). In a `container` or
`bwrap` sandbox with the network off the assistant is not asked; when the
daemon's reply shows its sandbox stopped the CLI (the floor's runtime
missing, the CLI not in the image, no network to the provider) the
consultation is skipped and cools down. Either way the Glassroom says why
(`skipped_sandbox`, with the daemon's sandbox report), and the task goes on:
the advice is optional and never evidence. A company on `host` with a daemon
without a floor runs them on the host, as before. No command policy is sent
for this path: the backend has none for it (the console producer's allow
list names a producer CLI's own argv, not an assistant's template).

**Which image ran, and the dependency cache** (issue #315,
`docs/design/EXECUTION_ENVIRONMENT.md`; the cache and its quarantine are
#514). In container mode the daemon asks the runtime for the image's content
ID and registry digest before the run and starts the container from that ID,
so a moved tag cannot change what ran; an image the run pulled is observed
after it and the report says so (`vscode-daemon/sandbox_image.py`). With
`DELIVERY_SANDBOX_CACHE` (off by default) a volume per project, selected by
both its name and its label, holds the package managers' caches
(`vscode-daemon/sandbox_cache.py`): kept after a run, removed after a failed
install, and quarantined after three failed installs in a row until a
Manager resets it (`POST /v1/companies/{id}/sandbox/cache/reset`, audited).
A same-named volume without the label is never used or removed, and
`FABRIQUE_SANDBOX_CACHE=off` on the daemon's machine refuses the cache.
With `DELIVERY_SANDBOX_ENV` (off by default) a second volume keeps what an
install leaves outside the worktree (Python's user site, Go's module cache).
It persists executable code between runs, so it is keyed by project, company,
image and the sha256 of the manifests, written only by an install and mounted
read-only by every other run (a test run cannot leave code for the next one),
reset and quarantined as the cache, and refused with
`FABRIQUE_SANDBOX_ENV=off`; its bin folders come after the image's on `PATH`.

**The command policy is a first pass, not the boundary.** A blocklist screens
a string; it cannot fence a process: `rm -rf`, `rm -r -f` and
`rm --recursive --force` are one intent with three spellings. DeepCode wrote
the same conclusion down in `core/harness/command_guard.py`:

> A blocklist can never be a security boundary; the sandbox is. But if we keep a
> blocklist at all, it should honestly catch what it *claims* to, rather than
> offering a false sense of coverage.

So the policy reads the command as a shell would and fails closed on what it
does not model. A request's `command_policy` is `allow` (each simple
command's words must start with a listed pattern's words, never a
substring; a redirection other than stream duplication or `/dev/null` is
refused) or `deny` (a pattern found in the text or in the unquoted words
refuses it). The scanner splits `;`, `&&`, `||`, pipes, background jobs,
subshells and `$(...)`, backtick and `<(...)` substitutions (each checked,
at most `MAX_DEPTH` deep), honouring quotes; text it cannot read, a
here-document, `$'…'` quoting, a comment or an unknown mode is refused, and
on Windows a policy refuses every command rather than read it with the wrong
quoting rules. The daemon's machine may keep its own deny list,
`FABRIQUE_COMMAND_DENY`, which a request cannot relax. A refusal is
`{"success": false, "refused": true, "error": <reason>}` and nothing runs.

**The wire is versioned and observed** (issue #517). Protocol version 1 is
written down in `vscode-daemon/protocol/PROTOCOL.md`, with a JSON Schema per
subject (`vscode-daemon/protocol/schemas/v1/`) and recorded request/reply
pairs both sides replay. The daemon publishes `workspace.heartbeat` every
`FABRIQUE_HEARTBEAT_SECONDS` (at most 25; `vscode-daemon/daemon_status.py`);
the backend records when *it* observed each one and decides `online`,
`stale` or `offline` (`services/workspace/daemon_liveness.py`). A daemon
whose protocol range does not overlap the backend's is `incompatible`, with
a reason naming the side to update, never a generic timeout. What the daemon
says about itself is kept under `reported_by_daemon`, apart from what the
backend observed.

**The bus** gives the daemon its own NATS user, which can answer requests but
not send them (§8.6, `scripts/nats_config.py`).

**The commands the pipeline picks itself are not model output.**
`detect_test_command` (`backend/app/services/workspace/workspace_daemon.py`)
derives the test command from repository markers (`package.json`,
`pytest.ini`, `pyproject.toml`, a `tests/` folder) or takes
`DELIVERY_TEST_COMMAND`; the Developer's test tool runs that command or a
narrower one on the same runner, never an arbitrary one. No model picks the
string, so prompt injection cannot reach it; the code it runs is
agent-written, which is what the sandbox is for.

**What remains open:**

- `host` is still the default and an allowed mode. A deployment that runs
  agent code sets `DELIVERY_SANDBOX=container` and, on the daemon's machine,
  `FABRIQUE_SANDBOX_REQUIRED=container` (§10.8).
- The backend does not send a per-company command policy yet
  (`docs/CONFIGURATION.md`): the only policy it sends is the console
  producer's, which allows only that CLI's own command
  (`services/producers/console_clis.py`, `command_policy`). Without a policy
  and without `FABRIQUE_COMMAND_DENY`, every command runs.
- A console assistant's sandbox mounts the project folder (the person's
  checkout, not a task worktree) read-write, as every sandboxed run mounts its
  folder; on the host it had the whole machine, so this is narrower, but the
  advice needs only a read-only mount. With the network on and the provider's
  key in its environment, a prompt-injected assistant can reach any host the
  sandbox allows.
- The sandbox's network is on or off; there is no egress allowlist.
- The dependency cache is writable by every run of a project, so one run can
  leave something for the next; that is why it is off by default and removed
  after a failed install.

Until those close, treat `workspace.execute` as privileged: connect the
daemon only to brokers you trust, with credentials (§8.6), and do not point
it at a workspace whose commands you would not run yourself.

### 8.4 LangGraph-Specific

- Validate all **state** passed between nodes. Do not assume earlier nodes sanitized data.
- Keep agent graphs deterministic where possible; constrain branching based on fixed logic, not LLM output, for security-critical branches.
- Use structured output parsing for security decisions. Never rely on free-text model response to determine authorization.
- Restrict LangGraph tool nodes to a pre-registered set. Implement a registry of allowed tools with permissions.

### 8.5 Model Output Handling

- **Encode output** before rendering in frontend to prevent XSS.
- Sanitize any model output inserted into SQL, Cypher, JSON, HTML, or logs.
- Limit output size to avoid DoS.
- Do not render markdown/HTML from untrusted model output without a sanitizer.

### 8.6 The Message Bus (NATS)

Anything that can publish on NATS can ask the workspace daemon to write files
and run commands on the computer that holds the user's projects, so the bus is
part of the daemon's attack surface, not plumbing behind it (issue #327).

- **One user per component.** `scripts/nats_config.py` writes
  `nats/nats-server.conf` (`--check` fails when the committed file is stale;
  the backend suite checks it too, `test_nats_security_config.py`). The `backend` user publishes the work (`workspace.>`,
  `task.>`, `glassroom.>`, `audit.>`, `system.>`) and owns JetStream; its
  replies arrive under `_INBOX.backend`. The `daemon` user subscribes to
  `workspace.>` and `_INBOX.daemon.>`, answers under `_INBOX.backend.>`
  (by subject, not with `allow_responses`: that grant would let a holder of
  the daemon's credentials publish to `workspace.delivered` with
  `workspace.execute` as reply subject and then "answer" itself, commanding
  the real daemon), and publishes only `workspace.delivered`, its heartbeat
  `workspace.heartbeat` (an event nobody answers, §8.3), `glassroom.*` and
  the JetStream calls of its own consumer on the deliveries stream. It
  cannot send a request (`workspace.execute`…), assign a task, read the
  backend's replies, or create, change or delete a stream; the streams are the
  backend's, and the daemon waits until they exist. `--namespace` writes the
  same rules for a deployment with `NATS_NAMESPACE`.
- **Credentials.** The broker turns users on when both
  `NATS_BACKEND_PASSWORD` and `NATS_DAEMON_PASSWORD` are set (`nats/start.sh`;
  only one set refuses to start; neither keeps the old unauthenticated broker).
  The backend and the daemon read `NATS_USER`/`NATS_PASSWORD`, or
  `NATS_CREDS_FILE` (a `.creds` file, which wins), or `NATS_NKEY_SEED` (a seed
  file). Passwords live only in the environment: the configuration names
  `$NATS_BACKEND_PASSWORD`, never the value, and no log line or `/health`
  field carries a secret (`/health` says only `authenticated` and `tls`).
- **TLS.** With `nats/certs/server.crt` and `server.key` the broker requires
  TLS; `NATS_TLS=true` makes both components connect with it, `NATS_TLS_CA`
  pins the CA and `NATS_TLS_CERT`/`NATS_TLS_KEY` present a client
  certificate (verified by the broker with `NATS_TLS_VERIFY=true`). TLS asked
  for explicitly is TLS or nothing: with `NATS_TLS=true` or
  `NATS_TLS_VERIFY=true`, a missing `server.crt`, `server.key` or (for
  verification) `ca.crt` stops the broker instead of starting it in the clear.
  `docs/OPERATIONS.md`, "Securing the bus", generates a self-signed CA.
- **Exposure.** Without credentials the broker is safe only on loopback; the
  backend warns at startup when `NATS_BIND` is anything else, and the daemon
  when it connects to a broker on another machine without credentials.

The design follows the scoped credentials of the agents that ship this
already: OpenHands' scoped API keys and the per-integration tokens of GitHub
Copilot coding agent and OpenAI Codex, one credential per component with only
what it needs.

### 8.7 MCP servers: who may start what

A company's Manager configures the MCP servers its agents may call
(`POST /v1/mcp/servers`, `services/tools/mcp_config.py`). A server is either a
**stdio** server, a process the backend spawns with its own user, network and
files, or an **HTTP/SSE** server, a URL the backend connects to. Both cross a
trust boundary the Manager does not own: the backend's host and its network.
The trust model (WSTG review, `services/tools/mcp_policy.py`):

- **The operator decides what can run; the company decides what it uses.**
  What may be started is set in the backend's environment, never through the
  API: `MCP_STDIO_ALLOWED_COMMANDS` (absolute paths of the commands a stdio
  server may spawn) and `MCP_HTTP_ALLOWED_HOSTS` (hosts that may use plain
  http). A Manager can only pick among what the operator allowed.
- **stdio is off by default.** With an empty allowlist a stdio server is
  refused when saved (400), when enabled (`PATCH`, 409) and when started.
  A command must be exactly one of the listed paths: a bare name looked up on
  `PATH`, a relative path, `..` or `//` in it, another case or a symlink under
  another name is refused. The process is spawned as `[command, *args]`
  without a shell, so an argument is never interpreted; arguments are a list
  of at most 64 strings of at most 4096 characters without NUL. An
  environment variable that changes how a program loads its code (`PATH`,
  `LD_*`, `DYLD_*`, `PYTHON*`, `NODE_*`, `NPM_CONFIG_*`, `GIT_*`,
  `JAVA_TOOL_OPTIONS`, `BASH_ENV`...) is refused, and `cwd` must be an
  absolute path. **Allowing an interpreter or a package runner (`npx`, `uvx`,
  `node`, `python`, a shell) lets a Manager run any code as the backend,**
  since the arguments choose the program: allow a fixed server binary or a
  wrapper script that execs one (docs/OPERATIONS.md, MCP servers). The
  backend logs at startup which commands are allowed, an entry that allows
  nothing, and a warning for an entry that is an interpreter, a package
  runner or a shell.
- **A stored row is checked again before every start.** The allowlist can
  shrink after a server was saved: such a row lists with `start_refused`
  naming the reason, and the tools endpoint, the task worker
  (`mcp_tools.Toolbox.load`) and the client itself (`mcp_client.connect`)
  refuse to start it. Listing the servers never starts one.
- **Starting a server takes a Manager, and every start is audited.**
  `GET /v1/mcp/servers/{id}/tools` connects to the server, so it takes the
  Manager role (`docs/architecture/authorization.json`); a Viewer gets 403
  and nothing runs. Each start it asks for, and each refusal, writes an audit
  row (`mcp_server_start`, `mcp_server_start_refused`: the server, the
  command or the host, the number of arguments, never a value or a secret)
  before anything starts. The task worker audits every start of a stdio
  server (its discovery and each call) as `agent:<role>`, and does not start
  one it cannot audit.
- **An HTTP server is reached only on a public address.** Its URL must be
  https (http only for a host in `MCP_HTTP_ALLOWED_HOSTS`, which does not
  waive the address check), carry no credentials, and its host must resolve
  only to public addresses (`services/platform/egress.py`, the helper of the
  webhook, the OIDC client and the research fetches): loopback, private,
  link-local (169.254.0.0/16, the cloud metadata service), CGNAT, multicast,
  reserved and the IPv6 forms that embed them are refused when the server is
  saved and again right before each connection, which goes to the address
  just checked, with the host in `Host` and TLS SNI. A request for any other
  host (another origin, a scheme change) is refused by the client
  (`mcp_client.PinnedTransport`), environment proxies (`HTTPS_PROXY`) are not
  used, and the
  MCP transports follow a redirect only within the origin. There is no
  setting that allows a private MCP host.
- **A failure says nothing about the inside.** A server that cannot be
  reached answers 502 with a fixed message; the exception (an address, a
  port, a library's text) is only in the backend's log.
- **The rest is unchanged:** secrets (`env`, `headers`) are Fernet-encrypted
  and listed by name only, masked out of the results the model sees, and
  every tool call is an observation on the run's evidence (§8.2).

---

## 9. Incident Response

### 9.1 Reporting

- Report security incidents and vulnerabilities privately through this
  repository's **Security** tab → **Advisories** → **Report a
  vulnerability** (see "Reporting a vulnerability" at the top; the owner must
  have enabled private vulnerability reporting in the repository settings).
- Do **not** open a public issue for suspected vulnerabilities.
- Include affected component, timestamp, impact, and any reproduce steps.

### 9.2 Severity Levels

| Severity | Examples | Response Time |
|---|---|---|
| Critical | Unauthorized admin access, remote code execution, prompt injection leading to tool abuse, secret exposure | 24h containment |
| High | Data exfiltration, auth bypass, XSS on auth pages | 72h |
| Medium | Missing security headers, excessive logging, misconfiguration | 1 week |
| Low | Best practice deviations, low-impact issues | Next sprint |

### 9.3 Process

1. **Preparation:** maintain contact list, runbooks, backup restore procedures.
2. **Detection and analysis:** SIEM alerts, audit logs, tool-call anomaly detection.
3. **Containment:** revoke compromised tokens/keys, isolate affected services, disable tool or agent.
4. **Eradication:** remove injected data, patch root cause, rotate secrets.
5. **Recovery:** restore from clean backups, re-enable services with increased monitoring.
6. **Post-incident:** blameless retro, update SECURITY.md, add regression tests.

### 9.4 Immediate Actions for LLM Incidents

- Suspend the affected agent/tool.
- Freeze any agent state and logs for forensics.
- Revoke tool credentials and rotate them.
- Determine if prompt injection came from external data or user input.
- Assess whether model output was rendered unsafely or executed.

---

## 10. Security Review Checklist for Agents and PRs

Every PR that touches backend, frontend, agents, or infrastructure must pass this checklist before merge.

### 10.1 General

- [ ] No secrets or credentials in code, config, or comments
- [ ] Dependencies pinned and lockfiles updated
- [ ] Dependency and container scans pass
- [ ] No secret patterns in logs
- [ ] Error messages do not leak stack traces or internal details
      (`backend/tests/test_error_bodies.py` fuzzes every route; an unhandled
      error answers an error id only, WSTG-v42-ERRH-01/02)

### 10.2 Authentication and Authorization

- [ ] New endpoints enforce authentication unless explicitly public and approved,
      and are declared in `docs/architecture/authorization.json` (the matrix
      test fails otherwise)
- [ ] Authorization checked server-side for every role/permission
- [ ] Token validation includes signature, a single allowed HMAC algorithm, expiry, token type, issuer and audience (`backend/app/auth_tokens.py`); for an SSO ID token, also the nonce and an asymmetric algorithm (`services/platform/oidc.py`, §3.1)
- [ ] No default `admin` or insecure fallback credentials
- [ ] Agent inherits caller permissions and cannot exceed them

### 10.3 Input and Injection

- [ ] All user and agent input validated and sanitized; a request body is a
      `schemas.RequestModel` (no undeclared privileged field)
- [ ] A URL a company chooses is fetched only through `services/platform/egress.py`
      (public addresses, resolved once and pinned, no redirects followed;
      `backend/tests/test_ssrf_vectors.py`)
- [ ] SQL/Cypher queries parameterized; no string concatenation
- [ ] No dynamic evaluation of model output (`eval`, `exec`, shell, raw SQL)
- [ ] File uploads validated by content type and size; stored outside web root
- [ ] Output from untrusted sources encoded before rendering

### 10.4 LLM and Agent Security

- [ ] System prompts treat external data as untrusted
- [ ] Tools are allowlisted per agent and validate inputs
- [ ] High-impact tool calls require human approval or explicit confirmation
- [ ] Tool credentials are scoped and not hard-coded
- [ ] Tool calls logged with enough detail for audit
- [ ] Model output not used for authorization decisions

### 10.5 Frontend

- [ ] No secrets in client code or Vite env
- [ ] CSP, HSTS, anti-XSS headers configured
- [ ] Tokens stored securely: `HttpOnly` cookies or memory, never `localStorage` or `sessionStorage` (`useAuthStore.test.ts`); unsafe cookie requests carry `X-Fabrique-CSRF`
- [ ] CORS restricted to known origins; credentials never with `*`
- [ ] User-supplied HTML/markdown sanitized

### 10.6 Data and Infrastructure

- [ ] Database access uses least privilege; no admin connections from app
- [ ] PII/sensitive data minimized, encrypted, or pseudonymized
- [ ] Kubernetes manifests use non-root, read-only root FS, drop capabilities
- [ ] Network policies allow only required traffic
- [ ] Images built from minimal pinned base and signed

### 10.7 Testing

- [ ] Security unit tests for authorization and input validation
- [ ] Negative tests for prompt injection and tool abuse
- [ ] Regression tests for previous security bugs

### 10.8 Deployment Checklist

Before the stack serves anyone but the person running it:

- [ ] **Bind.** `NATS_BIND` stays `127.0.0.1` unless the daemon runs on
      another machine; the frontend and API sit behind TLS.
- [ ] **Bus credentials.** `NATS_BACKEND_PASSWORD` and `NATS_DAEMON_PASSWORD`
      set (or `.creds` files), `python3 scripts/nats_config.py --check` passes,
      and `/health` reports `nats.authenticated: true` (§8.6).
- [ ] **Bus TLS.** Whenever NATS leaves the machine: certificates in
      `nats/certs`, `NATS_TLS=true` and `NATS_TLS_CA` on the backend and the
      daemon, `/health` reports `nats.tls: true`.
- [ ] **API tokens.** External callers use per-company API tokens
      (`/v1/auth/tokens`), stored hashed, named, with the narrowest role that
      works (`Viewer` by default; a Manager at most, never above the creator's
      own role), an expiry (90 days by default, at most 365) and revoked when
      no longer used. A token cannot create tokens, and the audit rows its
      requests write name it (`api-token:<id>`); `JWT_SECRET_KEY` set (at
      least 32 bytes for HS256, or the backend treats it as unset) and
      `AUTH_REQUIRED` on.
- [ ] **Browser sessions.** The frontend and the API served over https under
      one site, `SESSION_COOKIE_SECURE` left on, `CORS_ORIGINS` listing the
      frontend's origins (never `*`), and `ACCESS_TOKEN_EXPIRE_MINUTES` and
      `REFRESH_TOKEN_ABSOLUTE_DAYS` no longer than the defaults (15 minutes,
      7 days) unless the owner accepts more (§3.1, `docs/design/SESSIONS.md`).
- [ ] **Exposure.** `API_DOCS_ENABLED` false (the default; the compose
      file turns it on) where the API faces the internet;
      `TRUSTED_PROXY_IPS` listing exactly the reverse proxies in front of
      the backend, nothing wider; `/metrics` and `/health/deep` reachable only from the
      internal network; `ERROR_REPORT_URL` unset or an https endpoint the
      operator controls; the frontend's ingress sends the headers of §3.3;
      `JWT_ISSUER`/`JWT_AUDIENCE` distinct from any other deployment sharing
      `JWT_SECRET_KEY`.
- [ ] **Single sign-on.** `SSO_CALLBACK_URL` and `SSO_FRONTEND_URL` are the
      public https addresses; `SSO_BREAK_GLASS_EMAILS` names at least one
      Owner before any company requires SSO; `SSO_ALLOW_PRIVATE_ISSUERS`
      stays off; `FERNET_SECRET_KEY` is set, so client secrets are encrypted
      (§3.1, `docs/OPERATIONS.md`).
- [ ] **Secrets at rest.** `FERNET_SECRET_KEY` set, so provider keys, MCP
      secrets, run secrets and project credentials are encrypted (the backend
      warns at startup without it); `AUDIT_SIGNING_KEY` keys the audit chain.
- [ ] **MCP servers.** `MCP_STDIO_ALLOWED_COMMANDS` empty unless a stdio
      server is needed, and then only fixed server binaries or wrapper
      scripts, never an interpreter, a package runner or a shell, in a
      directory the backend's user cannot write; `MCP_HTTP_ALLOWED_HOSTS`
      empty unless a server cannot speak https (§8.7).
- [ ] **Sandbox.** With `DELIVERY_TESTS_ENABLED`, `DELIVERY_SANDBOX=container`
      (or each company's mode), and `FABRIQUE_SANDBOX_REQUIRED=container` on the
      daemon's machine so nothing falls back to the host.

---

## 11. Secure Coding Rules for All Agents

These are non-negotiable rules. Apply them in every task.

1. **Never trust input.** Validate all data from users, external APIs, files, and model outputs.
2. **Never trust model output.** Do not execute, query, or render model output as code.
3. **Least privilege everywhere.** Services, DB users, tools, and agents get only necessary permissions.
4. **Parameterize all database queries.** Use ORM or prepared statements. Never concatenate user input into SQL/Cypher.
5. **No secrets in code, config, logs, or frontend.** Use secret manager and env placeholders.
6. **Fail closed.** If authorization cannot be verified, deny access.
7. **Log security-relevant events.** Log auth failures, tool calls, state changes, and admin actions without sensitive data.
8. **Encode output.** Escape or sanitize before inserting into HTML, JSON, SQL, logs.
9. **Keep dependencies up to date.** Pin, scan, and patch.
10. **Design for auditability.** Every agent and service action must be traceable to a user and tool call.
11. **A claim is not an observation.** Follow the design rules of `docs/POSITIONING.md`: a claim and an observation never share a field, an agent's report of its own test run is a claim, and a hash or signature is never accepted from the party being recorded.

---

## 12. Audit Guide for Security Agent

When auditing the platform, verify the following controls:

### Authentication & Authorization
- Are all API routes behind authentication except health checks?
- Are roles enforced with deny-by-default?
- Do agents validate user permissions on tool calls?
- Are tokens short-lived and securely stored?

### Secrets
- Run `gitleaks`/`trufflehog` over repo and image layers.
- Check Helm values and `.env` for real secrets.
- Verify secret manager integration and no default fallback.

### Dependencies and Images
- Confirm lockfiles and `trivy`/`grype` scans in CI.
- Inspect base images for non-root, distroless, pinned tags.
- Verify SBOM generation and image signing.

### LLM/Tool Security
- Review all system prompts for injection resistance.
- List all registered tools; compare to allowlist.
- Check tool input validation and audit logging.
- Attempt prompt injection against a staging agent.
- Confirm high-risk tools require human approval.

### Frontend/API Exposure
- Check CORS, CSP, secure cookies, and token storage.
- Inspect Vite env for accidental secret usage.
- Test for XSS in model output rendering.

### Data and Infrastructure
- Verify DB users are least privilege; no admin connection strings.
- Check network policies and Kubernetes security context.
- Confirm TLS and encrypted backups.

---

## 13. References

- OWASP Top 10 for LLM Applications
- OWASP ASVS
- OWASP Kubernetes Top 10
- MITRE ATT&CK for Kubernetes and LLM threats
- NIST SSDF
- CIS Kubernetes Benchmarks

---

## 14. Contact

Report a vulnerability privately through this repository's **Security** tab →
**Advisories** → **Report a vulnerability** (GitHub private vulnerability
reporting, enabled by the repository owner in the repository settings).
There is no security mailbox. Do not include sensitive details in public
issues, pull requests or discussions.

---

## 15. Supported Versions and Security Releases

La Fabrique is released as `vX.Y.Z` tags on `main`, following Semantic
Versioning; each tag becomes a GitHub Release whose notes are its section of
`CHANGELOG.md`, and publishes the five images to GHCR
(`docs/OPERATIONS.md`, Releasing; `.github/workflows/release.yml`,
`.github/workflows/images.yml`; issue #512, phase 1). No stable version has
been tagged yet: until the first one, only `main` is supported.

| Version | Security fixes |
|---|---|
| `main` | Yes: every fix lands here first |
| The latest minor release (`X.Y.*`) | Yes: a patch release `X.Y.Z+1` |
| Older minor releases | No: upgrade to the latest minor |
| Pre-releases (`vX.Y.Z-rc.N`) | No: they are for staging only |

How a fix ships:

1. The report is triaged privately against §9.2; the fix is prepared in a
   private security advisory's temporary fork or on a branch whose name and
   pull request do not describe the vulnerability before it is released.
2. It merges to `main` with its line under `[Unreleased]` → `### Security`
   in `CHANGELOG.md`, worded so it does not hand over an exploit before the
   release is out.
3. A patch release is cut at once by the usual process (`docs/OPERATIONS.md`,
   Releasing), not held for the next planned release.
4. The advisory is published with the fixed version, and a CVE requested
   through GitHub when the severity warrants it. Fixes to third-party or
   adapted code listed in `NOTICE` ship the same way.

Operators upgrade by the steps in `docs/OPERATIONS.md`, Upgrade; a migration
that cannot be rolled back is named in the release notes.


## 16. Yearly Person-Led Penetration Test

The automated checks (§10.7, the matrix, the SSRF vectors, the error fuzz,
the weekly scans) find what they were written to find. Once a year, and before
a release that changes authentication, roles or the workspace daemon, a person
who did not write the code tests the deployed stack. The scope, by the OWASP
Web Security Testing Guide (ids as `WSTG-v42-…`, or `latest@87cf4a4` where
v4.2 lacks the test):

1. **Business logic** (WSTG-v42-BUSL-01 to BUSL-07): approvals and holds (a
   held delivery accepted without the role, twice, after it changed, from a
   stale message); autonomy levels and the Manager agent's policy (what it may
   decide alone); undo and carry-over (undoing another company's delivery,
   undoing twice, racing a publish); sprint control (start, pause, stop,
   archive) out of order.
2. **Identity across tenants** (WSTG-v42-ATHN-04, ATHZ-02, ATHZ-04,
   IDNT-01/02): a hostile identity provider run by an Owner asserting another
   company's people, account pre-hijacking by email, `require_sso` and
   break-glass, API tokens beyond their company or role, invitations and
   member removal; every route of `docs/architecture/authorization.json`
   re-tested by hand on a deployed stack with real cookies and CSRF.
3. **The bus and the daemon** (`SECURITY.md` §8.3, §8.6): a NATS client with
   stolen or no credentials, subjects outside a user's permissions, replayed
   or forged daemon replies, the command policy and the sandbox floor
   (`FABRIQUE_SANDBOX_REQUIRED`), path traversal and symlinks through the
   workspace tools.
4. **Chat spoofing** (WSTG-v42-BUSL-02, latest@87cf4a4 SESS-10 where a
   signed value is involved): forged or replayed Slack and Teams
   interactions, a link code claimed by someone else, a click on an old
   message.
5. **Prompt injection through agent output** (§8.1–§8.5): repository content,
   research pages and tool results that try to steer tools, approvals or the
   Verifier; agent output rendered in the frontend (WSTG-v42-CLNT-01, CLNT-03).
6. **The deployment** (WSTG-v42-CRYP-01, CONF-07, latest@87cf4a4 CONF-14):
   TLS configuration of the frontend, the API and NATS, HSTS, the headers the
   ingress adds, `/metrics` and `/docs` exposure, cookie flags over https.

The report follows the WSTG reporting structure (chapter 5): an executive
summary, the test parameters (scope, dates, tester, the commit and the
configuration tested), and findings, each with its WSTG id, severity by §9.2,
the steps to reproduce, the evidence, and a fix; plus the tests that were not
run and why. Findings are filed as issues; each fix ships with a test that
reproduces it, added to the suite for its area.


## 17. Security Programme: Exceptions, Metrics and Training

This section is kept by the `sdl-program` skill (`.claude/skills/sdl-program/`,
Microsoft SDL practices 1, 9 and 10). The repository owner accepts risk; every
contributor and every agent meets the standard (`CLAUDE.md`, `AGENTS.md`); a
programme reviewer, person or agent, keeps this section current once a
quarter and before a release tag. Entries written by an agent are proposals
until the owner accepts them in the pull request that adds them.

### 17.1 Exception register

Every accepted or pending risk: why it exists, what limits it meanwhile, how
it ends, when it is looked at again and who accepted it. An entry whose date
has passed goes back to the owner. "Not recorded" means the repository does
not say who accepted it; the owner confirms or removes the entry.

*Read on 2026-11-18 at commit `8ddbe9f`.*

| Id | Exception | Why | Interim mitigation | How it ends | Review by | Accepted by |
|---|---|---|---|---|---|---|
| E-01 | `pypi:cffi`, licence MIT-0 (`.github/licence-exceptions.txt`) | MIT-0 is not on the allowlist of `scripts/licence_scan.py`; it comes in through `pynacl` and `cryptography` (backend, daemon) | The scan matches only this licence for this package; a licence change stops the match | The owner adds MIT-0 to `ALLOWED`, or keeps the exception | 2026-12-31 | **Open owner decision**, not accepted |
| E-02 | `pypi:psycopg2-binary`, LGPL | LGPL with the OpenSSL linking exception; imported unmodified and dynamically by the backend and the proxy | Unmodified use; the obligation is the LGPL source on request when an image ships it | The owner accepts LGPL for an unmodified database driver, or the code moves to another driver | 2026-12-31 | **Open owner decision**, not accepted |
| E-03 | `pypi:regex`, Apache-2.0 AND CNRI-Python | CNRI-Python (the parts derived from CPython's `sre`) is not on the allowlist; it comes in through `tiktoken` | As E-01 | The owner adds CNRI-Python to `ALLOWED`, or keeps the exception | 2026-12-31 | **Open owner decision**, not accepted |
| E-04 | `npm:caniuse-lite`, CC-BY-4.0 | Browser-support data reaching the runtime set through `autoprefixer`, which `frontend/package.json` lists in `dependencies`; none of it is in the built bundle | Build-time only | Move `autoprefixer`, `postcss` and `tailwindcss` to `devDependencies`, or accept CC-BY-4.0 for data | 2026-12-31 | **Open owner decision**, not accepted |
| E-05 | `npm:cldrjs`, UNKNOWN | The package declares MIT in the deprecated `licenses` array, which `package-lock.json` does not record; it comes in through `globalize` | The package's own `LICENSE-MIT` | The owner confirms MIT, or the scan learns to read the legacy field | 2026-12-31 | **Open owner decision**, not accepted |
| E-06 | Dependabot `ignore`: `sqlmodel` minor and patch (backend) | 0.0.48 and later require timezone-aware datetimes; `backend/app` still calls `datetime.utcnow()` (74 calls on this date, `grep -rn utcnow backend/app`). Locked 0.0.38, latest 0.0.50 | Dependabot still opens security updates; the pip-audit job fails on a known vulnerability | A migration of the data model to aware datetimes, then remove the ignore | Not set | Not recorded |
| E-07 | Dependabot `ignore`: `react`, `react-dom`, `@types/react`, `@types/react-dom` majors | React 19 breaks `tsc` and the frontend tests while the pair is on 18. Locked 18.3.1, latest 19.3.1 (`npm outdated`) | As E-06 (`npm audit --omit=dev` gates pull requests) | A React 19 migration with its own tests | Not set | Not recorded |
| E-08 | The Trivy file scan on pull requests does not gate, and its SARIF goes nowhere | The `security` job of `security-scan.yml` sets no `exit-code`, its upload step has `continue-on-error: true`, and code scanning is not enabled on the repository, so the results are not stored. That job is also the one status check `main` requires | The weekly baseline (`security-baseline.yml`) runs Trivy failing on CRITICAL and HIGH; pull requests are gated by pip-audit, npm audit, the licence scan, gitleaks and Semgrep | Enable code scanning, or make the pull-request scan fail on CRITICAL and HIGH, and decide which check `main` requires | Not set | Not recorded |
| E-09 | Dependabot `ignore`: `tailwindcss` majors | Tailwind 4 moves its PostCSS plugin and needs a config migration; #463 broke the build. Locked 3.4.19, latest 4.3.5 | As E-07 | A Tailwind 4 migration | Not set | Not recorded |
| E-11 | Dependabot `ignore`: `python` base image minor and major (the five Python images and compose) | The backend's dependencies publish no wheels for newer Pythons and the slim image has no compiler; not re-verified on this date | 3.12.x patch updates still arrive | Move when the wheels exist | Not set | Not recorded |
| E-13 | `slsa-framework/slsa-github-generator` referenced by tag `@v2.1.0`, not by sha | The generator runs only when called by a release tag | `backend/tests/test_workflow_pinning.py` allows that one reference and no other; Dependabot updates it | Upstream support for sha calls | Not set | Not recorded |
| E-15 | gitleaks exceptions for 27 findings triaged as fixtures or examples (#522) | Triaged on 2026-11-18 from the weekly scan and a local full-history run of v8.21.2: 5 distinct values, none a live credential. An alphabet-run OpenAI-style key and AWS key id, and a stub RSA key with the body `abc`, in tests that check a secret is refused or never opened; the placeholder `X-Voice-Key` value `clave-demo` in a curl example of `AGENTS.md`; and two CVE identifiers in the deleted pytm `docs/threat_model.json` (false positives). 23 of the 27 are only in the history: those files were split, rewritten or deleted. The 4 in the tree are in `backend/tests/test_standards.py`, a protected file whose standards check must be fed values it recognises | History-only findings are ignored by fingerprint (`.gitleaksignore`, `commit:file:rule:line`), which never matches a new commit. The live file has one `.gitleaks.toml` entry per rule: that path AND that exact value (`condition = "AND"`). The same value in another file, or another value in that file, is still reported (checked with a scratch file on 2026-11-18). No test guards their shape yet: a backend test reading `.gitleaks.toml` and `.gitleaksignore` needs both in the `backend` filter of `changes.yml`, a protected path the owner edits | The owner, or an agent the owner allows to edit the protected test, builds the fixtures of `test_standards.py` by concatenation; its two `.gitleaks.toml` entries then go. The history entries stay as long as the history does | 2027-02-18 | **Proposed** in this pull request; the owner accepts it by merging |

Closed entries, kept for the history:

| Id | Exception | Closed on | How |
|---|---|---|---|
| E-12 | `safeReturnPath()` as the compensating control for GHSA-wrjc-x8rr-h8h6 and GHSA-337j-9hxr-rhxg (`react-router` 6) | 2026-11-18 (read) | `react-router-dom` 7.18.5 carries the fix; the function stays as defence in depth |
| E-14 | `safety check \|\| true`, a dependency check that never failed | 2026-11-15 | PR #523: `pip-audit` and `npm audit` fail the job, exceptions only in `.github/pip-audit-ignore.txt` (empty on 2026-11-18) |
| E-10 | Dependabot `ignore` on `vite`, `vitest` and `@vitejs/plugin-react` majors, whose reason ("the project is on vite ^5") no longer held | 2026-11-18 | The ignore is removed (#522). The project is on `vite` 8, `@vitejs/plugin-react` 6 and `vitest` 5, the latest majors that day; their next majors arrive together in the `vite-toolchain` Dependabot group, since `@vitejs/plugin-react` and `vitest` declare peer ranges on `vite`'s major |

**Open owner decisions.** Nothing in this list is decided by the reviewer:

1. E-01 to E-05, the five licence exceptions, before 2026-12-31.
2. "Require review from Code Owners" on `main` (#182), and how it fits
   `CONTRIBUTING.md` rule 5 (agents merge pull requests). The settings read
   on 2026-11-18 (`gh api repos/{owner}/{repo}/branches/main/protection` and
   `.../rulesets`) disagree with each other: the classic branch protection
   has `require_code_owner_reviews: true` with 0 required approvals, and both
   active rulesets (`main`, `factory-status-gates`) have
   `require_code_owner_review: false`. The owner says which state is meant,
   and §5.3 is corrected to it.
3. E-06 to E-09, E-11 and E-13 have no review date and no recorded
   acceptor: the owner sets both, or removes the entry.
4. E-15, the gitleaks exceptions, is proposed with a review date of
   2027-02-18; accepting it is merging the pull request that adds it.

### 17.2 Security metrics

Every number names its source and the date it was read. A number the reviewer
could not read is marked **not read**, never estimated.

*Read on 2026-11-18 at commit `8ddbe9f`, unless the row says otherwise.*

| Metric | Value | Source |
|---|---|---|
| Open security issues | 0 | `gh api "repos/{owner}/{repo}/issues?labels=security&state=open"` |
| Open security issues by severity | **Not measurable**: the repository has no severity labels from §9.2 (the `risk:*` labels are the task risk tier, not the bug bar) | `gh api repos/{owner}/{repo}/labels` |
| Open Dependabot alerts | **Not read**: the API answered 403 to the reviewing session. Last known: 64 on 2026-10-29 (§5.4) | `gh api repos/{owner}/{repo}/dependabot/alerts` |
| Advisories in the shipped frontend | 0 (0 with the build tooling too) | `cd frontend && npm audit --omit=dev`; `npm audit` |
| Python advisories (backend, proxy and daemon locks) | 0: the `dependencies` job passed on `8ddbe9f`; no ignore entries | `security-scan.yml` run 44364348276; `.github/pip-audit-ignore.txt` |
| Code-scanning alerts | **Not available**: code scanning is not enabled (E-08) | `gh api repos/{owner}/{repo}/code-scanning/alerts` |
| Weekly baseline | Failed on 2026-11-02, 2026-11-09 and 2026-11-16. On 2026-11-16 only gitleaks failed; Semgrep and Trivy passed. With the E-15 exceptions, the same gitleaks command over the full history exits 0 locally on 2026-11-18; the next scheduled run is the first in CI | `gh api repos/{owner}/{repo}/actions/workflows/security-baseline.yml/runs`, run 43798024603 |
| gitleaks findings in the history | 22 on 2026-11-16 (22 on 2026-11-09, 2 on 2026-11-02); 27 on 2026-11-18 at `b6596ce`, the 5 more from commits that split `test_agent_tools.py` and `test_developer_tools.py`. **Triaged on 2026-11-18: 0 live credentials**; 27 fixtures, examples or false positives, 5 distinct values, excepted narrowly (E-15). Open after the exceptions: 0 | The job log of run 43798024603 (values redacted); `gitleaks detect --source . --config .gitleaks.toml` v8.21.2, with and without the exceptions |
| Time to fix | 9 issues labelled `security` closed from 2026-10-30 to 2026-11-18: median 12.7 h, worst 26.6 h (#327). The two filed as vulnerabilities, #524 and #525, closed in 2.3 h and 1.2 h. Measured from opened to closed, not to a release; **not comparable with the bug bar**, since none carries a severity | `gh api "repos/{owner}/{repo}/issues?labels=security&state=all"` |
| Dependency age, backend | 10 top-level packages behind: 1 major (`bcrypt` 4.3.0 → 5.0.0), 1 held by E-06 (`sqlmodel`), `sqlalchemy` 2.0.57 → 2.1.9, 7 minor or patch | `cd backend && poetry show --outdated --top-level` |
| Dependency age, LLM proxy | 1 patch behind (`sqlalchemy` 2.1.8 → 2.1.9) | `cd llm-proxy && poetry show --outdated --top-level` |
| Dependency age, daemon | **Not read** | — |
| Dependency age, frontend | 17 behind: 6 majors (`react`, `react-dom`, `@types/react`, `@types/react-dom` held by E-07; `tailwindcss` held by E-09; `typescript` 5.9.3 → 7.0.2, not held), 11 minor or patch | `cd frontend && npm outdated` |
| Dependabot pull requests | 0 open; the last merged was #607 on 2026-11-16 | `gh api "repos/{owner}/{repo}/pulls"` |
| Third-party actions not pinned by sha | 1, the SLSA generator (E-13); the local reusable workflow `./.github/workflows/changes.yml` is not counted | `grep -rn "uses: " .github/workflows \| grep -v "@[0-9a-f]\{40\}"` |
| Audit chain health | **Not read**: no staging deployment was reachable | `GET /v1/audit/chain` |
| Person-led penetration test (§16) | **None on file**: no report has been filed since §16 was written | §16 |
| Threat models (practice 10) | 11 of the 15 design documents added under `docs/design/` since 2026-10-29 have a threats section; `CODE_GRAPH.md`, `EXECUTION_ENVIRONMENT.md`, `SAVED_VIEWS.md` and `SELF_HOSTED_RUNNER.md` do not | `grep -nE "^#+.*(Threat\|STRIDE)" docs/design/*.md` |

Compared with the previous review (§5.4 of 2026-10-29): the advisories in the
shipped frontend went from 1 to 0; the weekly gitleaks findings rose from 2 to
22 (27 by 2026-11-18), and on 2026-11-18 all were triaged as fixtures,
examples or false positives and excepted narrowly (E-15, #522).

### 17.3 Training notes

The project's contributors are people and agents; both need the why, the what
and the how for their role (SDL practice 10).

**Agents.** The rules an agent follows are stated in `CLAUDE.md`, `AGENTS.md`,
`.github/copilot-instructions.md` and §11 of this file. On 2026-11-18 the first
three state the design rules of `docs/POSITIONING.md` (a claim and an
observation never share a field; an agent's report of its own test run is a
claim; hashes and signatures are never accepted from the party being
recorded); §11 now points to them too (rule 11). The repository's skills give
an agent the procedure: `threat-model` before a design that crosses a trust
boundary, `sdl-review` before a pull request that touches a sensitive area,
`sdl-program` for this section.

**People.** `CONTRIBUTING.md`, Security onboarding, is the short path for a new
contributor: §10 and §11 here, the design rules, the two skills and the
lessons below.

**Lessons from this repository.** Update this list when an incident or a near
miss teaches something new.

- **#39, `decision_signature` taken from the request body.** The hash that
  proved a decision was supplied by the caller, so the party being recorded
  could write its own proof. It is now computed by the server. The rule it
  taught is rule 6 of the positioning: hashes and signatures are never
  accepted from the party being recorded.
- **#524, the access token in `localStorage`.** Any script in the page, an
  XSS through agent output included, could read it. Browser sessions moved
  to HttpOnly cookies with rotated refresh tokens (`docs/design/SESSIONS.md`).
  Agent output is untrusted input wherever it is rendered.
- **#525, console assistants outside the sandbox.** Their commands ran on the
  host, past the daemon's sandbox floor and command policy. A new path that
  runs a command goes through the same policy as the old ones.

**Practice.** Threat modeling is learned by doing it: the count of design
documents with a threats section is a row of §17.2.
