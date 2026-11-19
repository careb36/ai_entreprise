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
| Governance | Executing | ITIL transition set (`transition.md`, #696) | per release: the integration's tasks, its suite run, publication and local merge, review, the Verifier's readings at integration, undo audit rows, the sprint's hold events; one person section |
| Governance | Closing | Final report (`final_report.md`, phase 2) | specifications, sprints, task events, verifications, effort, model usage, carries, published integrations; three person sections |
| Scope | Planning | Scope statement (`scope.md`) | specification, tasks |
| Scope | Planning | Work breakdown structure (`wbs.md`) | specification, tasks |
| Scope | Planning | Quality management plan (`quality_plan.md`, phase 2) | the backend's standards catalogue, gate checks and risk tiers, specification quality targets, the status report's figures; one person section |
| Scope | Monitoring and Controlling | Requirements traceability matrix (`traceability.md`) | specification, tasks, verifications, integrations |
| Schedule | Planning | Schedule and milestones (`schedule.md`) | approvals, sprints, integrations |
| Schedule | Planning | Release and iteration plan (`release_plan.md`, phase 2) | sprints and their plan, specifications, carries, published integrations; one person section |
| Resources | Planning | Responsibility assignment, RACI (`raci.md`) | roster, members, tasks, factory rules |
| Stakeholders | Initiating | Stakeholder register (`stakeholder_register.md`, #691) | members, teams, approvals of specifications and architectures, roles hired, factory rules; one person section |
| Stakeholders | Planning | Communications management plan (`communications_plan.md`, phase 2) | members, the channels' configuration records, factory rules; two person sections |
| Risk | Planning | Risk register (`risks.md`) | verifications, holds; one person section |
| Scope | Monitoring and Controlling | Security test checklist, OWASP WSTG (`wstg_checklist.md`, CC BY-SA 4.0) | specification, architecture, stack discovery, the Verifier's WSTG items, people's recorded reviews |
| Risk | Monitoring and Controlling | Security test report (`security_report.md`, CC BY-SA 4.0) | the same |
| (C4) | - | Architecture diagrams (`c4.md`) | architecture, stack discovery, stored code graph |

Finance and Stakeholders have no document of their own in phase 1: what they
need (a budget, external stakeholders) is not recorded, and the check results
say so per domain. The book's minimal adaptive set that phase 1 does not yet
produce (status and work performance reports, the release and iteration plan
as its own document, the final report, cost baselines) is listed under the
next phases; phase 2 adds the first three.

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
| Release and iteration plan | Target release dates and scope ahead (phase 2) |
| Quality management plan | Quality objectives and acceptance thresholds (phase 2) |
| Communications management plan | Stakeholders outside the company and what they need to know; meetings, reports and cadence agreed with people (phase 2) |
| Stakeholder register | Stakeholders outside the company: interest, influence, expectations and engagement (#691) |
| Final report | Acceptance by the sponsor, benefits realisation outlook, handover notes (phase 2) |
| ITIL transition set | Handover of each release: who takes it over, what they receive, workarounds for its known errors (#696) |

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
| Risk | The security tests that apply are run | applicable WSTG tests observed passing, against the specification's security target (measured; STD-PO-1) |
| Scope | Deliveries meet the standard without rework | done tasks accepted on their first delivery, from the backend's effort observation (measured; phase 2) |
| Schedule | Work planned for a sprint is finished in it | tasks carried to a later sprint over the tasks planned (measured; phase 2) |
| Finance | Model spend is known for every completed sprint | each completed sprint's usage reported by the provider and priced (phase 2) |
| Resources | The effort each task took is observed, not reported | every task of a completed sprint has an `effort_observed` event (phase 2) |
| Scope | Requirements are delivered by the iterations | requirements a done task covers, over the documented specifications' requirements (measured; phase 2) |
| Schedule | Completed iterations leave the factory as releases | completed sprints whose integration was published as a request (measured; phase 2) |
| Scope | Deliveries are verified by the evidence | the Verifier's readings with the verdict verified, over every reading (measured; phase 2) |
| Scope | The specification names a quality target for every area | the quality areas the specification check expects against the documented specifications' quality attributes (STD-PO-1; met or not met; phase 2) |
| Stakeholders | Approvals are traced to who gave them | the approvals of specifications and architectures by the approving account's current company role, the Manager agent or another account (measured; #691) |
| Stakeholders | The company is told about its work outside the product | the notification webhook, Slack, Teams and the brief by mail that are on, from their configuration records (measured; phase 2) |
| Risk | Nothing is left on hold when the project closes | holds and high-risk readings whose task is neither done nor carried: met / not met once every sprint is closed, measured while one runs (phase 2) |

`CHECKS` is a dictionary of lists per domain: a later phase adds a check by
appending `(outcome, how, function)`.

#### More computed checks per domain (#692)

Five checks join the table, in `project_docs/domain_checks.py`, over the
snapshot key `check_basis` (`project_docs/collect_checks.py`: each sprint's
`duration_days`, the tasks accepted into a sprint from an intake request, and
each business unit's token quota with its digest; a unit's name is free text
and is not read). Each reads only what the platform wrote; none reads a
retrospective, an agent message or a delivery's text.

| Domain | Outcome (our wording) | Result | How it is computed | Function | Cites | Not computable when |
|---|---|---|---|---|---|---|
| Governance | Work starts on an approved plan | met / not met | each sprint planned from a specification (`Sprint.specification_id`): its first task event against that specification version's `approved_at` (`history`); a version never approved counts as not met; sprints not planned from one are counted apart | `domain_checks.approved_first` | the sprint and the specification version, each with its digest | no sprint planned from a specification has started, or a started sprint's version is no longer in the bounded history (counted, never assumed approved) |
| Scope | A sprint's scope holds once it is planned | measured | the tasks that joined a sprint after it was planned, accepted from an intake request (`intake_queue` writes a `note` task event under the actor `system` with `intake_item_id`; the same payload under another actor is a claim and is not read), over the sprints' tasks | `domain_checks.churn` | each such task event with its chain hash | no task is recorded, or `check_basis` was not read |
| Schedule | Iterations finish by their planned end | measured | completed sprints whose last task event falls on or before `start_date + duration_days − 1` (a one-day sprint ends on its start date); sprints without a length are counted apart | `domain_checks.on_time` | the sprint, and its planned length with a digest | no sprint is completed, or no completed sprint has both a start date and `duration_days` |
| Finance | Model usage stays within the configured quota | measured | the tokens counted against the business units' `token_quota_limit` this period (`token_quota_used`, which the LLM proxy adds from the usage the provider reported), with how many units reached it | `domain_checks.quota` | each unit's quota record with its digest | no business unit has a quota (0 is unlimited) |
| Resources | Each sprint's work goes to the team chosen for it | met / not met | the tasks of the sprints that record a team (`Sprint.team`) are assigned to a role on it (canonical role names) | `domain_checks.sprint_team` | the sprints with their digest | no sprint records a team (it then runs with every hired role, which `team` already checks) |

Limits worth knowing: `duration_days` is the length recorded now, which a
Manager can edit after the sprint (each edit is audited as `sprint_updated`);
the sprint's close is read as its last task event, since the sprint has no
closing time of its own and the backend's effort observation is written at
close. The quota is a token limit per period, reset by an Owner, so its figure
moves with every model call and `checks.md` goes stale with it, as it already
does with the spend.

Stakeholders and Risk gain nothing here: each already has three computed
checks, and what is left in them is in the list below.

**Outcomes the records cannot decide** (each stays a person's section, a
"not computable" line, or out of the table):

| Domain | Outcome | Why not |
|---|---|---|
| Governance | Benefits are realised; the project is still justified | the business case and expected benefits are a person's (charter sections) |
| Scope | The sponsor accepts the delivered scope | acceptance is a person's decision (final report section); the Verifier's readings are evidence for it, not the acceptance |
| Schedule | The project finishes on its target date | no target release date is recorded (release plan section); sprints without `duration_days` have no planned end |
| Finance | The project delivers within its budget | no budget in money is recorded; the token quota is a period limit on model calls, not the project's budget, and spend in dollars is only known where the provider reported usage |
| Finance | Cost performance against a baseline | no cost baseline or planned value is recorded |
| Stakeholders | People outside the company are engaged and satisfied | they are not in the records (register and communications sections) |
| Resources | People's capacity and availability are planned | the records hold no capacity or calendar for people; agents are not scheduled resources |
| Risk | Risks stay within the organisation's appetite; project-level risks are handled | the appetite and market, staffing and budget risks are a person's (risk register section) |

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

### Status and work performance report (`status_report.md`; Governance, Monitoring and Controlling; inputs: sprints, events, verifications, effort, costs, statements_status_report)

Phase 2. The book's chain of work performance data, information and report
maps onto observation, analysis and claim: `collect_status` reads the
observations, `performance` is the analysis, `status_docs` renders the
figures, and the only claim is a person's forecast below them.

| Field | Source | Kind |
|---|---|---|
| Tasks done / planned, blocked | each sprint's tasks and their board status | sprint ref (digest) |
| Carried out / in | `carried_over` task events (`direction` `to` / `from`) | event ref (chain hash) |
| Verifier readings, high risk | every `Verification` of the sprint's tasks, verdict and tier | verification ref (artifact digest) |
| First pass, rework cycles and requests, send-backs, review failures | the latest `effort_observed` event per task, written by the backend at sprint close (`services/models/effort.py`); an event of that kind by any other actor or observer is not read | event ref (chain hash) |
| Holds and escalations | `blocked`, `escalated`, `risk_hold` task events | event ref (chain hash) |
| Model spend | the sprint's turns whose usage the LLM proxy reported, priced from the catalogue (`run_cost.sprint_costs`); unknown with its reason, never 0 | `cost` ref (digest of the usage summary the backend computed) |
| Forecast and decisions requested | `status_report.forecast`, a person's statement | person, **claim** |

Retrospectives, agent messages and delivery texts are not inputs: a claim can
neither appear in the report nor make it stale. A price change in the model
catalogue changes a sprint's spend, and so makes the report stale; that is
intended, the figure it shows would no longer be the one the backend computes.

### Release and iteration plan (`release_plan.md`; Schedule, Planning; inputs: specification, sprints, iteration_plans, carries, releases, statements_release_plan, and specifications when there are several)

Phase 2. The plan an adaptive project keeps of its iterations and releases,
compiled from what was recorded rather than written ahead: what each iteration
was planned to do, which requirements it carries, what moved between
iterations and what left the factory. `collect_release` reads the three new
snapshot keys, `iterations` counts, `release_docs` renders.

| Field | Source | Kind |
|---|---|---|
| Iteration: planned from | `Sprint.specification_id`, with the version when the snapshot holds that specification | sprint ref (digest), specification ref |
| Iteration: goal as planned, team, who chose it, scope size | `Sprint.goal`, `team`, `team_source`, `scope_size` as recorded when the sprint started; shown as the plan, never as a result | sprint ref (digest) |
| Iteration: done / planned, requirements carried | each sprint's tasks, their board status and `covers` (a criterion id read as its requirement) | sprint ref (digest) |
| Requirement by iteration | the tasks whose `covers` name it, in its own specification's sprints; delivered (a covering task done), planned, not planned | requirement ref, task ref (digests) |
| Carry-over | `carried_over` task events leaving a sprint, written by the platform (actor `system`, `services/loop/carry_over.py`), with the task and sprint they went to; any other actor is a claim and is not read | event ref (chain hash) |
| Release | a `SprintIntegration` with a `publication`: the daemon's commit, how it was published (`gh`, `glab`, `compare`), and the audit row the backend wrote (`sprint_published`), its time and chain hash; the publisher's name and the request's address are left out | `release` ref (the audit row's chain hash) |
| Target release dates and scope ahead | `release_plan.targets`, a person's statement | person, **claim** |

A release here is what the factory can observe today: a sprint's integration
published as one pull or merge request. A tagged version and its deployment
are the ITIL release and deployment plan of phase 5. Retrospectives and agent
messages are not inputs. A date ahead is never computed: the plan shows the
dates the records hold (a sprint's start, its first and last event, a
publication), and the dates the project commits to are the person section's.

### Quality management plan (`quality_plan.md`; Scope, Planning; inputs: specification, quality_basis, sprints, events, verifications, effort, costs, statements_quality_plan, and specifications when there are several)

Phase 2 (planned for phase 3, brought forward). How quality is defined, built
in and checked, from what the backend applies rather than from a document a
model could rewrite. `collect_plans.quality_basis` reads the definitions from
the code, with no database, and hashes each entry; `quality_docs` renders.
PMBOK 8 has no quality domain: quality planning sits in Scope, beside the
WSTG checklist.

| Field | Source | Kind |
|---|---|---|
| Standards: id, area, roles bound, title, checked without a model (and how) | `services/evidence/standards.catalogue()`, the catalogue `docs/STANDARDS.md` is held to (`test_standards.py`) | `standard` ref (digest of the entry) |
| Specification quality targets, areas without one | each documented specification's `quality`; `standards.missing_quality_areas` over `standards.QUALITY_AREAS` (STD-PO-1) | specification ref (digest) |
| Gate before QA: check, problem named, what the loop asks for | `delivery_gate`'s table of refuting checks and `CODE_PROBLEMS` (the code is fixed, or the task is run again in full), plus `delivery unconfirmed` | `gate` ref (digest of the row) |
| Risk tiers by area | `risk.classify` called on one path per area, so the tier is the one the classifier computes | `risk_rule` ref (digest of the row) |
| Measured outcomes | `performance.totals`: readings by verdict, first-pass, rework, returns by a person, review failures, high-risk readings, risk holds | verification, task event (chain hash) refs |
| Quality objectives and acceptance thresholds | `quality_plan.objectives`, a person's statement | person, **claim** |

The gate's table is read from `delivery_gate`'s private constant: the evidence
is a protected path, so a public alias waits for an Owner's change there. A
changed standard, gate row or classifier rule changes its digest and makes the
plan, and the check results, stale.

### Communications management plan (`communications_plan.md`; Stakeholders, Planning; inputs: members, communications, statements_communications_plan)

Phase 2. Who is told what, through which channel and when. The channels are
the platform's own; none is invented. `collect_plans.communications` reads the
configuration records; `comms_docs` renders.

| Field | Source | Kind |
|---|---|---|
| Who is in the company | `members` (counts by company role), with what each role receives and decides as the routes enforce it | membership ref (digest), rule ref |
| Board, review inbox, documentation pack, audit log | fixed by the platform: always on | rule ref |
| Notification webhook | `CompanyWebhook`: on or off and the events chosen; never the URL or secret | `webhook` ref (digest) |
| Slack, Microsoft Teams | `ChatConnection`: on or off, the last test; the number of linked chat accounts; never a workspace, channel, credential or key | `chat` ref (digest) |
| Client's brief by mail | a `ManagerPolicy` row, `MANAGER_AGENT_ENABLED`, whether `SMTP_HOST` is set, `MANAGER_BRIEF_INTERVAL_HOURS` | `brief` ref (digest) |
| Audit anchors | `AuditAnchorSettings`: on or off, cadence, webhook, whether a timestamp authority is set; never its URL or certificate | `anchors` ref (digest) |
| Stakeholders outside the company; meetings and cadence | `communications_plan.stakeholders`, `communications_plan.cadence`, a person's statements | person, **claim** |

Delivery counters (`last_status`, outbox rows) change at every post and are
left out, so a delivery never makes the plan stale; the pack's own approval is
not read either, so approving a pack cannot make it stale.

### Stakeholder register (`stakeholder_register.md`; Stakeholders, Initiating; inputs: members, roles, stakeholders, statements_stakeholder_register)

Issue #691. Who has a stake in the project, from the company's records;
nobody is named. `collect_stakeholders.stakeholders` reads the teams and the
approvals; `stakeholder_docs.register` renders.

| Field | Source | Kind |
|---|---|---|
| People in the company | `members` (counts by own company role), with what each role receives and decides as the routes enforce it (`require_company_role` in the cited router) | membership ref (digest), rule refs |
| Teams | `Team`: the role it grants, its members who are company members, counted; never its name, description or author (free text that may name a person) | `team` ref (digest) |
| Approvals given | each `Specification` and `Architecture` with `approved_by`, reduced to the approving account's effective role now (own role raised by its teams, `services/platform/teams`), the Manager agent, a deployment without sign-in, or an account no longer a member; the address never leaves `collect_stakeholders` | specification / architecture ref (digest of the approval entry) |
| The agent team | `roles` (the roles hired, canonical) | membership ref (digest) |
| Stakeholders outside the company | `stakeholder_register.external`, a person's statement | person, **claim** |

The role shown for an approval is the account's role now, not at the time of
the approval, and the text says so. The pack's own approvals are not read,
so approving a pack cannot make its register stale; a team's rename changes
nothing either (`updated_at` is left out).

### Final report (`final_report.md`; Governance, Closing; inputs: specification, sprints, events, verifications, carries, releases, effort, costs, statements_final_report, and specifications when there are several)

Phase 2. What the project delivered and what it leaves behind, compiled at
closure from the records rather than written from memory. It needs no new
snapshot key: `closure` counts from the keys the status report and the
release plan already read, `final_docs` renders.

| Field | Source | Kind |
|---|---|---|
| Requirement: delivered / not delivered | the tasks whose `covers` name it, in its own specification's sprints: delivered when one is done on the board; not delivered, planned (tasks cover it, none done) or not planned (none covers it) | requirement ref, task ref (digests) |
| Requirement: latest reading | the Verifier's newest `Verification` of each done covering task, its verdict | verification ref (artifact digest) |
| Iterations closed; interim | sprints completed or cancelled over all; while one is neither, the report says it is interim and names the sprints | sprint ref (digest) |
| Measured outcomes | the status report's figures over the whole project (`performance`): tasks done, carried out, the Verifier's readings, first pass and rework from the backend's `effort_observed` events, the reported model spend (unknown with its reason, never 0) | sprint, event (chain hash), verification, `cost` refs |
| Releases cut | the published sprint integrations (`releases`), the daemon's commit and how it was published | `release` ref (the audit row's chain hash) |
| Holds left open | the newest `blocked` or `risk_hold` event per task whose task is still blocked, or `escalated` event with no later `decided`, on a task neither done nor carried to a later iteration by the platform (`carries`, actor `system`) | event ref (chain hash) |
| High-risk readings left open | the Verifier's newest reading per task with tier `high`, on a task neither done nor carried | verification ref (artifact digest) |
| Lessons learned | a pointer to `lessons.md`; nothing read | none |
| Acceptance by the sponsor, benefits realisation outlook, handover notes | `final_report.acceptance`, `.benefits`, `.handover`, a person's statements | person, **claim** |

Retrospectives, procedures and agent messages are not inputs. The lessons
belong to their register, which quotes each retrospective as a claim and
shows what the procedures that followed measured; the final report points
there instead of repeating or counting what was said. A blocked task the
platform carried to a later sprint is followed in that sprint (its new task),
not counted as left open, and a carry reported by an agent is not read.

### ITIL transition set (`transition.md`; Governance, Executing; inputs: transition, statements_transition)

Issue #696. What each release changed and left behind, after the practices
ITIL 4 (AXELOS / PeopleCert) names for moving a change into use: change
enablement, release management, problem management with its known errors,
and the handover. Only the practice names are taken; the wording is ours.
A release is a sprint integration that left the factory: published as one
request, or merged locally into the base branch. `collect_transition` reads
one snapshot key, `transition`, from the release context's tables;
`transition` derives what each release delivered and its known errors;
`transition_docs` renders.

| Field | Source | Kind |
|---|---|---|
| Change record: what changed | the integration's `tasks` (state, commit, run, verification as recorded at integration), the tasks' `covers` read as requirements, the base and the integration commit | integration ref (its audit row's chain hash), task refs (digest of the entry) |
| Change record: approved by | the integration's latest decision (`IntegrationReview`, `approve` or `request_changes`; else its latest review), its author reduced to the account's effective company role now, `former` or `no_account`; never the name, address or words | `review` ref (its audit row's chain hash) |
| Change record: integration review | the review's event, outcome, commit (whether it is the integration's), `diff_sha256` the backend computed from the daemon's diff, files, segregation-of-duties outcome, the number of reviews | `review` ref |
| Change record: suite result | the integration's status and the daemon's `test_run` (command, exit code, timeout, failing tests counted), the tested commit | `suite` ref (digest of the kept facts) |
| Change record: published | `publication` (how, base, the daemon's commit, the `sprint_published` audit row) and `details.local_merge` (base, fast-forward or merge commit, the commit after, who merged as a role, the `sprint_merged_locally` audit row); never the request's address | `release` and `merge` refs (audit rows' chain hashes) |
| Change record: undone | `run_delivery_undone` audit rows naming a run of the release; a delivery on a Git branch is undone by discarding the branch, which the platform does not record, and the record says so | `undo` ref (audit row's chain hash) |
| Release notes | per release, the tasks merged or already in, the requirements they cover, and the done tasks left out with their state | task refs |
| Known errors | a delivery the release carries whose reading at integration was refuted, unverifiable or high risk; a hold open in its sprint at release time: the newest `blocked` or `risk_hold` event of a task whose status after its newest event then was `blocked`, or an `escalated` one with no later `decided`, on a task the release does not deliver and the platform (actor `system`) had not carried by then | verification ref (artifact digest), event ref (chain hash) |
| Handover | `transition.handover`, a person's statement | person, **claim** |

A review's words, retrospectives and agent messages are not inputs: a claim
can neither appear in the set nor make it stale. The approver's role is the
account's role now, not at the time of the review, as in the stakeholder
register.
Acceptance, the benefits after closure and the handover are what only a
person can say at closure, so they are person sections, never computed.

### Architecture diagrams, C4 (`c4.md`; inputs: architecture, stack, code graph)

| Level | Elements | Source |
|---|---|---|
| Context | the system (arch `title`, `overview`); "Users of X" for an `http`, `cli` or `event` interface no module consumes; databases and infrastructure | architecture, interface, discovery refs |
| Container | each module (the record does not say which are deployed apart; the note says so), databases and infrastructure inside the boundary; `depends_on` and interface consumers as lines | module, interface, discovery refs |
| Component | per module, its core and the interfaces it provides; consumers as lines | module, interface refs |
| Code (issue #693) | per module, a **Claim.** line (its path, the architecture's `implements` edge) and an **Observed.** table of the files the stored code graph holds under that path with the sha256 the daemon reported and the names each defines; then the references between the components' files (a name one uses and another defines) | module ref marked as a claim; a `code_file` ref per file with the daemon's sha256 |

Each diagram carries `nodes`, `edges`, `groups`, `notes`, its Mermaid source
and `input_sha256`; the document carries the three Mermaid blocks with an
element table each.

**The code level** (`c4_code`, `collect_code`, issue #693) is a section of
the document, not a fourth diagram. `collect_code` reads the stored code
graph of the folder the task worker stores it for (`docs/design/CODE_GRAPH.md`,
slice 2) through the tools context's interface (`services/tools/code_level`),
and never asks the daemon: the pack shows the graph as the daemon last
reported it, with that time. The snapshot's `code_graph` keeps the two kinds
of statement in separate fields: `claims` (each module's `implements` edge
to its path, from the architecture the pack documents) and `observed` (the
stored files under those paths, their sha256 as the daemon computed it on
the machine that holds them, the names they define and the references
among them, resolved with the daemon's rule; bounded, with counts of what
was left out). The document says which is which before the first
component. Nothing stored, the store `off` or no claimed path holding a
stored file give "not observed" and the section is listed in `missing`; a
component whose path holds no stored file says so; nothing is inferred.
`code_graph` is an input of the document's digest and not of the diagrams'
(they do not read it), so a cited file whose sha256 changes makes `c4.md`
stale and leaves the diagrams current; the time of the last report is shown
but not hashed, so a sync that changed no cited file does not. The project
folder is not put in the snapshot (it may carry a person's name), and file
paths and names, parsed from files agents may have written, pass
`md_inline` like any record text.

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

### The language of a pack: English or Spanish

A pack is written in the project's documentation language,
`Company.doc_language` (`en` or `es`; migration `0023` adds the column).
"New company" sends the creator's interface language, so a company created
from a Spanish interface gets Spanish packs; an empty value reads as English.
An Owner or Manager changes it on the Documentation page
(`GET`/`PUT /v1/companies/{id}/doc_language`, Viewer / Manager, audited as
`doc_language_updated`).

- **Every fixed string comes from a template** (`project_docs/texts.py`):
  headings, labels, table headers, the "no source recorded" lines, the person
  templates, the claim header, the source kinds in the list of sources, the
  check outcomes and results, the C4 titles, notes and the labels that are our
  text ("Users of X", "X core", "depends on", "calls X"), and the record
  statuses, verdicts and tiers as shown. Both tables hold the same keys and
  placeholders (a test holds them in step); the wording is ours in both, and
  the Spanish is a neutral professional register. The domain and focus area
  names follow the language in the Markdown and stay the canonical English
  names in the data (`domain`, `focus_area`).
- **What does not change with the language:** record text (titles, names,
  reasons, the module and interface names from the architecture, which keep
  their spelling in the diagrams too), ids (`REQ-1`, `T-41`, `R-1`), the
  citation marks and their order, the digests and the input digests, the file
  names under `docs/project/` and the role names. A test compiles the same
  records in both languages and compares all of them.
- **The prose** is asked of the TechWriter in the pack's language (the prompt
  ends with the instruction for that language) and stays a quoted claim.
- **The pack records its language** (`DocumentationPack.language`). The
  language is not an input of any document's digest; instead the lint flags a
  pack whose language is no longer the project's, which blocks its approval
  until it is compiled again.
- **A test** builds a pack in each language and fails if any template
  fragment of the other language (three words or more, not shared by both)
  appears in it, with a control that the pack's own fragments are found.

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
| T8 | B1 | D | A large company makes compiling or rendering heavy | L | L | Low | Every list in the snapshot capped at 200 rows; text fields cut; fifteen files per write |
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
- **ITIL 4** (AXELOS / PeopleCert): the names of the practices the transition
  set follows (change enablement, release management, problem management and
  its known errors, the handover; #696), summarised in our own words; no text,
  definition or table of ITIL is reproduced.
- **COBIT** (ISACA): named as a later phase; nothing taken.

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
release and iteration plan as its own document, and the final report from a
completed project's requirements, readings, measurements, releases and open
holds, with the lessons left to their register since retrospectives are
claims (all done: `status_report.md`, `release_plan.md`, `final_report.md`).
The communications and quality plans, first planned for phase 3, are done
too (`communications_plan.md`, `quality_plan.md`). More computed checks per
domain are done too (#692: approval before work, backlog churn, iterations on
time, usage against the quota, sprint teams). The stakeholder register from members, teams and
approvals plus a person's external list is done too (`stakeholder_register.md`,
#691). After that: DOCX/PDF export and organisation templates (phase 4, with a signed
manifest of the pack), the COBIT governance matrix (phase 5; the ITIL transition set is done,
`transition.md`, #696), and bench grading of packs against person-written expectations.
The C4 code level from the stored code graph is built (issue #693, under
"Architecture diagrams, C4").

## Later phases: audit-ready documents

Auditors of IT general controls, and assessors where a project handles card
data, ask for a different set from a PMO's. The Owner's structural summary of
*Information Technology Control and Audit* (A. R. Otero, 5th ed., CRC Press,
2019) and of a PCI DSS v4.0.1 guide (NHI Mgmt Group, 2026), written in our own
words, names ten. None is built in phase 1; each fits the same extension
points (a builder over the snapshot, its inputs for the drift digest, sources
per row, person sections where a decision is a person's), and every one shows
the result of verifying the audit chain (`GET /v1/audit/chain`) and the task
event chains it draws on. The book's text is not stored or used for
retrieval (its copyright page forbids it); PCI DSS requirement text belongs to
PCI SSC and is not reproduced: requirements are cited by number only.

| Document | Built from (records) | Sources in the summary |
|---|---|---|
| Control matrix | each in-scope general control and, for card data, PCI DSS Req 6, 7, 8, 10, 11, 12; its owner (role or person), the record that evidences it, whether it is checked automatically | IT audit control areas; PCI DSS 4.3 |
| Change management evidence report | every change in a period from the chains, the count tied to sequence numbers; per change the requirement, risk tier, producer, the Verifier's verdict with log hash and file sha256, reviewer, the approving person and time, release, undo; exceptions apart (no approval, approver equal to requester, emergency, overridden failed check) | change control objectives; PCI DSS 6.5.1 |
| Segregation-of-duties matrix with violations | agents and people against specify, produce, verify, review, approve, release; the rule that one identity holds no two adjacent steps; the violations found in the period | change control (independent deployer); PCI DSS 6.5.4 |
| Access review | members, roles, last activity, grants and revocations, API tokens and service accounts, with the Owner's sign-off every six months (a person section) | access security controls; PCI DSS 7.2.4, 7.2.5, 8.6 |
| Audit-trail extract with chain verification | a chain segment, the verification output, the signing key's identifier and an external anchor of the chain head once one exists; a table of the event types logged | log integrity; PCI DSS 10.2, 10.3, 10.5 |
| Secure SDLC report mapped to PCI DSS Req 6 | per release: the phases and their artifacts (specification, ADRs, threat model, review, the Verifier's tests), dependency, secret and container scan results with triage, an SBOM, a test-data scan for card numbers, a CSP/SRI check for web front ends | SDLC controls; PCI DSS 6.2, 6.3.2, 6.4.3, 6.5.5, 6.5.6 |
| Findings register | every hold, escalation or failed check as a finding: condition, criteria, cause, effect, recommendation, then management response, owner, target date, status and retest evidence (the response and dates are person sections) | the book's finding record and management letter, plus a cause field it lacks |
| Risk assessment | a project risk register for risks beyond each delivery's tier, and a targeted risk analysis per frequency the project chooses (person sections, with the observed risks of this phase's register as input) | risk management area; PCI DSS 12.3.1 |
| Scope and data-flow statement | systems, data flows, third parties and their attestations; for a project in PCI scope, a statement that La Fabrique itself (daemon, backend, CI, LLM proxy) is a connected system | PCI DSS 12.5.2, 12.8 |
| Test evidence index and release sign-off | acceptance criteria to tests, to the Verifier's verdicts, to the person's approval: the traceability matrix of this phase, extended with test logs and the sign-off | SDLC testing; the book's testing checklist |

What these documents need that the records do not yet hold (an external
anchor for the chain, a four-eyes rule and its report, an access lifecycle,
an emergency-change path, a backout plan and security-impact note per change,
an SBOM per release, a findings lifecycle) is listed in the summary's
recommendations and belongs to its own issues; the documents report the gap
as "No source recorded" until then.

### A COBIT 2019 evidence matrix per project

From the Owner's structural summary of COBIT 2019 (ISACA), in our own words.
ISACA's usage guidelines (isaca.org/COBITuse) must be read by the Owner
before any COBIT-derived text is published; the matrix cites objective and
practice ids with short names in our wording only, never activity, metric or
output text.

- **Rows:** one per practice (or practice and output) of the objectives the
  Owner targets with the design factors (by default the focus objectives of the
  summary).
- **Column groups:** the objective and practice ids with our short name;
  activity references as numbers with their capability level; the output's
  name as a label; the record that answers it (specification, architecture,
  verdict, approval, release, audit range) with its locator (record id, audit
  sequence range, commit, digest); its nature, observation or claim (only
  observations count); who recorded it and its integrity status (chain and
  signature verified, digest recomputed; a digest from the party recorded is
  never accepted); the check type, rule id and result; the period and the date
  last evaluated; the status (evidenced, partial, none) and why; the person
  accountable for a manual or empty row (a member's role, never an agent); the
  remediation task in the backlog; the target capability set by the Owner,
  the evidence-supported ceiling and the per-level rating; the assessor's
  sign-off (a person, a date, an approval).
- **Capability, honestly:** computed deterministically from recorded
  observations only, never written by an agent (no agent may write a level or
  rating field). Per level, the share of that level's activities with passing
  evidence in the period, graded on COBIT's scale; a level counts only when it
  and every lower one are achieved. The result is shown as an
  **evidence-supported ceiling, never a certified level**: the rating belongs
  to a qualified, independent assessor. A row with no record is capped at what
  a person attests, recorded as an approval labelled "attested" and shown
  apart from "evidenced". Coverage, the period and any integrity failure are
  shown beside the ceiling; a partial grade is never rounded up. The matrix,
  its inputs and the computed ceiling are hashed into the audit chain so an
  auditor can re-perform the computation.
- **Quick wins the summary names**, all derived from records already kept:
  a risk profile (APO12.03) from risk tiers, holds and findings; configuration
  status and integrity reports (BAI10.04, BAI10.05); a control-deficiency
  register (MEA02.04); an incident and hold trend (DSS02.07); an SBOM per
  release (BAI03.04).

## Security test plan and checklist (OWASP WSTG, issue #539)

A project already gets secret scanning, a dependency audit and SAST
(`evidence/secops_checks.py`) and the standards STD-SEC-1..3, and the
security probe (#540) observes some of the running application's answers.
Nothing said which web security tests apply to the project or which ran. This
section adds that, as evidence.

### The catalogue and its licence

`backend/app/data/wstg/catalogue.json` is built by `scripts/wstg_catalogue.py`
from the guide's `checklists/checklist.json` at a pinned commit (87cf4a4) and
`checklists/WSTG-Checklist_v4.2.xlsx`. Each test carries a versioned
identifier in the probe's scheme: `WSTG-v42-<CAT>-<NN>` when release 4.2 has
the same test under the same identifier (names compared without the
"Test"/"Testing for" prefixes, case, punctuation and plurals), else
`WSTG-87cf4a4-<CAT>-<NN>` (a test 4.2 lacks, or an identifier 4.2 gave to
another test: `APIT-01` was GraphQL in 4.2 and is API Reconnaissance now).
`services/platform/wstg_catalogue.py` reads it and maps any spelling of an id
to the versioned one, so a probe item, a QA test name and a person's review
cite the same row.

The catalogue is a derivative of the guide and stays under **CC BY-SA 4.0**:
the licence, the attribution, the commit and what La Fabrique changed are
fields of the file, and `NOTICE` lists it. The generated checklist and report
are adaptations too: each ends with a footer giving the licence, the
attribution and the commit (`security_docs.footer`, in the pack's language),
and the document carries `licence`. The code that reads and builds them stays
Apache-2.0. Only the tests' names, references and objectives come from the
guide; every other string is ours, and the report's structure follows chapter
5 (Reporting) without reproducing its text.

### The rule table

`services/planning/wstg_plan.py`. A signal is read from the specification,
the architecture and the detected stack, and cited by where it was found
(`specification REQ-2: "log in"`, an `http` interface, a framework, a
database); words are matched on word boundaries in English and Spanish. No
model decides a signal or a rule.

| Rule | Signal | Tests (auto by the probe / auto by a QA test / manual) |
|---|---|---|
| R-WEB | an HTTP surface | CONF-04, -06, -07, -12, -14, CLNT-07, -09, ERRH-01, -02 / - / INFO-02, CONF-02, CRYP-01 |
| R-BROWSER | a browser client | - / INJT-01, INJT-02 / CLNT-01, -03, -12, -14 |
| R-AUTH | sign-in | SESS-02 / ATHN-03, ATHN-04, IDNT-04, SESS-03, -05, -06 / ATHN-01, -02, -06, -07, -09, SESS-01, -07 |
| R-ROLES | roles and permissions | - / ATHZ-02, -03, -04 / IDNT-01 |
| R-API | an API | - / INJT-03, INJT-20 / APIT-01, APIT-03 |
| R-API-AUTH | an API and sign-in | - / APIT-02, APIT-04 / - |
| R-UPLOAD | file uploads | - / BUSL-08, ATHZ-01 / BUSL-09 |
| R-DATABASE | a database | - / INJT-05 / - |
| R-OUTBOUND | outbound fetches | - / INJT-19 / - |
| R-PAYMENTS | payments | - / - / BUSL-10, BUSL-01, BUSL-04 |
| R-OAUTH | OAuth or OpenID Connect | - / - / ATHZ-05 |
| R-SAML | SAML | - / - / ATHN-12 |
| R-GRAPHQL | GraphQL | - / APIT-99 / - |
| R-WEBSOCKETS | WebSockets | - / CLNT-10 / - |
| R-JWT | JSON Web Tokens | - / SESS-10 / - |

The probe rows are exactly the ten tests `services/evidence/security_probe.py`
(#540) observes. A sign-in, an API or a browser client implies an HTTP
surface. A test no rule selects is not applicable, with the signals that
would bring it in ("no sign of sign-in in the specification, the
architecture or the detected stack"). A model may only propose additions
(`plan(..., proposals=[{id, role, reason}])`): a proposed test no rule
selected becomes a `manual` row marked as a claim with who proposed it; a
proposal never removes or changes a rule's row. No producer calls it yet.

### Statuses: observations and reviews only

`project_docs/wstg_status.py`. A row's status comes from the newest of:

- an **observation**: a WSTG item of a Verifier check stored on a
  `Verification` (the probe's `security_probe`, and `wstg_tests`, which reads
  the daemon's run for tests named `test_wstg_<cat>_<nn>_…`), cited with the
  verification id, the run, the sha256 of the run's evidence
  (`AgentRun.artifact_digest`) and the tool (the check's name, its tool or
  command, and the WSTG commit); `pass` is observed-pass, `refuted` /
  `finding` / `fail` observed-fail; `unverifiable` sets nothing and is shown
  beside the row;
- a **person's recorded review** (`WstgReview`, migration `0034`): who
  reviewed (kept in the database only), the date, the artifact, the result
  (`pass`, `fail`, or `not_applicable` with a note), the recorder's company
  role and `record_sha256`, computed by the backend over the stored fields.

Without either, an applicable `auto` row is not-run, a `manual` one
needs-manual, and a row the plan did not select not-applicable with its
reason. No row is observed without a verification id or a review id (a test
holds it); nothing a producer said about its own run is read.

A Manager records a review with
`POST /v1/companies/{id}/security/wstg/reviews` (Owner or Manager; a Viewer
and other companies are refused; 422 for a test the catalogue does not hold,
a future date, or `not_applicable` without a note; audited as
`wstg_review_recorded` with the digest). `GET /v1/companies/{id}/security/wstg`
(Viewer) returns the signals, every row with its status and evidence, and the
counts.

### The two documents

| Document | Built from | Kind |
|---|---|---|
| WSTG checklist (`wstg_checklist.md`) | the signals with where they were found; per applicable or observed test: how it is settled, why it applies (the rules, in the pack's language), its status and the verification or review it rests on; then every other test with why it does not apply | catalogue, specification, architecture, discovery, verification and review refs |
| Security test report (`security_report.md`) | chapter 5's structure: 1. introduction (version control, who tested as counts, scope, limitations, timeline, disclaimer), 2. executive summary (the counts; prose would be a quoted claim), 3. findings (observed failures only, with severity and the evidence), appendices (the rule table, the severity scale, the tools) | the same; remediation is a person section |

Both are written in the pack's language (`texts_wstg_en.py`,
`texts_wstg_es.py`), read `specification`, `architecture`, `stack`,
`wstg_observations` and `wstg_reviews` (their drift digest), and end with the
licence footer. The check results gain "The security tests that apply are
run" (Risk, measured): the applicable tests observed passing, against the
specification's security target (STD-PO-1), whose threshold the Owner sets.

### Where the plan feeds the rest

- **Documentation:** the two documents above and the measured check (built).
- **STD-PO-1:** the measured check gives the security attribute a number;
  `docs/STANDARDS.md` says so (built).
- **QA's tasks:** `wstg_plan.security_test_tasks(plan, limit)` shapes one
  test-writing task per applicable `qa_test` row, like the acceptance test
  tasks, and the Verifier's `wstg_tests` check reads the tests back (built).
  The sprint planner does not add the tasks yet: a QA task is judged by its
  own run, and a security test that fails because the application is weak
  would refute QA's delivery and send it back to QA, who cannot fix the
  application. Wiring it needs either an exemption like the acceptance
  tests' (`criteria_tests.excused_failures`) for `test_wstg_*` failures in
  QA's own task, or the tasks placed after the implementation they test; that
  is the next step, together with a sprint-planning switch.
- **The threat model:** the `threat-model` skill and a person reviewing a
  design read the checklist's applicable rows as the tests a mitigation must
  pass; a row a threat model adds by hand is a proposal (a claim) until a
  rule or a review settles it (documented, not built).

### Threat model (the review endpoint and the documents)

| ID | Boundary | STRIDE | Threat | Mitigation |
|---|---|---|---|---|
| W1 | B1 | S, E | A Viewer, an outsider or an agent records a review to turn a row observed-pass | `require_company_role` Manager on the route; agents have no route to it; the audit chain records the person (`wstg_review_recorded`). Tests: `test_security_tests_router.py` |
| W2 | B1 | T, R | A request hands in a digest or a hash of the artifact to make a review look verified | The request's extra fields are ignored; `record_sha256` is the backend's over the stored fields; the artifact is a reference, not a proof. Test: a supplied digest is not kept |
| W3 | B5 | T | A producer reports its own security test results or probe items to set rows | Only `Verification.checks` items (the daemon's output and the probe's reply) are read; `wstg_tests` reads `test_run` output, never `claims`. Test: a claimed result is not read |
| W4 | B5→B1/B3 | T, E | A review's artifact or note, or a probe observation, carries Markdown or HTML into the pack | `md_inline` on every value; ids through `md_id`; the frontend's renderer drops HTML (T1 above) |
| W5 | B1/B3 | I | The reviewer's name reaches a repository shared with outsiders | The snapshot carries the recorder's role, never the name; a test holds it |

### What was taken from where

- **OWASP Web Security Testing Guide** (github.com/OWASP/wstg, CC BY-SA 4.0):
  the checklist (test ids, names, references, objectives) as the catalogue,
  release 4.2's workbook to tell which ids are the same test, and the report
  structure of chapter 5. Adapted and shared under the same licence.
- **The security probe (#540)**: the versioned id scheme and the item shape
  (`{id, name, status, observation}`), reused so its results mark rows.
- **The acceptance tests** (`evidence/acceptance.py`): the test-name token
  read back from the runner output, for `test_wstg_*`.
- **Kiro (Amazon)**: requirements first, so a plan can be derived from the
  specification and the design rather than asked of a model.
