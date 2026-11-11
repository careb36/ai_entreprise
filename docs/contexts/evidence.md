# The evidence context

Part of the project's guidance for agents; the index is [`CLAUDE.md`](../../CLAUDE.md). Read this document before changing code in `backend/app/services/evidence/`.

- **Folder:** `backend/app/services/evidence/`
- **Responsibility:** Judging a run from what was observed: the Verifier's checks and verdict, the risk tier, the quality standards check, acceptance tests per criterion, the context-read check, the application and browser checks as read from the daemon's reply, SecOps scans, the gate before QA, the changes of a run and the ingestion of AgentRun records. It reads the daemon's reply as data from contracts; it never calls the daemon, the loop, the tools or a producer.
- **May import:** [contracts](contracts.md), [platform](platform.md) (`docs/architecture/contexts.json`, checked by `scripts/check_architecture.py`).
- **Modules:** `acceptance`, `agent_run_service`, `app_check`, `browser_smoke`, `console_run`, `context_read`, `criteria_tests`, `delivery_gate`, `governance`, `review_changes`, `risk`, `secops_checks`, `sources`, `standards`, `verification`.

## Components

### What a run changed

**`backend/app/services/evidence/review_changes.py`** — What a run changed, the way a code review shows it: `GET /v1/agent_runs/{id}/changes` reads the daemon's per-file diffs from the latest verification whose evidence carries them, or a pull request's diff (the artifact of a GitHub-ingested run), or, with neither, a diff the producer sent as its artifact (labelled `artifact`), with one `git diff` parser; the files the producer only declared come back apart (`declared_not_observed`), never as changes. The run page's **Files changed** tab (`components/ReviewChanges.tsx`, `lib/patch.ts`) renders it like a pull request.

### The quality standards

**`backend/app/services/evidence/standards.py`** — The quality standards every project is held to: a fixed catalogue by discipline (`STD-ARCH-1` … `STD-PO-1`, with `STD-OPS-2` for DevOps, `STD-DATA-1` for data and migrations and `STD-UX-1` for accessibility, `docs/STANDARDS.md`) that no model, request or procedure edits; `for_role` reads a role through `roles.canonical` and binds the specialised developers to every Developer standard too (`DISCIPLINES`), never fewer. `prompt_section(role)` goes into every prompt by role (the task prompt before the company's procedures, QA's review, the PO's draft and plan, the Architect's design); a procedure adds to the standards, never relaxes them. `check_delivery` is the Verifier's `standards` check over the files a deliverable declares: a secret-looking value (keys, tokens, private keys, a password assigned in code; placeholders and `os.environ` references are not) refutes the delivery and the gate sends it back (`standards violated`); code without a test, a `TODO`/`FIXME` left behind and an unpinned dependency (`requirements*.txt`, `package.json`) are findings the reviewer sees. `missing_quality_areas` gives the specification check its `quality_area_missing` warnings. `GET /v1/standards` serves the catalogue; `tests/test_standards.py` keeps the document in step with it.

## Described elsewhere

- `verification`, `acceptance`, `criteria_tests`, `risk` and `delivery_gate` at work: steps 4 to 6 of [the sprint loop](../SPRINT_LOOP.md).
- `app_check`, `browser_smoke` and the `sandbox` check: [configuration](../CONFIGURATION.md) (`DELIVERY_TESTS_ENABLED`, stack profiles and the application check).
- `context_read` and `sources`: the Developer's workspace tools and the research tools in [tools](tools.md).
- `console_run`: the console producers paragraph in [producers](producers.md).
- The Verifier benchmark (`bench/verifier/`): [testing](../TESTING.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-5).
