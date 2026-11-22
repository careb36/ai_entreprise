# The management context

The owner as a client: the Manager agent (inbox, policy, rules, observations,
actions, record), the client's brief, the self-improvement cycle's record, the
Reviewer and Support agents. Read this before changing
`backend/app/services/management/`; each module's docstring has the detail.

- **Folder:** `backend/app/services/management/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md), [evidence](evidence.md), [workspace](workspace.md), [tools](tools.md), [learning](learning.md), [planning](planning.md), [producers](producers.md), [loop](loop.md), [release](release.md), [integrations](integrations.md). Nothing imports it.
- **Interface:** none.
- **Modules:** `brief_mail`, `intake_triage`, `manager_actions`, `manager_actions_extra`, `manager_agent`, `manager_brief`, `manager_inbox`, `manager_inbox_extra`, `manager_observe`, `manager_policy`, `manager_record`, `manager_record_extra`, `manager_rules`, `reviewer_agent`, `rsi_cycle`, `rsi_cycle_import`, `rsi_cycle_steps`, `rsi_cycle_view`, `sod_report`, `support_agent`.

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

### The segregation-of-duties report

`sod_report.py`: `GET /v1/companies/{id}/sod/report` (Manager): every approval
beside its requesters, the refusals, and the matrix of duties per member.
Computed from records only ([design](../design/SEGREGATION_OF_DUTIES.md)).

### The Support agent on the intake queue

`intake_triage.py`: in the Support agent's loop, up to five untriaged requests
get a suggested decision, category and duplicate, stored in `suggested_*`. A
Manager decides ([planning](planning.md#the-intake-queue)).

### The Reviewer and Support agents

`reviewer_agent.py` suggests a decision on another producer's run nobody has
decided; `support_agent.py` triages help-desk tickets. Both store suggestions
beside what a person chose.

## See also

- Settings (`MANAGER_AGENT_ENABLED`, `REVIEWER_AGENT_ENABLED`, `SUPPORT_AGENT_ENABLED`, `RSI_*`, `SMTP_*`): [configuration](../CONFIGURATION.md).
- The inbox the agent reads: step 6 of [the sprint loop](../SPRINT_LOOP.md).
- The Review page, the policy panel and the record card: [frontend](frontend.md).
