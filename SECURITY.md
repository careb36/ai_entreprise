# Security Policy

**Owner:** the repository owner accepts risk; every contributor and agent meets this policy.
**Applies to:** the FastAPI backend, the React frontend, the LangGraph agents, the workspace daemon and NATS, PostgreSQL, Neo4j, the LLM proxy and the Kubernetes manifests.
**Last reviewed:** 2026-11-18 (§5.4 and §17), 2026-11-15 (the rest), with the `sdl-program` skill.

A control that cites a file is built. A control without a file, or marked
**target**, is a rule the code does not meet yet.

---

## Reporting a vulnerability

Report it privately: this repository's **Security** tab → **Advisories** →
**Report a vulnerability** (GitHub private vulnerability reporting). Do not
open a public issue, pull request or discussion. Include the component, the
version or commit, the impact and the steps to reproduce.

The owner must enable private reporting (Settings → Code security). Until it
is on, open a public issue that says only "security report, please contact
me", with no detail.

We triage against the severity table in §9.2, fix on `main` and cut a patch
release (§15).

---

## 1. Purpose and Scope

This policy is binding for every agent, service and pull request that touches
the API, the frontend, agent graphs, prompts and tools, the databases, the
workspace daemon and its bus, container images, Kubernetes manifests
(`deployment/k8s/`) and CI.

---

## 2. Threat Model Overview

Trust boundaries:

1. **User ↔ frontend:** XSS, CSRF, token theft.
2. **Frontend ↔ API:** authentication, authorization, injection, TLS.
3. **API ↔ agents:** prompt injection, unsafe tool choice, exfiltration.
4. **Agents ↔ external tools:** tool-call abuse, leaked secrets.
5. **Services ↔ PostgreSQL/Neo4j:** unauthorized queries, data leaks.
6. **CI ↔ registry and cluster:** malicious images, leaked secrets.
7. **Backend ↔ NATS ↔ daemon ↔ the user's disk:** whoever can publish a request can make the daemon write files and run commands (§8.3, §8.6).
8. **Backend ↔ identity providers and DNS:** forged ID tokens, cross-company assertions, SSRF (`docs/design/SSO.md`).
9. **Backend ↔ Slack and Teams:** forged or replayed interactions (`docs/design/CHAT_APPROVALS.md`).
10. **Local software ↔ the desktop app's local server:** DNS rebinding, cross-origin calls (`docs/design/SESSIONS.md`).
11. **Backend ↔ timestamping authority and customer anchors:** an insider re-signing the audit chain (`docs/design/AUDIT_ANCHORS.md`).

Frameworks: OWASP Top 10 for LLM Applications, OWASP ASVS 4.0, NIST SSDF.
The generated threat model is `docs/threat_model.md`.

---

## 3. Authentication and Authorization

### 3.1 Identity

Settings are in `docs/CONFIGURATION.md`; designs in `docs/design/SESSIONS.md`
and `docs/design/SSO.md`.

- **Passwords:** bcrypt, cost 12, at least 12 characters (`backend/app/passwords.py`).
  Login is rate-limited per address and backs off per account
  (`services/platform/login_throttle.py`). Login, registration and
  invitations answer the same for known and unknown emails.
- **Access tokens:** HS256 JWT under `JWT_SECRET_KEY`, 15 minutes by default
  (`backend/app/auth_tokens.py`). Signature, algorithm, `exp`, `iss`, `aud`
  and `typ` are checked. A key shorter than the hash counts as unset.
- **Browser sessions:** `HttpOnly; Secure; SameSite=Strict` cookies. Refresh
  tokens are stored as HMAC, rotate once, revoke their family on reuse and
  end 7 days after sign-in (`services/platform/sessions.py`). Unsafe cookie
  requests must send `X-Fabrique-CSRF`.
- **API tokens:** one company, a role no higher than Manager or the
  creator's, an expiry (90 days default, 365 max), stored as sha256, audited
  as `api-token:<id>` (`services/platform/api_tokens.py`).
- **SSO:** OpenID Connect with PKCE, `state` and nonce; asymmetric ID token
  signatures only (`services/platform/oidc.py`). The role always comes from
  La Fabrique's membership. Allowed domains are verified by DNS TXT.

**Known gaps.** No DNSSEC validation. A Bearer access token from
`"token_delivery": "bearer"` cannot be revoked by logout (it lives 15
minutes). Login limits are per process. Registration is open and unverified.

### 3.2 API Authorization

- **Deny by default.** `AUTH_REQUIRED` is `true`; `false` is for a local demo.
- **Roles per company:** Owner > Manager > Viewer, checked by
  `require_company_role`. Team roles can raise a member's role, never to
  Owner and never across companies (`services/platform/teams.py`).
- **Every route declares who may call it** in
  `docs/architecture/authorization.json`.
  `backend/tests/test_authorization_matrix.py` fails on an undeclared route
  and calls each route with every role and from another company.
- **Segregation of duties:** whoever started the work does not approve it
  (`services/platform/sod.py`, `docs/design/SEGREGATION_OF_DUTIES.md`).
- **Bodies** are `schemas.RequestModel`s; an undeclared privileged field is a 422.
- **Company scoping** comes from the caller's membership, never the payload.

**Public routes.** The `public_routes` of `authorization.json`. Adding one is
a security decision.

- `/health`, `/health/deep`, `/metrics` (keep the last two internal)
- registration, login, refresh, logout and the CSRF echo
- the SSO start and exchange
- `/v1/capabilities`, `/v1/models` and the model catalogue, `/v1/standards`
- support bug reporting and chat
- the retired `/v1/approve_task` (answers 410)
- refused without their signature, ticket or state: the GitHub webhook, the
  Slack and Teams endpoints, the SSO callback and the Glassroom stream

**Known gaps.**

- `GET /v1/workspace/browse` and `/inspect` are open to any signed-in
  person. Where users do not trust each other, restrict registration.
- A run does not carry its caller's identity. The route that starts the work
  checks the role; the sandbox and command policy (§8.3) bound the agents.
- Service-to-service identity is not mTLS (target).

### 3.3 Frontend Exposure

The backend sends a strict header baseline (CSP `default-src 'none'`, HSTS,
`nosniff`, `X-Frame-Options: DENY`, `no-store` and others) on every answer
(`backend/app/security.py`). `/docs` and `/openapi.json` answer 404 unless
`API_DOCS_ENABLED=true`. Validation errors never echo the value sent.

The repository ships no production web server, so the ingress in front of
the frontend must send:

- `Content-Security-Policy` without `unsafe-inline` for scripts
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy` limited to required features
- `Cross-Origin-Opener-Policy: same-origin`

Tokens live only in `HttpOnly` cookies or memory, never in web storage
(`frontend/src/store/useAuthStore.test.ts`). Build-time variables hold no
secrets. CORS never uses `*` with credentials.

---

## 4. Secret Handling

- No secrets in source, Dockerfiles, manifests, logs or frontend bundles.
- Least privilege for service accounts and keys; static credentials live at
  most 90 days (target).
- A secret manager is the target. Today secrets live in `.env`, which git
  ignores.
- Stored secrets (provider keys, MCP secrets, run secrets, project
  credentials) are encrypted with `FERNET_SECRET_KEY`. Without it they are
  stored with a `plaintext:` prefix and the backend warns.
- Detection: gitleaks with `.gitleaks.toml` runs in the pre-commit hook, on
  every pull request's commits and weekly over the full history. A
  confirmed exposure starts the incident process (§9).

---

## 5. Dependency and Supply Chain Security

### 5.1 Lockfiles and Pinning

- Lockfiles are committed: `backend/poetry.lock`, `llm-proxy/poetry.lock`,
  `frontend/package-lock.json`, and `vscode-daemon/requirements.txt`
  (hashed, installed with `--require-hashes`).
- No `latest` or floating tags in production builds.
- Every third-party action is pinned by commit sha
  (`backend/tests/test_workflow_pinning.py`); no workflow names a `docker://` image.

### 5.2 Scanning

Every pull request runs `.github/workflows/security-scan.yml`:

- `pip-audit` over the backend, proxy and daemon locks, and
  `npm audit --omit=dev --audit-level=high`. Both fail the job. Exceptions
  live only in `.github/pip-audit-ignore.txt`, each with a reason and a
  review date that fails the job once passed.
- The licence scan (§5.3).
- gitleaks over the pull request's commits.
- Semgrep, failing on findings the pull request adds.
- Trivy over the files (SARIF, not gating; §17.1, E-08).

Weekly, `.github/workflows/security-baseline.yml` runs Trivy failing on
CRITICAL and HIGH, and full Semgrep and gitleaks scans. Dependabot covers
pip, npm, docker, compose and Actions. A dynamic scan (DAST) of the running
API is a target, not built.

### 5.3 Supply Chain

**Licence allowlist.** `scripts/licence_scan.py` fails on a runtime
dependency of the four components whose licence is unknown or not one of:
MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, MPL-2.0, PSF-2.0,
Python-2.0, Unlicense, CC0-1.0, 0BSD, BlueOak-1.0.0. Exceptions live only in
`.github/licence-exceptions.txt`, each with a reason and a review date, and
are listed in §17.1.

**Artifacts.** A release attaches a CycloneDX SBOM per component
(`release.yml`). `images.yml` signs each image by digest with keyless cosign
and attaches its SBOM as an attestation (`docs/OPERATIONS.md`, Releasing).
Neither has run on a real tag yet. No cluster verifies signatures at
admission (target).

**Repository.** `.github/CODEOWNERS` covers the paths that decide security.
Signed commits are a target. The self-hosted runner shares its machine with
the daemon (`docs/design/SELF_HOSTED_RUNNER.md`).

### 5.4 Open advisories and their triage

*Read on 2026-11-18 at commit `8ddbe9f`.*

- `npm audit`, with and without dev dependencies: 0 advisories.
- `pip-audit` over all Python locks: passed; no ignore entries.
- The weekly Trivy scan of 2026-11-16: no critical or high finding.
- Open Dependabot alerts: not read (the API answered 403).

Triage by surface. `vite`, `vitest`, `esbuild` and `electron` never reach the
built `dist/` bundle, so an advisory there matters for a compromised build
machine, not for a visitor. Majors held back in `.github/dependabot.yml` are
entries of §17.1.

---

## 6. Container and Kubernetes Hardening

### 6.1 Container Images

Targets: minimal images, a fixed non-root UID, read-only root, all
capabilities dropped, resource limits, no `docker.sock` or host mounts.

### 6.2 Kubernetes Deployments

Targets: Pod Security `restricted`, default-deny network policies, encrypted
volumes, one minimal ServiceAccount per workload, admission policies that
block privileged pods and unknown registries.

### 6.3 Where the code stands

Not met yet. The Dockerfiles use base images by tag and run as root.
`deployment/k8s/backend.yaml` uses `:latest`, no `securityContext` and no
NetworkPolicy. The sandbox containers that run agent code do drop every
capability (§8.3).

---

## 7. Data Privacy and Database Security

### 7.1 PostgreSQL and Neo4j

The API must not connect as a superuser. Use least-privilege users, TLS and
audit logging of privileged operations.

### 7.2 Data Handling

Store only what the product needs. Keep sensitive data out of logs, traces
and error messages. Encrypt and restrict backups, and test restores
(`docs/OPERATIONS.md`, Backups). Retention is enforced by the janitor
(`docs/OPERATIONS.md`, Retention).

### 7.3 Neo4j-Specific

Use parameterized Cypher only. Do not connect the application as the `neo4j`
admin. Disable Neo4j Browser in production unless needed.

### 7.4 Third-party processors

**No provider receives a company's context unless a person chose it for that
company, and the choice is recorded.** Nothing is sent because a tool happens
to be installed (GDPR art. 25 and 28; ISO/IEC 27001 A.5.19–A.5.23; NIST SP
800-53 AC-20).

| Recipient | Receives | Chosen by |
|---|---|---|
| Model providers behind the LLM proxy (DeepSeek, Qwen) | The agents' prompts | The deployment's keys and the company's model choices, audited |
| A console producer's CLI | The task's prompt and worktree, inside the sandbox | A Manager names the CLI for a role (`PUT /v1/companies/{id}/console_producers`); never on by default or by detection |
| A forge (GitHub, GitLab, Bitbucket) | Branches and pull requests | The company's forge policy, set by an Owner, audited |
| An MCP server | What a tool call sends | The operator's allowlist and a Manager's registration (§8.7) |

---

## 8. LLM and Agent Security

### 8.1 Prompt Injection

Treat all external content as hostile, other agents' output included.

- Model output is untrusted. Never run it as code, SQL, Cypher or shell.
- Separate instructions from data, with delimiters.
- Agents fetch URLs only from an allowlist, through `services/platform/egress.py`.
- System prompts forbid following instructions found in data.
- High-impact actions need human approval.

### 8.2 Tool-Call Abuse

- Each role calls only the tools assigned to it.
- Each call is validated (schema, permission, rate, timeout, result size).
- Prefer narrow tools: read-only over write, one resource over `*`.
- Each tool has its own scoped credential.
- Every tool call is logged and recorded as an observation on the run's evidence.
- Never expose a raw database connection or the Kubernetes API as a tool.

### 8.3 Workspace Command Execution: the Sandbox and the Command Policy

The daemon (`vscode-daemon/`) is the only component that runs commands on the
projects' machine. For each `workspace.execute`, the command policy decides
whether it runs and the sandbox decides where.

**The sandbox is the boundary** (`vscode-daemon/sandbox.py`; modes in
`docs/CONFIGURATION.md`):

- `host` (default): no isolation.
- `container`: an ephemeral container with only the worktree mounted,
  network off by default, resource limits, every capability dropped,
  `no-new-privileges`, and the image pinned by content ID.
- `bwrap`: bubblewrap namespaces on Linux, no CPU or memory limit.

The daemon reports the mode it applied and never falls back to the host.
`FABRIQUE_SANDBOX_REQUIRED=container` on the daemon's machine forces every
request into that mode or refuses it (`backend/tests/test_sandbox_floor.py`).
Cache volumes: `docs/design/EXECUTION_ENVIRONMENT.md`.

**The command policy is a first pass, not the boundary**
(`vscode-daemon/command_policy.py`). It parses commands as a shell would and
refuses what it cannot read. `FABRIQUE_COMMAND_DENY` adds a local deny list a
request cannot relax. Test commands come from repository markers, never from
a model.

**What remains open:** `host` is the default; the backend sends no per-company command
policy; there is no egress allowlist. Until these close, treat
`workspace.execute` as privileged and set the sandbox as in §10.8.

### 8.4 LangGraph-Specific

Validate state between nodes. Security-critical branches follow fixed logic,
not model output. Authorization never depends on free-text model output.
Tool nodes come from a registered set.

### 8.5 Model Output Handling

Encode model output before rendering it. Sanitize it before it reaches SQL,
Cypher, JSON, HTML or logs. Cap its size. Never render untrusted markdown or
HTML without a sanitizer.

### 8.6 The Message Bus (NATS)

Whoever can publish on NATS can command the daemon. Setup:
`docs/OPERATIONS.md`, Securing the bus.

- **One user per component.** `scripts/nats_config.py` writes
  `nats/nats-server.conf` (`--check` fails when stale). The `daemon` user
  can answer requests by subject but never send one or change a stream; it
  does not get `allow_responses`, which would let stolen daemon credentials
  command the real daemon.
- **Credentials.** Both `NATS_BACKEND_PASSWORD` and `NATS_DAEMON_PASSWORD`
  turn users on; one alone refuses to start. No secret reaches logs or `/health`.
- **TLS asked for is TLS or nothing:** a missing certificate stops the broker.
- **Without credentials** the broker is safe only on loopback; both sides warn.

### 8.7 MCP servers: who may start what

A Manager registers a company's MCP servers (`services/tools/mcp_policy.py`;
`docs/OPERATIONS.md`, MCP servers).

- **The operator decides what can run** (`MCP_STDIO_ALLOWED_COMMANDS`,
  `MCP_HTTP_ALLOWED_HOSTS`, environment only); the company picks from it.
- **stdio is off by default.** A command must be exactly a listed absolute
  path, run without a shell. Allowing an interpreter or package runner
  (`npx`, `uvx`, `python`, a shell) lets a Manager run any code as the backend.
- **Every start is re-checked, needs a Manager and is audited.**
- **HTTP servers** must be https on public addresses, re-checked before each
  connection. No setting allows a private host.
- Secrets are encrypted and listed by name only.

---

## 9. Incident Response

### 9.1 Reporting

Report privately as described in "Reporting a vulnerability" at the top. Do
not open a public issue. Include the component, time, impact and steps to
reproduce.

### 9.2 Severity Levels

| Severity | Examples | Response time |
|---|---|---|
| Critical | Unauthorized admin access, remote code execution, prompt injection leading to tool abuse, secret exposure | 24 h containment |
| High | Data exfiltration, auth bypass, XSS on auth pages | 72 h |
| Medium | Missing security headers, excessive logging, misconfiguration | 1 week |
| Low | Best-practice deviations, low-impact issues | Next sprint |

Changing this table is an owner decision.

### 9.3 Process

1. **Prepare:** contacts, runbooks, tested restores.
2. **Detect:** audit logs, the audit chain (`GET /v1/audit/chain`), tool-call anomalies.
3. **Contain:** revoke tokens and keys (`docs/OPERATIONS.md`, Rotate keys), isolate services, disable the agent or tool.
4. **Eradicate:** remove injected data, fix the root cause, rotate secrets.
5. **Recover:** restore from clean backups, monitor closely.
6. **Learn:** blameless retro, update this file and §17.3, add a regression test.

### 9.4 Immediate Actions for LLM Incidents

Suspend the agent or tool. Freeze its state and logs. Revoke and rotate its
credentials. Find whether the injection came from external data or user
input. Check whether output was rendered unsafely or executed.

---

## 10. Security Review Checklist for Agents and PRs

Every pull request that touches the backend, frontend, agents or
infrastructure meets this list. The `sdl-review` skill walks it.

### 10.1 General

- [ ] No secrets in code, config, comments or logs
- [ ] Lockfiles updated, scans pass
- [ ] Errors leak no internals (`backend/tests/test_error_bodies.py`)

### 10.2 Authentication and Authorization

- [ ] A new route is declared in `docs/architecture/authorization.json` and protected unless approved as public (§3.2)
- [ ] Authorization is checked server-side
- [ ] Tokens are validated for signature, algorithm, expiry, type, issuer and audience (§3.1)
- [ ] No default `admin` or fallback credential
- [ ] Agents cannot exceed what the route that started them allowed

### 10.3 Input and Injection

- [ ] Request bodies are `schemas.RequestModel`s
- [ ] A URL a company chooses is fetched only through `services/platform/egress.py` (`backend/tests/test_ssrf_vectors.py`)
- [ ] SQL and Cypher are parameterized
- [ ] No `eval`, `exec`, shell or raw SQL on model output
- [ ] Untrusted output is encoded before rendering

### 10.4 LLM and Agent Security

- [ ] Prompts treat external data as untrusted
- [ ] Tools are allowlisted per role and validate input
- [ ] High-impact calls need human approval
- [ ] Tool credentials are scoped; tool calls are logged
- [ ] Model output never decides authorization

### 10.5 Frontend

- [ ] No secrets in client code; headers of §3.3 in place
- [ ] Tokens only in `HttpOnly` cookies or memory; unsafe cookie requests send `X-Fabrique-CSRF`
- [ ] User-supplied HTML and markdown sanitized

### 10.6 Data and Infrastructure

- [ ] Least-privilege database access; sensitive data minimized
- [ ] Containers and manifests meet §6

### 10.7 Testing

- [ ] Tests for authorization and input validation
- [ ] Negative tests for prompt injection and tool abuse
- [ ] A regression test for each security bug fixed

### 10.8 Deployment Checklist

Before the stack serves anyone but the person running it (variables in
`docs/CONFIGURATION.md`):

- [ ] **Bind.** `NATS_BIND` stays `127.0.0.1` unless the daemon runs elsewhere; frontend and API behind TLS.
- [ ] **Bus credentials.** `NATS_BACKEND_PASSWORD` and `NATS_DAEMON_PASSWORD` set (or `.creds` files); `python3 scripts/nats_config.py --check` passes; `/health` shows `nats.authenticated: true` (§8.6).
- [ ] **Bus TLS.** When NATS leaves the machine: certificates in `nats/certs`, `NATS_TLS=true` and `NATS_TLS_CA` on backend and daemon; `/health` shows `nats.tls: true`.
- [ ] **Auth.** `AUTH_REQUIRED` on; `JWT_SECRET_KEY` set (at least 32 bytes for HS256); external callers use API tokens with the narrowest role and an expiry.
- [ ] **Browser sessions.** Frontend and API over https on one site; `SESSION_COOKIE_SECURE` on; `CORS_ORIGINS` lists the frontend's origins, never `*`; token lifetimes no longer than the defaults (15 minutes, 7 days) unless the owner accepts more.
- [ ] **Exposure.** `API_DOCS_ENABLED` false where the API faces the internet; `TRUSTED_PROXY_IPS` lists exactly your reverse proxies; `/metrics` and `/health/deep` internal only; `ERROR_REPORT_URL` unset or an https endpoint you control; the ingress sends the §3.3 headers; `JWT_ISSUER`/`JWT_AUDIENCE` distinct from any deployment sharing the key.
- [ ] **Single sign-on.** `SSO_CALLBACK_URL` and `SSO_FRONTEND_URL` are public https addresses; `SSO_BREAK_GLASS_EMAILS` names an Owner before any company requires SSO; `SSO_ALLOW_PRIVATE_ISSUERS` off.
- [ ] **Secrets at rest.** `FERNET_SECRET_KEY` set; `AUDIT_SIGNING_KEY` set.
- [ ] **MCP servers.** `MCP_STDIO_ALLOWED_COMMANDS` empty, or only fixed server binaries in a directory the backend cannot write, never an interpreter, package runner or shell; `MCP_HTTP_ALLOWED_HOSTS` empty unless a server cannot speak https (§8.7).
- [ ] **Sandbox.** With `DELIVERY_TESTS_ENABLED`, `DELIVERY_SANDBOX=container` (or each company's mode) and `FABRIQUE_SANDBOX_REQUIRED=container` on the daemon's machine.

---

## 11. Secure Coding Rules for All Agents

1. **Never trust input**, from users, APIs, files or models.
2. **Never trust model output.** Do not execute, query or render it as code.
3. **Least privilege** for services, database users, tools and agents.
4. **Parameterize every query.** Never concatenate input into SQL or Cypher.
5. **No secrets** in code, config, logs or the frontend.
6. **Fail closed.** If authorization cannot be verified, deny.
7. **Log security events** (auth failures, tool calls, state changes, admin actions) without sensitive data.
8. **Encode output** before it reaches HTML, JSON, SQL or logs.
9. **Keep dependencies current:** pin, scan, patch.
10. **Design for audit.** Every action traces to a user and a tool call.
11. **A claim is not an observation** (`docs/POSITIONING.md`): a claim and an observation never share a field, an agent's report of its own test run is a claim, and a hash or signature is never accepted from the party being recorded.

---

## 12. Audit Guide for Security Agent

Audit against §3, §4, §5, §8 and §10, using the `sdl-review` skill for a
change and the `sdl-program` skill for the whole programme.

---

## 13. References

OWASP Top 10 for LLM Applications, OWASP ASVS, OWASP WSTG, NIST SSDF, Microsoft SDL.

---

## 14. Contact

GitHub private vulnerability reporting (see the top of this file). There is
no security mailbox.

---

## 15. Supported Versions and Security Releases

Releases are SemVer tags on `main` (`docs/OPERATIONS.md`, Releasing). No
stable version is tagged yet, so only `main` is supported.

| Version | Security fixes |
|---|---|
| `main` | Yes, first |
| Latest minor (`X.Y.*`) | Yes, as a patch release |
| Older minors | No: upgrade |
| Pre-releases (`-rc.N`) | No |

How a fix ships:

1. Triage privately against §9.2. Prepare the fix in a private advisory fork,
   or on a branch whose name does not describe the flaw.
2. Merge to `main` with a `### Security` line in `CHANGELOG.md` that does not
   hand over an exploit.
3. Cut a patch release at once.
4. Publish the advisory with the fixed version; request a CVE when the
   severity warrants it.

Operators upgrade by `docs/OPERATIONS.md`, Upgrade.

---

## 16. Yearly Person-Led Penetration Test

Once a year, and before a release that changes authentication, roles or the
daemon, a person who did not write the code tests the deployed stack by the
OWASP WSTG. Scope:

1. **Business logic:** approvals and holds, autonomy levels, undo, sprint control out of order.
2. **Identity across tenants:** a hostile identity provider, account pre-hijacking, break-glass, API tokens beyond scope, every route of `authorization.json` by hand.
3. **The bus and the daemon** (§8.3, §8.6): stolen credentials, forged replies, the sandbox floor, path traversal.
4. **Chat spoofing:** forged or replayed Slack and Teams interactions.
5. **Prompt injection through agent output** (§8.1–§8.5), including frontend rendering.
6. **The deployment:** TLS, headers, `/metrics` and `/docs` exposure, cookie flags.

Each finding carries its WSTG id, §9.2 severity, steps and a fix, becomes an
issue, and ships with a test.

---

## 17. Security Programme: Exceptions, Metrics and Training

The `sdl-program` skill keeps this section current once a quarter and before
a release. Entries proposed by an agent need the owner's acceptance.

### 17.1 Exception register

An entry past its review date goes back to the owner. *Read on 2026-11-18.*

| Id | What | Why | Review by |
|---|---|---|---|
| E-01 | `pypi:cffi`, licence MIT-0 | Not on the allowlist; comes in through `pynacl` and `cryptography` | 2026-12-31 (owner decision open) |
| E-02 | `pypi:psycopg2-binary`, LGPL | Used unmodified and dynamically by the backend and proxy | 2026-12-31 (owner decision open) |
| E-03 | `pypi:regex`, Apache-2.0 AND CNRI-Python | CNRI-Python not on the allowlist; comes in through `tiktoken` | 2026-12-31 (owner decision open) |
| E-04 | `npm:caniuse-lite`, CC-BY-4.0 | Build-time data via `autoprefixer`, listed in `dependencies`; not in the bundle | 2026-12-31 (owner decision open) |
| E-05 | `npm:cldrjs`, UNKNOWN | Declares MIT in a legacy field the lockfile does not record | 2026-12-31 (owner decision open) |
| E-06 | Dependabot ignores `sqlmodel` updates | 0.0.48+ needs timezone-aware datetimes; `backend/app` still uses `datetime.utcnow()` | Not set |
| E-07 | Dependabot ignores React 19 majors | React 19 breaks `tsc` and the frontend tests | Not set |
| E-08 | Trivy on pull requests does not gate; SARIF is not stored | Code scanning is not enabled; the weekly baseline gates on CRITICAL and HIGH | Not set |
| E-09 | Dependabot ignores Tailwind majors | Tailwind 4 needs a config migration | Not set |
| E-11 | Dependabot ignores Python base image minor and major | Dependencies lack wheels for newer Pythons | Not set |
| E-13 | SLSA generator referenced by tag, not sha | The generator runs only from a release tag; `test_workflow_pinning.py` allows only this one | Not set |
| E-15 | gitleaks exceptions for 27 findings | Fixtures, examples or false positives, no live credential. History-only ones ignored by fingerprint (`.gitleaksignore`); the 4 in `backend/tests/test_standards.py` by exact path AND value (`.gitleaks.toml`) | 2027-02-18 (proposed) |

Closed: E-10 (vite majors ignore removed), E-12 (`react-router-dom` 7.18.5
carries the fix), E-14 (`pip-audit` and `npm audit` now fail the job).

**Open owner decisions:** the five licence exceptions (E-01 to E-05) before
2026-12-31; review dates and acceptors for E-06 to E-09, E-11 and E-13; E-15;
and whether "Require review from Code Owners" is meant to be on for `main`
(the branch protection and the rulesets disagree).

### 17.2 Security metrics

A number not read is marked so, never estimated. The commands are in the
`sdl-program` skill.

*Read on 2026-11-18 at commit `8ddbe9f`.*

| Metric | Value |
|---|---|
| Open security issues | 0 |
| Open issues by severity | Not measurable: no §9.2 severity labels |
| Open Dependabot alerts | Not read (403) |
| Frontend and Python advisories | 0 |
| Code-scanning alerts | Not available (E-08) |
| gitleaks findings in history | 27, all triaged (E-15); 0 live credentials |
| Time to fix (`security` label, closed 2026-10-30 to 2026-11-18) | Median 12.7 h, worst 26.6 h |
| Actions not pinned by sha | 1 (E-13) |
| Audit chain health | Not read (no staging deployment) |
| Penetration test (§16) | None on file |

### 17.3 Training notes

**Agents** follow `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`
and §11, and use the skills `threat-model`, `sdl-review` and `sdl-program`.
**People** start with `CONTRIBUTING.md`, Security onboarding.

**Lessons from this repository** (add one when an incident teaches something):

- **#39:** the caller supplied the hash proving its own decision. Hashes and
  signatures are never accepted from the party being recorded.
- **#524:** the access token sat in `localStorage`, readable by any script.
  Agent output is untrusted wherever it is rendered.
- **#525:** console assistants ran commands on the host, past the sandbox.
  Every path that runs a command goes through the same policy.
