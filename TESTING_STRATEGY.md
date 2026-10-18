# TESTING_STRATEGY.md

| Field | Value |
|---|---|
| **Owner** | QA Lead (La Fabrique) |
| **Contributors** | Backend, Agents, Frontend, Data, DevOps, Security |
| **Status** | Approved for Sprint 0 — Foundations |
| **Version** | 2.0 |
| **Last updated** | Sprint 0 |
| **Supersedes** | v1.x (single-layer pytest description) |
| **Review cadence** | End of every sprint; ad-hoc on stack change |
| **Related docs** | `ARCHITECTURE.md`, `EVAL_STRATEGY.md` (LLM quality), `SECURITY_TESTING.md`, `RUNBOOK.md` |

---

## 0. Changelog

| Version | Sprint | Summary |
|---|---|---|
| 2.0 | 0 | Full rewrite: 6-layer pyramid, contract testing gates, fixture catalog, local + CI execution contracts, coverage gates per package, flake policy. |
| 1.x | pre-0 | Informal pytest-only guidance. Retained historically; do not use. |

---

## 1. Purpose and scope

This document defines **how La Fabrique verifies software**, and more importantly **where each kind of verification lives**, so that a failure points at one cause and a fix is cheap. It is written to satisfy a simple acceptance test: *a QA engineer with a clean laptop, and the CI pipeline, can run every documented command without being told something that is missing from this document.*

### 1.1 In scope

- Automated tests for the Python/FastAPI API, the LangGraph agent workflows, the React/TypeScript web app, Neo4j graph queries, PostgreSQL + pgvector similarity search, and the SSE streaming surface.
- Fixtures, fakes, mocks, and test data ownership.
- Coverage gates, quality metrics, and reporting.
- Local developer/QA execution and CI execution, including setup prerequisites.
- Flakiness handling and quarantine.

### 1.2 Out of scope (referenced, not duplicated)

| Topic | Where it lives |
|---|---|
| LLM answer quality, groundedness, eval datasets, prompt regression | `EVAL_STRATEGY.md` |
| Penetration testing, threat model, authz matrix | `SECURITY_TESTING.md` |
| Manual exploratory charters, UAT scripts | QA test management tool |
| Load/soak/stress beyond the smoke budgets in §5.6 | `PERF_PLAN.md` |
| Infrastructure/DR testing | `RUNBOOK.md` |

### 1.3 Audience

Backend, Agents, Frontend, Data, DevOps, Security engineers, and QA. Anyone adding a feature is expected to add tests at the layers described here (§14 defines "done").

---

## 2. The test pyramid