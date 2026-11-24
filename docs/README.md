# Documentation

Start with the [README](../README.md) for what La Fabrique is and the quick
start. Coding agents start from [`CLAUDE.md`](../CLAUDE.md).

## Orientation

- [`POSITIONING.md`](POSITIONING.md): why the evidence layer is the product, and the design rules.
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md): how the pieces fit; the backend's contexts and their dependency rule.
- [`contexts/`](contexts/): one document per backend context, indexed by [`../CLAUDE.md`](../CLAUDE.md).
- [`SPRINT_LOOP.md`](SPRINT_LOOP.md): the task worker's steps, from the start of a sprint to its close.
- [`STANDARDS.md`](STANDARDS.md): the quality standards every project is held to.
- [`API.md`](API.md): every HTTP route.
- [`WORKFLOWS.md`](WORKFLOWS.md): workflows per column, task kind or sprint close (design, not built).
- [`design/`](design/): designs in progress, such as [`rsi-loop.md`](design/rsi-loop.md) and [`agent-toolsets.md`](design/agent-toolsets.md) (the tools each role should have).
- [`archive/`](archive/README.md): design records of built features, cited by code and tests.

## Operating

- [`../USER_GUIDE.md`](../USER_GUIDE.md): the manager's guide, without code.
- [`CONFIGURATION.md`](CONFIGURATION.md): every environment variable.
- [`OPERATIONS.md`](OPERATIONS.md): deploy, upgrade, key rotation, backups, incidents.
- [`TESTING.md`](TESTING.md): the test suites, the end-to-end run, the bench and CI.

## Security

- [`../SECURITY.md`](../SECURITY.md): the security policy and how to report a vulnerability.
- [`threat_model.md`](threat_model.md): what can go wrong and what the system does about it.

## History and contribution

- [`../CONTRIBUTING.md`](../CONTRIBUTING.md): branches, commits, landing.
- [`../CHANGELOG.md`](../CHANGELOG.md): what changed, release by release.
- [`retrospectives/2026-11-self-hosting.md`](retrospectives/2026-11-self-hosting.md): La Fabrique run on itself (issue #329).
- [`architecture/MIGRATION.md`](architecture/MIGRATION.md): the move to context folders (ADR 0016), step by step.
- [`HACKATHON.md`](HACKATHON.md): the competition entry and its demo path.
- [`../CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md): the rules for participating.
