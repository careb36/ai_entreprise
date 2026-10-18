# La Fabrique — Agent Knowledge Base

| Field | Value |
|---|---|
| **Purpose** | Single entry point for every agent role: what to read, what to run, what "done" means |
| **Owner** | Product Owner |
| **Sprint** | Sprint 0 — Foundations |
| **Status** | Active |
| **Review cadence** | Every sprint close, and on any change to the command surface or `docs/` tree |
| **Enforced by** | `make docs-check` (CI gate G2) — fails the build on any broken repo-relative link |

---

## 0. TL;DR — 10-minute onboarding

1. Find your role in §1. Read **only** the ● documents in your row. Do not read the repo.
2. Open your role card in §4 and follow the **Start here** list in order.
3. Run `make bootstrap && make verify` to confirm your environment matches the pinned toolchain.
4. Read §5 (sprint workflow) — you cannot act outside a gate.
5. Register yourself in Engram: `fbrq agent register --role <role>` — this makes you addressable for handoffs.

> **Context discipline is a rule, not a preference.** Reading beyond your role's required set wastes tokens, drifts your judgement, and is the single most common cause of scope creep at La Fabrique. If your card is missing something you genuinely need, open a PR against this file — do not improvise from the source tree.

---

## 1. Role → required context matrix

● = required before your first action · ○ = required before work in that area · blank = not required

| Doc | Code | PO | Architect | Dev | QA | DevOps | Security | Data |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| [Architecture overview](../architecture/README.md) | ARCH | ○ | ● | ● | ● | ● | ● | ● |
| [LangGraph orchestration](../architecture/langgraph-orchestration.md) | LG | | ● | ○ | | | ● | |
| [GlassRoom observability](../architecture/glassroom-observability.md) | GR | ○ | ● | ○ | ● | ● | | |
| [Engram memory](../architecture/engram-memory.md) | EN | | ● | ○ | | | ○ | ● |
| [Semantic search](../architecture/semantic-search.md) | VS | | ● | ○ | | | | ● |
| [Data model](../data/data-model.md) | DM | ○ | ● | ○ | ○ | ○ | | ● |
| [Migrations & backfills](../data/migrations.md) | MG | | ○ | ○ | | ○ | | ● |
| [Coding standards](../standards/coding-standards.md) | CS | | ○ | ● | ○ | ○ | | ○ |
| [Testing strategy](../standards/testing-strategy.md) | TS | | ○ | ● | ● | ○ | | ● |
| [API contracts](../standards/api-contracts.md) | API | | ● | ● | ● | ○ | ● | ○ |
| [Documentation standards](../standards/documentation-standards.md) | DOC | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| [Security standards](../security/security-standards.md) | SEC | ○ | ○ | ● | ○ | ● | ● | ● |
| [Threat model](../security/threat-model.md) | TM | | ● | ○ | ○ | ○ | ● | |
| [CI/CD pipeline](../ci-cd/pipeline.md) | CI | | ○ | ● | ○ | ● | ● | ○ |
| [Sprint workflow & gates](../process/sprint-workflow.md) | SW | ● | ● | ● | ● | ● | ● | ● |
| [Definition of Done](../process/definition-of-done.md) | DOD | ● | ● | ● | ● | ● | ● | ● |
| [Decision records](../process/decision-records.md) | DR | ● | ● | ○ | ○ | ○ | ● | ○ |

**Universal (all roles, always):** SW, DOD. Everything else is role-scoped. This matrix is the contract — it is what `make docs-check` reads alongside link integrity.

---

## 2. Operating rules for every agent

These apply regardless of role. Violating one is a gate failure, not a style note.

1. **Work inside a bounded outcome.** No task exists outside a sprint item with written acceptance criteria. If you cannot point at the item, stop and escalate to the PO.
2. **Evidence over assertion.** "Done" means a link: a PR, a test run id, a GlassRoom `run_id`, a migration id. Prose claims are not evidence.
3. **Write back to Engram.** Every decision, finding, risk, and test result you produce is persisted as a node (§7). Knowledge that isn't in Engram does not exist for the next agent.
4. **Respect tool boundaries.** Tool permissions are declared per agent in the orchestration graph (LG). Do not request broader scope to unblock yourself — raise it as a decision record.
5. **Never widen scope silently.** If acceptance criteria are ambiguous or contradictory, escalate (§9). Do not interpret favourably.
6. **Security findings are never self-waived.** Only a human stakeholder can accept a residual risk.
7. **Update this index in the same PR** that adds, moves, or removes a doc. A doc not listed here is undiscoverable and will be treated as non-existent.

---

## 3. Canonical document map