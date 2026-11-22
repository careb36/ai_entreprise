# The tools context

What a model may call during a task: the bounded tool loop, MCP servers, the
Developer's workspace tools, checked edits, the repository map, the related
code and the research tools. Read this before changing
`backend/app/services/tools/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/tools/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [evidence](evidence.md), [workspace](workspace.md).
- **Interface:** `code_graph`, `code_level`, `developer_tools`, `mcp_tools`, `registry_lookup`, `research_tools`, `signature_lookup`, `workspace_edits`.
- **Modules:** `code_graph`, `code_graph_neo4j`, `code_graph_store`, `code_level`, `developer_tool_replies`, `developer_tool_schemas`, `developer_tools`, `edit_lint`, `mcp_client`, `mcp_config`, `mcp_policy`, `mcp_tools`, `registry_lookup`, `repo_map`, `research_http`, `research_registries`, `research_schemas`, `research_tools`, `signature_lookup`, `test_runs`, `tool_history`, `workspace_edits`.
- **HTTP adapters:** `mcp_client` (the MCP transports), `research_http` (`default_http`, raising `ResearchFetchError`). Only these import an HTTP library; they return data and raise named errors.

## How it works

The task worker builds a toolbox for the role (`developer_tools.toolbox_for`,
a `CombinedToolbox` of the workspace, research and MCP boxes) and runs
`mcp_tools.chat_with_tools`: at most a bounded number of rounds, then one
request without tools, so a task always ends in a deliverable. Tools are
offered as `<server>__<tool>`; `workspace` and `research` are reserved server
names. Each call is recorded on the Verifier's evidence as `tool_calls`.

## Rules

- Each call is an observation the backend made (tool, arguments, duration,
  sha256 of the result). The model's reading of a result is a claim. The tools
  never decide a verdict.
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
  checkout. An edit never delivered never stays on disk.
- Lint after an edit never writes: a command with an autofix or write option,
  or one the check cannot read, is refused.
- Research fetches go only to allowed hosts, https unless named explicitly,
  with no credential.

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
- `developer_tool_schemas.py`: the schemas. `developer_tool_replies.py`: what
  the model sees, after SWE-agent's interface (a numbered window per read, a
  count instead of a silent cut).
- `workspace_edits.py`: `workspace__edit`, a line range or one SEARCH/REPLACE
  block, applied by the daemon as a working copy and delivered at the end with
  the answer's files (`tool_edit_files` on the evidence).
- `edit_lint.py`: the stack profile's lint command after an edit, in the
  sandbox, its result shown to the model in the same turn.
- `test_runs.py`: what `workspace__run_tests` may run, per folder, installing
  first in an isolated sandbox. The delivery's own test run is the evidence.
- `tool_history.py`: older rounds' results become one line from their
  observation; the evidence keeps them whole.

### The repository map and the code graph

- `repo_map.py`: the daemon's `workspace.map`, definitions ranked by use, in
  the Developer's, QA's and the Architect's prompts.
- `code_graph.py`: `## Related code` around a task's seeds, from the stored
  graph or the daemon's `workspace.related`, with recent runs that touched
  them. Design: [`CODE_GRAPH.md`](../design/CODE_GRAPH.md).
- `code_graph_store.py`, `code_graph_neo4j.py`: the stored graph (Postgres or
  Neo4j, `CODE_GRAPH_STORE`), synced in the background from `workspace.graph`
  for the project folder only. Edges from the approved architecture are
  claims; `verified` edges are observations.
- `code_level.py`: the code level of the documentation pack, read from the
  stored graph only.

## See also

- Settings (`MCP_*`, `DEVELOPER_TOOLS_ENABLED`, `REPO_MAP_MAX_CHARS`, `CODE_GRAPH_*`, `EDIT_LINT_*`, `TOOL_HISTORY_KEEP_ROUNDS`, `RESEARCH_*`, `GROUNDING_*`): [configuration](../CONFIGURATION.md).
- The daemon side (`vscode-daemon/workspace_read.py`, `workspace_edit.py`, `repo_map.py`, `code_graph.py`, `signatures.py`): [workspace](workspace.md).
- The evidence side (`context_read`, `grounding`, `sources`, `risk`): [evidence](evidence.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-7-tools).
