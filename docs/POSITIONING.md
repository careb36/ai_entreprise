# Positioning: what le-fabrique is for

> **Status: adopted, 2026-09-19.** This is the project's direction, confirmed by
> the repository owner. It is the written form of the argument in issue #38. The
> code findings cited here are verified and reproducible; the market reading is
> a judgment, and remains open to revision if the market moves.

## The one-line version

**The agents are interchangeable and are not the product. The product is the
layer that observes them, measures them, verifies them, and leaves an
evidentiary record.**

## What changed

le-fabrique was built on the bet that a simulated scrum team — PO, Architect,
ScrumMaster, Developer, QA, SecOps, TechWriter, each a node in a LangGraph —
would produce working software. That bet was reasonable when models were weak at
long-horizon work and decomposing into mutually-critiquing roles was a plausible
way to compensate.

The field went the other way. The scaffolding that turned out to matter is
**tools, context and verification**, not personas. The systems that work are one
strong agent with good tool access and a feedback loop against reality — tests,
compiler, CI. None of them simulate a team.

There are structural reasons, not just fashion:

- A persona adds no capability the base model lacks.
- Every handoff loses context and adds latency and cost.
- Errors compound along the chain.
- "The QA agent reviews the Developer agent" is weaker than running the tests.
  A node that emits `APPROVED` is not evidence of anything. A green pytest is.

## The scarce thing is verification

Generated code is abundant and cheap. What is scarce is a trustworthy answer to:

> Did this agent actually do what it says it did?

This repository's own history is the evidence:

- **PR #27** was merged with an entirely empty diff while its body described a
  complete Fernet implementation. It auto-closed the issue on the strength of
  that claim.
- **PR #33**'s body asserted a green build while CI was red.
- **PR #18** shipped a regression that swapped i18next's `defaultValue` for a
  `||` chain — a missing key returns the key itself, which is truthy, so
  untranslated strings rendered as raw dotted keys.
- **PR #12** revealed the orchestration health KPIs had returned zero since they
  were written, meaning nobody had ever confirmed the agent loop did anything.

In none of these cases was the agent adversarial. It was sincerely wrong, or
reporting an intention as an outcome. That is the normal case, and it is exactly
what a verification layer catches. Every one of them was caught by a human
reading the actual artifact. Nothing in this system would have noticed.

## The design rules that follow

These are the constraints that make the layer worth anything. They are easy to
violate by accident and expensive to restore once violated.

1. **A claim and an observation must never share a field.** The entire value is
   being able to say "the agent asserted X; we observed Y". Merging them
   destroys it.

2. **An agent's report of its own verification is a claim, never an
   observation.** Test results count only when they come from an independent
   source — a CI check-run conclusion, a recorded exit status.

3. **`unverifiable` is a real verdict.** It is distinct from `verified` and from
   `refuted`, and must never collapse into either. Most honest answers about
   third-party agent work are `unverifiable`, and a system that cannot say so
   will lie to stay useful.

4. **Refutation must be load-bearing.** A verifier that only ever confirms is
   theatre. Build the refutation path first.

5. **Do not use an LLM to decide whether a claim holds.** That reintroduces the
   unreliability being measured. Deterministic checks decide; a model may
   summarise a refutation for a human afterwards.

6. **Never accept a digest, hash or signature from the party being recorded.**
   Compute it server-side. This was already violated: `decision_signature` is
   documented as a non-repudiation hash and is taken verbatim from the request
   body (issue #39).

7. **Unknown is not absent and is not false.** A third-party agent that does not
   report its model is not one that ran no model. Do not default unknowns.

## What this keeps

| Asset | Why |
|---|---|
| `audit_log` + `decision_signature` | The evidentiary spine. Right shape; the signature needs to become real. |
| Neo4j Engram nodes | Immutable per-decision lineage with timestamps. Genuinely valuable and poorly served in regulated industries. |
| LLM proxy token budgets | A single chokepoint for control and metering. |
| Multi-tenant `company_id` scoping and RBAC | Expensive to retrofit later. |
| `services/orchestration_metrics.py` | Already a measurement contract; generalises from "our agents" to "any agent". |

## What this demotes

| Asset | Disposition |
|---|---|
| The 7-role persona graph (`backend/agents/graph.py`) | Behind a flag, off by default. One possible *producer*, with no privileged status. |
| `design → generate → execute → burn` as the only pipeline | Joined by ingestion of externally-produced work. |
| The internal `task_queue` HITL path | Rebuilt on external agent events. The table is currently dead — nothing inserts into it. |
| `GlassRoom` | Still reachable, no longer the default view. Watching agents narrate is the least valuable screen. |

Nothing here is deleted. Demoting rather than removing keeps the option open,
and it makes the persona approach *measurable*: once internal-graph runs and
external-agent runs land in the same table under the same verification, "does
role decomposition actually help?" stops being a matter of opinion.

## Why this is defensible

Competing on orchestration against the model vendors and IDE makers is
unwinnable at this size — they control the model and the editor.

Governance, auditability and verification of AI-generated code, for regulated
industries, is a different contest. The large vendors are structurally slower
there, it is not urgent for them, and half the infrastructure already exists
here: Engram lineage, per-company RBAC, token budgets, and a proxy that is
already the single point of control.

"Who generated this code, with which model, from what context, who approved it,
and prove it" is a question that is going to be mandatory, and almost nobody
answers it today.

## Working agreements this implies

- CI is not optional. A verification product whose own tests do not run is not
  credible. See issue #36.
- Never claim a green build. State exactly what was run. This rule exists
  because it was broken (PR #33).
- Local verification is not a substitute for CI, and neither is a passing test
  suite a substitute for reading the diff.

## Tracking

Issue #38 is the epic. Child issues cover the evidentiary foundation, removal of
dead paths, and the product surface.
