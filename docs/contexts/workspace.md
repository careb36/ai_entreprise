# The workspace context

The backend's client of the workspace daemon over NATS, and the daemon itself
(`vscode-daemon/`), the only component that touches the person's files. Read
this before changing `backend/app/services/workspace/` or the daemon; each
module's docstring has the detail.

- **Folder:** `backend/app/services/workspace/`
- **May import:** [contracts](contracts.md), [platform](platform.md).
- **Interface:** `branch_view`, `daemon_liveness`, `delivery_commands`, `git_workflow`, `project_profile`, `sandbox`, `stack_image`, `workspace_daemon`. The loop reaches a delivery's daemon commands only through `delivery_commands`.
- **Modules:** `branch_view`, `browser_images`, `daemon_abilities`, `daemon_liveness`, `delivery_commands`, `dependency_install`, `folder_commands`, `git_workflow`, `project_profile`, `run_log_reader`, `run_logs`, `sandbox`, `sandbox_http`, `sandbox_manager`, `stack_image`, `stack_profiles`, `stack_roots`, `workspace_daemon`, `workspace_snapshot`.
- **HTTP adapters:** `sandbox_http` (the pre-QA sandbox's health and validate endpoints, raising `SandboxUnreachable`). Only it imports an HTTP library.

## How it works

The daemon runs natively on the person's macOS, Linux or Windows machine
(`scripts/start-daemon.*`) and answers NATS subjects: `workspace.generate`
writes a delivery, `workspace.execute` runs a command in the sandbox,
`workspace.git.*` handles branches, merges and publishing, and others read the
project. The backend sends requests, keeps the replies as observations, and
never judges them; the evidence does. Paths are the host's own and pass
through the backend as opaque strings. The wire is versioned in
[`vscode-daemon/protocol/PROTOCOL.md`](../../vscode-daemon/protocol/PROTOCOL.md),
with a JSON Schema and recorded fixtures per subject that both sides replay.

## Rules

- Hashes are computed by the side that observed the bytes. The daemon replies
  with the sha256 it read back from disk; for full logs and files read at a
  commit it replies with no hash and the backend hashes what it received. A
  hash from the agent or the client is never accepted.
- Agent code runs in the sandbox mode the company asked for (`host`,
  `container`, `bwrap`), and the daemon reports what it applied. With
  `FABRIQUE_SANDBOX_REQUIRED` set, the daemon raises a request to that mode or
  refuses it, never the host.
- Every command goes through the daemon's command policy (`command_policy.py`).
- A delivery never touches the person's checkout when the Git workflow is on:
  it commits on `fabrique/task-<id>` in a worktree. A folder that is not a
  repository is delivered with a snapshot that can be undone.
- Forge contact follows the company's `forge_policy` (`none` by default:
  nothing fetched or pushed). Publishing and reading reviews need `publish`.
  The daemon's own `FORGE_POLICY_MAX` caps whatever the backend asks for.
- Git never prompts: no terminal prompt, no askpass, ssh in batch mode. A
  remote that needs interaction fails at once.
- The daemon only fast-forwards: a diverged base is refused with both counts,
  a merge aborts at a conflict with nothing changed, and nothing is pushed by
  a local merge.
- The daemon never opens VCS metadata, a file that may hold secrets or a
  symlink out of the project for the read tools, the map or the graph.
- What the daemon says about itself (version, abilities) is a claim, kept under
  `reported_by_daemon`. The backend's liveness comes from heartbeats it
  observed on its own clock. An unknown state is never shown as stopped.
- A daemon outside `SUPPORTED_PROTOCOL` is `incompatible`, with a reason naming
  the side to update, never a generic timeout.
- `vscode-daemon/sandbox.py`, `sandbox_image.py`, `sandbox_cache.py` and
  `run_logs.py` are protected paths: bench grading imports them
  (`scripts/bench/sandbox_grading.py`, threat model
  [`SANDBOX_GRADING_THREAT_MODEL.md`](../archive/design/SANDBOX_GRADING_THREAT_MODEL.md)).

## Components

### The daemon client and liveness

- `workspace_daemon.py`: NATS requests about the person's machine (status,
  browse, inspect, the test-run record), behind `routers/workspace.py`;
  `no_responders(exc)` tells a daemon that is not running (NATS's "no
  responders") from one that did not answer in time.
  `output_tail` keeps the end of a command's output with the end of stdout
  last and most of the room, so stderr's warnings never hide the result.
- `daemon_liveness.py`: heartbeats to `online`, `stale` or `offline`, the
  protocol range check, and `announces(subject, feature)`.
- `daemon_abilities.py`: parses the subjects and fields a daemon announces.
- `workspace_snapshot.py`: read-only views of the repository for prompts and
  for what a deliverable's files really hold.

### Sandbox and images

- `sandbox.py`: the sandbox request for a company (`request_for`, `scope_for`)
  and the cache reset.
- `stack_image.py`: the project's own sandbox image, built by the daemon from
  fixed templates when `DELIVERY_SANDBOX_IMAGE` is empty.
- `browser_images.py`: picks the browser image for the project's Playwright
  release, or says `unknown`.
- `sandbox_manager.py`, `sandbox_http.py`: the ephemeral pre-QA sandbox
  (Docker or Kubernetes).

### A delivery's commands

- `delivery_commands.py`: the one entry point for install, tests, folder
  commands and the application check. It composes requests and returns
  replies; the loop chooses the commands.
- `stack_profiles.py`, `project_profile.py`: how to install, test, lint and
  start a project, detected from the daemon's snapshot and overridden by the
  deployment and the company. Configuration, never evidence.
- `stack_roots.py`, `folder_commands.py`: a profile per folder in a repository
  with several projects. Which suites run is decided from the daemon's write
  report, never the agent's list.
- `dependency_install.py`: installs when a manifest changed since the last
  successful install, keyed per folder and sandbox mode.
- `run_logs.py`: reads a full log back from the JetStream object store and
  hashes it. `run_log_reader.py`: serves it again (`GET
  /v1/agent_runs/{id}/logs`), checking the bytes streamed against the recorded
  sha256; a byte range is marked unverified.

### The Git workflow

- `git_workflow.py`: the company's workflow, the task's `git` spec
  (`task_spec`: branch, base, `forge_policy`, the done prerequisites'
  branches; the tools' worktree is prepared from it before the first turn,
  [design](../design/first-attempt-in-task-worktree.md)), the delivery's
  (`delivery_spec`, with the commit message), and `record_reply`. It is set from what `workspace.inspect`
  observed: on only for a repository.
- `branch_view.py`: what a task's branch changes against its base, for QA,
  from `workspace.git.diff`.

### The daemon (`vscode-daemon/`)

Dependencies are `nats-py` and `nkeys`, locked with hashes in
`requirements.txt` and installed with `--require-hashes`.

- Core: `daemon.py` (the subject handlers), `nats_connect.py` (credentials,
  TLS, waits for the backend's streams), `nats_scope.py` (namespaced stream
  names), `daemon_status.py` and `abilities.py` (status, heartbeat, announced
  abilities), `host_folders.py` (folder picker and `workspace.inspect`).
- Delivery: `delivery.py`, `file_edits.py` (SEARCH/REPLACE edits, syntax check
  before writing), `file_diff.py`, `file_hash.py` (`workspace.hash`),
  `delivery_snapshot.py` (snapshots and `workspace.undo`), `delivery_journal.py`
  (a queued delivery applied once), `project_lock.py` (one writer per project).
- Execution: `command_policy.py`, `shell_exec.py`, `sandbox.py`,
  `sandbox_image.py` (which image ran, by content ID), `sandbox_cache.py`
  (per-project cache and environment volumes, quarantine), `stack_image.py`,
  `win_job.py` (Windows process trees), `run_logs.py` (full logs to the object
  store).
- Application check: `app_run.py` (`workspace.app.check`), `security_probe.py`,
  `browser_probe.py`, `playwright_pin.py`, the browser image in `images/browser/`.
- Git: `git_workspace.py` (worktrees, `run_git`), `git_base.py` (where a branch
  starts), `git_env.py` (never a prompt), `git_local.py`, `git_sync.py`
  (fast-forward the person's base), `git_read_at.py` (files at the base's
  head), `git_diff.py`, `git_view.py` (the agents' read-only views on
  `workspace.git.diff`: status, changed, one path's diff, log, and blame of
  a line range without names or e-mails; PROTOCOL.md §5.6), `local_merge.py`, `sprint_integration.py`,
  `platform_reviews.py`, `forge_gate.py`.
- Tools and producers: `workspace_read.py`, `document_text.py` (a DOCX's
  text for `workspace.read` with `extract: "text"`, standard library only, no
  DTD accepted; PDF refused for now; PROTOCOL.md §5.8), `workspace_edit.py`,
  `workspace_move.py` (move lines, delete an emptied file; a delivery's
  `delete` entry, committed and undone like a write; PROTOCOL.md §5.1.4), `repo_map.py`,
  `code_graph.py`, `code_lookup.py` (definition and references lookups on
  `workspace.related`, PROTOCOL.md §5.7), `signatures.py` (see
  [tools](tools.md)); `console_run.py`
  (see [producers](producers.md); with `private_git: false` it prepares the
  task's worktree for the Developer's tools, keeping git's pointer,
  PROTOCOL.md §5.1.5).

## See also

- The protocol, its subjects and refusal codes: [`PROTOCOL.md`](../../vscode-daemon/protocol/PROTOCOL.md).
- The execution environment and what was taken from AixleHQ/flow: [`EXECUTION_ENVIRONMENT.md`](../archive/design/EXECUTION_ENVIRONMENT.md).
- Sandbox, command policy, stack profiles, application check and the daemon's settings: [configuration](../CONFIGURATION.md).
- Publishing, local merge, integration and undo: [release](release.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-6-workspace).
