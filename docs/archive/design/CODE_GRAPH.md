# The code knowledge graph (issue #438)

A task's context should be the code it will touch and the code that depends
on it, chosen from the project's structure rather than by the model's
guesses. This note records what the first two slices build, what they leave for
later and what was studied. The modules are described in
[tools](../../contexts/tools.md), [workspace](../../contexts/workspace.md),
[evidence](../../contexts/evidence.md) and [loop](../../contexts/loop.md).

## Slice 1: computed on demand, no new storage

- **Seeds** (`services/loop/worker/related.py`). A task starts from the
  `path` of every approved-architecture module that covers one of its
  requirements, and, on rework, from the files the daemon reports its own
  earlier runs wrote. Both come from records the factory already keeps: the
  approved architecture (a person approved it) and `workspace_writes` (the
  daemon's observation).
- **The graph** (`vscode-daemon/code_graph.py`, `workspace.related`). The
  daemon walks the repository map's file graph from the seeds, one or two
  hops in both directions. It reuses the map's candidate files and guards,
  its parsers and its edge rule (`repo_map.reference_edges`, factored out of
  the ranking so both use the same one), and its parse cache keyed by each
  file's sha256: the graph is incremental by file for free, and a project
  the map has just read is not parsed again. The reply holds paths, names
  and hashes, never contents.
- **What touched these files** (`services/evidence/touched.py`). The latest
  task-worker runs whose daemon report wrote any of the reached files, with
  the verdict the Verifier recorded. A read of observations, never a new
  verdict.
- **The prompt** (`services/tools/code_graph.py`). A `## Related code`
  section after `## Repository`, priority 3 in the prompt budget (#443), cut
  at `CODE_GRAPH_MAX_CHARS`. Without seeds, or without a daemon that answers,
  the prompt is byte-identical to what it was.

It is context, never evidence. The graph is a parse of the files as they are
(a name used is not a call proven; a name defined in more than
`MAP_MAX_DEFINERS` files gives no edge), and a past run's verdict is about
that run's delivery. The positioning rules hold: nothing here is a claim
turned into a fact, and the Verifier does not read the section.

## Slice 2: the graph stored, keyed by sha256

- **The report** (`vscode-daemon/code_graph.py`, `workspace.graph`). The
  backend sends the sha256 it holds per file; the daemon reads the same
  candidate files as the map, and for each file whose sha256 differs sends
  its definitions (name, line, depth) and the names it uses (name, count),
  parsed with the map's parsers and parse cache. A file whose sha256 the
  backend holds is neither parsed nor sent. Known files that are no longer
  candidates are `removed` (none when the file cap cut the listing, since a
  file past it may still be there). The reply is paged (`max_files`, 512 KB,
  the deadline) with `more`. Paths, names, lines and hashes only.
- **The store** (`services/tools/code_graph_store.py`, `models/code_graph.py`).
  Per company and project folder: `code_graph_files` (path, the daemon's
  sha256, parse status, when it was observed), `code_graph_definitions` and
  `code_graph_references`, owned by their file. A reported file replaces its
  own rows and nothing else; a removed one is deleted. The `references` edge
  is not stored as a file-to-file row: it is resolved when it is read (a
  file that uses a name, the files that define it, with the daemon's
  `MAP_MAX_DEFINERS` rule), so replacing one file can never leave an edge
  pointing at another file's old definitions. This is SCIP's shape (a
  document's occurrences belong to the document) without resolving a name to
  one symbol yet.
- **Edges as stored data** (`code_graph_edges`). Each row names its `source`
  and the record it was copied from (`source_ref`), and an edge from one
  source is never merged with one from another:
  - `implements`, a module to each requirement it covers and to its path,
    and `depends_on`, a module to the modules it names: source
    `architecture`, a claim a person approved;
  - `verified`, a file to each run whose latest verdict is `verified` and
    whose daemon report wrote it, with the sha256 that report holds (so a
    reader can tell whether the verification was of the content stored now):
    source `verification`, an observation copied from the Verifier's records.
  The task's own `depends_on` between tasks and `touched` (a run to the
  files it changed, already readable from `evidence/touched.py`) are not
  stored yet.
- **When it is updated.** Before a Developer's `## Related code` section,
  the task worker starts `code_graph.persist_soon` (`services/loop/worker/related.py`):
  in a background thread, one sync of the company's project folder with the
  daemon (only changed files travel) and the edges replaced from the current
  records. The task never waits for it: in the end-to-end run's step 9d, on
  a snapshot of this repository (about 1,450 files, 106,000 definition and
  reference rows), a sync on the task's path held the first task 2 min 17 s
  and each rework, in its own worktree, about a minute, and the sprint
  missed the harness's wait (PR #681). At most one sync per company and
  folder runs in a process; none starts a page past `SYNC_BUDGET_SECONDS`
  (the next one goes on from what was stored); while one is part way
  through (pages stored, more to come) the section does not read the
  half-written graph. The section reads the stored graph and says it is the
  graph as the daemon last reported it. With nothing
  stored for the folder, no seed in it, `CODE_GRAPH_STORE=off`, a daemon
  without `workspace.graph` or any failure, it asks `workspace.related` on
  demand exactly as in slice 1.
- **Where it lives.** Postgres by default. Neo4j is opt-in:
  `CODE_GRAPH_STORE=neo4j` while `NEO4J_ENABLED` is on
  (`services/tools/code_graph_neo4j.py`, the same primitives; files and
  symbols as nodes, `DEFINES` and `REFERENCES` relationships, the record
  edges as `CODE_EDGE` relationships with their source). This amends the
  earlier plan ("Neo4j when `NEO4J_ENABLED`"): the knowledge graph is on by
  default for the engrams, and turning it on should not silently move where
  the code graph is kept; the Neo4j path is exercised only through a fake
  driver so far.
- **The keys.** A graph is per company and folder. Only the company's
  project folder is stored: a task's worktree is its own folder, read by
  nobody once merged, and storing it would copy the whole project's rows
  for each task, so its section is computed on demand. The sha256 is the daemon's, computed on the machine that
  holds the files: the daemon is the observer, never the party being
  recorded (rule 6 of `docs/POSITIONING.md`). Nothing a producer reports
  about its own files is stored.

## Readers of the stored graph

- **The documentation pack's C4 code level** (issue #693,
  `services/tools/code_level.py`, read by
  `planning/project_docs/collect_code`): per architecture component, the
  stored files under the module's path (the architecture's claim), the
  names they define and the references among them (observed, with the
  daemon's sha256), in separate fields. It reads the store only; with
  nothing stored the pack says "not observed". See
  [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md).

## Later slices

- **Measuring.** Whether the section helps is measured, not assumed: the
  bench's reference specifications (#316) with and without it, and the
  `prompt_sections` measure on each run's evidence (#443) for its weight and
  how often the budget cuts it. Then decide whether it replaces part of the
  generic map.
- **More edges.** `depends_on` between tasks (`task_relations`), `touched`
  (a run to the files the daemon observed it change) and `refuted` (a
  verification to a run), and the architecture's `depends_on` checked
  against the file edges, each still with its own source.
- **Sharing parses.** A worktree's unchanged files could point at the
  project's rows by sha256, so a worktree's section could read the store
  too, instead of being computed on demand.

## What was studied

- **Aider's repository map** (Apache-2.0): the file graph of references to
  definitions, ranked with PageRank. Already followed by the daemon's
  `repo_map.py`; this slice walks the same graph instead of ranking it.
- **Sourcegraph SCIP**: an index of symbols and their occurrences
  (definition or reference) per document. Its symbol/occurrence model is the
  shape a persisted graph would take; this slice keeps names per edge and
  does not yet resolve a name to one symbol.
- **OpenHands and SWE-agent**: retrieval of the files around a task before
  and during editing (the agent's own searches, and localisation of the
  files to change). Here the starting points come from the approved
  architecture and recorded writes, so the model starts from them and still
  reads through its tools.

No code was taken from any of them.
