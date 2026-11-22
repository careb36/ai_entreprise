<div align="center">
  <img src="frontend/public/fabrique-icon.svg" width="90" alt="La Fabrique" />

# La Fabrique

[![CI](https://github.com/careb36/la-fabrique/actions/workflows/backend-tests.yml/badge.svg)](https://github.com/careb36/la-fabrique/actions/workflows/backend-tests.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

**IT projects run by a company of AI agents, with proof of what they did.**

[English](README.md) | [Español](README.es.md)

</div>

## What it is

La Fabrique is a company of AI agents that plans, builds and documents IT
projects, and records verifiable evidence of what the agents did. Today the
loop runs software tasks end to end: from a specification to files on disk,
tests, review and a pull request. It also compiles a project's documentation
pack from its records. Data science, machine learning, automation and research
are direction, not built yet ([`docs/POSITIONING.md`](docs/POSITIONING.md)).

The agents are interchangeable producers. The product is the layer that checks
their work:

- **Claims and observations never share a field.** What an agent says (its
  summary, its file list, its own test report) is a claim. Only what the
  platform observed counts as evidence.
- **A Verifier decides without a model.** Each run is `verified`, `refuted` or
  `unverifiable`, from deterministic checks: files on disk, tests the daemon
  ran, acceptance tests, standards, packages that really exist.
- **A gate stands before QA.** Failed or unconfirmed work goes back for rework.
  High-risk changes (auth, secrets, payments, CI) wait for a person.
- **The record is tamper-evident.** The audit log is a hash chain per company,
  and hashes are computed by the platform, never taken from the agent.

![Evidence overview: runs with their verdicts](docs/img/evidence-overview.png)

## What you can do today

- **Plan**: the PO turns a brief into a specification with testable criteria;
  the Architect proposes the architecture; the PO splits it into sprint tasks.
- **Run sprints**: a board with five columns, a review of each run's diff like
  a pull request, and a retrospective at each sprint close.
- **Work on your code**: the daemon writes files, runs tests in a sandbox, and
  can keep one git branch per task and publish a draft pull request.
- **Document**: a documentation pack and a project wiki compiled from the
  records, each statement citing one.
- **Measure**: [the bench](bench/README.md) grades the factory on reference
  specifications with tests a person wrote.

## Quick start

You need Docker 24+ with Compose v2, and Python 3.9+ on the computer that holds
your projects.

```bash
cp .env.example .env
# Fill in POSTGRES_PASSWORD, NEO4J_PASSWORD and NEO4J_AUTH, JWT_SECRET_KEY,
# FERNET_SECRET_KEY, AUDIT_SIGNING_KEY and DEEPSEEK_API_KEY.

docker compose up -d          # backend :8000, frontend :5173, LLM proxy :5100
./scripts/start-daemon.sh     # on the computer that holds your projects
                              # (.\scripts\start-daemon.ps1 on Windows)
```

`DEEPSEEK_API_KEY` is the only model key you need. `QWEN_API_KEY` is optional:
set it to run a role on a Qwen model. DeepSeek's API processes requests in
China; before sending personal or client data, read `DEEPSEEK_API_KEY` in
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

Then:

1. Check the stack: `curl -s localhost:8000/health` (`/health/deep` for each
   dependency). The API docs are at `http://localhost:8000/docs`.
2. Check the model keys: `curl -s localhost:5100/v1/providers`.
3. Open `http://localhost:5173`, register and create a **New company**. Pick a
   project folder on the daemon's computer, such as `/home/you/src/app`.
4. Write a specification or plan a sprint, and follow it on the board.

Neo4j is optional and on by default. To run without it:
`NEO4J_ENABLED=false docker compose up -d --scale neo4j=0`.

## Architecture

```
React frontend --REST/SSE--> FastAPI backend --> Postgres (pgvector), Neo4j (optional)
                                |      |
                                |      +--> LLM proxy --> DeepSeek (default), Qwen
                                |
                                +--NATS JetStream--> workspace daemon (files, git, tests, sandbox)
```

- **Backend** (`backend/`): fourteen contexts with a checked dependency rule.
  The evidence context imports only contracts and platform, so what judges a
  run never depends on the code that produced it
  ([`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)).
- **Workspace daemon** (`vscode-daemon/`): runs natively on macOS, Linux or
  Windows. It is the only component that touches your files.
- **LLM proxy** (`llm-proxy/`): every model call goes through it. It enforces
  per-company token budgets and routes by model id.

## Documentation

- [`docs/README.md`](docs/README.md): the index of every document
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md): every environment variable
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md): deploying, upgrades, backups, incidents
- [`docs/TESTING.md`](docs/TESTING.md): the test suites and CI (`scripts/check.sh quick` or `full`)
- [`docs/POSITIONING.md`](docs/POSITIONING.md): the direction and the design rules
- [`USER_GUIDE.md`](USER_GUIDE.md): a guide for managers, without code
- [`CONTRIBUTING.md`](CONTRIBUTING.md): how to contribute; coding agents start from [`CLAUDE.md`](CLAUDE.md)
- [`SECURITY.md`](SECURITY.md): the security policy and how to report a vulnerability

## Security

The agents write files and run commands where the daemon runs. Point it only
at folders you may modify, prefer the `container` sandbox, and keep NATS bound
to `127.0.0.1` unless the daemon is on another machine.

## Licence

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
