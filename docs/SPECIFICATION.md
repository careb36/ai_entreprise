# La Fabrique — its own specification

This is La Fabrique described the way La Fabrique describes a project: a
numbered specification, an architecture with ADRs, and the quality standards
the factory holds its own code to. It is the most honest test of the
product (issue #329).

## Summary

La Fabrique is an AI software factory: a React dashboard, a FastAPI control
plane and a Flask LLM gateway run agent roles (PO, Architect, ScrumMaster,
Developer, QA, SecOps, TechWriter) over sprints. Since the repositioning
(issue #38) the agents are interchangeable producers, and the product is the
layer that verifies their work and records evidence.

## Requirements

### REQ-1 Verify every delivery
- **Criterion REQ-1.1**: given a delivery, when the daemon reports the files
  written and the test run, then the Verifier records a verdict (verified /
  refuted / unverifiable) and the evidence behind it.

### REQ-2 Record evidence, never claims
- **Criterion REQ-2.1**: given an agent run, when it is ingested, then its
  claims and the daemon's observations are stored in separate fields.

### REQ-3 Per-task branches
- **Criterion REQ-3.1**: given a company with git workflow on, when a task
  delivers, then the commit lands on `fabrique/task-<id>` in a worktree and
  the person's checkout is untouched.

## Quality
- NFR-1: backend tests pass on SQLite without external services.
- NFR-2: every user-facing string is translated (en + es).

## Constraints
- Python 3.11+, Node 24, no Alembic until #326 lands.