# Documentation

Everything in one place. Start with the [README](../README.md) for the pitch and
quickstart; the files below go deeper, each with a single job.

## Orientation

| Document | What it answers |
| --- | --- |
| [`POSITIONING.md`](POSITIONING.md) | Why the evidence layer is the product, and the design rules that follow from that position. |
| [`retrospectives/2026-11-self-hosting.md`](retrospectives/2026-11-self-hosting.md) | La Fabrique run on itself (issue #329): what worked, what the factory could not do on its own repository, the follow-up issues. `scripts/self_host/` is the harness. |
| [`STANDARDS.md`](STANDARDS.md) | The quality standards every project is held to, by discipline, and which of them the loop checks without a model. |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | How the pieces fit: FastAPI, NATS, the daemon, the proxy, the engram graph. |
| [`API.md`](API.md) | Every HTTP route, generated and verified against the running app. |
| [`contexts/`](contexts/) | One document per backend context: what it owns, what it may import, and how its modules work. Indexed by [`../CLAUDE.md`](../CLAUDE.md). |
| [`SPRINT_LOOP.md`](SPRINT_LOOP.md) | Data flow for a sprint: the task worker's steps from the start of a sprint to its close. |
| [`design/COMPANY_STANDARDS.md`](design/COMPANY_STANDARDS.md) | Per-company standards imported from the repository (issue #555, design, not built): checks the Verifier runs without a model, Owner-activated and hashed by the backend, that add to the catalogue and never relax it; La Fabrique's own rules as the worked example. |
| [`design/CODE_GRAPH.md`](design/CODE_GRAPH.md) | The code knowledge graph (issue #438): what the first slice computes on demand, the later slices and what was studied. |

## Operating

| Document | What it answers |
| --- | --- |
| [`../USER_GUIDE.md`](../USER_GUIDE.md) | The manager's guide: what the factory does and how to run a company through it. |
| [`CONFIGURATION.md`](CONFIGURATION.md) | Every environment variable of the backend, the proxy, the broker and the daemon. |
| [`TESTING.md`](TESTING.md) | How to run the test suites, the end-to-end run, the bench and what CI runs. |

## Security and quality

| Document | What it answers |
| --- | --- |
| [`../SECURITY.md`](../SECURITY.md) | The security policy and how to report a vulnerability. |
| [`threat_model.md`](threat_model.md) | What can go wrong, what the system does about it, and what it assumes. |

## History and contribution

| Document | What it answers |
| --- | --- |
| [`HACKATHON.md`](HACKATHON.md) | The competition context, the pitch, and the judge-facing demo path. |
| [`architecture/MIGRATION.md`](architecture/MIGRATION.md) | The record of the move to context folders (ADR 0016), step by step. |
| [`../CHANGELOG.md`](../CHANGELOG.md) | What changed, release by release. |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | How to contribute: branches, commits, review. |
| [`../CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md) | The rules for participating. |
