---
name: threat-model
description: Produce a short STRIDE-based threat model for a new La Fabrique feature or design (Microsoft SDL practice 3, security design review and threat modeling), fitted to this system's trust boundaries (browser to backend, backend to NATS to the workspace daemon, daemon to the user's disk and sandbox, backend to LLM proxy to providers, agent output as untrusted input, GitHub and webhooks). Use it before building a feature that adds an endpoint, a NATS subject, a role, a producer, a data store, a tool agents can call, a new kind of agent-produced input, or anything that crosses one of those boundaries; also when sdl-review finds a design change without a threat model. Saves the result under docs/design/ or in the PR or issue.
---

# Threat model

A lightweight, written threat model for one feature, before the code. It
follows Microsoft SDL practice 3 (model from the attacker's side, mark trust
boundaries, record threats and mitigations, turn mitigations into work items
and tests) using STRIDE as the prompt list. The system's standing boundaries
and controls are catalogued in [`reference.md`](reference.md); read it first.

Keep it short: one to three pages. A threat model nobody reads protects
nothing. It is a design document, and like every model output in this project
it is a claim: a person reviews it before it guides work.

## Inputs

1. The feature: the issue, spec or plan. If it is vague, write down the
   assumptions you make and list them as open questions.
2. The context documents for every context the feature touches
   (`docs/contexts/*.md`, mapped through `docs/architecture/contexts.json`).
3. `SECURITY.md` §2 and §8, `docs/threat_model.md` (the sandbox and NATS
   sections), `docs/POSITIONING.md` (design rules 1, 2, 5, 6, 7).
4. [`reference.md`](reference.md): boundaries B1–B7 and the existing controls.

## Procedure

### 1. Describe what is built

Two or three sentences: what the feature does, who triggers it (person, agent,
schedule, webhook), and what it changes (files on the user's disk, rows, a
pull request, a message on the bus, a provider call).

### 2. List the assets

What an attacker would want or break. Typical ones here:

- The user's machine and files (the daemon runs there).
- Secrets: provider keys, `JWT_SECRET_KEY`, `FERNET_SECRET_KEY`,
  `AUDIT_SIGNING_KEY`, NATS passwords, run secrets, Git credentials.
- The evidence: audit rows and their hash chain, run observations, the
  Verifier's verdicts. Their integrity is the product (`docs/POSITIONING.md`).
- Company data and isolation between companies.
- Availability and cost: model spend, the single worker, the self-hosted
  runner.

### 3. Draw the flow and mark the boundaries

A Mermaid or ASCII data flow diagram: each component, each flow labelled with
its data and protocol (REST/SSE, NATS subject, HTTPS, git, docker), and each
trust boundary crossed, named by its id from `reference.md` (B1–B7). A new
boundary gets a new id and a sentence on why it is one.

### 4. Find threats per boundary with STRIDE

For each flow that crosses a boundary, ask each question. Write a threat only
when it is plausible for this design; "not applicable" is a fine answer.

| Letter | Question to ask here |
|---|---|
| S — Spoofing | Can someone act as another user, company, component or producer? (forged JWT, a stolen NATS credential, a webhook without its HMAC, a producer naming another agent) |
| T — Tampering | Can data change between where it is made and where it is trusted? (a producer's report altering an observation, a file changed after the hash was read, an audit row edited) |
| R — Repudiation | Could an actor deny the action because nothing recorded it, or the record is not tied to them? (missing audit row, row without `api-token:<id>`, hash supplied by the actor) |
| I — Information disclosure | Can a secret, another company's data or the user's files leak? (logs, `/health`, error messages, prompts sent to a provider, sandbox env, a fetch to an attacker's host) |
| D — Denial of service | Can it exhaust the worker, the bus, the disk, the runner or the model budget? (unbounded input, a loop of retries, a fork bomb in the sandbox) |
| E — Elevation of privilege | Can a Viewer act as a Manager, an agent as the backend, model output as a shell command, a sandboxed run as the host? |

Then ask the questions STRIDE misses, which practice 3 warns about: what
happens if the control in front of this one fails (assume breach)? Which
assumption, if false, breaks the design? Does any step let a model decide
something a deterministic check should (POSITIONING rule 5)?

For each threat give likelihood and impact as High, Medium or Low and a
severity from SECURITY.md §9.2. Be honest: Low/Low threats are still worth
one line so the reader knows they were considered.

### 5. Map mitigations

For each threat: the existing control that covers it, with a file reference
(from `reference.md`), or the new mitigation the feature must add. Prefer
layered mitigations: one that holds when another fails. Every new mitigation
becomes a task and a test that proves it (a negative test: the attack is
refused).

### 6. Record residual risk and open questions

What stays after mitigation, who accepts it (the owner, per `sdl-program`),
and the questions a person must answer before the design is final.

### 7. Save and link

- A design-sized model: `docs/design/<FEATURE>_THREAT_MODEL.md`, linked from
  the issue and the PR. Check `docs/design/` for the naming used there.
- A small change: a "Threat model" section in the PR description or issue.
- If the feature changes the architecture the generated model describes,
  update `scripts/threat_model.py` and regenerate `docs/threat_model.md`
  (instructions at the top of the script), or say in the PR why not.
- If it changes a standing boundary or control, update `SECURITY.md` §2 or §8.

## Output template

```markdown
# Threat model: <feature>

Status: draft | reviewed by <role> on <date> · Issue: #<n> · SDL practice 3

## What is built
## Assets
## Data flow and trust boundaries
<diagram; boundaries by id from the threat-model skill's reference.md>

## Threats
| ID | Boundary | STRIDE | Threat | Likelihood | Impact | Severity | Mitigation (existing → file, or new → task) |
|---|---|---|---|---|---|---|---|

## Tests that prove the mitigations
## Residual risks
## Open questions
```

## Worked mini-example: a new daemon subject `workspace.archive`

*Feature (hypothetical).* The backend asks the daemon to zip a task's
worktree and return the archive so a person can download it.

*Assets.* The user's files outside the worktree; secrets in the worktree
(`.env`); the archive's integrity as evidence; the daemon machine's disk.

*Flow.* Backend → NATS (`workspace.archive`, request, B2) → daemon → reads the
worktree on the user's disk (B3) → reply on `_INBOX.backend.>` → backend
stores the archive and its sha256 → browser downloads it (B1).

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| T1 | B2 | S | A holder of the daemon's credentials publishes `workspace.archive` to make the daemon archive something | L | H | High | Existing: the daemon user cannot publish requests (`scripts/nats_config.py`); new subject added only to the backend's publish list, `--check` regenerates the conf; test in `test_nats_security.py` |
| T2 | B3 | I | The requested path escapes the worktree (`../`, a symlink) and zips `~/.ssh` | M | H | Critical | New: resolve the path, refuse anything outside the task's worktree and do not follow symlinks out, as the read tools already do (`docs/contexts/tools.md`); negative tests for `..` and a symlink |
| T3 | B3 | I | The archive includes `.env` or key files | M | H | High | New: apply the read tools' secret-file exclusions; list excluded files in the reply as an observation |
| T4 | B2 | T | The backend stores a sha256 the daemon reported instead of computing one | M | M | High | New: backend hashes the bytes it received (POSITIONING rule 6); the daemon's value, if kept, is a claim field |
| T5 | B3 | D | A huge worktree fills the disk or blocks the daemon | M | M | Medium | New: size bound and timeout, refuse above it with the reason |
| T6 | B1 | E | A Viewer of another company downloads the archive | L | H | High | Existing: company-scoped routes with role checks (`auth_deps`); new route requires Manager, 404 for another company; test both |
| T7 | — | R | No record of who exported the code | M | L | Low | Existing: audit row through `AgentService`, attributed to the user or `api-token:<id>` |

*Residual risk.* An archive taken from a host-mode worktree contains whatever
the agent wrote there, including code a person has not reviewed; the download
page says so. *Open question.* Should Viewers ever download code?

Source: Microsoft, SDL practice 3, *Perform security design review and threat
modeling*, https://www.microsoft.com/securityengineering/sdl/practices/secure-by-design
(overview: https://www.microsoft.com/en-us/securityengineering/sdl/practices).
The procedure and wording are this project's own.
