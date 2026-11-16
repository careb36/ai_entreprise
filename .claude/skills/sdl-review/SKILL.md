---
name: sdl-review
description: Review a change (a diff, a pull request or a plan) in La Fabrique against Microsoft's Security Development Lifecycle (SDL) practices 2 to 9, and report findings with severity, file:line, the practice number and a concrete fix. Use it before opening a pull request that touches authentication or roles, secrets or keys, cryptography or hashing, the audit chain, the workspace daemon or its sandbox and command policy, NATS subjects or credentials, dependencies or lockfiles, Dockerfiles or CI workflows, the LLM proxy, webhooks, or anything that executes or stores agent-produced code or output. It does not replace a human security review.
---

# SDL review

A checklist review of one change against Microsoft's SDL practices, written
for this repository. The practices, their sources and what La Fabrique already
has for each are in [`reference.md`](reference.md); read its mapping table once
per session before the first review.

**What this is not.** It is not a human security review, a penetration test or
a scan. Every finding is a claim made by a model reading code: verify it
(reproduce, read the surrounding code, run the test) before acting on it, and
say in the pull request which findings were verified and how. That is the
project's own rule (`docs/POSITIONING.md`, rules 1 and 2): a report is a claim
until something independent observes it.

## 1. Gather the change

1. Find the scope:
   - A branch: `git fetch origin main && git diff --stat origin/main...HEAD`,
     then `git diff origin/main...HEAD`.
   - A pull request: `gh pr diff <n>` and `gh pr view <n>`.
   - A plan or design (no code yet): read it, and review the design against
     practice 3 with the `threat-model` skill first.
2. List the files changed and map each to its context with
   `docs/architecture/contexts.json` (`services/<context>/`). Read the
   context's document in `docs/contexts/` before judging the code.
3. Read every changed file in full around each hunk, not only the hunk: a
   security property usually lives in the caller or the check before it.

## 2. Classify the stages it touches

The SDL frames work as four stages under Zero Trust (assume breach, verify
explicitly, least privilege). Mark each that applies; it decides which
checklists below to run.

| Stage | Touched when the diff changes | Run practices |
|---|---|---|
| Design | a new endpoint, subject, role, trust boundary, data store, producer, or a flow between components | 2, 3 |
| Code | `backend/app/`, `vscode-daemon/`, `llm-proxy/`, `frontend/src/` | 2, 4, 7 |
| Build and deploy | `.github/`, `Dockerfile*`, `docker-compose.yml`, `k8s/`, `deployment/`, lockfiles, `pyproject.toml`, `package.json`, `.pre-commit-config.yaml`, `scripts/` | 5, 6 |
| Run | `nats/`, `scripts/nats_config.py`, the sandbox, `/health`, `/metrics`, logging, the janitor, backups, `docs/OPERATIONS.md` | 8, 9 |

A change that adds a trust boundary or a new kind of input from an agent,
browser, webhook or provider is a Design change even when it is small.

## 3. Checklists

Mark each item pass, fail (a finding) or not applicable. Do not mark pass
without having read the code that makes it so.

### Practice 2 — proven security features, languages and frameworks

- [ ] Authentication goes through `auth_deps.get_current_user` and the company
      role checks (Owner > Manager > Viewer, `docs/contexts/platform.md`, Auth
      and multi-tenancy). No route re-implements token parsing.
- [ ] A new route is protected unless it is on the documented public list
      (`/health`, login/registration, the HMAC-signed GitHub webhook, support,
      `/v1/models`, `/v1/capabilities`). A new public route is a finding unless
      the PR justifies it and updates that list.
- [ ] Company ownership comes from the caller's membership, never from the
      request body or payload metadata (multi-tenancy is by `company_id`).
- [ ] An action that changes what agents do (prompt, model, budget, folder,
      sandbox, research hosts, MCP servers) requires Manager.
- [ ] Passwords use `backend/app/passwords.py` (bcrypt); API tokens use
      `services/platform/api_tokens.py` (only the sha256 stored, shown once,
      role never above the creator's).
- [ ] Database access is through SQLModel/SQLAlchemy with bound parameters;
      Cypher is parameterised; no string-built SQL or Cypher.
- [ ] Model output is never passed to `eval`, `exec`, `subprocess(..., shell=True)`,
      raw SQL or rendered as unsanitised HTML (SECURITY.md §8.1, §8.5).
- [ ] Secrets come from the environment or the encrypted stores
      (`FERNET_SECRET_KEY`, run secrets, project credentials), never from code,
      a committed file, a Vite `VITE_*` variable or a default value.
- [ ] Frontend strings go through `useTranslation` (not a security rule, but a
      reviewer will flag it).

### Practice 3 — design review and threat modeling

- [ ] If the stage table marked Design, a threat model exists for the change
      (the `threat-model` skill), linked from the PR or saved in `docs/design/`.
- [ ] The change keeps claims and observations in separate fields
      (`docs/POSITIONING.md`, rule 1), and an agent's own report of a test run
      is stored as a claim (rule 2).
- [ ] A new input from an agent, a producer, a provider or a webhook is treated
      as untrusted: validated with a schema, bounded in size, never trusted for
      identity or authorisation.
- [ ] Failure is closed: an unreachable dependency, a missing key or an
      unreadable policy refuses rather than continuing in a weaker mode, or the
      weaker mode is reported as a finding (as the sandbox does for `host`).

### Practice 4 — cryptography standards

- [ ] No home-made cryptography: no custom cipher, MAC, KDF or random token.
      Tokens use `secrets`; encryption uses Fernet (`cryptography`); MACs use
      `hmac` with SHA-256.
- [ ] Content identity is sha256 (files, outputs, tokens at rest). MD5 or SHA-1
      only where a non-security identifier is clearly meant, and commented.
- [ ] MACs and digests are compared with `hmac.compare_digest`, as in
      `backend/app/audit_signing.py`, `audit_chain.py`,
      `services/producers/github_adapter.py` and `llm-proxy/connections.py`.
- [ ] **A digest, hash or signature is never accepted from the party being
      recorded** (`docs/POSITIONING.md`, rule 6). The backend computes it, or
      the daemon reads it back from disk (`workspace.generate`,
      `workspace.hash`). A field such as `sha256`, `signature` or `chain_hash`
      read from a request body or a producer's report is a high finding.
- [ ] Keys follow `docs/CONFIGURATION.md`: `JWT_SECRET_KEY`,
      `FERNET_SECRET_KEY`, `AUDIT_SIGNING_KEY`, `LLM_PROXY_INTERNAL_TOKEN` and
      the NATS passwords come from the environment, are generated with the
      commands given there, never logged, never returned by an endpoint, and
      `/health` reports only booleans about them.
- [ ] A change to what a key signs or encrypts says what happens to existing
      rows on rotation and updates `docs/OPERATIONS.md`, Rotate keys.
- [ ] Traffic that leaves the machine is TLS (NATS with `NATS_TLS`, providers
      over HTTPS); a downgrade path is a finding.

### Practice 5 — the software supply chain

- [ ] A new dependency is pinned in the right manifest and its lockfile is
      committed in the same PR: `backend/poetry.lock`, `llm-proxy/poetry.lock`,
      `vscode-daemon/requirements.txt`, `frontend/package-lock.json`.
- [ ] Its licence is compatible with Apache-2.0 (`LICENSE`); a copyleft or
      unknown licence is a finding for a human to decide.
- [ ] Adapted code names its source in the PR and in `NOTICE` with its
      copyright and licence (as for AixleHQ/flow).
- [ ] The dependency is known to its registry and maintained; check its
      advisories (OSV) and age. A typo-squat-shaped name is a high finding.
- [ ] A new GitHub Action is pinned by full commit sha with the version in a
      comment (`aquasecurity/trivy-action@<sha> # v0.36.0`). A tag (`@v7`) is a
      finding on new lines; do not demand a repo-wide fix in an unrelated PR.
- [ ] A downloaded binary in a workflow is pinned and checked with
      `sha256sum -c` (as `security-baseline.yml` does for gitleaks and uv).
- [ ] Base images are pinned (no `latest`); a new image or ecosystem is added
      to `.github/dependabot.yml`, and an `ignore` there carries its reason.

### Practice 6 — the engineering environment

- [ ] A workflow declares `permissions:` at the narrowest level (`contents:
      read` by default); a write scope is justified next to it.
- [ ] No `pull_request_target` with a checkout of the PR head; no expression
      from an issue, PR title, branch name or comment interpolated into `run:`
      (pass it through `env:` instead).
- [ ] Secrets are never echoed, never written to an artifact, never passed on a
      command line that a log can show.
- [ ] Jobs run on the self-hosted runner `[self-hosted, linux, x64, lafabrique]`,
      which is not ephemeral and shares the machine with the daemon
      (`.github/CODEOWNERS` header, `docs/TESTING.md`): a job cleans up what it
      starts, writes under `$RUNNER_TEMP`, and does not leave credentials,
      containers or background processes behind.
- [ ] A change under `.github/`, `deployment/`, `llm-proxy/` or `vscode-daemon/`
      is called out for the code owner's review (`.github/CODEOWNERS`).
- [ ] No `.env` or credential file is added (the pre-commit `no-secrets` hook
      and `.gitleaks.toml`); test fixtures use obviously fake values that do not
      look like real keys.

### Practice 7 — security testing

- [ ] A security-relevant change ships a test that fails without it, in the
      suite for its area: `test_security.py`, `test_security_headers.py`,
      `test_nats_security_*.py`, `test_audit_chain.py`, `test_audit_signature.py`,
      `test_webhooks.py`, `test_run_secrets.py`, or the context's own tests.
- [ ] There are negative tests: the wrong role gets 403, another company's
      resource is 404 or 403, a forged or replayed signature is refused, an
      oversized or malformed input is rejected, a command the policy denies does
      not run.
- [ ] A change to `nats/` or the subjects regenerates the config
      (`python3 scripts/nats_config.py`, then `--check` passes).
- [ ] The PR's CI includes the Security Scan workflow (Trivy) green; note that
      Semgrep and gitleaks run weekly in `security-baseline.yml`, so run them
      locally for a sensitive change (commands in `reference.md`).
- [ ] The architecture check and the backend suite pass
      (`python3 scripts/check_architecture.py`; `docs/TESTING.md`).

### Practice 8 — operational platform security

- [ ] Agent-written code still runs in the company's sandbox mode, the daemon
      still reports the mode it applied, and `FABRIQUE_SANDBOX_REQUIRED` still
      refuses the host (`vscode-daemon/sandbox.py`,
      `services/workspace/sandbox.py`).
- [ ] `workspace.execute` still goes through `vscode-daemon/command_policy.py`;
      a request can never relax the machine's `FABRIQUE_COMMAND_DENY`.
- [ ] NATS: a new subject is added to `scripts/nats_config.py` for the one
      component that needs it, the daemon still cannot send requests or answer
      itself, and `nats/nats-server.conf` is regenerated (SECURITY.md §8.6).
- [ ] A new default is the safe one (loopback bind, auth on, sandbox not
      weaker); a weaker opt-in warns at startup.
- [ ] SECURITY.md §10.8 (Deployment checklist) and `docs/CONFIGURATION.md` are
      updated for any new variable that affects exposure.

### Practice 9 — monitoring and response

- [ ] A security-relevant action (role change, token create or revoke, sandbox
      or policy change, cache reset, publish, undo) writes an audit row through
      `AgentService`, attributed to the user or `api-token:<id>`, and so enters
      the company's hash chain (`backend/app/audit_chain.py`).
- [ ] The change never deletes or rewrites audit rows outside the janitor's
      documented retention; `GET /v1/audit/chain` still verifies.
- [ ] Logs carry no secret, token, password, key, full prompt with credentials
      or full provider response; redaction (as in the daemon's read and lint
      tools) is kept.
- [ ] Errors returned to clients carry no stack trace or internal path.
- [ ] A new failure mode is visible: `/health/deep` or `/metrics` reflects it,
      and `docs/OPERATIONS.md` (When something is wrong) says what to do.

## 4. Report

Write the findings first, most severe first. Severity follows SECURITY.md §9.2
(Critical, High, Medium, Low). Use this shape for each:

```markdown
### [High] Producer-supplied digest stored as an observation
- **Where:** backend/app/services/producers/example.py:142
- **Practice:** 4 (cryptography standards); POSITIONING rule 6
- **What:** `run.output_sha256` is copied from the producer's report.
- **Why it matters:** the party being recorded vouches for its own output.
- **Fix:** compute sha256 over the bytes the daemon read back; keep the
  producer's value in the claim field.
- **Verified:** no / yes (how)
```

Then the coverage table, one row per practice, so the reader sees what was and
was not looked at:

```markdown
| Practice | Applies | Result | Notes |
|---|---|---|---|
| 2 Proven features | yes | 1 finding | |
| 3 Threat modeling | yes | pass | docs/design/<file>.md |
| 4 Cryptography | yes | 1 finding | |
| 5 Supply chain | no | n/a | no manifest or workflow changed |
| 6 Engineering env | ... | | |
| 7 Testing | ... | | |
| 8 Operations | ... | | |
| 9 Monitoring | ... | | |
```

Close with: what was not reviewed (files skipped, generated code, practices
1 and 10, which concern the programme rather than a change; see the
`sdl-program` skill), and the reminder that the findings are claims for a
person to verify and that this review does not replace a human security
review.

Source: Microsoft, Security Development Lifecycle practices,
https://www.microsoft.com/en-us/securityengineering/sdl/practices (practice
pages cited in `reference.md`). The checklists are this project's own wording.
