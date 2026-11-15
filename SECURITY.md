# SECURITY.md — La Fabrique Multi-Agent Platform

**Version:** 1.1
**Owner:** the repository owner (accepts risk); every contributor and agent meets it
**Applies to:** FastAPI control plane, React/Vite frontend, LangGraph agents, the workspace daemon and NATS, PostgreSQL, Neo4j, the LLM proxy, Kubernetes deployments
**Last reviewed:** 2026-11-15 — checked against the code and against Microsoft SDL practices with the `sdl-program` skill (`.claude/skills/sdl-program/`), issue #522

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
8. **Backend ↔ a company's identity provider** — forged or replayed ID tokens, login CSRF, one company's provider asserting another company's people, SSRF through the issuer URL (§3.1, `docs/design/SSO.md`).

Key threat actors: malicious end users, compromised external content, malicious model output, insider developers, CI/CD attackers.

Reference frameworks:
- OWASP Top 10 for LLM Applications
- OWASP ASVS 4.0
- OWASP Kubernetes Top 10
- NIST 800-218 SSDF

---

## 3. Authentication and Authorization

### 3.1 Identity: what is built

La Fabrique authenticates people itself today; there is no external identity
provider yet.

- **Passwords** are hashed with `bcrypt` directly, cost 12
  (`backend/app/passwords.py`). The schema rejects a password over bcrypt's
  72-byte limit instead of truncating it (`backend/app/schemas.py`), and a
  successful login re-hashes a stored hash whose cost is below the current one
  (`backend/app/routers/auth.py`).
- **Access tokens.** `POST /v1/auth/login` issues a JWT signed with **HS256**
  and the shared secret `JWT_SECRET_KEY` (`backend/app/auth_tokens.py`,
  `backend/app/config.py`). Claims: `sub`, `email`, `iat`, `exp`, `typ:
  access`. It lives `ACCESS_TOKEN_EXPIRE_MINUTES`, **60 minutes** by default.
  Validation checks the signature, the single configured algorithm, `exp`,
  and that `sub`, `exp` and `typ` are present and `typ` is `access`; there is
  no `iss`, `aud` or `nbf` claim. There is no refresh token: a person logs in
  again when the token expires.
- **Transport of the token.** The API reads it from the `Authorization:
  Bearer` header (`backend/app/auth_deps.py`, `HTTPBearer`), never from a
  cookie. The Glassroom stream, which a browser `EventSource` cannot give a
  header, takes a five-minute ticket for one sprint instead
  (`create_stream_ticket`, `typ: glassroom`; membership is checked again
  when it is used).
- **Without `JWT_SECRET_KEY`** login answers 503 and the backend warns at
  startup (`backend/app/lifecycle.py`).
- **API tokens** for external callers (`POST /v1/auth/tokens`,
  `backend/app/services/platform/api_tokens.py`): one company per token, a
  role (`Viewer` by default, `Manager` at most, never above its creator's),
  an expiry (90 days by default, at most 365), revocable. Only the token's
  sha256 is stored; the raw value (`fab_` + 32 random bytes) is shown once.
  A request made with one acts as `api-token:<id>`, so audit rows name it.
- **Rotation.** Changing `JWT_SECRET_KEY` ends every session and stream
  ticket; API tokens keep working (`docs/OPERATIONS.md`, Rotate keys).

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
  cap). A one-time, 60-second code is exchanged for the same access token the
  password login issues. An Owner can require SSO for the company's members
  once the connection is enabled and an Owner has signed in through it;
  Owners listed in `SSO_BREAK_GLASS_EMAILS` keep their password, and each
  such sign-in is audited. Every sign-in and every configuration change
  writes an audit row (never the secret); the public routes are limited by
  `SSO_RATE_LIMIT`.
- **Known gaps.** Allowed domains are not verified by DNS (the first company
  to list one holds it); sessions already issued stay valid until they
  expire when SSO becomes required or a person is removed at the provider;
  the password login itself has no rate limit. Still **targets, not
  present**: access tokens of 15 minutes or less and server-side refresh
  tokens rotated on use (with #524, the token out of `localStorage`). A
  deployment that needs a shorter session lowers
  `ACCESS_TOKEN_EXPIRE_MINUTES`.

### 3.2 API Authorization: what is built

- **Enforcement is on by default.** `AUTH_REQUIRED` defaults to `true`
  (`backend/app/config.py`); with it, a protected route without a valid token
  answers 401 (`backend/app/auth_policy.py`, `current_user_if_required`,
  `require_authenticated`). `AUTH_REQUIRED=false` is for a local demo only:
  a token, when sent, is still resolved, but none is required.
- **Roles per company**, ranked **Owner > Manager > Viewer**
  (`ROLE_RANK` in `backend/app/auth_policy.py`). Every company-scoped route
  calls `require_company_role(session, user, company_id, minimum)`: a caller
  who is not a member gets 403, a member below the minimum gets 403. An API
  token carries its own company and role and is held to the same check.
  Creating a company makes its creator the Owner; inviting a member requires
  Owner. Help-desk administrators are a separate, deployment-wide list
  (`SUPPORT_ADMIN_EMAILS`, `is_support_admin`).
- **Public routes** are an explicit list: `/health`, registration and login,
  the HMAC-signed GitHub webhook, support bug reporting and chat,
  `/v1/models`, `/v1/capabilities` and the retired `/v1/approve_task` (410)
  (`docs/contexts/platform.md`, Auth and multi-tenancy).
- **Company scoping.** Data is namespaced by `company_id`; audit writes and
  memory stores take the company from the caller's membership, never from the
  request's payload.
- **Rate limiting.** `slowapi`, `RATE_LIMIT_DEFAULT` (100/minute by default,
  `backend/app/security.py`).

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
- Service-to-service identity is **not mTLS**. The backend and the daemon
  each have their own NATS user, with TLS and optional client certificates
  (§8.6); the backend and the LLM proxy share `LLM_PROXY_INTERNAL_TOKEN`
  (`docs/OPERATIONS.md`, Rotate keys). mTLS or a service-mesh identity
  between all services is a target.

### 3.3 Frontend Exposure

**Where the code stands.** The backend's `SecurityHeadersMiddleware`
(`backend/app/security.py`, `SECURITY_HEADERS_ENABLED`) sends
`Content-Security-Policy: default-src 'self'` (a looser policy only on
`/docs`, `/redoc` and `/openapi.json`), `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Strict-Transport-Security`,
`Referrer-Policy: strict-origin-when-cross-origin` and a `Permissions-Policy`
that turns off geolocation, microphone and camera; it does not send
`Cross-Origin-Opener-Policy`. CORS allows only `CORS_ORIGINS`. The frontend
keeps its access token in `localStorage` (`frontend/src/store/useAuthStore.ts`,
zustand `persist`, key `fabrique-auth-storage`), which the rule below forbids:
a known gap until the token moves to memory or an `HttpOnly` cookie (#524).

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
but do not run a secret scanner. Stored secrets (provider keys, MCP secrets,
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

### 5.3 Supply Chain

- Use signed commits and signed tags.
- Container images signed with `cosign`; verify signature in admission control.
- Build from pinned, minimal base images. Do not pull `latest` base images.
- Restrict CI/CD pipeline permissions. Approval required for changes to `.github/workflows` or deploy files.

**Where the code stands** (§5.2 and §5.3). Lockfiles are committed
(`backend/poetry.lock`, `llm-proxy/poetry.lock`, `frontend/package-lock.json`,
`vscode-daemon/requirements.txt`) and Dependabot covers pip, npm, docker,
docker-compose and GitHub Actions (`.github/dependabot.yml`). Every pull request
runs, in `.github/workflows/security-scan.yml`: Trivy over the files (SARIF,
not gating); a dependency audit that fails on a known vulnerability —
`pip-audit` over the hashed export of `backend/poetry.lock` and
`llm-proxy/poetry.lock`, and `npm audit --omit=dev --audit-level=high` over
`frontend/package-lock.json`, with exceptions only in
`.github/pip-audit-ignore.txt` (each with a reason and a review date that
fails the job once passed); gitleaks over the pull request's commits; and
Semgrep failing on the findings the pull request adds. The gating Trivy and
the full Semgrep and gitleaks scans run weekly
(`.github/workflows/security-baseline.yml`). Every third-party action is
pinned by commit sha, checked by `backend/tests/test_workflow_pinning.py`
(the SLSA generator is the one exception: it runs only from a release tag).
`vscode-daemon/requirements.txt` is a version range, not a lock, so the audit
does not cover the daemon yet. A SLSA provenance workflow exists
(`generator-generic-ossf-slsa3-publish.yml`, on a created GitHub Release or
by hand), but it hashes images it builds itself rather than the ones
`images.yml` publishes, and a Release created by `release.yml` with the
workflow's own token does not start it; the five published images have no
SBOM and are not signed, and commits and tags are not
required to be signed. SBOMs and image signing are noted in issue #522 for
later.

### 5.4 Open advisories and their triage

*Snapshot taken on 2026-10-29 (commit `6f343f7`); the counts were not re-read
in the 2026-11-15 review, since the Dependabot API was not reachable from it.
Re-read them with the commands in `.claude/skills/sdl-program/SKILL.md`, step 1.*

Dependabot reported 64 open alerts on `main`. That number mixes what reaches a
browser with what only runs on a build machine, so it is worth separating:

| Surface | Advisories | Manifest |
|---|---|---|
| Shipped to the browser | 1 (`react-router`, moderate) | `frontend/package-lock.json` |
| Build and test only | 62 — 2 critical, 15 high, 35 medium, 10 low | `frontend/package-lock.json` |
| Test tooling | 1 (`pytest`, medium) | `backend/poetry.lock` |

`npm audit --omit=dev`, which is the surface a user actually receives, returns
two entries for a single advisory.

**Build-only.** `vitest` and its worker pool `tinypool` (the two criticals),
`vite`, `esbuild`, `electron` and `extract-zip` do not appear in the `dist/`
bundle: the frontend is a Vite SPA built to static assets. The `frontend`
service in `docker-compose.yml` runs the Vite dev server with bind mounts — it
is a development container, not a deployment artifact, and it is the only place
where these packages execute. They matter for a compromised build agent, not for
a visitor.

**`react-router` — GHSA-wrjc-x8rr-h8h6 (open redirect via a backslash in
`<Link>` / `useNavigate`) and GHSA-337j-9hxr-rhxg (constructor injection in
`deserializeErrors()`).** Neither is fixed inside v6; both are patched in
`react-router-dom` 7.18.5, a major migration.

- The `deserializeErrors()` advisory requires **SSR hydration**. This frontend
  mounts with `createRoot`; there is no `hydrateRoot` and no `renderToString`,
  so the vulnerable path does not exist here.
- The open-redirect advisory needs a user-controlled destination reaching
  `<Link>` or `useNavigate`. Every such call site — `Login`, `Register`,
  `CompanySelect` — passes through `safeReturnPath()` in
  `frontend/src/lib/returnPath.ts`, which accepts only a path starting with a
  single `/` and rejects `//`, `/\`, any backslash, control characters, their
  percent-encoded forms, and the auth-loop paths. Ten unit tests cover it, and
  the advisory's own vector, a backslash, is one of the rejected cases.

That is a compensating control, not a fix: the v7 migration stays on the
roadmap.

**`pytest` — GHSA-6w46-j5rx-g56g (temporary directory handling).** Patched in
9.0.3, and the pin is `^8.1.1`. This one is worth taking: it is test tooling
only.

**Why the majors are not taken blind.** `vite` 5→8, `vitest` 3→5, `electron`
→45 and `react-router` 6→7 each change APIs the build depends on. Each `ignore`
in `.github/dependabot.yml` carries its reason next to it, so an upgrade is a
planned piece of work with its own tests instead of a red PR every Monday.

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
2. **The sandbox** (`vscode-daemon/sandbox.py`) decides where it runs, when
   the request carries a `sandbox`.
3. Otherwise it runs in the host shell, with the daemon's privileges.

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
a `host` request in that mode instead and says so (`forced_by_daemon`).

Requests that carry a `sandbox`: the delivery test run
(`services/loop/delivery_checks.py`), the Developer's test tool
(`services/tools/developer_tools.py`), the lint after an edit
(`services/tools/edit_lint.py`), the dependency install
(`services/workspace/dependency_install.py`), a console producer's run
(`services/producers/console_producer.py`) and the application check
(`vscode-daemon/app_run.py`).

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
- `FABRIQUE_SANDBOX_REQUIRED` applies only to a request that carries a
  `sandbox`. The console assistants' consultations
  (`services/producers/console_assistants.py`, a configured CLI template run
  with the agent's prompt) send `workspace.execute` with neither a `sandbox`
  nor a `command_policy`, so they run on the host; only the daemon's own
  `FABRIQUE_COMMAND_DENY` applies to them (#525).
- The backend does not send a per-company command policy yet
  (`docs/CONFIGURATION.md`): the only policy it sends is the console
  producer's, which allows only that CLI's own command
  (`services/producers/console_clis.py`, `command_policy`). Without a policy
  and without `FABRIQUE_COMMAND_DENY`, every command runs.
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
  the backend suite checks it too, `test_nats_security.py`). The `backend` user publishes the work (`workspace.>`,
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

### 10.2 Authentication and Authorization

- [ ] New endpoints enforce authentication unless explicitly public and approved
- [ ] Authorization checked server-side for every role/permission
- [ ] Token validation includes signature, a single allowed algorithm, expiry and token type (`backend/app/auth_tokens.py`); for an SSO ID token, also the issuer, audience, nonce and an asymmetric algorithm (`services/platform/oidc.py`, §3.1)
- [ ] No default `admin` or insecure fallback credentials
- [ ] Agent inherits caller permissions and cannot exceed them

### 10.3 Input and Injection

- [ ] All user and agent input validated and sanitized
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
- [ ] Tokens stored securely; no `localStorage`
- [ ] CORS restricted to known origins
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
      requests write name it (`api-token:<id>`); `JWT_SECRET_KEY` set and
      `AUTH_REQUIRED` on.
- [ ] **Single sign-on.** `SSO_CALLBACK_URL` and `SSO_FRONTEND_URL` are the
      public https addresses; `SSO_BREAK_GLASS_EMAILS` names at least one
      Owner before any company requires SSO; `SSO_ALLOW_PRIVATE_ISSUERS`
      stays off; `FERNET_SECRET_KEY` is set, so client secrets are encrypted
      (§3.1, `docs/OPERATIONS.md`).
- [ ] **Secrets at rest.** `FERNET_SECRET_KEY` set, so provider keys, MCP
      secrets, run secrets and project credentials are encrypted (the backend
      warns at startup without it); `AUDIT_SIGNING_KEY` keys the audit chain.
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
