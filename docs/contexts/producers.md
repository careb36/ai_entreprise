# The producers context

The interchangeable producers other than the task worker's own model call. A
producer's output is a claim handed to the evidence. Read this before changing
`backend/app/services/producers/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/producers/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md), [evidence](evidence.md), [workspace](workspace.md), [tools](tools.md).
- **Interface:** `console_producer`.
- **Modules:** `agent_worker`, `cli_cooldowns`, `console_clis`, `console_producer`, `github_adapter`, `graph`, `graph_agents`, `graph_nodes`, `openproject_client`, `openproject_tools`.
- **HTTP adapters:** `github_adapter` (the GitHub API), `openproject_client` (`post_work_package`). Only these import an HTTP library; they return data and raise named errors.

## How it works

Three producers live here: console producers (a terminal coding agent, the
Claude, Codex, Gemini or Cursor CLI, produces a role's tasks instead of the
model call), the optional LangGraph kickoff, and the GitHub adapter that
ingests pull requests as runs. What they produce goes through the same
delivery, Verifier and QA as the task worker's own output.

## Rules

- A console producer is opt-in per company (`Company.console_producers`). No
  detection or default ever turns one on.
- It fails closed. It runs only in a `container` or `bwrap` sandbox with the
  network on, never on `host`: the CLIs run with their permission prompts off,
  so the sandbox is the boundary. Otherwise the model produces the task, and
  the board, the run's evidence (`console_producer_skipped`) and the Glassroom
  say why.
- It works only in the task's worktree with the Git workflow on, never in the
  person's checkout.
- The provider's key goes in `Company.console_env`, which only the console run
  sees, never in `sandbox_env`, which every delivery test also sees.
- The CLI's output is a claim, quoted in the deliverable and never put on the
  evidence. The files the daemon read back, each with its sha256, are the
  observation.
- The host repository's metadata is guarded. The CLI sees a private repository
  inside the worktree; the daemon restores git's pointer with file operations
  before any host git runs; a change to the metadata refuses the run. The
  daemon's alarm (`daemon_refused`) wins over every other outcome.
- A deletion, a truncated listing or a text past the reply's bound refutes the
  run rather than deliver a tree the CLI never produced.

## Components

### Console producers

- `console_producer.py`: the task worker's one hook (`produce`, called through
  `services/loop/console_host.py`). It returns `None` when the model should
  produce the task; otherwise it prepares the worktree, runs the CLI and
  collects its files as `agent["tool_edits"]` (`source: console_producer`).
- `console_clis.py`: the CLIs and their headless commands, prompt quoting, the
  command size bound, the sandbox refusal (`sandbox_refusal`) and the run's
  sandbox environment.
- `cli_cooldowns.py`: a quota message or a missing binary cools the CLI down
  and hands the task to the model. A run that exited 0 never starts one.
- Daemon side: `vscode-daemon/console_run.py`, see [workspace](workspace.md).
  Settings: `GET`/`PUT /v1/companies/{id}/console_producers`, on the Models page.

### The LangGraph producer

- `graph.py`, `graph_agents.py`, `graph_nodes.py`: off unless
  `AGENT_GRAPH_ENABLED=true`. Runs are ingested as `AgentRun` records with
  `producer: "le-fabrique-graph"`.
- `agent_worker.py`: the NATS consumer that runs the graph.
- `openproject_tools.py`, `openproject_client.py`: the PO node's work package,
  simulated without OpenProject.

### The GitHub adapter

`github_adapter.py`: ingests a pull request as a run.

## See also

- `CONSOLE_PRODUCER_TIMEOUT_SECONDS`, `CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES`, `AGENT_GRAPH_ENABLED`: [configuration](../CONFIGURATION.md); step 9 of [the sprint loop](../SPRINT_LOOP.md).
- Console assistants were removed; a task's second opinion is the Architect's consultation, in [loop](loop.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-10-producers).
