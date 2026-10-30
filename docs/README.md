# Documentation

Everything in one place. Start with the [README](../README.md) for the pitch and
quickstart; the files below go deeper, each with a single job.

## Orientation

| Document | What it answers |
| --- | --- |
| [`POSITIONING.md`](POSITIONING.md) | Why the evidence layer is the product, and the design rules that follow from that position. |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | How the pieces fit: FastAPI, NATS, the daemon, the proxy, the engram graph. |
| [`API.md`](API.md) | Every HTTP route, generated and verified against the running app. |
| [`DEMO.md`](DEMO.md) | The scripted demo: seed one run per verdict and show the screen that distinguishes them. |
| [`verificador-sandbox.md`](verificador-sandbox.md) | How the verifier is sandboxed so a submission cannot game its own verdict. |

## Operating

| Document | What it answers |
| --- | --- |
| [`../USER_GUIDE.md`](../USER_GUIDE.md) | The manager's guide: what the factory does and how to run a company through it. |
| [`../runbook.md`](../runbook.md) | Day-to-day operation: start, reset, diagnose, recover. |
| [`factory-smoke-test.md`](factory-smoke-test.md) | The one-command check that the whole stack still works. |
| [`alerting-rules.yml`](alerting-rules.yml) | Prometheus alerting rules for the stack. |

## Security and quality

| Document | What it answers |
| --- | --- |
| [`../SECURITY.md`](../SECURITY.md) | The security policy and how to report a vulnerability. |
| [`threat_model.md`](threat_model.md) | What can go wrong, what the system does about it, and what it assumes. |
| [`threat_triage.md`](threat_triage.md) | How a reported finding is triaged. |
| [`../TESTING_STRATEGY.md`](../TESTING_STRATEGY.md) | What is tested, where, and how CI runs it. |
| [`front-coverage.md`](front-coverage.md) | Frontend test coverage, by page and component. |

## Planning and history

| Document | What it answers |
| --- | --- |
| [`HACKATHON.md`](HACKATHON.md) | The competition context, the pitch, and the judge-facing demo path. |
| [`IMPROVEMENT_PLAN.md`](IMPROVEMENT_PLAN.md) | Open improvements and the order they are intended to land in. |
| [`../CHANGELOG.md`](../CHANGELOG.md) | What changed, release by release. |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | How to contribute: branches, commits, review. |
| [`../CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md) | The rules for participating. |

## For AI agents working in this repo

- [`../AGENTS.md`](../AGENTS.md) — the instructions any agent must read first.
- [`../CLAUDE.md`](../CLAUDE.md) — long-form architecture notes (some stale; see
  the "Docs that mislead" section in `AGENTS.md`).
- [`../CODING_STANDARDS.md`](../CODING_STANDARDS.md) — layout and style rules.
