# Project management documentation as a deliverable (issue #530)

Clients, auditors and PMOs ask an IT project for its management
documentation: architecture diagrams, a charter, a scope statement, a work
breakdown, a schedule, a responsibility matrix, a risk register, lessons
learned. La Fabrique already records almost everything those documents state.
This note designs **documentation packs**: per project, a versioned set of
documents compiled from the records, with a model writing only the prose that
has no structured source. Phase 1 (this note's scope) is built; the later
phases are listed at the end. The modules are described in
[planning](../contexts/planning.md) and the page in
[frontend](../contexts/frontend.md).

## The evidence rule, applied to documents

`docs/POSITIONING.md` holds here as everywhere:

1. **Every statement cites its source.** A row or sentence ends with `[S1]`,
   `[S2]`…; the document closes with the list: the record's kind and id, and a
   digest the backend computed or observed (the canonical JSON of a
   specification, the chain hash of a task event, a run's artifact digest, the
   commit the daemon reported for an integration). Never a hash a producer
   handed in.
2. **Generated prose is a claim.** The one prose section of phase 1 (the
   charter's background) is quoted under "**Claim.** Written by the TechWriter
   … not checked against them and not a source for any other statement", and
   listed in the document's `claims`. It never shares a field with a statement
   built from a record, and the drift digest ignores it.
3. **No record, no content.** A section with nothing behind it says
   "_No source recorded: …_" and is listed in the document's `missing`. Nothing
   is inferred to fill it: the scope statement says the specification records
   no exclusions rather than inventing an out-of-scope list.
4. **Agent text quoted is what it said.** A block reason or a retrospective is
   shown as "Reason as recorded by QA: …" or "Reported (a claim by the party
   recorded)", never as a finding.
5. **Stale is flagged.** Each document stores the digest of exactly the parts
   of the records it read (`input_sha256`); the lint recomputes it from the
   current records and a mismatch blocks the Owner's approval.
6. **No personal data in the pack.** Who approved or drafted is reduced to a
   role ("PO", "a person"), and people are counted by company role, never
   named: the pack is written to a repository that may be shared more widely
   than La Fabrique.

## The documents and where each field comes from

The documents are structured after the **PMBOK Guide, 8th edition (Project
Management Institute, 2025)**: each sits in one of its seven performance
domains (Governance, Scope including quality, Schedule, Finance, Stakeholders
including communications, Resources, Risk) and serves one of its five focus
areas (Initiating, Planning, Executing, Monitoring and Controlling, Closing).
The placement is ours (`layout.PLACEMENT`), worked out from a structural
summary the Owner wrote in her own words; the book itself was not read by any
model, and no text, definition or table of it is reproduced. Only the
framework and edition are named. The diagrams follow the C4 model (Simon
Brown, c4model.com, CC BY 4.0).

| Domain | Focus area | Document (file) | Built from |
|---|---|---|---|
| Governance | Initiating | Project charter (`charter.md`) | specification; four person sections |
| Governance | Executing | Issue log (`issues.md`) | task events, verifications |
| Governance | Executing | Lessons learned register (`lessons.md`) | retrospectives, procedures |
| Governance | Monitoring and Controlling | Change log (`changes.md`) | specification and architecture versions, carry-over events |
| Governance | Monitoring and Controlling | Check results by domain (`checks.md`) | all of the above |
| Scope | Planning | Scope statement (`scope.md`) | specification, tasks |
| Scope | Planning | Work breakdown structure (`wbs.md`) | specification, tasks |
| Scope | Monitoring and Controlling | Requirements traceability matrix (`traceability.md`) | specification, tasks, verifications, integrations |
| Schedule | Planning | Schedule and milestones (`schedule.md`) | approvals, sprints, integrations |
| Resources | Planning | Responsibility assignment, RACI (`raci.md`) | roster, members, tasks, factory rules |
| Risk | Planning | Risk register (`risks.md`) | verifications, holds; one person section |
| (C4) | - | Architecture diagrams (`c4.md`) | architecture, stack discovery |

Finance and Stakeholders have no document of their own in phase 1: what they
need (a budget, external stakeholders) is not recorded, and the check results
say so per domain. The book's minimal adaptive set that phase 1 does not yet
produce (status and work performance reports, the release and iteration plan
as its own document, the final report, cost baselines) is listed under the
next phases.

### Fields only a person can fill

The book's appendix on artificial intelligence places machine output as
automation or as a draft a person reviews, with a person accountable for every
decision; `docs/POSITIONING.md` says the same with claims and observations.
So the fields La Fabrique has no record for and an agent must not decide are
rendered as **templates for a person** ("**For a person to complete.** …
no agent fills it in"), listed in each document's `persons`, and counted in
the index:

| Document | Person sections |
|---|---|
| Charter | Business justification, expected benefits, budget envelope, sponsor |
| Risk register | Risk appetite and project-level risks (market, staffing, budget) |
| Check results | Finance (within budget), Stakeholders (outside the company) |

Phase 1 renders the templates; letting an Owner or Manager fill them, with the
author recorded in the audit, is the first item of phase 2. The TechWriter's
prose is never placed in a person section (a test holds it).

### Check results per domain

Each domain of the book ends with target outcomes and a way to confirm each.
`checks.py` turns the ones La Fabrique can confirm from its records into
computed checks, in our own wording, with four honest results: **met** /
**not met** (the records answer), **measured** (a number whose threshold is
the Owner's, such as requirements stability), **not computable** (nothing to
measure yet) and **person** (a judgement or data not held). Phase 1's set:

| Domain | Outcome (our wording) | How it is computed |
|---|---|---|
| Governance | Lessons are learned during the project | each completed sprint has a retrospective message |
| Governance | The plan's decisions are taken by a person | specification and architecture approved |
| Scope | Requirements are clearly understood | every requirement has criteria; no open question |
| Scope | Requirements stability is measured | specification versions; requirement ids added and removed since the first (measured) |
| Scope | All the required work is planned | every requirement covered by a task |
| Schedule | Schedule documentation is complete | every sprint dated, every task assigned |
| Finance | Delivers within its budget | person |
| Stakeholders | The people who decide are identified | an Owner recorded |
| Stakeholders | Outside stakeholders are engaged | person |
| Resources | Work goes to the team that was formed | assigned tasks go to hired roles |
| Risk | The risk process runs on every delivery | every verified delivery carries a risk tier |

`CHECKS` is a dictionary of lists per domain: a later phase adds a check by
appending `(outcome, how, function)`.

The snapshot (`services/planning/project_docs/collect.py`) is the plain data
every builder reads; "spec" is the company's newest approved specification
(else its newest), "arch" that specification's newest approved architecture
(else its newest). The source tables follow, in the order of the domains.

### Project charter (`charter.md`; Governance, Initiating; inputs: project, specification)

| Field | Source | Kind |
|---|---|---|
| Name | spec `title` | specification ref |
| Authorised | spec `status`, `approved_at` (approver withheld) | specification ref |
| Purpose | spec `summary` | specification ref |
| Background | TechWriter, from the spec's title, summary and requirement titles | **claim**, bounded to 1,200 characters |
| Objectives | spec `requirements[].id/title` | requirement ref (digest of the requirement) |
| Success measures | spec `quality[].id/attribute/target` | requirement ref |
| Constraints | spec `constraints` | specification ref |
| Business justification, expected benefits, budget envelope, sponsor | none recorded | **person** template |

### Issue log (`issues.md`; Governance, Executing; inputs: sprints, events, verifications)

| Row | Source | Kind |
|---|---|---|
| Blocked task | `blocked` task event, the reason quoted; status from the task now (open, back in work, resolved) | event ref with its chain hash |
| Escalation | `escalated` task event; "decided by the ScrumMaster" when a later `decided` event exists | event ref |
| Failed verification | `Verification.verdict` `refuted` or `unverifiable` | verification ref with the artifact digest |

### Change log (`changes.md`; Governance, Monitoring and Controlling; inputs: history, events)

| Row | Source | Kind |
|---|---|---|
| Revision | a specification or architecture version with `supersedes_id`; for a specification, the requirement ids added and removed against the version it revises; approved date or status | specification / architecture ref |
| Carried task | the `carried_over` event with direction `to` (one row per carry; the copy's `from` event is not repeated) | event ref |

In adaptive work most change lives in the backlog; the document says so and
points to the WBS.

### Scope statement (`scope.md`; Scope, Planning; inputs: specification, sprints)

| Field | Source | Kind |
|---|---|---|
| In scope, per requirement | spec `requirements[]` with `description` | requirement ref |
| Acceptance criteria | `criteria[].given/when/then` | requirement ref |
| Delivery | tasks whose `covers` names the requirement or one of its criteria, with their status | task ref (digest of the task) |
| Quality attributes, constraints | spec `quality`, `constraints` | requirement / specification ref |
| Out of scope | none recorded | "No source recorded" line |
| Open questions | spec `questions` without an answer | specification ref |

### Work breakdown structure (`wbs.md`; Scope, Planning; inputs: specification, sprints)

| Field | Source | Kind |
|---|---|---|
| Level 1 | spec `title` | specification ref |
| Work packages (1.n) | spec `requirements[]`: the record has no epics, so requirements play that part | requirement ref |
| Tasks (1.n.m) | `BacklogItem` with `covers`, sprint, status, assignee | task ref |
| Work not mapped | tasks with no `covers` | task ref |

### Requirements traceability matrix (`traceability.md`; Scope, Monitoring and Controlling; inputs: specification, sprints, verifications, integrations)

| Column | Source | Kind |
|---|---|---|
| Requirement | spec `requirements[].id` | requirement ref |
| Criteria covered | the task's `covers` within that requirement | task ref |
| Task, status | `BacklogItem` | task ref |
| Latest verification | the newest `Verification` reached through the task's `verified` event, its verdict | verification ref with the artifact digest |
| Integrated at | the newest `SprintIntegration.tasks[]` entry for the task, its merge (else source) commit | integration ref with the commit |

A requirement no task covers gets a row "none" and is listed in `missing`.
Every link is something La Fabrique observed (a task naming a criterion, the
Verifier's reading, the daemon's report of the merge), which is a stronger
matrix than one kept by hand.

### Schedule and milestones (`schedule.md`; Schedule, Planning; inputs: specification, architecture, sprints, integrations)

| Field | Source | Kind |
|---|---|---|
| Specification / architecture approved | `approved_at`, `status` | specification / architecture ref |
| Sprint integrated | `SprintIntegration.status`, `tested_commit` or `commit`, `created_at` | integration ref with the commit |
| Sprints | `Sprint.sprint_name/goal/status/start_date`; first and last `TaskEvent.created_at`; done / total tasks | sprint ref (digest of the sprint) |
| Planned end dates | none recorded | said in the text |

### Responsibility assignment, RACI (`raci.md`; Resources, Planning; inputs: specification, architecture, sprints, roles, members)

| Field | Source | Kind |
|---|---|---|
| Roles hired | `Agent.role`, canonical (`services/contracts/roles.py`) | membership ref (digest of the list) |
| People | `CompanyMember.human_role`, counted | membership ref |
| Specification / architecture: R | `drafted_by` reduced to the role or "a person" | specification / architecture ref |
| A for those | only a Manager approves | factory rule, `docs/contexts/planning.md` |
| Tasks per role: R | `BacklogItem.assignee`, canonical; "(not hired)" when the role is not on the roster | task refs (five shown, the rest counted) |
| A, C, I for tasks; verification; retrospective | how the loop routes work | factory rule, `docs/SPRINT_LOOP.md` |
| Documentation pack | the TechWriter compiles, an Owner approves | factory rule, this note |

Consulted and informed are not recorded per deliverable; the document says so.

### Risk register (`risks.md`; Risk, Planning; inputs: sprints, events, verifications)

| Row | Source | Kind |
|---|---|---|
| Change risk | the Verifier's `risk_tier` check with tier `high` (`Verification.checks`), the task through its `verified` event | verification ref with the run's artifact digest |
| Acceptance hold | `risk_hold` task event | event ref with its chain hash |
| Status | the task's current status | task record |
| Risk appetite and project-level risks | none recorded | **person** template |

What already went wrong (a block, an escalation) is an issue, in the issue
log, not a risk. Risks nobody recorded are not listed. A model-predicted risk
would be a claim and has no place in this register (a later phase may add a
separate, marked section).

### Lessons learned register (`lessons.md`; Governance, Executing; inputs: sprints, retrospectives, procedures)

| Field | Source | Kind |
|---|---|---|
| Reported | the ScrumMaster's `retrospective` message (`AgentMessage`) | message ref, **claim** |
| Procedures after it | `Procedure` with `source_sprint_id`, its status | procedure ref |
| Measured result | `Procedure.outcome.result` (kept / reverted), from the board's numbers | procedure ref |

### Architecture diagrams, C4 (`c4.md`; inputs: architecture, stack)

| Level | Elements | Source |
|---|---|---|
| Context | the system (arch `title`, `overview`); "Users of X" for an `http`, `cli` or `event` interface no module consumes; databases and infrastructure | architecture, interface, discovery refs |
| Container | each module (the record does not say which are deployed apart; the note says so), databases and infrastructure inside the boundary; `depends_on` and interface consumers as lines | module, interface, discovery refs |
| Component | per module, its core and the interfaces it provides; consumers as lines | module, interface refs |
| Code | not compiled in phase 1 | "No source recorded" line |

Each diagram carries `nodes`, `edges`, `groups`, `notes`, its Mermaid source
and `input_sha256`; the document carries the three Mermaid blocks with an
element table each.

### The index (`README.md`)

The documents ordered by domain, with their focus area, file, the number of
sources cited, the sections with no record, the sections for a person and the
prose by a model; how to read the brackets; the frameworks credited with the
edition.

## Decisions

### Diagram format: Mermaid source, drawn in the app from the same data

| | Structurizr DSL | PlantUML (C4-PlantUML) | Mermaid |
|---|---|---|---|
| Renders in the browser without a server | needs Structurizr's renderer or an export | needs a server-side Java renderer (or a public server, which leaks the diagram) | yes (JavaScript) |
| Renders where the pack is written | no host renders it inline | no | GitHub, GitLab, Azure DevOps and Bitbucket render `mermaid` fences in Markdown |
| Text in the repository, diffable | yes | yes | yes |
| C4 vocabulary | native | native | flowchart (stable) or C4 syntax (experimental) |

**Mermaid** wins: the pack's main destination is `docs/project/` in the
project's repository, where the code hosts render it with no JVM and no
server. The source is a `flowchart TB` with subgraphs (the stable syntax),
generated from the diagram's data with our own ids and sanitized labels.

In the app, the page does **not** run Mermaid: it draws the same data as SVG
(`frontend/src/lib/c4Layout.ts`, `components/C4DiagramView.tsx`), with labels
as React text, and shows the Mermaid source as text on request. That keeps a
diagram library (and its HTML sanitizer) out of the bundle and makes script
injection through a label impossible by construction rather than by
configuration. Should the in-app picture need Mermaid's layouts later, it can
be lazy-loaded with `securityLevel: 'strict'`; the data does not change.

### Where packs live

- **The database**, one `DocumentationPack` row per compiled version
  (migration `0023`): the documents (Markdown, sources, missing, claims,
  inputs, input digest), the diagrams, the digest of all inputs, the status
  (`draft`, `approved`, `superseded`), the approval and the files written.
- **The repository**, `docs/project/*.md`, written only from an approved pack
  through the daemon's `workspace.generate`, the path
  `arch_author.write_document` already uses; the sha256 the daemon read back
  per file is kept on the pack (`written_files`), as `written_sha256` is on an
  architecture.

### Who compiles: the TechWriter, no new role

The compiling is deterministic code; the one model call is the charter's
background, asked of the company's TechWriter (its model and system prompt,
`company_models.resolve`). A new role would touch the role catalogue, the
model defaults and the team selection for one prose call. The RACI marks the
TechWriter "(not hired)" when the company has none; compiling works anyway,
with the deployment's default model for the role.

### Approval by the Owner, with the existing pattern

`document_approval.approve_pack` follows `approve_specification`: the
deterministic check must not block (here the drift lint), the previously
approved pack becomes superseded, the caller commits. The approval and each write enter the company's audit chain (`documentation_pack_approved`, `documentation_pack_written`, attributed to the person, with the pack's input digest and the files' sha256). Only an Owner approves
(the pack speaks for the company to its clients and auditors); a Manager
compiles and writes; a Viewer reads. A new compile supersedes older drafts.

### Context placement

`backend/app/services/planning/project_docs/`, in **planning** ("it writes
plans and documents; it never runs a task"). Planning may import contracts,
platform, tasks, models, evidence, workspace and tools; the pack reads the
other records (retrospectives, procedures, integrations, verifications)
through their ORM models, which sit outside `services/`, so it imports neither
learning nor release and adds no edge. The builders (`refs`, `sanitize`,
`layout`, `c4`, `scope_docs`, `control_docs`, `log_docs`, `checks`) import
only each other and `contracts` (roles, verdicts): plain data in, Markdown
out. `collect` reads the database;
`pack` compiles, lints, asks for the prose and writes through the daemon.
`scripts/check_architecture.py` reports 0 allowlisted edges.

## Threat model

Status: draft by the implementing agent, to be reviewed by a person · Issue
#530 · SDL practice 3. Boundaries by id from
`.claude/skills/threat-model/reference.md`.

**What is built.** A Manager asks the backend to compile a pack; the backend
reads the company's records (all written, in part, by agents), optionally asks
the TechWriter for a paragraph through the LLM proxy, stores Markdown and
diagram data, renders them for Viewers in the browser, and on a Manager's
request writes the approved Markdown into the user's repository through NATS
and the daemon.

**Assets.** The browser session of anyone who opens the page (B1); the user's
repository and disk (B3); the integrity of the evidence the pack cites; the
privacy of the company's members; the model budget.

```
Agent output (B5) ──> records ──> collect ──> builders ──> DocumentationPack ──B1──> browser (React, SVG)
                                       └── TechWriter prompt ──B4──> LLM proxy ──> prose (B5) ──┘
DocumentationPack (approved) ──B2──> NATS workspace.generate ──> daemon ──B3──> docs/project/*.md
```

| ID | Boundary | STRIDE | Threat | L | I | Severity | Mitigation |
|---|---|---|---|---|---|---|---|
| T1 | B5→B1 | T, E | A task title, module name, block reason or the TechWriter's prose carries HTML, a `javascript:` link or an image and runs or phones home in a viewer's browser | M | H | High | New: `sanitize.md_inline` escapes `<>&`, brackets, pipes, backticks, emphasis and autolinks; one line per value; ids filtered to id characters. Frontend: `react-markdown` with `skipHtml`, no `img`, `urlTransform` keeping only `#` and http(s), links `noopener`. Tests: `test_project_docs.py` (malicious snapshot), `PackMarkdown.test.tsx` |
| T2 | B5→B1 | T, E | A label breaks the Mermaid syntax (`"]`, `;`, newline, `click`, `%%{init}%%`) and injects a directive or a callback where the source is rendered (a code host, a future in-app Mermaid) | M | M | Medium | New: `sanitize.mermaid_label` keeps letters, digits, spaces and `.,/+-'()@`; node ids are ours (`n1`…). The app draws SVG from data and never runs the source. Tests: every source line matches the allowed grammar |
| T3 | B5→B3 | T | Record text written to the repository becomes structure a later tool trusts (a heading, a table row, a fake source line) | L | M | Low | Same escaping as T1; the sources list is built by the backend; files go only to `docs/project/` with fixed names, through `workspace.generate` (the daemon's path checks apply) |
| T4 | B1 | E | A Viewer or Manager approves; a Viewer compiles or writes; another company reads a pack | M | M | Medium | `require_company_role` on every route (Viewer read, Manager compile and write, Owner approve); pack ids resolve to their company. Tests: `test_documentation_router.py` roles and outsiders |
| T5 | B5 | R, T | A stale pack is approved and presented as current, or prose is mistaken for evidence | M | M | Medium | Input digests per document and diagram; approval refused while the lint blocks; prose quoted as a claim, outside the digest; the approval and the write audited with the pack's input digest. Tests: drift blocks approval; prose marked; audit rows written |
| T6 | B1/B3 | I | Members' emails reach a repository shared with outsiders | M | M | Medium | The snapshot holds roles and counts only; `approved_by` stays in the database. Test: the snapshot contains no `@` |
| T7 | B4 | I, D | The TechWriter prompt leaks repository content, or compile is spammed to burn budget | L | L | Low | The prompt carries only the spec's title, summary and up to 30 requirement titles; one bounded call (600 tokens) per compile; Manager only; the proxy's budgets apply |
| T8 | B1 | D | A large company makes compiling or rendering heavy | L | L | Low | Every list in the snapshot capped at 200 rows; text fields cut; thirteen files per write |
| T9 | B2/B3 | S, T | A forged daemon reply reports files written | L | M | Low | Existing: NATS users and subjects (`scripts/nats_config.py`); every file must be reported `written` with a sha256 or the write is a 502 and nothing is recorded |
| T10 | B5 | E, R | An agent decides what only a person may (a budget, a risk appetite) and the pack presents it as the company's position | M | M | Medium | New: those sections are templates no builder fills; the prose call writes only the charter's background, in its own quoted section. Test: the prose never lands in a person section |

**Assume breach.** If the backend's escaping missed a case, the frontend still
drops HTML, images and unsafe links; if the frontend were bypassed (the raw
Markdown endpoint opened directly), it is served as `text/markdown`, which a
browser does not execute.

**Residual risk.** A code host renders the Mermaid fences with its own
renderer and settings; the labels are restricted, but the host's renderer is
outside our control. Accepted by the Owner under `sdl-program`.

**Open questions.** Should an Owner be able to approve a pack with warnings
only, or must every document cite something? Should the pack be signed with
the audit key once approved (phase 4, with export)?

## What was taken from where

- **C4 model** (c4model.com, Simon Brown, CC BY 4.0): the levels and element
  kinds of the diagrams, and their colours' convention.
- **arc42** (arc42.org, CC BY-SA 4.0): the idea that each section says what it
  is built from and stays when empty; no text taken.
- **Kiro (Amazon)**: the spec-driven order, requirements with acceptance
  criteria before design and tasks, is what makes the WBS and the scope
  statement derivable: the pack reads that chain rather than asking a model.
- **PMBOK Guide, 8th edition** (PMI, 2025): the performance domains and focus
  areas the documents are placed in, the artifact names, the idea of a check
  per target outcome and the appendix's position that machine output is a
  draft a person stays accountable for. Taken from a summary in our own words,
  never from the book's text; the book forbids use for AI training and no
  model read it.
- **COBIT** (ISACA) and **ITIL** (AXELOS / PeopleCert): named as later phases;
  nothing taken.

## Extension points and next phases

- A document is a function `snapshot -> dict` (`layout.build`); a new one is a
  builder plus its `inputs` tuple, added to `pack.build_documents`. The lint
  and the write path need no change.
- `refs.ref` accepts any record kind; `collect.snapshot` is where a new kind is
  read.
- Prose is `layout.claim_block`; a new prose section is one more call through
  `pack.write_prose` with its own bounded prompt.

**Phase 2, first:** let an Owner or Manager fill the person sections (an
endpoint per section, the author and time recorded in the audit, the text
kept apart from the compiled fields and cited as the person's statement);
then the status and work performance reports (board state, sprint progress
against plan, holds, integrations: the book's chain of work performance data,
information and report maps onto observation, analysis and claim) and the
release and iteration plan as its own document. Then the final report from a
completed project's integrations, the traceability matrix and the
retrospectives, and more computed checks per domain (backlog rates, a
stakeholder register from members plus a person's external list). After that: communications and quality plans
(phase 3, the quality plan from `docs/STANDARDS.md` and the Verifier's
checks), DOCX/PDF export and organisation templates (phase 4, with a signed
manifest of the pack), the COBIT governance matrix and the ITIL transition set
(phase 5), and bench grading of packs against person-written expectations.
The C4 code level from the daemon's code graph (`workspace.related`) fits
phase 2 when a daemon is connected.
