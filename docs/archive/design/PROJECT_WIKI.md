# The project wiki (issue #445)

Vector memory returns fragments that look like the task; it does not say what
the project knows. The project wiki is compiled memory: a set of Markdown
pages per company, one per module of the approved architecture, one per
important concept, a glossary and the decisions in force, which the agents
update when a sprint closes from the evidence, and which a task's prompt
reads before any vector search. This note records the design, what this
slice builds and leaves, the threat model and what was studied. The modules
are described in [contracts](../../contexts/contracts.md),
[learning](../../contexts/learning.md), [evidence](../../contexts/evidence.md),
[loop](../../contexts/loop.md) and the [frontend](../../contexts/frontend.md); the
worker's steps in [the sprint loop](../../SPRINT_LOOP.md).

The wiki is **what the project knows**. The documentation packs (#530) are
**what is handed over**; a pack may later read wiki pages, never the other way
round.

## A page is a claim

The design rule that shapes everything else (`docs/POSITIONING.md`): the wiki
is context and a claim, never evidence.

- No table holds an observation (`models/wiki.py`). A page's statements cite
  the records that do; the backend resolves each citation against those
  records (`services/learning/wiki_sources.py`) and shows the resolution
  **beside** each statement (`GET /v1/companies/{id}/wiki/{slug}`, `blocks`),
  never folded into the text.
- A citation is never trusted: a run's verdict, a file's observer and a note's
  status are read from the records, not from what the page says.
- A page's links (to tasks, requirements, modules, decisions, runs, notes and
  files) are derived by the backend from the body on every save
  (`wiki.derive_links`, `wiki_links`); a client never sends them (after Plane's
  `PageLog`).
- The wiki never enters an evidence field, a verdict or a gate. The Verifier
  does not read it.

## Storage

Migration `0024` (expect renumbering: other branches add `0024`/`0025`), four
company-scoped tables:

| Table | What it holds |
|---|---|
| `wiki_pages` | `slug` (unique per company), `kind` (`module`, `concept`, `glossary`, `decisions`), `title`, `body` in force, `status` (`current`, `stale`, `proposed`), `version`, `current_version_id`, `proposed_version_id`, `person_locked`, the module's `module_ref`/`module_name`/`module_path`, `lint_reasons`, `linted_at` |
| `wiki_page_versions` | Every change: `number`, `author_kind` (`agent`, `person`), `author` (role or email), `sprint_id`, `title`, `body`, `sources` (the citations as resolved), `findings` (uncited statements), `status` (`current`, `proposed`, `rejected`, `superseded`), who decided and why |
| `wiki_links` | The links of the body in force: `kind`, `target`, `sha256` |
| `wiki_settings` | `write_to_repo` (off by default), who changed it |

## The syntax (language-independent)

**Citations** (`services/contracts/wiki.citations`), in square brackets at the
end of a statement:

| Citation | Source |
|---|---|
| `[run:123]` | An agent run of the company (its latest verdict is read) |
| `[verification:45]` | A verification of the company |
| `[task:12]` | A task of one of the company's sprints |
| `[note:7]` | A sprint board note (#439) of the company |
| `[retro:9]` | The retrospective of the company's completed sprint 9 |
| `[file:src/store.py@3f2a9c1b7d4e]` | A file as a recorded daemon report observed it: a 12–64 hex prefix of the sha256; the path relative, no `..` |
| `[REQ-1.2]`, `[NFR-1]` | A requirement or criterion of the company's specifications |
| `[ADR-3]` | A decision of an approved architecture |
| `[MOD-2]` | A module: a link, not a source |

A statement is a paragraph, a list item, a table row (not the header) or a
fact line; headings and fenced code are not. **An agent's** version with a
citation no record supports is refused (422 through the API; `rejected` in a
compilation), as is a malformed one (`[run:abc]`, `[file:../x@…]`). An agent's
statement without a source citation makes the version a **proposal** (its
`findings` list the lines). A person's version need not cite, but an unknown
citation is still refused.

**Facts** (`wiki.facts`): a fenced block whose info string is `facts`, one
`key: value [citation]` per line. Keys are lower-case English
(`auth.token_ttl`), values are compared with case, whitespace and a trailing
period folded.

````text
```facts
auth.token_ttl: 15 minutes [run:12]
db.engine: PostgreSQL 16 [file:docker-compose.yml@3f2a9c1b7d4e]
```
````

## The project's language

Pages are written in the project's language, English or Spanish: the
company's `doc_language` when it has one. The column is not on `main` yet (#530
may add it); `wiki_templates.language_of` reads it defensively and defaults to
`en`. The compilation request asks for that language; the fixed scaffolding
comes from `services/contracts/wiki_templates.py` (section headings per kind,
the decisions page's lines, the glossary and decisions titles, the lint's
reasons as the API returns them); the UI's own strings are in `en.json` and
`es.json`. The citation and facts syntax is never translated.

## Compilation at sprint close

`services/loop/wiki_close.after_close`, called from `close._finalize_sprint_if_ready`
after the retrospective and the procedures round, at a first close only;
never blocking (every failure is recorded in the summary, audited as
`wiki_compiled` under `sprint:<id>:wiki`).

1. **Inputs, as data** (`gather_inputs`): the sprint's verified runs and the
   files the daemon observed them write, with sha256 and the daemon's diff
   (`services/evidence/touched.verified_writes`), the retrospective's summary,
   the board's active notes, the approved architecture's modules and decisions,
   the specification's requirements.
2. **Plan** (`wiki_compile.plan`), at most `WIKI_COMPILE_MAX_PAGES` (5): module
   pages whose `path` holds a file a verified run wrote (most runs first),
   concept pages that link such a file, then the glossary.
3. **Decisions in force** are written without a model (`decisions_body`): one
   line per ADR of the approved architecture, citing it.
4. **Ask** the compiling role, the company's TechWriter or else its
   ScrumMaster, one request per page (`build_request`): the records, the page
   as it is, the rules of the syntax, the language and its headings. The answer
   is bounded (`WIKI_PAGE_MAX_CHARS`, 6000; longer is rejected).
5. **Save** through `wiki_store.save` as an agent's version: refused on an
   unknown citation; a proposal with an uncited statement or when a person's
   edit is in force; otherwise in force.

## People

A Manager writes a page (`PUT /v1/companies/{id}/wiki/{slug}`): a person's
version, in force at once, which locks the page (`person_locked`). **A person's
edit wins**: the next compilation may only propose (`proposed_version_id`; a
newer proposal supersedes an older one). A Manager accepts a proposal (its
citations are resolved again, the agent's text goes in force and the lock
lifts) or rejects it with a reason. A Viewer reads. Every write is audited
under `company:<id>:wiki`.

## Lint (deterministic, no model)

`services/contracts/wiki_lint.py`, run by `wiki_close.lint_company` at sprint
close and on `POST /v1/companies/{id}/wiki-lint` (Manager, evidence only):

1. `file_missing` / `file_changed`: a cited file the daemon reports missing, or
   whose latest observed sha256 does not start with the cited one. The
   reference is the daemon's `workspace.hash` of the file on disk **without**
   the Git workflow; with it, files live on task branches and the person's
   checkout is not the reference, so the latest verified write is used.
2. `module_changed`: a verified run observed after the page's current version
   changed a file in its module's path or a file it cites.
3. `contradiction`: two pages in force give the same fact key different values;
   both are flagged, each naming the other.

A flagged page is `stale`, its reasons kept and shown in the project's
language; a stale page is **left out of prompts** until it is revised (a new
version in force). The lint never clears `stale` by itself.

## Reading

Every Developer-path prompt (`execute._developer_prompt`) gets a `wiki`
section (`services/loop/worker/wiki_section.py`), priority 4, placed before the
vector memory: the budget (`prompt_budget.fit`) cuts it after the memory and
completed tasks (5) and before the repository and related code (3). It holds
the task's module pages (by the module's name, or its path holding a file the
task touches; `sprint_notes.task_scope`), then concept pages linking one of the
task's files or requirements, then the decisions and the glossary if room is
left, within `WIKI_MAX_CHARS` (3000). Only pages `current`: never `stale` or
`proposed`.

**Search stays the fallback**: the worker asks `wiki_then_memory` instead of
the vector memory; the engram search runs only when no module or concept page
applies. The prompt measure records `wiki: {enabled, chars, memory}` beside
`prompt_sections`.

## Optional write to the repository

With `write_to_repo` on (`PUT /v1/companies/{id}/wiki-settings`, Manager; off
by default), the pages compiled this sprint and in force go to
`docs/wiki/<slug>.md` through the daemon's `workspace.generate`: on the branch
`fabrique/wiki` with the Git workflow (the person's checkout is never touched),
otherwise with the daemon's snapshot. Each file starts with a comment saying it
is a claim. The daemon's reply (paths, sha256 read back, the branch and commit)
is audited as `wiki_written_to_repo`.

## The measure

The issue asks for prompt tokens per task and rework per task, wiki vs vector
memory alone, on the bench (#316). `WIKI_ENABLED=false` is vector memory alone.
The scorecard's prompt object counts `wiki_prompts` and `memory_prompts`
(`scripts/bench/prompt_weight.py`), and `compare.py` shows them beside the
per-section weights and rework per task. How to run it: [bench](../../../bench/README.md).

## Threat model

Status: draft by the implementing agent, to be reviewed by a person · Issue:
#445 · SDL practice 3.

**What is built.** At sprint close the backend asks a model for wiki pages
from recorded evidence; the text is stored, rendered in the browser, put into
later task prompts, and optionally written into the user's repository through
the daemon. A Manager edits pages and decides proposals.

**Assets.** The user's repository (B3); the integrity of later agents'
prompts; company isolation; the evidence (must stay untouched by wiki text);
model spend.

**Flow and boundaries.** Records → backend → LLM proxy → provider (B4) →
model text (B5) → `wiki_pages` → browser (B1) · → later prompts (B4, B5) ·
→ NATS `workspace.generate` → daemon → `docs/wiki/` (B2, B3). A Manager's edit
crosses B1.

| ID | Boundary | STRIDE | Threat | L | I | Severity | Mitigation |
|---|---|---|---|---|---|---|---|
| W1 | B5→B1 | T/E | Agent text with HTML or script runs in the browser (stored XSS) | M | H | High | Existing: React escapes text. New: `WikiMarkdown` skips raw HTML, drops images, allows only http(s)/mailto/anchor URLs, `rel=noopener noreferrer nofollow` (`WikiMarkdown.test.tsx`) |
| W2 | B5→B4 | T/E | Prompt injection: a page tells a later agent to do something (exfiltrate, skip tests, write elsewhere) | M | M | Medium | New: the section header calls pages claims and not instructions; control-looking lines (file-manifest fences with `path=`, `NOTE kind=`, `DECISION`, `END`, conflict markers) are quoted (`wiki.for_prompt`); bounded by `WIKI_MAX_CHARS` and the budget; only `current` pages; uncited agent text is only a proposal. Existing: the Verifier judges every delivery from observations, so a misled agent's work is still checked; the daemon's command policy and sandbox |
| W3 | B5 | T | Laundering: the model invents sources so a claim looks verified | M | M | Medium | New: every citation resolved against the company's records; unknown → refused; the verdict and observer shown are read from the records; uncited → proposal |
| W4 | B5→B3 | T/E | Writing to the repo: a slug or path escapes `docs/wiki/`, or overwrites the person's work | L | H | Medium | New: slugs match `^[a-z0-9][a-z0-9-]{0,79}$`, the path is built by the backend; off by default (Manager); branch `fabrique/wiki` with the Git workflow. Existing: the daemon's path resolution refuses paths outside the workspace; snapshot without the workflow |
| W5 | B1 | E/S | A Viewer or another company's member edits or decides | L | M | Medium | `require_company_role` Manager for writes, Viewer for reads; versions are looked up and their company checked (`test_project_wiki_api.py::test_roles_edit_history_and_decisions`) |
| W6 | B1 | I | Citations reveal another company's records | L | M | Low | Resolution scoped to `company_id` for every kind (`test_citations_resolve_only_against_the_companys_records`) |
| W7 | B4 | D | Compilation cost or time grows without bound | M | L | Low | At most `WIKI_COMPILE_MAX_PAGES` calls per close, bounded inputs (runs, diff excerpts, notes, retrospective), bounded answer; failures recorded, the close goes on |
| W8 | — | R | Who changed a page is unknown | L | L | Low | Every version records its author kind and id; person writes audited; compilations audited with their summary |
| W9 | B5 | T | Wiki text leaks into the evidence | L | H | Medium | No evidence field reads a page; the Verifier and gates do not import the wiki (dependency rule: evidence imports only contracts and platform) |

**Tests that prove the mitigations.** `backend/tests/test_project_wiki.py`
(citation resolution and refusal, company scoping, person precedence, lint
rules, stale pages out of force), `backend/tests/test_project_wiki_loop.py`
(compilation refuses unknown citations), `test_project_wiki_prompt.py`
(prompt excludes stale pages, budget cut), `test_project_wiki_api.py` (API
roles), `frontend/src/components/WikiMarkdown.test.tsx` (no raw HTML,
unsafe URLs and images dropped).

**Residual risks.** A cited, plausible but wrong statement in a page a person
accepted stays a claim that later prompts read; the lint only catches changed
files, changed modules and fact contradictions. A page's text can still
persuade a model; the Verifier is the backstop. Pages written to the repo are
committed text that other tools may read without our labels.

**Open questions.** Should the QA review prompt read the wiki? Should accepting
a proposal require the page's lint to be clean?

## What this slice leaves

- Concept pages are created by people (or a compiled concept page that already
  links a touched file); the compiler does not invent new concepts.
- The lint runs at sprint close and on demand, not before every prompt.
- `doc_language` is read defensively until #530 (or another change) adds it.
- The real bench run needs real models (below).

## What was taken, and from where (design only, no code)

- **Karpathy's "LLM wiki" gist**: a knowledge base the model compiles and
  maintains from raw sources, with lint passes over it. Taken: compiled pages
  per topic instead of raw retrieval, and deterministic lint passes.
- **Plane** (AGPL, commit `290383d7f92fa8995ea2adbd12442220b3b9b525`, design
  only): `PageLog` derives a page's links from its content on every save;
  `PageVersion` keeps each version. Taken: links derived on the backend from
  the body, never sent by the client; a version per change.
- **The Neo4j engrams and retrospectives we keep**: inputs, and the fallback.
