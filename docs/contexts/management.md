# The management context

The owner as a client: the Manager agent (inbox, policy, rules, observations,
actions, record), the client's brief, the self-improvement cycle's record, the
owner actions run from the app, the Reviewer and Support agents. Read this before changing
`backend/app/services/management/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/management/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md), [evidence](evidence.md), [workspace](workspace.md), [tools](tools.md), [learning](learning.md), [planning](planning.md), [producers](producers.md), [loop](loop.md), [release](release.md), [integrations](integrations.md). Nothing imports it.
- **Interface:** none.
- **Modules:** `brief_mail`, `company_export`, `company_import`, `in_process_api`, `intake_triage`, `manager_actions`, `manager_actions_extra`, `manager_agent`, `manager_brief`, `manager_inbox`, `manager_inbox_extra`, `manager_observe`, `manager_policy`, `manager_record`, `manager_record_extra`, `manager_rules`, `owner_action_runner`, `owner_actions`, `owner_git_actions`, `owner_git_runner`, `reviewer_agent`, `rsi_cycle`, `rsi_cycle_import`, `rsi_cycle_steps`, `rsi_cycle_view`, `self_host_snapshot`, `sod_report`, `support_agent`.

## How it works

The Manager agent (`MANAGER_AGENT_ENABLED`) reads what waits for a person with
the facts the platform observed, asks a model for a suggestion, and acts alone
only where a rule the company enabled holds on those facts. Everything else
stays a suggestion for a person. The Reviewer and Support agents only suggest;
a person decides.

## Rules

- Every rule is off until a Manager names it in the policy.
- `ALWAYS_ESCALATE` is what no policy unlocks: cancel or delete a sprint,
  change autonomy, spend above budget, change the protected paths
  (`change_protected_paths`), merge a kept self-improvement change
  (`merge_self_change`). A policy naming one is refused (422).
- A rule's check reads only observed facts; an unknown fact never lets it hold.
  The agent acts only when a rule holds *and* the suggestion is that rule's
  decision, never over budget, after an override on the item, or under a
  flagged rule.
- The model's suggestion (`suggested_*`) is a claim; the platform's reason for
  refusing it is a field of its own (`refusal_reason`).
- The agent decides only on the situation it asked about: it re-reads before
  acting and accepts through one conditional update. One `ManagerDecision` per
  situation (`item_state`, unique), under `locks.MANAGER_ITEM`.
- A person may override an agent's decision once, and only while it is still
  the latest on the item; the override is audited as the person's.
- A rule with two or more overrides among its last five decisions is flagged:
  the agent stops applying it until a Manager saves the policy again.
- A delivery that touches a protected path, for a company that hosts the
  factory, is never accepted by the agent, whatever its tier.
- The self-improvement cycle's merge and resume are a signed-in Owner's only;
  every step fails closed.
- Suggestions from the Reviewer, Support and intake agents never move the
  record; the text they read is data and they have no tools.
- An owner action runs only catalogue code with schema-checked parameters,
  started by a signed-in Owner (never a Manager or an API token) after a
  confirmation whose digest the start echoes; one run of an action at a time;
  start, cancel and finish are audited; its observations are what the
  platform created or measured.

## Components

### The Manager agent

- `manager_agent.py`: the loop, under `locks.MANAGER_AGENT`, at most
  `ITEMS_PER_CYCLE` model calls a cycle; a suggestion is accepted only in the
  `DECISION … CATEGORY … REASON … END` format. Each call is billed to the
  company's business unit and audited `manager_agent_call`.
- `manager_inbox.py`, `manager_inbox_extra.py`: the items and their observed
  facts (verification, risk tier, files, criteria, budget); the category is the
  platform's. The extra items (drafts, credit-paused sprints, branches to merge
  or publish, deliveries to undo) appear only when a rule names them.
- `manager_policy.py`, `manager_rules.py`: the rules a Manager may enable and
  one check per rule (`CHECKS`). The strict ones are `approve_specification`,
  `approve_architecture` and `accept_high_risk`.
- `manager_observe.py`: probes what nothing stored (a credit call, a remote)
  only when the rule is named and suggested, and makes the daemon requests.
- `manager_actions.py`, `manager_actions_extra.py`: each action goes through
  the code a person's decision goes through, audited
  `manager_agent_<category>`; `override` undoes it where it can (a local merge
  or an undo is recorded but not reverted; a publication is a 409).
- `manager_record.py`, `manager_record_extra.py`: the client's decision read
  from what they did, never reported, and the counts and `flagged_rules`.

### The client's brief

`manager_brief.py`: `GET /v1/companies/{id}/brief`, deterministic: what was
done, decided and overridden in a window, what waits for the client, the tokens
beside the quotas, and the refused suggestions with their `refusal_reason`.
`brief_mail.py` mails it to Owners and Managers.

### The self-improvement cycle

`rsi_cycle.py`, `rsi_cycle_steps.py`, `rsi_cycle_view.py`,
`rsi_cycle_import.py`: one row per cycle, its limits and its record
([design](../design/rsi-loop.md)).

- `rsi_cycle.start` refuses with an audited reason: `RSI_ENABLED` off, a
  company not hosting the factory, the stop after cycles without a keep, a
  cycle running, the daily limit, an unknown budget.
- Each step checks the kill switches and stops the cycle and its sprint. Over
  the token budget nothing is kept; a diff too large, or one the daemon did not
  observe in full, leaves the cycle `unmeasured`. The fitness decision is
  `fitness.decide`'s, never a model's.
- A kept cycle waits in the Manager inbox as `merge_self_change`; a signed-in
  Owner records the merge pinned to C's sha. A Manager, a token or the agent
  gets `not_an_owner`.
- `rsi_cycle_import.import_record` validates a runner's record and hashes each
  kept file it can read; a claimed sha256 that differs is refused, an absolute
  path is never read, and an unreadable file keeps its hash as a claim only.

### Owner actions

[Design and threat model](../design/owner-actions.md), issue #701.

- `owner_actions.py`: the catalogue (`self_host_container_run`,
  `land_branch`, `push_main`, `close_issue`), parameter validation, the confirmation digest,
  one run at a time (`locks.in_transaction`), and the run's record: start,
  cancel request and finish audited `owner_action_<step>` as `human:<id>`.
- `owner_action_runner.py`: the job. The checkout goes through the daemon
  (`workspace.self_host.checkout`, a folder name only); the harness's other
  steps (`scripts/self_host`) run in a worker thread with `in_process_api.py`,
  ASGI requests to the running application carrying the Owner's
  `InProcessPrincipal` ([platform](platform.md)), no token minted. Before
  each call it checks the cancel request and the tokens the platform recorded
  for the self-hosting company against the run's budget; over it, or on
  cancel, it stops the sprint.
- `self_host_snapshot.py`: finds and imports the harness
  (`SELF_HOST_REPOSITORY_DIR`), and reads the snapshot for the `records` step
  through `workspace.git.read_at` and `workspace.git.diff`'s `status` view.
- `owner_git_actions.py`: the three actions on La Fabrique's own repository.
  Their preview observes first, through the daemon's read-only
  `workspace.self_host.git` views and the GitHub adapter
  ([producers](producers.md), injected by the router), and the digest covers
  those facts: a branch or `main` that moved, or another comment, is a 409.
  The protected paths of a land are computed here with
  `contracts/protected_paths` from the daemon's changed files and main's test
  map, as land.sh does. Blockers (`not_fast_forward`, `fix_not_on_origin`…)
  refuse a start; a land also refuses while a sprint is active, and conflicts
  with a self-hosting run and a push.
- `owner_git_runner.py`: their jobs, one call each: `workspace.self_host.land`
  (with `land_owner` only after the step-up, `step_up.py` in
  [platform](platform.md)), `workspace.self_host.push`, or the comment and the
  close on GitHub with exactly the confirmed text. land.sh's verification
  record is stored as it wrote it (`verification_record`, a claim), apart
  from what the daemon observed (`main_before`, `main_after`, `exit_code`).
- Routes: `routers/owner_actions.py` (and `GET …/{name}/options`: the
  daemon's branches or the open issues to pick from); the inbox lists the catalogue for an
  Owner (`owner_actions`) and marks a blocked task that names an action.

### The segregation-of-duties report

`sod_report.py`: `GET /v1/companies/{id}/sod/report` (Manager): every approval
beside its requesters, the refusals, and the matrix of duties per member.
Computed from records only ([design](../archive/design/SEGREGATION_OF_DUTIES.md)).

### The Support agent on the intake queue

`intake_triage.py`: in the Support agent's loop, up to five untriaged requests
get a suggested decision, category and duplicate, stored in `suggested_*`. A
Manager decides ([planning](planning.md#the-intake-queue)).

### The Reviewer and Support agents

`reviewer_agent.py` suggests a decision on another producer's run nobody has
decided; `support_agent.py` triages help-desk tickets. Both store suggestions
beside what a person chose.

### The company's export

`company_export.py`: `GET /v1/companies/{id}/export` (Owner) returns the
company's configuration as one file: its settings (an explicit field list),
agents, business units, routines and provider connections. A provider's key
and headers and a project credential's value are left out and named in
`required_secrets`; every string is read with the Verifier's secret detectors
(`evidence/standards`) and a secret-looking line is replaced, its place
listed in `redacted`; a URL's user and password are dropped. No members,
emails or history. `manifest.sha256` is the backend's digest of the content,
recorded in the `company_exported` audit row. From Paperclip's company
portability export (MIT).

### Importing a company

`company_import.py`: `POST /v1/companies/import` (any signed-in person)
creates a company from an export file, owned by the caller, with its agents,
business units and routines. Each value passes the check of its own route;
what fails is listed in `skipped`, never half applied. A file never sets the
stack commands, research hosts, sandbox, console producers, protected paths,
forge policy or integration review, and level 3 autonomy becomes 2: the
Owner sets those on their own audited routes. Routines arrive turned off;
provider connections are listed under `to_set_up`, not created. The
backend's digest of the content and the file's claimed one are both in the
`company_imported` audit row, with whether they match.

## See also

- Settings (`MANAGER_AGENT_ENABLED`, `REVIEWER_AGENT_ENABLED`, `SUPPORT_AGENT_ENABLED`, `RSI_*`, `SMTP_*`): [configuration](../CONFIGURATION.md).
- The inbox the agent reads: step 6 of [the sprint loop](../SPRINT_LOOP.md).
- The Review page, the policy panel and the record card: [frontend](frontend.md).
