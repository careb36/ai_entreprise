# SDL reference for La Fabrique

Background for the `sdl-review`, `threat-model` and `sdl-program` skills: what
each of Microsoft's ten SDL practices asks for (summarised in this project's
own words), and where La Fabrique stands against it. The state below was read
from `main` on 2026-11-15; re-check a row before quoting it, since files move.

## Sources

Microsoft, *Security Development Lifecycle (SDL) practices*,
https://www.microsoft.com/en-us/securityengineering/sdl/practices. The
overview places the practices across four stages (Design, Code, Build and
Deploy, Run) under Zero Trust: assume breach, verify explicitly, least
privilege. The practice pages (the `aka.ms` links redirect to the
`microsoft.com/securityengineering/sdl/practices/...` addresses given here):

| # | Practice | Page |
|---|---|---|
| 1 | Establish security standards, metrics, and governance | https://www.microsoft.com/securityengineering/sdl/practices/security-program-management |
| 2 | Require use of proven security features, languages, and frameworks | https://www.microsoft.com/securityengineering/sdl/practices/secure-platforms |
| 3 | Perform security design review and threat modeling | https://www.microsoft.com/securityengineering/sdl/practices/secure-by-design |
| 4 | Define and use cryptography standards | https://www.microsoft.com/securityengineering/sdl/practices/cryptography |
| 5 | Secure the software supply chain | https://www.microsoft.com/securityengineering/sdl/practices/sscs |
| 6 | Secure the engineering environment | https://www.microsoft.com/securityengineering/sdl/practices/secure-dev-infra |
| 7 | Perform security testing | https://www.microsoft.com/securityengineering/sdl/practices/security-testing |
| 8 | Ensure operational platform security | https://www.microsoft.com/securityengineering/sdl/practices/operational-security |
| 9 | Implement security monitoring and response | https://www.microsoft.com/securityengineering/sdl/practices/monitoring-and-response |
| 10 | Provide security training | https://www.microsoft.com/securityengineering/sdl/practices/security-training |

Microsoft's text is copyrighted; the summaries below paraphrase it and are not
a substitute for reading the pages.

## What each practice asks, in brief

1. **Governance.** Keep security requirements current and tracked; base them
   on recognised frameworks (NIST SSDF, OWASP); set a minimum baseline; define
   a severity "bug bar" with fix deadlines and do not loosen it; label security
   work so it can be measured; run a written, time-limited exception process
   approved at the right level; assign who accepts risk and who meets the
   standard.
2. **Proven building blocks.** Use standard features and libraries for
   authentication, authorisation and audit rather than custom code; one
   consistent approach for each; strong identity and least privilege with
   time-limited elevation; secrets in a vault, inventoried and rotated, never
   in code or shared between production and test; guard AI features against
   prompt injection; log security events.
3. **Design review and threat modeling.** Model the design early, from an
   attacker's viewpoint: a data flow diagram with trust boundaries, assets,
   threats (STRIDE as a prompt, not the whole method), layered mitigations,
   work items and tests that prove each mitigation; share and track it.
4. **Cryptography.** A written standard; vetted libraries only, no custom
   algorithms; encrypt in transit (modern TLS only) and at rest, at the
   application layer where disk encryption is not enough; design for
   algorithm agility and post-quantum migration; manage keys and certificates
   over their whole life, with fast rotation and audited access.
5. **Supply chain.** A governed way to take in open source; an inventory of
   direct and transitive dependencies with alerts and updates; machine-readable
   SBOMs with checksums; signed artifacts and provenance attestations; extra
   scrutiny for high-risk components.
6. **Engineering environment.** Treat build systems and developer machines as
   targets; Zero Trust and least privilege for engineering access; source
   control with strong authentication; protected branches with required
   review; hardened machines for privileged work; managed, standardised
   environments; security logging of engineering systems.
7. **Security testing.** Static analysis in the developer loop and in every
   build; dynamic testing of the running system; software composition
   analysis; penetration tests, red/blue exercises and scheduled testing.
8. **Operational security.** Strong authentication for administrators,
   separate admin identities and just-in-time elevation; enforced baselines
   with drift detection; isolation between processes, hosts and networks;
   attack-surface reduction; patching; regular platform testing; DoS defence.
9. **Monitoring and response.** Deliberately designed logs that support
   detection and forensics, archived and queryable; detection and posture
   tracking; a written incident response plan with contacts and a servicing
   process for inherited and third-party code, rehearsed before it is needed.
10. **Training.** Role-specific training for everyone whose decisions affect
    security, at onboarding and refreshed; threat modeling as a core skill;
    varied formats and champions; content updated from real incidents.

## Practice to repository mapping

"Exists" cites what was found; "Missing" is a gap or a stale document, to
confirm before filing.

| # | Exists in La Fabrique | Missing or weak |
|---|---|---|
| 1 | `SECURITY.md` (requirements, §9.2 severity table with response times, §10 PR checklist, §5.4 advisory triage); `docs/STANDARDS.md`; `security-baseline.yml` header says findings are tracked; `.github/PULL_REQUEST_TEMPLATE.md`; `CHANGELOG.md` per PR | Fixed in the #522 part A pull request: `SECURITY.md` header now reads "Last reviewed: 2026-11-15", the placeholder contact is replaced by GitHub private vulnerability reporting (which the owner must enable in the repository settings), §3 describes the HS256 JWT and marks OIDC/PKCE/mTLS/≤15-minute tokens as planned (#512 phase 3), and §15 states the supported versions. Still open: §5.4 alert counts are a dated snapshot (2026-10-29), not re-read; no written exception process; no security label or metric (open findings, time to fix) |
| 2 | bcrypt (`backend/app/passwords.py`); JWT via `auth_deps.get_current_user`; browser sessions in `HttpOnly; Secure; SameSite=Strict` cookies with 15-minute access tokens, rotated refresh tokens stored as HMAC with reuse detection and absolute expiry, a signed double-submit CSRF header, and CORS credentials only with explicit origins (`session_cookies.py`, `services/platform/sessions.py`, `docs/design/SESSIONS.md`); `AUTH_REQUIRED` on by default; Owner > Manager > Viewer and company scoping (`docs/contexts/platform.md`); API tokens hashed with roles and expiry (`services/platform/api_tokens.py`); Fernet for stored secrets; run secrets (`services/platform/run_secrets.py`); prompt-injection rules (SECURITY.md §8.1–8.5); research tools fetch only from an allowlist | Secrets live in `.env`, no vault; `FERNET_SECRET_KEY` optional with a `plaintext:` fallback (`docs/CONFIGURATION.md`); no IDE-time analyser recommended; a Bearer access token from `"token_delivery": "bearer"` has no family, so logout and revocation do not reach it (it lives 15 minutes; automation should use API tokens); agent runs do not carry the caller's identity (SECURITY.md §3.2) |
| 3 | SECURITY.md §2 trust boundaries; `docs/threat_model.md` generated by `scripts/threat_model.py` (OWASP pytm 1.4.0, STRIDE), with hand-written sections on the sandbox and NATS; `docs/design/` for design notes | The pytm output is largely generic library text; no per-feature threat modeling procedure or template (now the `threat-model` skill); no rule that a design PR carries one |
| 4 | HMAC-SHA256 audit signing and per-company hash chain (`backend/app/audit_signing.py`, `backend/app/audit_chain.py`, `AUDIT_SIGNING_KEY`); `hmac.compare_digest` in those and in `services/producers/github_adapter.py`, `services/tasks/task_events.py`, `llm-proxy/connections.py`; sha256 read back from disk by the daemon (`workspace.generate`, `workspace.hash`); POSITIONING rule 6; key generation commands (`docs/CONFIGURATION.md`); rotation table (`docs/OPERATIONS.md`, Rotate keys); NATS TLS that refuses to fall back (SECURITY.md §8.6) | No written cryptography standard (algorithms allowed, minimum TLS); rotating `FERNET_SECRET_KEY` does not re-encrypt (values are lost); JWT is a shared HS256 secret; no statement on algorithm agility |
| 5 | Secure the software supply chain | https://www.microsoft.com/securityengineering/sdl/practices/sscs |
| 6 | Secure the engineering environment | https://www.microsoft.com/securityengineering/sdl/practices/secure-dev-infra |
| 7 | Perform security testing | https://www.microsoft.com/securityengineering/sdl/practices/security-testing |
| 8 | Sandbox modes with the applied mode as evidence and `FABRIQUE_SANDBOX_REQUIRED` (`vscode-daemon/sandbox.py`, `sandbox_image.py`, `services/workspace/sandbox.py`); command policy (`vscode-daemon/command_policy.py`, `FABRIQUE_COMMAND_DENY`); NATS per-component users and subjects (`scripts/nats_config.py`, `nats/nats-server.conf`, `--check`); loopback default and startup warnings; deployment checklist (SECURITY.md §10.8); retention by the janitor; backups (`docs/OPERATIONS.md`) | SECURITY.md §8.3 now describes the sandbox, command policy, image identity, cache quarantine and versioned protocol (fixed in the #522 part A pull request). Still open: the backend does not send a company command policy yet; `host` remains the default and an allowed mode (`FABRIQUE_SANDBOX_REQUIRED` now reaches every `workspace.execute`, a request without `sandbox` included, #525; a new third-party recipient of a company's context needs a recorded per-company choice, SECURITY.md §7.4; check that a new daemon path that runs a command goes through `sandbox.normalize`, `backend/tests/test_sandbox_floor.py`); no egress allowlist; the images run as root and `deployment/k8s/backend.yaml` has no `securityContext` (SECURITY.md §6.3) |
| 9 | Audit rows through `AgentService` (`services/platform/agent_service.py`) into a per-company hash chain, verified by `GET /v1/audit/chain`; actions attributed to a user or `api-token:<id>`; `/health`, `/health/deep`, `/metrics` (`docs/OPERATIONS.md`); runbook (When something is wrong); incident process (SECURITY.md §9) | No security metric or alert (failed logins, refused commands, chain breaks); the plan has never been rehearsed; no log shipping or archive defined. The incident contact is now GitHub private vulnerability reporting (fixed in the #522 part A pull request), effective once the owner enables it |
| 10 | Agent-facing rules (SECURITY.md §11, `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`); these skills | No training notes for people; no onboarding security material; no record of threat-modeling practice |

## Practice 7 and the OWASP WSTG

The OWASP Web Security Testing Guide (CC BY-SA 4.0) names each test; the ids
below are cited as `WSTG-v42-…` (release 4.2) or `latest@87cf4a4` (the
repository at that commit) where 4.2 lacks the test. Ids only; read the guide
for the test itself. A change in one of these areas keeps or extends the test
named.

| Area | WSTG | Where it is tested |
|---|---|---|
| Error handling, stack traces | WSTG-v42-ERRH-01, ERRH-02 | `backend/tests/test_error_bodies.py` (every route: malformed JSON, wrong types, a 2 MiB body, overflowing ids; the 500 answers an error id, the report is redacted) |
| Admin interfaces (the API docs) | WSTG-v42-CONF-05 | `test_security_headers.py` (`API_DOCS_ENABLED=false` answers 404) |
| HSTS | WSTG-v42-CONF-07 | `test_security.py`, `test_security_headers.py` |
| CSP, other security headers, clickjacking, browser cache | latest@87cf4a4 CONF-12, CONF-14; WSTG-v42-CLNT-09, ATHN-06 | `test_security_headers.py` (per route class: authenticated JSON, public, docs, 404/401/422, unhandled 500, a stream) |
| Account enumeration | WSTG-v42-IDNT-04 | `test_auth_limits.py`, `test_auth_jwt.py` |
| Lockout, rate limits | WSTG-v42-ATHN-03 | `test_auth_limits.py`, `test_sessions.py` (refresh), `test_sso_flow.py` (SSO) |
| Weak authentication, default credentials | WSTG-v42-ATHN-02, ATHN-07 | `test_passwords.py`, `test_auth_enforcement*.py` |
| Authorization schema, IDOR, function and object level | WSTG-v42-ATHZ-02, ATHZ-04; latest@87cf4a4 APIT-02, APIT-04 | `test_authorization_matrix.py` (every route, deny by default), `test_cross_company_access.py`, `test_tenant_scoping.py`, `test_auth_enforcement*.py` |
| Session management, cookies, CSRF, logout, timeout | WSTG-v42-SESS-01 to SESS-07 | `test_sessions.py`, `test_cors_credentials.py` |
| JSON Web Tokens | latest@87cf4a4 SESS-10 | `test_jwt_claims.py` (`iss`/`aud`, `alg: none`, HS/RS confusion, ticket vs access token), `test_auth_jwt.py`, `test_sso_tokens.py` |
| SSRF | WSTG-v42-INPV-19 (latest@87cf4a4 INJT-19) | `test_ssrf_vectors.py` (one vector set against the webhook, the OIDC client and research fetches), `test_webhooks.py`, `test_sso_tokens.py` |
| Mass assignment | latest@87cf4a4 INJT-20 | `test_mass_assignment.py` |
| SQL injection | WSTG-v42-INPV-05 | by construction (SQLModel bound parameters, practice 2); no dedicated test |
| Business logic: forged requests, integrity, workflow circumvention | WSTG-v42-BUSL-02, BUSL-03, BUSL-06 | `test_chat_inbound.py`, `test_audit_chain.py`, `test_run_reviews.py`, `test_review_gate.py`; and the yearly test (`SECURITY.md` §16) |
| Transport security | WSTG-v42-CRYP-01 | not testable in the suite; the deployed stack in the yearly test |
| Dynamic scan of the running API | (all of the above, automated) | not built; plan in `SECURITY.md` §5.2 |

## Commands a reviewer can run locally

```bash
# Secrets in the working tree and history (as the weekly baseline does)
gitleaks detect --source . --config .gitleaks.toml --redact --no-banner

# Static analysis with the baseline's rule set
uv tool run --from "semgrep==1.182.1" semgrep scan --config p/security-audit --severity ERROR --metrics=off .

# Dependencies and files
trivy fs --severity CRITICAL,HIGH --ignore-unfixed .
poetry -C backend export --format requirements.txt --all-groups --output /tmp/backend.txt
pip-audit --requirement /tmp/backend.txt --require-hashes --disable-pip
pip-audit --requirement vscode-daemon/requirements.txt --require-hashes --disable-pip
cd frontend && npm audit --package-lock-only --omit=dev --audit-level=high

# Licences of the runtime dependencies (each lock in a throwaway environment first;
# docs/TESTING.md, CI)
python3 scripts/licence_scan.py --python backend=/tmp/lic-backend/bin/python \
  --npm-lock frontend/package-lock.json

# The bus configuration and the dependency rule
python3 scripts/nats_config.py --check
python3 scripts/check_architecture.py

# Workflow syntax and common mistakes
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint@sha256:887a259a5a534f3c4f36cb02dca341673c6089431057242cdc931e9f133147e9
```

Pin the tool versions to those in the workflows; a newer scanner can report
differently and the difference is then the tool, not the change.

## Patterns that are findings in this repository

- A request or producer field named like `*_sha256`, `*_hash`, `signature`,
  `chain_hash`, `exit_code` or `tests_passed` copied into an observation
  (POSITIONING rules 1, 2 and 6).
- `company_id` taken from the body of a write request instead of the caller's
  membership.
- `subprocess` with `shell=True` on a string that contains model or request
  text, outside the daemon's policy-checked `workspace.execute`.
- A new NATS subject published by the daemon, or `allow_responses` in the
  daemon's permissions (SECURITY.md §8.6 explains why).
- `==` or `!=` comparing a MAC, token or digest.
- `logger.*(...)` with a variable named like `*key*`, `*token*`, `*secret*`,
  `*password*`, or with a full request or provider payload.
- A workflow that adds `permissions: write-all`, `pull_request_target`, or
  `${{ github.event.* }}` inside `run:`.
- A new `uses:` by tag, a `curl | sh`, or a binary downloaded without a
  checksum.
