# Segregation of duties and the periodic access review (issue #543)

Status: draft, written with the `threat-model` skill; a person reviews it
before it guides more work · Issue: #543 · SDL practice 3 · Builds on teams
and effective roles (#512, `docs/design/TEAMS_AND_USAGE.md`)

Taken from IT general controls audit practice and PCI DSS 4.0 (Requirement
6.5, change management: who requests or makes a change does not approve it,
emergency changes on their own path; Requirement 7.2.4, access reviewed
periodically, inactive accounts handled, service accounts inventoried).
Structure only; no text was copied.

## What is built

**(1) The rule, enforced.** At each approval point the backend compares the
account approving with the accounts that asked for or wrote the work, read
from records; when they are the same account, the approval is refused (403),
the refusal is an audit row (`sod_refused`, with the action, the subject, the
actor and the requesters) and nothing else changes. The rule is one pure
function, `services/platform/sod.decide`; `sod.enforce` applies it with the
company's policy.

| Approval point | Action | Who asked for the work (record) | Code |
|---|---|---|---|
| Accepting a held delivery from the web (`POST /v1/agent_runs/{id}/reviews`, `approve`) | `accept_held_delivery` | the account that started the task's sprint (`sprints.started_by_user_id`) | `services/loop/run_review._segregate` |
| Marking a held delivery done on the board (`PATCH /v1/backlog/{id}`, `status: done`), which the hold's note offers | `accept_held_delivery` | the same | `run_review.segregate_board_acceptance`, called by `routers/sprint.py` |
| The same from a Slack or Teams button (`routers/chat_interactions.decide`, which calls `submit_review`) | `accept_held_delivery` | the same | the same; the claimed message is released so another person can decide |
| Approving a specification (`POST /v1/specifications/{id}/approve`) | `approve_specification` | the person in `drafted_by` (whoever last wrote or revised the draft; an agent's draft names nobody) | `routers/specifications.py` |
| Merging a run into the base branch (`POST /v1/agent_runs/{id}/merge`) | `release` | the run's sprint's starter | `routers/reviews.py`, refused before the daemon is asked |
| Merging a sprint's integration (`POST /v1/sprints/{id}/integration/merge`) | `release` | the sprint's starter | `routers/integration.py`, idem; the merge now records `merged_by_user_id` |
| Approving a sprint's integration in a review (`POST /v1/sprints/{id}/integration/reviews`, `approve`) | `release` (subject `sprint_integration_review`) | the sprint's starter | `services/release/integration_review._segregate`, after the daemon's diff is read; a refusal writes only its `sod_refused` row |
| Approving another producer's run (`reviews` `approve`, or `POST /v1/agent_runs/{id}/approve`) | `approve_run` | the starter of the sprint its subject names | `run_review._segregate`, `routers/agent_runs.py` |
| Approving an architecture (`POST /v1/architectures/{id}/approve`) | `approve_architecture` | the person in `drafted_by` | `routers/architectures.py` |

The company's **mode** (`sod_policies.mode`, an Owner's setting, audited
`sod_policy_updated`) says which actions are held: `high_risk` (the default:
held deliveries, specifications, releases), `all` (adds other producers' runs
and architectures) or `off`. Opening a pull request (`publish`) is not an
approval here: the platform's own review and branch protection decide it.

A person is matched by account id, else by email. An **API token acts for
its creator** (`sod.actor_of` reads `api_tokens.created_by`), so minting a
token does not get around the rule. With `AUTH_REQUIRED=false` and no token
there is no actor: the approval is `unattributed` and passes (a local demo).

**The single-person exception.** A company in which exactly one person can
approve (one member whose effective role is Manager or Owner, teams counted)
cannot separate duties. Its Owner may grant the exception with a reason of
10 to 500 characters (`POST /v1/companies/{id}/sod/exception`, audited
`sod_exception_granted`; withdrawn with `DELETE`, `sod_exception_withdrawn`).
It is honoured only while one person can approve: when a second person can
(a new Manager, or a Viewer whose team makes them Manager), the requester's
approval is refused again, with the reason. Each use is an audit row
(`sod_exception_applied`, with the reason) and is listed in the report.

**(2) The matrix and the violation report.** `GET /v1/companies/{id}/sod/report`
(Manager; `Cache-Control: no-store`; `services/management/sod_report.py`)
lists every approval recorded — held deliveries accepted and other runs
approved (`run_reviews`), specifications and architectures approved, run and
integration merges — with its requesters, approver and status:
`separated`, `exception` (the same person, matched to its
`sod_exception_applied` row), `violation` (the same person with no such row:
approved before this rule, or while it was off), `unattributed` (no recorded
requester) or `not_a_person` (an agent approved, such as the Manager agent
under its policy). `violations` lists both violations and exceptions; the
`sod_refused` rows are listed too. The matrix counts, per member (with their
effective role), per effective role and per agent role, how many times each
did each duty — request (sprints started), plan (drafts written), deliver
(runs by agent role), verify (the Verifier's verifications, QA's reviews, a
person's comments and change requests), approve, release (merges and
publications) — and flags a member who both requests and approves. All of
it is computed from rows the backend wrote; `AgentRunApproval.approver_name`
is a typed name, a claim, and is not read.

**(3) The access review.** `GET/POST /v1/companies/{id}/access-review`
(signed-in Owner only; `services/platform/access_review.py`). The listing
holds every member with their own role, their teams and roles, their
effective role, whether an SSO identity is linked, their chat links and their
last activity (the latest browser session use or SSO sign-in, observed), and
every API token that still works (role, creator, dates, last use). An
account with no activity, or none within `inactive_days` (90 by default), is
flagged inactive. The Owner decides each item, `confirm` or `remove`; the
POST is refused (422) until every listed item is decided, and an Owner does
not remove themselves (409). A removed member loses the membership and their
teams in the same transaction and their browser sessions end; a removed
token is revoked. The review is a row (`access_reviews`: the items as listed,
each decision, a summary, a note, whether it was on time) and an audit row
(`access_review_completed`, with the removed ids).

The schedule is a pure function (`access_review.schedule`): the next review
is due `review_interval_days` (90 by default) after the last one was
completed, or after the company's first audit row when there was none; it is
`scheduled` before that, `due` for 14 days, then `missed` until one is
completed, and a review completed while missed is recorded late
(`on_time: false`). With no record at all, one is due at once.

**(4) The service accounts inventory.** `GET /v1/companies/{id}/service-accounts`
(signed-in Owner only; `services/platform/service_accounts.py`): API tokens
(role and scopes, creator, age, last use, expiry, status), the outbound
webhook (host only, events), chat connections, the SSO client (whether it
holds a secret), model provider connections (whether they hold a key) and
GitHub webhook bindings, each with owner, scope and age; and for the
deployment, how the backend authenticates to NATS (method and user name),
that the daemon's credential is not observed from here, and whether
`LLM_PROXY_INTERNAL_TOKEN` is set. Never a value, a hash or a full URL.

**The page.** `/company/duties` ("Duties and access", Owner menu;
`frontend/src/pages/CompanyDuties.tsx`) shows the policy and the exception,
the access review with its schedule (a missed one as an alert), the report
and the inventory.

Migration `0036` adds `sod_policies` and `access_reviews`; no existing row
changes, and a company without a policy row gets the defaults.

## Assets

- The approval boundary itself: an approval is the evidence that a second
  person looked. Its value is that the approver is not the requester.
- The audit chain: refusals, exceptions, policy changes and reviews.
- Who can reach a company (members, effective roles, tokens).
- The names of service credentials (not their values, which never leave the
  places that hold them).

## Data flow and trust boundaries

```
Manager's browser ──B1── POST /reviews approve ──► run_review._segregate ──► sod.enforce ──► audit_log
Slack/Teams ──B9── signed interaction ──► chat_interactions.decide ──► submit_review ──► (same)
Manager's browser ──B1── POST /specifications/{id}/approve, /merge, /integration/merge ──► sod.enforce
Owner's browser ──B1── /sod/policy, /sod/exception, /access-review, /service-accounts
Manager's browser ──B1── GET /sod/report ──► run_reviews, specifications, architectures,
                                             run_publications, sprint_integrations, audit_log
```

No new boundary. The requesters are read from rows the backend writes from
the session (`started_by_user_id`, `drafted_by`), never from a request body
or an agent (B5).

## Threats

| ID | Boundary | STRIDE | Threat | Likelihood | Impact | Severity | Mitigation |
|---|---|---|---|---|---|---|---|
| S1 | B1 | E | The requester approves their own held delivery, specification or release | H | H | High | `sod.enforce` at each point; `tests/test_sod.py` (web review, board move, spec, merge, integration merge) |
| S2 | B9 | E | The requester approves from a chat button, a path the web check might miss | M | H | High | The chat decision calls the same `submit_review`; `test_a_chat_button_refuses_the_starter_and_keeps_the_message_open` |
| S3 | B1 | S/E | The requester mints an API token and approves with it | M | H | High | A token acts for its creator (`sod.actor_of`); `test_a_token_the_starter_made_is_refused_like_its_creator` |
| S4 | B1 | E | A Manager turns the rule off or grants the exception to approve their own work | M | H | High | Policy and exception are Owner-only, person-only (`routers/sod.owner_person`); every change audited; the exception is refused while two people can approve; tests for each |
| S5 | B1 | E | The exception granted while alone keeps working after the company grows | M | M | Medium | Eligibility re-checked at every use (teams counted); `test_the_exception_stops_when_a_second_person_can_approve` |
| S6 | B1 | R | A refusal or an exception leaves no trace | L | M | Low | `sod_refused` is committed before the 403 (the caller's rollback keeps it); `sod_exception_applied` carries the reason; tested |
| S7 | B1 | T | The requester is made to look like someone else (request body, typed name) | L | H | Medium | Requesters come only from session-written columns; `approver_name` and request bodies are never read |
| S8 | B1 | E | The Owner, alone, both requests and approves under a silent default | M | M | Medium | No silent bypass: without the exception a one-person company is refused too; the exception is explicit and listed |
| S9 | B1 | I | The report or the inventory leaks another company's data or a secret | L | H | Medium | Every query filters `company_id`; the inventory returns names, scopes and dates only (`test_the_inventory_names_owner_scope_and_age_never_a_value` asserts the token, webhook path and proxy token are absent); `no-store` |
| S10 | B1 | E | A Manager or token completes an access review and removes an Owner | L | H | Medium | Owner-only, person-only; the last Owner cannot be removed; an Owner does not remove themselves; tested |
| S11 | B1 | R | Access reviews stop and nobody notices | M | M | Medium | The schedule flags `missed`; a late review is recorded `on_time: false`; tested |
| S12 | B1 | D | A huge review payload | L | L | Low | At most 2000 decisions; every listed item must be decided, extra ones ignored |
| S13 | — | E | An agent (Manager agent) approves under a policy the requester set, an indirect path around the rule | M | M | Medium | Residual: the report lists such approvals as `not_a_person`; the Manager agent's policy is an Owner's (#394). Next: hold the agent to the policy's author |

Assume breach: if `sod.enforce` were bypassed at one point, the report still
lists the approval as a violation from the records, so the control fails
visibly rather than silently.

## Tests that prove the mitigations

`backend/tests/test_sod.py` (S1–S8: each refusal, the token, chat) and
`test_sod_policy.py` (the policy and the exception, the report's statuses
and matrix),
`backend/tests/test_access_review.py` (S9–S12: the schedule, inactivity,
the listing with team roles, decisions and removals, the missed flag and the
late record, the inventory without values),
`frontend/src/pages/CompanyDuties.test.tsx` and the menu cases in
`frontend/src/AppNavigation.test.tsx`.

## Residual risks

- A person moving a task QA did not approve (blocked for another reason) to
  done is a person's move, recorded as a task event, not an approval this
  rule holds; only the held delivery's acceptance is.
- `drafted_by` keeps the last person who wrote the draft; an earlier editor
  who later approves is not seen. Accepted until edits are recorded as events.
- An API token minted while authentication was off has no recorded creator;
  its approvals are `unattributed` and pass. Such tokens show in the access
  review and the inventory with no owner, to be removed there.
- A run ingested from another producer names no submitter; its requester is
  the sprint its subject names, else it is `unattributed`.
- The exception's audit row is written before the approval; if the approval
  then fails (a blocking check), the row stays and the report does not count
  it (it matches exceptions only to recorded approvals).
- A specification approval that uses the exception commits its audit row
  first, which releases the row lock taken by the route; acceptable for a
  one-approver company.

## Next (designed, not built)

**(5) Emergency changes.** A Manager marks a release or a held delivery as an
emergency with a reason; it is allowed even when the requester approves it
(or with the rule otherwise refusing), is recorded on its own path
(`emergency_changes`: subject, reason, who, when, a deadline, by default 72
hours), and requires an after-the-fact approval by another person before the
deadline. The report lists every emergency change with its state (`pending`,
`approved`, `overdue`); an overdue one is a violation, and the access review
shows open emergencies. Threats to cover first: the emergency path used
routinely to skip the rule (cap per period, list in the brief), the
after-the-fact approver being the requester (the same `sod.enforce`), and the
reason as untrusted text in chat (escaped as chat text already is).

Also next: intake requesters (#532) as requesters when intake lands; hold
the Manager agent to the author of its policy (S13); a reminder in the
Owner's inbox and the brief when a review is due or missed.
