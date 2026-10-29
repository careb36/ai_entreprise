# Contributing to La Fabrique

La Fabrique is an open-source (Apache 2.0) multi-agent orchestration platform: a React web
app, a FastAPI control plane, and LangGraph-based sprint orchestration with
first-class observability.

This document is the single source of truth for **proposing, reviewing, and
merging changes** — for humans and for AI agents. If you are an agent, read
§10 before you open a PR.

- Normative: `CONTRIBUTING.md`, `SECURITY.md`, `CODEOWNERS`, accepted ADRs.
- Precedence: `SECURITY.md` > accepted ADR > `CONTRIBUTING.md` > tooling default.
- Questions: open a `question` issue or ask in `#fabrique-eng`.

---

## 1. Who does what

| Role | Responsibility | Merge authority |
|------|----------------|-----------------|
| Product Owner | Sprint scope, acceptance criteria, prioritization | No |
| Architect | System design, contracts, ADRs, cross-cutting review | Yes (design areas) |
| Scrum Master | Sprint flow, status hygiene, unblocking, escalation | No |
| Security | Threat model, secrets, dependency & data-handling review | Yes (security-sensitive) |
| Developers | Implementation, tests, observability | Yes (own area, after review) |
| QA | Verification against acceptance criteria | No |
| AI agents | Draft specs, implement, review, verify — per §10 | **Never** (see §10.2) |
| Human Owner | Final authority on `main` | Yes |

---

## 2. Non-negotiables

1. **No secrets in the repo.** Ever. Use the secret manager; `gitleaks` runs in CI.
2. **Third-party IP.** Do not paste code, prompts, or designs from La Fabrique into
   third-party tools without Security sign-off. No new dependency without §7.4.
3. **`main` is always releasable.** Nothing merges to `main` with a red pipeline.
4. **Every change traces to an issue.** PRs without a linked issue are closed.
5. **Agents do not merge to `main`.** A human merges every PR (§10.2).
6. **Tests and observability ship with the change**, not after it (§12).

---

## 3. Local setup