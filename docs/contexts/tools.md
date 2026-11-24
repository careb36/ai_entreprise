# The tools context

What a model may call during a task: the bounded tool loop, MCP servers, the
Developer's workspace tools, checked edits, the repository map, the related
code and the research tools. Read this before changing
`backend/app/services/tools/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/tools/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [evidence](evidence.md), [workspace](workspace.md).
- **Interface:** `code_graph`, `code_level`, `developer_tools`, `mcp_tools`, `registry_lookup`, `research_tools`, `review_tools`, `signature_lookup`, `task_worktree`, `workspace_edits`.
- **Modules:** `check_scripts`, `code_graph`, `code_graph_neo4j`, `code_graph_store`, `code_level`, `developer_tool_replies`, `developer_tool_schemas`, `developer_tools`, `edit_lint`, `mcp_client`, `mcp_config`, `mcp_policy`, `mcp_tools`, `registry_lookup`, `repo_map`, `research_http`, `research_registries`, `research_schemas`, `research_tools`, `review_tools`, `signature_lookup`, `task_worktree`, `test_commands`, `test_runs`, `tool_history`, `workspace_edits`, `workspace_git`.
- **HTTP adapters:** `mcp_client` (the MCP transports), `research_http` (`default_http`, raising `ResearchFetchError`). Only these import an HTTP library; they return data and raise named errors.

## How it works

The task worker builds a toolbox for the role (`developer_tools.toolbox_for`,
a `CombinedToolbox` of the workspace, research and MCP boxes) and runs
`mcp_tools.chat_with_tools`: at most a bounded number of rounds, then one
request without tools, so a task always ends in a deliverable. Tools are
offered as `<server>__<tool>`; `workspace` and `research` are reserved server
names. Each call is recorded on the Verifier's evidence as `tool_calls`.

The loop counts its own rounds: the answer carries `tool_loop`, and the run's
evidence `rounds_used` (rounds in which the model called tools), `rounds_max`
and `rounds_exhausted` (the limit was reached and the last request went
without tools). When the final answer is only a tool call typed out as text
(`contracts.tool_calls.written_as_text`), `tool_loop.not_a_deliverable` says
so, and the worker takes it like an empty answer (`loop/answers.llm_failure`):
no delivery, the task blocked with "the model's last answer was a tool call
written as text; the tool budget had run out".

## Rules

- Each call is an observation the backend made (tool, arguments, duration,
  sha256 of the result). The model's reading of a result is a claim. The tools
  never decide a verdict.
- A failed call's observation carries `error`: the first 300 characters of
  the error text the backend produced or received (what the model was
  shown), secret-looking lines redacted as in a read and an MCP server's
  secrets masked (`mcp_tools.error_excerpt`).
- A workspace call NATS answered "no responders" (no daemon running) is marked
  `daemon_absent`; a run whose workspace calls all are is not delivered and
  pauses the sprint (`loop/daemon_gate.py`).
- The repository map, the related code and the stored code graph are context,
  never evidence.
- A tool without a read-only annotation raises the delivery's risk tier to at
  least `medium`; so does a modified file never read while the tools were in
  use.
- MCP servers are the operator's to allow (`mcp_policy.py`, SECURITY.md §8.7).
  Stdio only for a command whose absolute path is on
  `MCP_STDIO_ALLOWED_COMMANDS` (empty by default, so off), spawned without a
  shell. HTTP only on https to hosts that resolve to public addresses, pinned
  to the resolved address. The policy is checked when a server is saved, before
  every start and again on connect. Every stdio start is audited before it
  happens, and a start that cannot be audited does not happen.
- MCP secrets are Fernet-encrypted; the API returns only their names. Results
  are masked for the server's secrets and cut.
- The workspace tools never reach outside the project, into VCS metadata, a
  file that may hold secrets or a symlink that leads out. Secret-looking lines
  are redacted before the model sees them (`context_read.redact_secrets`).
- An edit is applied only if the file still parses, and only in the task's own
  worktree (or a project without the Git workflow), never the person's
  checkout. With the Git workflow the worktree is prepared before the first
  turn (`task_worktree`), so a first attempt has the write tools too. An edit
  never delivered never stays on disk.
- Lint after an edit never writes: a command with an autofix or write option,
  or one the check cannot read, is refused.
- Research fetches go only to allowed hosts, https unless named explicitly,
  with no credential. A refused fetch sends nothing; the reply to the model
  starts `Refused: host not allowed` with why, and the observation carries
  `refused: host not allowed` and `allowed: false`.

## Components

### MCP tools

- `mcp_tools.py`: `Toolbox.load` for a role, `chat_with_tools`, the observations.
  An unreachable server is named in `unavailable` and the task goes on.
- `mcp_config.py`: the company's servers (`McpServer` rows, `routers/mcp.py`),
  `start_refusal` and `audit_start`.
- `mcp_policy.py`: what may be saved and started (`normalize_server`).
- `mcp_client.py`: the transports on the official `mcp` package, with the
  pinned HTTP transport.

### The research tools

- `research_tools.py`: `research__fetch`, `research__package`,
  `research__advisories`, offered to the roles that decide. The evidence's
  `sources.sources_of` keeps the observations on the specification and the
  architecture; `sources.check_sources` is the Verifier's `sources` check.
- `research_registries.py`: PyPI, npm, crates.io, the Go proxy, OSV.
- `research_schemas.py`: the tool schemas. `research_http.py`: the fetch.

### Answers for the `grounded` check

- `registry_lookup.py`: after a delivery writes a manifest, asks the registry
  about each dependency not yet answered and records `registry_lookups`.
- `signature_lookup.py`: asks the daemon's `workspace.signatures` for the
  signatures the delivered Python calls use. It never fails or holds a delivery.

### The Developer's workspace tools

- `developer_tools.py`: `workspace__list`, `read`, `search`, `find_file`,
  `edit` and `run_tests`, offered to the roles that deliver files.
  `repository_section` puts the tree and the map into the task prompt.
  `read_tools` offers the four read tools alone, to any role (the Architect's
  brief, `loop/worker/architect_brief`).
- `review_tools.py`: QA's read-only toolbox (`list`, `read`, `search`,
  `find_file`; never `edit`, `move`, `delete` or `run_tests`) on the
  `workspace_root` the task's latest verification recorded, for
  `QA_REVIEW_TOOL_ROUNDS` rounds before the verdict. No recorded delivery or
  no daemon: no toolbox.
- `developer_tool_schemas.py`: the schemas. `developer_tool_replies.py`: what
  the model sees, after SWE-agent's interface (a numbered window per read, a
  count instead of a silent cut). A read without `lines` asks the daemon for
  400 and shows as many whole lines as fit the model's result budget; the
  observation's `last_line` is the last one shown. A daemon older than
  `lines` answers its default 100.
- `workspace_edits.py`: `workspace__edit`, a line range or one SEARCH/REPLACE
  block, applied by the daemon as a working copy and delivered at the end with
  the answer's files (`tool_edit_files` on the evidence). Beside it, when
  every daemon announces them, `workspace__move` (the same bytes from one file
  to another, `moved_sha256` from the daemon) and `workspace__delete` (only an
  emptied file; delivered as a deletion, `tool_edit_deletions`). Design:
  [`move-and-delete-files.md`](../design/move-and-delete-files.md). When move
  is offered, the task prompt gets `## Moving code between files`
  (`MOVE_NOTE`): a split moves the lines instead of retyping both files.
  `offered` puts `tools_offered` on the run's evidence: every tool name the
  loop offered and, under `withheld`, the workspace's write tools it did not
  offer with why (the person's checkout of a branch-delivering company, which
  holds on a first attempt only when a daemon does not announce
  `private_git`; no heartbeat announcing move and delete yet, as right after
  a backend restart; or a daemon that does not announce them). The backend's own decision, an observation.
- `task_worktree.py`: with the Git workflow, `enter` asks the daemon for the
  task's worktree before the first turn (`workspace.console.prepare` with
  `private_git: false`, only when every daemon announces that feature) and
  makes it the attempt's tool root (`developer_tools.tool_root` reads
  `prepared_for`; `leave` when the tools are done). A reply that does not
  name `fabrique-worktrees/fabrique__task-<id>` is not used. The reply goes
  on the evidence as `tools_offered.task_worktree`. Design:
  [`first-attempt-in-task-worktree.md`](../design/first-attempt-in-task-worktree.md).
- A call to a tool that is not offered (a bare `read`, an empty name) is
  recorded with the name asked (`requested`) and answered with that name, the
  nearest offered one and the list (`mcp_tools.unknown_tool_note`), in the
  workspace, research and MCP toolboxes alike.
- `edit_lint.py`: the stack profile's lint command after an edit, in the
  sandbox, its result shown to the model in the same turn.
- `test_runs.py`: what `workspace__run_tests` may run, per folder, installing
  first in an isolated sandbox, and the one `workspace.execute` request every
  run goes through. The delivery's own test run is the evidence.
- `test_commands.py`: what a narrower `workspace__run_tests` command runs.
  Leading `VAR=value` assignments are set aside on both sides before the
  wrapper and the runner are compared; the folder command's own come back and
  the requested ones are never applied. A leading `cd <folder> &&` for the test
  folder is dropped (with no `folder` argument it names the folder), a missing
  wrapper is put back, `pytest` / `python -m pytest` / `python3 -m pytest` run
  as the folder's spelling, and a trailing `| tail -n N`, `2>&1` or `; echo
  ...$?` is dropped: it only changes what is printed, the reply gives the exit
  status and the output's end, and refusing would run the whole suite. What
  is left must hold no shell operator. Each change is said before the output
  (`developer_tool_replies.tests_text`); anything else runs the folder's
  command, with the reason.
- `check_scripts.py`: `workspace__run_tests` with exactly `python3
  scripts/<name>.py [args]` (or `python`) runs that check at the project root,
  in the sandbox and under the daemon's command policy, when the script is
  committed on the default branch (`workspace.git.read_at`) and its bytes on
  disk are those bytes (the sha256 computed here against the daemon's
  `workspace.hash`). A script this task edited, moved or delivered over is not
  run: a check the task rewrote is not evidence about it. The arguments take
  plain characters only (no shell operator, quote, variable, glob, `..` or
  absolute path), so no shell reads more than one simple command. Anything
  else keeps the substitution: the folder's test command runs and the reply
  says why. A `poetry run` / `uv run` / `pipenv run` wrapper, a
  `../scripts/<name>.py` that resolves to the root's `scripts/` from the call's
  `folder`, and the same trailing noise are read as that command, each said
  in the reply. The observation carries `check_script` (path, commit, both
  sha256, or `refused` and why).
- `workspace_git.py`: `workspace__git`, read-only git with a fixed set of
  operations and no free arguments: `status` (porcelain, bounded), `changed`
  (the files this task changed against the company's base branch, committed
  or not, untracked included), `diff` (one path against that base, bounded),
  `log` (the last 20 commits at most) and `show_base` (one path as the base's
  head has it, `workspace.git.read_at`, its sha256 computed here). The first
  four are the daemon's views on `workspace.git.diff` (`vscode-daemon/git_view.py`,
  PROTOCOL.md §5.6). Each operation is offered only when every daemon heard
  announces what it needs (the `view` feature, or `workspace.git.read_at`);
  none announced, no tool. A path that is absolute, has `..`, or names VCS
  metadata or a file that may hold secrets is refused here and by the daemon;
  patches and files are redacted like reads. Each call is an observation with
  `read_only` true, `git_operation`, and the base and merge base the daemon
  used. It takes any toolbox with `requester` and `workspace_root`, so
  another read-only toolbox can offer it.
- `tool_history.py`: older rounds' results become one line from their
  observation; the evidence keeps them whole. A read still the latest view of
  its lines (no later edit, move or delete of the file, no later read covering
  them) stays whole while all the results fit `TOOL_HISTORY_MAX_CHARS`; past
  it the oldest reads go, each line naming the path, the lines and the
  `workspace__read` call that shows them again (issue #766).

### The repository map and the code graph

- `repo_map.py`: the daemon's `workspace.map`, definitions ranked by use, in
  the Developer's, QA's and the Architect's prompts.
- `code_graph.py`: `## Related code` around a task's seeds, from the stored
  graph or the daemon's `workspace.related`, with recent runs that touched
  them. Design: [`CODE_GRAPH.md`](../archive/design/CODE_GRAPH.md).
- `code_graph_store.py`, `code_graph_neo4j.py`: the stored graph (Postgres or
  Neo4j, `CODE_GRAPH_STORE`), synced in the background from `workspace.graph`
  for the project folder only. Edges from the approved architecture are
  claims; `verified` edges are observations.
- `code_level.py`: the code level of the documentation pack, read from the
  stored graph only.

## See also

- Settings (`MCP_*`, `DEVELOPER_TOOLS_ENABLED`, `REPO_MAP_MAX_CHARS`, `CODE_GRAPH_*`, `EDIT_LINT_*`, `TOOL_HISTORY_KEEP_ROUNDS`, `TOOL_HISTORY_MAX_CHARS`, `RESEARCH_*`, `GROUNDING_*`): [configuration](../CONFIGURATION.md).
- The daemon side (`vscode-daemon/workspace_read.py`, `workspace_edit.py`, `repo_map.py`, `code_graph.py`, `signatures.py`): [workspace](workspace.md).
- The evidence side (`context_read`, `grounding`, `sources`, `risk`): [evidence](evidence.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-7-tools).
