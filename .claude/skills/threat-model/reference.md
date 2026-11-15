# Trust boundaries and existing controls

The standing trust boundaries of La Fabrique, for the `threat-model` skill.
Name boundaries by these ids in a threat model; add a new id for a new one.
Controls were read from `main` on 2026-11-15; check the file before relying on
a row. The wider picture is `SECURITY.md` §2 and §8 and `docs/threat_model.md`.

```
 Browser ──B1── Backend (FastAPI) ──B2── NATS ──B2── Workspace daemon ──B3── User's disk
                  │   │   │                                  │
                  │   │   └──B6── GitHub / webhooks          └──B3── Sandbox (container | bwrap | host)
                  │   └──B4── LLM proxy ──B4── Model providers
                  └── Postgres / Neo4j (inside the backend's trust zone)
 Agent and producer output enters at B5, wherever it is read.
 CI on the self-hosted runner is B7; a company's identity provider is B8;
 Slack and Microsoft Teams (approvals in chat) are B9.
```

## B1 — Browser ↔ backend

- **Crosses:** REST and SSE from the React frontend; API tokens from external
  callers.
- **Threats to look for:** missing auth on a new route, cross-company reads,
  role escalation, XSS from rendered model output, token theft, CSRF.
- **Controls:** JWT (HS256, `JWT_SECRET_KEY`) resolved by
  `backend/app/auth_deps.py`; `AUTH_REQUIRED` on by default; roles Owner >
  Manager > Viewer and company scoping (`docs/contexts/platform.md`, Auth and
  multi-tenancy); API tokens hashed with role and expiry
  (`services/platform/api_tokens.py`); Glassroom SSE by five-minute ticket;
  security headers (`backend/tests/test_security_headers.py`); bcrypt
  (`backend/app/passwords.py`); `safeReturnPath()` for redirects
  (`frontend/src/lib/returnPath.ts`).

## B2 — Backend ↔ NATS ↔ daemon

- **Crosses:** requests the backend sends (`workspace.generate`,
  `workspace.execute`, `workspace.hash`, …), the daemon's replies on
  `_INBOX.backend.>`, deliveries on JetStream.
- **Threats:** anyone on the bus commanding the daemon; the daemon answering
  itself; replayed or forged replies; secrets on the wire.
- **Controls:** one NATS user per component with fixed subjects, no
  `allow_responses` for the daemon (`scripts/nats_config.py`,
  `nats/nats-server.conf`, `--check`, `backend/tests/test_nats_security.py`);
  passwords or `.creds`/NKey from the environment; TLS that refuses to fall
  back (SECURITY.md §8.6; `docs/OPERATIONS.md`, Securing the bus); loopback
  bind by default with a startup warning otherwise.

## B3 — Daemon ↔ user's disk and sandbox

- **Crosses:** files the daemon writes and reads, commands it runs, agent code
  executed for tests.
- **Threats:** path traversal or symlinks out of the project; reading secret
  files; arbitrary commands; agent code reaching the network, the user's home
  or the host; resource exhaustion.
- **Controls:** sandbox modes `container` / `bwrap` / `host`, the applied mode
  reported and recorded by the Verifier, `FABRIQUE_SANDBOX_REQUIRED`
  (`vscode-daemon/sandbox.py`, `sandbox_image.py`,
  `services/workspace/sandbox.py`; `docs/threat_model.md`, sandbox table);
  command policy with a machine-side deny list the request cannot relax
  (`vscode-daemon/command_policy.py`); read tools that refuse secret files and
  symlinks out of the project and redact secret-looking lines
  (`docs/contexts/tools.md`); deliveries in a Git worktree, never the person's
  checkout (`vscode-daemon/git_workspace.py`); sha256 read back from disk.
- **Known gap:** `host` mode runs with the user's privileges and is the
  default (`FABRIQUE_SANDBOX_REQUIRED` on the daemon's machine raises every
  `workspace.execute` to its mode, a request without `sandbox` and the
  console assistants' consultations included, #525); the backend sends no
  per-company command policy (SECURITY.md §8.3, "What remains open").

## B4 — Backend ↔ LLM proxy ↔ providers

- **Crosses:** prompts (which can include repository content), company
  provider keys, model responses.
- **Threats:** a company's key used by another; keys in logs; prompt content
  leaking secrets to a provider; cost exhaustion.
- **Controls:** company connections only with `LLM_PROXY_INTERNAL_TOKEN`
  compared in constant time, no fallback to deployment keys
  (`llm-proxy/connections.py`); provider keys encrypted with
  `FERNET_SECRET_KEY`; budgets per agent; the proxy is the only place that sees
  deployment keys (`.github/CODEOWNERS` header).

## B5 — Agent and producer output as untrusted input

- **Crosses:** everything a model or a third-party producer returns: code,
  file lists, test reports, digests, tool calls, research requests.
- **Threats:** prompt injection steering tools; an agent's claim stored as an
  observation; a producer-supplied hash or exit status trusted; model output
  executed or rendered.
- **Controls:** claims and observations in separate fields, the Verifier
  judging from observations, `unverifiable` as a verdict, no model deciding a
  claim (`docs/POSITIONING.md`, rules 1–7; `docs/contexts/evidence.md`);
  research fetches only from an allowlist, without credentials
  (`services/tools/research_*`); tool loop bounded in rounds; SECURITY.md §8.

## B6 — GitHub and webhooks

- **Crosses:** incoming GitHub webhooks; outgoing notification webhooks;
  publishing branches and pull requests with the person's credentials.
- **Threats:** forged webhooks; replay; SSRF through a webhook URL; leaking
  the signing secret; publishing to the wrong repository.
- **Controls:** incoming webhook HMAC checked with `hmac.compare_digest`
  (`services/producers/github_adapter.py`, `backend/tests/test_webhooks.py`);
  outgoing deliveries signed `X-Fabrique-Signature-256` with retries and an
  outbox (`docs/contexts/integrations.md`); publish requires Manager
  (`docs/contexts/release.md`).

## B7 — CI and the self-hosted runner

- **Crosses:** workflow code from pull requests, the repository token, actions
  and downloaded tools.
- **Threats:** a malicious PR or action running on a non-ephemeral runner that
  also hosts the daemon; token over-scope; secrets in logs; unpinned actions.
- **Controls:** `permissions:` declared per workflow; CODEOWNERS on
  `.github/`; actionlint; Trivy pinned by sha; tools downloaded with checksum
  (`security-baseline.yml`); SLSA provenance on release.
- **Known gaps:** runner not ephemeral (actions are pinned by sha since #523); required
  Code Owner review pending (#182).

## B8 — The backend and a company's identity provider

- **Crosses:** the issuer URL an Owner configures, the discovery document and
  signing keys fetched from it, the ID token and the browser's redirects.
- **Threats:** forged or replayed ID tokens; login CSRF; one company's
  provider (any Owner can run one) asserting another company's people;
  account pre-hijacking; SSRF through the issuer URL; a company locking
  itself out.
- **Controls:** the backend validates the ID token itself (asymmetric
  algorithms only, `iss`, `aud`, `exp`, nonce; `services/platform/oidc.py`);
  PKCE with a one-time `state` bound to the browser; identity by
  `(issuer, sub)`, the role from La Fabrique's membership; linking by email
  only for an account in this company alone, one company per domain
  (`services/platform/sso_flow.py`); https, public addresses re-checked per
  fetch, no redirects, a size cap (`services/platform/oidc_client.py`);
  "require SSO" only after an Owner signed in through it, with break-glass;
  audit rows; `docs/design/SSO.md`.
- **Known gaps:** domains not verified by DNS; issued sessions not revoked.

## B9 — The backend and Slack or Microsoft Teams

- **Crosses:** approval messages the outbox posts with a company's bot token
  (Slack, the fixed `https://slack.com/api/`) or incoming webhook URL (Teams);
  interactions and commands those platforms post back to the public
  `/v1/integrations/{slack|teams}/{public_id}/…` endpoints.
- **Threats:** a forged or replayed interaction accepting a held delivery; a
  chat user acting as another person; a role or user id read from the
  payload; a click on an old message deciding a task that changed; one
  company's workspace deciding for another; SSRF through the Teams URL; the
  bot token, signing secret or webhook URL leaking through the API or logs.
- **Controls:** signature over the raw body before parsing, Slack v0 with the
  timestamp within 300 s, the Teams outgoing webhook's HMAC with the signed
  activity's timestamp within 300 s, `hmac.compare_digest`
  (`services/integrations/chat_signatures.py`); interaction ids stored once
  (`chat_interactions`, unique); the connection's workspace or tenant
  required; identity only from a link made by a one-time code typed in chat
  (`chat_links.py`), the role from `CompanyMember` through
  `require_company_role`, the decision through `run_review.submit_review`
  with `via`; the hold event's hash and the run's version re-checked and the
  message claimed by a conditional update (`chat_approvals.claim`);
  credentials Fernet-encrypted and never returned, Owner-only settings, API
  tokens refused (`routers/chat_approvals.py`); Teams URL on a Microsoft host
  with public addresses re-checked per post, Slack calls to one host
  (`chat_client.py`); `docs/design/CHAT_APPROVALS.md`,
  `backend/tests/test_chat_inbound.py`.
- **Known gaps:** a stolen link code claimed within ten minutes binds the
  thief's chat account (the victim sees the link); Teams refusals are thread
  replies, not ephemeral.

## Data stores (inside the backend's zone)

Postgres (with pgvector) and Neo4j hold company data and the audit chain.
Integrity of the evidence: HMAC-SHA256 per row and a per-company hash chain
(`backend/app/audit_signing.py`, `backend/app/audit_chain.py`,
`AUDIT_SIGNING_KEY`, `GET /v1/audit/chain`). Retention by the janitor
(`backend/janitor/`); backups in `docs/OPERATIONS.md`.
