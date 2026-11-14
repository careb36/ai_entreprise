# The code knowledge graph (issue #438)

A task's context should be the code it will touch and the code that depends
on it, chosen from the project's structure rather than by the model's
guesses. This note records what the first slice builds, what it leaves for
later and what was studied. The modules are described in
[tools](../contexts/tools.md), [workspace](../contexts/workspace.md),
[evidence](../contexts/evidence.md) and [loop](../contexts/loop.md).

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

## Later slices

- **Persisting the graph.** Store files, definitions and their edges per
  project and commit: Neo4j when `NEO4J_ENABLED` (the knowledge graph the
  platform context already reads), else Postgres tables. Updated from the
  daemon's per-file sha256, so only changed files are parsed and written.
- **Edges as stored data.** `implements` (a module or requirement to the
  files that realise it, from the architecture and the files its tasks
  wrote), `depends_on` (between modules, from the architecture, checked
  against the file edges) and `verified` (a file to the runs and acceptance
  tests that observed it, from verifications). Each edge keeps its source,
  so an edge the architecture claims and one the parse observed never share
  a field.
- **Measuring.** Whether the section helps is measured, not assumed: the
  bench's reference specifications (#316) with and without it, and the
  `prompt_sections` measure on each run's evidence (#443) for its weight and
  how often the budget cuts it.

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
