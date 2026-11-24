# The tools context

What a model may call during a task: the bounded tool loop, MCP servers, the
Developer's workspace tools, checked edits, the repository map, the related
code and the research tools. Read this before changing
`backend/app/services/tools/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/tools/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [evidence](evidence.md), [workspace](workspace.md).
- **Interface:** `board_tools`, `code_graph`, `code_level`, `developer_tools`, `mcp_tools`, `registry_lookup`, `research_tools`, `review_tools`, `signature_lookup`, `task_worktree`, `workspace_edits`.
- **Modules:** `board_tools`, `check_scripts`, `code_graph`, `code_graph_neo4j`, `code_graph_store`, `code_level`, `developer_tool_replies`, `developer_tool_schemas`, `developer_tools`, `docs_write_policy`, `edit_lint`, `mcp_client`, `mcp_config`, `mcp_policy`, `mcp_tools`, `registry_lookup`, `repo_map`, `research_http`, `research_registries`, `research_schemas`, `research_tools`, `review_tools`, `signature_lookup`, `task_worktree`, `test_commands`, `test_runs`, `tool_history`, `workspace_docs_build`, `workspace_documents`, `workspace_edits`, `workspace_facts`, `workspace_git`, `workspace_lint`, `workspace_navigation`, `workspace_reads`.
- **HTTP adapters:** `mcp_client` (the MCP transports), `research_http` (`default_http`, raising `ResearchFetchError`). Only these import an HTTP library; they return data and raise named errors.

## How it works

The task worker builds a toolbox for the turn's step
(`developer_tools.toolbox_for`, a `CombinedToolbox` of the workspace, research
and MCP boxes). The step sets the families and the role only narrows them
(`contracts/tool_steps`): an execution turn of any role gets the reads (R),
`run_tests` with the folder's commands and committed scripts (X), the edit,
move and delete tools where edits may go (W, `workspace_edits.allowed`) and
the research tools (S, allowed hosts only); a step without X or W never gets
those tools, whatever the role. The run's `tools_offered` names the tools,
those withheld and why, and the `step` and its `families`. The worker then
runs `mcp_tools.chat_with_tools`: at most a bounded number of rounds, then one
request without tools, so a task always ends in a deliverable. Tools are
offered as `<server>__<tool>`; `workspace`, `research` and `board` are reserved
server names. Each call is recorded on the Verifier's evidence as `tool_calls`.

The loop counts its own rounds: the answer carries `tool_loop`, and the run's
evidence `rounds_used` (rounds in which the model called tools), `rounds_max`
and `rounds_exhausted` (the limit was reached and the last request went
without tools). When the final answer is only a tool call typed out as text
(`contracts.tool_calls.written_as_text`), `tool_loop.not_a_deliverable` says
so, and the worker takes it like an empty answer (`loop/answers.llm_failure`):
no delivery. The worker queues the task again once, with a fresh tool budget
(`loop/worker/intake`), and blocks it with "the model's last answer was a tool
call written as text; the tool budget had run out" if it happens again.

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
- A `run_tests` command that was not run as asked (a shell operator, a `cd`
  to another folder, not the folder's runner) carries `refused` with why on
  its observation, as a check script that may not run does on its
  `check_script`; the retrospective counts both as tool demand.

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
  `research__advisories`, offered to the roles that decide
  (`RESEARCH_ROLES`, `toolbox_for`) and, whatever the role, to a step that
  allows research such as a task's execution (`company_toolbox`). The evidence's
  `sources.sources_of` keeps the observations on the specification and the
  architecture; `sources.check_sources` is the Verifier's `sources` check.
- `research_registries.py`: PyPI, npm, crates.io, the Go proxy, OSV.
- `research_schemas.py`: the tool schemas. `research_http.py`: the fetch.

### The record tools (family E)

- `board_tools.py`: `board__task` (a task's history: status moves, comments,
  attempts, rework requests, dependencies) and `board__evidence` (a run's
  Verifier checks and their observations, the delivered files with the
  sha256 the backend sent and the daemon read back, `tools_offered`, the
  loop's rounds, and the producer's claims). Read-only. The scope (`Scope`)
  is the backend's: the turn's company and, for a task turn, the task and the
  tasks it depends on (the ScrumMaster's triage: the task's sprint; the PO's
  plan: the company's board); a task outside it is refused (`Refused: task N is
  outside the turn's scope`, `refused` on the observation) and the arguments
  never widen it. The reply prints the platform's observations and the
  agents' and people's claims under two headings, never in one entry, is
  redacted like a read (`context_read.redact_secrets`) and cut at the result
  budget. This context may not read the tasks context, so the readers come
  from the caller (`loop/worker/board_record.py`). `attach` puts the tools
  beside a turn's toolbox (a `CombinedToolbox`) without raising its rounds.
  Offered with `BOARD_TOOLS_ENABLED` to the QA review, the Architect's brief
  and an execution turn on a rework, only beside tools the turn already has.

### Answers for the `grounded` check

- `registry_lookup.py`: after a delivery writes a manifest, asks the registry
  about each dependency not yet answered and records `registry_lookups`.
- `signature_lookup.py`: asks the daemon's `workspace.signatures` for the
  signatures the delivered Python calls use. It never fails or holds a delivery.

### The Developer's workspace tools

- `developer_tools.py`: `workspace__list`, `read`, `search`, `find_file`,
  `edit` and `run_tests`, offered by the step (`toolbox_for(..., step=)`,
  `offered_to(role, step)`): every role executing a task gets them.
  `repository_section` puts the tree and the map into the task prompt.
  `read_tools` offers the four read tools alone, to any role.
- `review_tools.py`: QA's read-only toolbox (`list`, `read`, `search`,
  `find_file`, and `workspace__git` with `blame` and
  `workspace__read_document` when announced: `workspace_reads.offer`; never
  `edit`, `move`, `delete`, `lint` or `run_tests`) on the
  `workspace_root` the task's latest verification recorded, for
  `QA_REVIEW_TOOL_ROUNDS` rounds before the verdict. No recorded delivery or
  no daemon: no toolbox. `for_step` gives the same read-only toolbox on the
  task's root (the project without a task) to the steps that only read: the
  Architect's brief, the PO's planning (`PLANNING_TOOL_ROUNDS`; the plan
  from a specification too), the PO writing a specification
  (`PLANNING_TOOL_ROUNDS`), the Architect writing an architecture
  (`ARCHITECT_TOOL_ROUNDS`; both with the research tools beside the reads,
  `planning_reads.author_turn`) and the ScrumMaster's decision
  (`SCRUM_TRIAGE_TOOL_ROUNDS`), through
  `developer_tools.read_tools`, which offers the same family R
  (`workspace_reads.offer`); `None` for a step or role that keeps no reading,
  0 rounds, or no daemon or root. `board_tools.attach` puts another built-in
  box beside such a toolbox (the record tools, the brief's research tools)
  within its rounds.
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
  `log` (the last 20 commits at most), `show_base` (one path as the base's
  head has it, `workspace.git.read_at`, its sha256 computed here) and `blame`
  (one path's line range, at most 200 lines, as groups of lines with the
  commit, its date and its subject that last changed them: when and why, for
  documenting a project). Blame never carries an author's or committer's name
  or e-mail: the daemon drops them while parsing and the groups are rebuilt
  here from the named fields only; subjects are redacted like log lines. The
  first four and `blame` are the daemon's views on `workspace.git.diff`
  (`vscode-daemon/git_view.py`, PROTOCOL.md §5.6). Each operation is offered
  only when every daemon heard announces what it needs (the `view` feature,
  the `blame` feature, or `workspace.git.read_at`);
  none announced, no tool. A path that is absolute, has `..`, or names VCS
  metadata or a file that may hold secrets is refused here and by the daemon;
  patches and files are redacted like reads. Each call is an observation with
  `read_only` true, `git_operation`, and the base and merge base the daemon
  used. It takes any toolbox with `requester` and `workspace_root`, so
  another read-only toolbox can offer it.
- `workspace_navigation.py`: `workspace__definition` (where a name is
  defined: path, line, the repository map's redacted signature, the
  parameters of a top-level Python function or class as
  `workspace.signatures` reads them, and the file's sha256 as the daemon read
  it) and `workspace__references` (the files and line numbers that use a
  name, what a module defines, or that module's name alone). Both ask
  `workspace.related` with its optional `lookup` field
  (`vscode-daemon/code_lookup.py`, PROTOCOL.md §5.7): the same candidate
  files, guards and parse cache as the code graph, no new subject. Offered by
  every `WorkspaceToolbox` (execution turns, the Architect's brief, QA's
  review) only when every daemon heard announces the `lookup` feature; a
  reply without `lookup` (an older daemon) is an error, never "nothing
  found". Paths are checked like `workspace__git`'s and again by the daemon;
  at most 20 files and ten lines a file; redacted. Each call is an
  observation with `lookup`, the files named with their sha256, `more` and
  `partial`.
- `workspace_lint.py`: `workspace__lint`, the stack profile's lint (and its
  `typecheck`, when a profile names one) on the paths the model gives, only
  where the step allows family X, run (`toolbox_for`, `contracts/tool_steps`;
  never a read-only toolbox, and dropped with a role's X deny), off with
  `EDIT_LINT_ENABLED`, and only when the profile names a lint or type check
  command (read once when the toolbox is made; the lint after an edit reuses
  that reading). It runs like `edit_lint`: `workspace.execute` in the
  company's sandbox, under the daemon's command policy, bounded by
  `EDIT_LINT_TIMEOUT_SECONDS`, never a command that may rewrite files
  (`edit_lint.writes_files`). A command ending in ` .` is narrowed to the
  paths, another runs as named and only its lines naming the paths are shown.
  The model gives paths only, never command text: at most 20, inside the tool
  root, plain characters, no leading `-`; files the stack's linter does not
  read are left out. The observation's `lint` records per command the
  command, `narrowed`, the exit status, `available` and the output's sha256.
- `workspace_documents.py`: `workspace__read_document`, the text of a DOCX in
  the project as numbered lines, paged with `start_line` (`workspace.read`
  with `extract: "text"`, `vscode-daemon/document_text.py`, PROTOCOL.md §5.8).
  Offered only when every daemon announces the `document_text` feature; the
  path is checked as `workspace__git` checks it, only `.docx` and `.pdf`. A
  PDF is refused by the daemon for now (no PDF library among its
  dependencies) and the model is told why. The text is redacted and bounded
  like a read's; the observation is a read's (path, the sha256 and size of
  the file's bytes as the daemon read them) plus `format`, so a statement
  about a document can cite the digest the platform read. A reply without
  `extracted` (an older daemon's bytes) is an error, never shown as text.
- `workspace_facts.py`: what the project declares, read statically by the
  daemon (`workspace.related` with `lookup.kind` set to the tool's name,
  `vscode-daemon/project_facts.py`, PROTOCOL.md §5.10), family R:
  `workspace__api_surface` (committed OpenAPI documents with their
  operations, and the routes FastAPI-style decorators declare: method, path,
  function, file:line, an `APIRouter(prefix=…)` of the same module applied,
  `include_router` prefixes listed apart; no server started),
  `workspace__config_vars` (the environment variables the code reads through
  `os.environ`, `os.getenv`, pydantic `BaseSettings` fields, `process.env`
  and `import.meta.env`, with file:line and a literal default, withheld by
  the daemon for a secret-looking name; no `.env` file is ever read) and
  `workspace__dependencies` (the direct dependencies of `pyproject.toml`,
  `requirements*.txt` and `package.json` as declared, with their group; a
  direct reference without its URL). Each is offered only when every daemon
  announces its kind as a feature of `workspace.related`; a reply without it
  (an older daemon) is an error. An optional `path` narrows to a folder or
  file, checked like `workspace__git`'s and again by the daemon. At most 120
  rows shown, redacted. The observation names every file a row came from with
  the sha256 the daemon read, plus `files_scanned` and `scanned_sha256` (a
  digest over every file scanned). Licences are not extracted: the
  dependencies reply points to `research__package`, whose record carries
  `license_by_registry`, the registry's claim.
- `workspace_docs_build.py`: `workspace__docs_build`, family X, offered
  beside the lint (`workspace_reads.offer_runs`) where the step allows runs
  and the stack profile names `docs_build` (a company's, or the one detected
  from `mkdocs.yml`, a Sphinx `conf.py` or `docusaurus.config.*`). No
  argument: it runs that command through `workspace.execute` in the company's
  sandbox, under the daemon's command policy, bounded like a test run. The
  observation's `docs_build` records the command, the exit status,
  `timed_out`, `available`, the first error lines and the output's sha256;
  the reply shows the first errors and the redacted tail. The delivery's own
  build is the evidence (`loop/docs_checks.py`, `evidence/doc_build.py`).
- `docs_write_policy.py`: the docs-only write policy. Where a turn writes is
  the step table's rule (`contracts/tool_steps.write_scope`): the
  `documentation` step, and the TechWriter in any step that writes (a role's
  narrowing within W, `ROLE_WRITE_SCOPE`), write documentation only. `apply`,
  called from `toolbox_for` with the turn's step, sets the toolbox's
  `docs_only` from it; then every write the edit tools ask
  (`workspace_edits._deliverable`: edit, move, delete) must name a file under
  `docs/` or `doc/`, a README, or a `.md`, `.markdown`, `.mdx` or `.rst` file
  (`tool_steps.is_doc_path`); anything else is refused before the daemon is
  asked, with a reply that says what may be written. The files an answer
  delivers in its own blocks follow the same rule in the loop
  (`loop/worker/deliver.docs_scope`).
- `workspace_reads.py`: family R beyond the four base reads, in one place:
  `offer` sets read-only Git (`workspace_git`), a document's text
  (`workspace_documents`), navigation (`workspace_navigation`) and what the
  project declares (`workspace_facts`) on a box, each when the daemons
  announce it; `offer_runs` sets the lint and the docs build where
  `toolbox_for` allows family X, from one reading of the stack profile;
  `schemas` and `run` list and route them all. Called by every toolbox that
  reads: the execution turn's and the read-only ones.
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
