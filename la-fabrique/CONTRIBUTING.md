# Contributing to La Fabrique

La Fabrique is an open-source (Apache 2.0) multi-agent orchestration platform: a React web
app, a FastAPI control plane, and LangGraph-based sprint orchestration with
first-class observability.

This document is the single source of truth for **proposing, reviewing, and
merging changes** — for humans and for AI agents. If you are an agent, read
§2 before you open a PR.

- Normative: `CONTRIBUTING.md`, `SECURITY.md`, `.github/CODEOWNERS`.
- Precedence: `SECURITY.md` > `CONTRIBUTING.md` > tooling default.
- Questions: open an issue.

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
| AI agents | Draft specs, implement, review, verify, merge | Yes (any PR, head green only) |
| Human Owner | Ownership, escalation, final call | Yes |

---

## 2. Non-negotiables

1. **No secrets in the repo.** Ever. Keep them in `.env`, which git ignores.
   No workflow scans a pull request for secrets (`gitleaks` runs over the
   history only weekly), so nothing will catch a committed key in time: check
   before you commit.
2. **Third-party IP.** Do not paste code, prompts, or designs from La Fabrique into
   third-party tools without Security sign-off. No new dependency without
   Security review.
3. **`main` is always releasable.** Nothing merges to `main` with a red pipeline.
4. **Every change traces to an issue.** PRs without a linked issue are closed.
5. **Agents merge pull requests** — their own and each other's — once every
   check on the head commit is green. No PR waits for a human signature. What
   still does not happen: merging a red or still-running head, merging a head
   that moved after it was verified, or weakening branch protection to force a
   merge through.
6. **Tests and observability ship with the change**, not after it.

### Licensing of contributions

La Fabrique is released under the [Apache License 2.0](LICENSE). By opening a
pull request you agree that your contribution is licensed under the same terms
(inbound = outbound, no separate CLA). Only contribute code you have the right
to submit: no copied code from a project whose licence is incompatible with
Apache 2.0, and keep third-party notices intact.

---

## 3. Local setup

See [`README.md`](README.md) — Installation for the full stack and the
workspace daemon. Per-project commands (backend tests, frontend gates, lint)
are in [`CODING_STANDARDS.md`](CODING_STANDARDS.md).

---

## 4. Branches

| Branch | What it is | Who merges into it |
|--------|-----------|--------------------|
| `main` | The web product, and the default branch | An agent or a person, with the head green (rule 5) |
| `fusion` | The NVIDIA × Nebius hackathon submission: this factory under `la-fabrique/`, with the desktop assistant and the coding-agent demo at the root | As for `main` |
| `meta-vr` | La Fabrique XR, the entry for the Meta VR Start Developer Competition (`xr/`) | As for `main` |

Work is trunk-based: one branch cut from the line it changes (`main`, `fusion`
or `meta-vr`), one pull request back into it, merged with a merge commit rather than a squash so the history keeps its shape.
There is no `develop` integration branch.

---

## 5. Schema migrations

The database schema is versioned with Alembic (`backend/alembic/`). The
backend starts only on a database at the migrations' head: the container's
entrypoint runs `alembic upgrade head` before uvicorn, the e2e does the same,
and a backend run by hand needs it too (`cd backend && alembic upgrade head`,
or `DB_AUTO_UPGRADE=true` for a local database). A database behind head
stops the start with both versions in the error; nothing patches it in place.

To change the schema:

1. Change the model (`backend/app/models/`).
2. Write the migration from the difference, against a database at head:
   `cd backend && alembic revision --autogenerate -m "what changes"`
   (`ALEMBIC_DATABASE_URL=sqlite:////tmp/x.sqlite` works for this; the
   first migration is the baseline every database converges to). Read the
   generated file: autogenerate misses renames and data moves, and a column
   added `NOT NULL` to a table with rows needs a default or a backfill.
3. Write the `downgrade` too; a migration that cannot be undone says so and
   why, and raises (the baseline, `0001`, does: there is no schema before it,
   so going back means restoring a backup).
4. Check that the models and the migrations agree: `alembic upgrade head &&
   alembic check` on a fresh database. CI runs this on a SQLite file
   (`backend-tests.yml`) and the e2e on Postgres; both fail on a difference.
5. Add the migration to the pull request with the model change. Never edit a
   migration that has merged; write the next one.

`vector_memory` is created by `db/init-db/init.sql` (it needs pgvector) and
the migrations leave it alone.
