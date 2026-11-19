# Teams and the usage report (issue #512, phase 5)

Status: draft, written with the `threat-model` skill; a person reviews it
before it guides more work · Issue: #512 · SDL practice 3

## What is built

**Teams.** A company's Owner groups its members in teams. A team has a name,
a description and a role scope, `Viewer` or `Manager` (never `Owner`), that
applies to its members within that company, so an organisation grants access
to a team rather than person by person. A member belongs to zero or more
teams. Their **effective role** in the company is the higher of their own
membership role and their teams' roles:

```
effective(member, company) =
    None                                          if no company_members row
    max(own_role, roles of the member's teams in this company
                  that are Viewer or Manager)     otherwise
```

- A team never makes anyone a member: a `team_members` row without a
  `company_members` row grants nothing. Removing a member
  (`DELETE /v1/company/{id}/members/{user_id}`, #524) also deletes their
  `team_members` rows of that company in the same transaction
  (`teams.drop_memberships`), so none is left behind.
- A team never grants `Owner`: a team row holding any other role (a database
  edited by hand) is ignored, and the API refuses one (422).
- Only teams of the same company count: `team_members.company_id` and
  `teams.company_id` are both checked.

The rule is one pure function, `services/platform/teams.effective_role`, read
through `teams.member_role` by `auth_policy.require_company_role` (every
company route), by the API token cap (`routers/api_tokens.py` when minted and
`services/platform/api_tokens.verify` on every use: a token never outranks
its creator's current effective role), by the Manager brief's recipients
(`services/management/brief_mail.recipients`) and by `/v1/auth/me`
(`effective_role` beside `human_role`; the frontend's `useCurrentRole` shows
the effective one). Places that ask for `Owner` (SSO linking, break-glass,
invitations) are unchanged, since a team cannot reach `Owner`.

Teams are managed only by a signed-in human Owner (`routers/teams.py`; an API
token is refused, whatever role it carries, since a team grants roles):
`GET/POST /v1/companies/{id}/teams`, `PATCH/DELETE …/teams/{team_id}`,
`PUT/DELETE …/teams/{team_id}/members/{user_id}` (only a member of the
company joins; 404 otherwise). Every change is an audit row (`team_created`,
`team_updated` with `changed: {field: {from, to}}`, `team_deleted` with the
members it had, `team_member_added`, `team_member_removed`, each with the
team's role) under `human:<email>`. Migration `0029` adds `teams`,
`team_members` and `sprints.started_by_user_id`.

**The usage report.** `GET /v1/companies/{id}/usage` (Manager, so a team can
grant it) and `GET /v1/companies/{id}/usage.csv` (Manager, audited
`usage_report_exported` with the range, the period and the generation time)
sum the company's records over a UTC date range (`since`, `until`, both
inclusive, at most 366 days; by default the last 30 days) by period (`day`,
`week` from Monday, `month`), person, team, agent role, producer and served
model (`services/models/usage_report.py`, `services/models/usage_csv.py`).

| Column | Read from | Notes |
|---|---|---|
| `tasks`, `rework` | `backlog_items` created in the range; `NEEDS WORK:` requests on its board | producer and model: its latest run |
| `runs` | `agent_runs` ingested in the range | `AgentRun.model` is the model the proxy reported serving |
| `verified`, `refuted`, `unverified` | the run's latest `verifications.verdict` | never the producer's own report |
| `approvals`, `changes_requested` | `run_reviews` (`approve`, `request_changes`) by `author_user_id` | web or chat (`submit_review`) |
| `turns`, `reported_turns`, `estimated_turns`, `unavailable_turns` | the LLM turns' audit rows (`run_cost.TURN_ACTIONS`) | |
| `input_tokens`, `output_tokens`, `reported_tokens` | only `reported` turns | the proxy's figures |
| `estimated_tokens` | `estimated` turns | counted apart, never priced |
| `cost_cents` (`cost_usd` in the CSV) | `run_cost.turn_cost` of the priced turns | `None`/empty, never 0, when none was priced |
| `cost_unknown_turns` | estimated, unavailable, unpriced or split-unknown turns | counted apart |

A **person** is the account that started the task's sprint
(`sprints.started_by_user_id`, set by `POST /v1/start_sprint` from the
session, never the body; an API token starts a sprint unattributed) for its
tasks, runs and turns, and the reviewer for a decision. A sprint the platform
opened, one from before `0029`, or a run not about a task is **unattributed**,
never guessed (`AgentRunApproval.approver_name` is a typed name, a claim, and
is not read). A person's **teams** are those they belong to when the report is
made; a person in two teams counts in both, so team rows may sum to more than
the total (the page says so). The proxy sees only the backend's own model
calls, so every turn is the task worker's producer; a console producer's
usage is its provider's and is not observed (its rows show runs, no tokens).

The report is deterministic: the same records and the same `now` give the
same JSON and the same CSV, byte for byte (rows sorted by key with
unattributed last, cents rounded to six places; `tests/test_usage_report.py`
builds it twice). Timestamps are read as UTC (an aware one converted), and
rows are read a day wider than the range and filtered exactly in Python, so a
row stored with an offset is not lost to a text comparison.

**Taken from Aixle Flow** (Apache-2.0, design only, no code):
`docs/design/project-teams.md` (a team is a named group of members of the same
tenant, managed by the tenant's admins, a member must already belong before
joining) and `docs/design/tenant-usage-report.md` (periods in UTC with weeks
from Monday; usage measured from the records so measuring again never changes
a number; the usage report separate from billing). Where it differs: Flow's
team picks one member to own a run, here a team carries a role scope; Flow's
tenant report left CSV out, this one exports it.

## Assets

- Company isolation and the role boundary (Owner > Manager > Viewer): a team
  is a new way to grant a role.
- The audit chain: who granted what, who exported what.
- People's activity per company (names, decisions, spend): the report is a
  view over it.
- The integrity of the numbers: observations, never an agent's claim.

## Data flow and trust boundaries

```
Owner's browser ──B1── PUT /teams/{id}/members/{uid} ──► teams, team_members ──► audit_log
Any member ──B1── any company route ──► require_company_role ──► company_members + teams
Manager's browser ──B1── GET /usage[.csv] ──► usage_report (agent_runs, verifications,
                                              audit_log turns, run_reviews, sprints, teams)
                         └── CSV ──► the Manager's spreadsheet (a new consumer, B1')
```

No new boundary beyond B1: the report reads only the backend's own rows.
The spreadsheet that opens the CSV is a consumer outside the product; the
cells it reads include text agents (B5: a role or a model id) and people (a
name, a team name) wrote.

## Threats

| ID | Boundary | STRIDE | Threat | Likelihood | Impact | Severity | Mitigation |
|---|---|---|---|---|---|---|---|
| T1 | B1 | E | A Manager or Viewer creates a team or joins one to raise themselves | M | H | High | Team writes are Owner-only and person-only (`routers/teams.py::_owner`); `tests/test_teams.py::test_only_a_signed_in_owner_reaches_teams` |
| T2 | B1 | E | A team reaches `Owner` (API value, or a row edited by hand) | L | H | Medium | Pydantic pattern and `check_fields` refuse it (422); `effective_role` ignores any role outside `TEAM_ROLES`; tests for both |
| T3 | B1 | E | A team row for a non-member (stale after removal, or another company's user) grants access | M | H | High | `effective_role(None, …) is None`; join refused for non-members (404); both company ids checked; `test_a_team_never_makes_a_member_nor_reaches_another_company` |
| T4 | B1 | E | Access stays after a person leaves the team | M | M | Medium | The role is computed on every request (no cached role, the JWT carries none); `test_a_manager_team_grants_manager_routes_until_the_person_leaves_it` |
| T5 | B1 | E | A token minted while a team made its creator Manager outlives the team membership (or the creator is demoted or removed) | M | M | Medium | Closed: on every use `api_tokens.verify` reads the creator's current effective role in the token's company; a token above it, or one whose creator is no longer a member or has no account, is refused (401) and audited `api_token_refused` with the reason (`creator_role_below_token`, `creator_not_a_member`). Refused, not lowered (below). The creator is matched by account id (`created_by_user_id`, migration `0048`), so a renamed or reused email address neither drops nor hands on a token's standing; a row the migration could not match keeps the email rule. `tests/test_api_tokens.py`: demotion, removal, a gone account, an equal role, an Owner creator, a team seat ended, the HTTP 401, a rename, a reused address, the backfill |
| T6 | B1 | R | Grants and exports happen without a record | L | M | Low | Every team change and every CSV export is an audit row under `human:<email>` with the change; tested |
| T7 | B1 | I | The report shows another company's data | L | H | Medium | Every query filters `company_id` (tasks through their sprint's company); `test_another_company_sees_none_of_it` |
| T8 | B1 | I | The report exposes people's activity and names beyond who should see it | M | M | Medium | Manager+ only (a Viewer gets 403); names only, never emails (the Owner-only Teams page shows emails); `Cache-Control: no-store` on the CSV |
| T9 | B1' / B5 | T/E | CSV injection: a name, team, role or model id starting with `=`, `+`, `-`, `@`, tab or CR runs as a formula in the Manager's spreadsheet (OWASP WSTG-INPV, CSV injection) | M | H | High | `usage_csv.safe_text` prefixes `'` on every text cell starting with one of them; numbers are written as numbers; the `csv` module quotes the rest; `test_the_csv_document` asserts no cell starts with a formula character |
| T10 | B1 | D | A huge range or many rows exhausts the worker | L | M | Low | At most 366 days; reads are by indexed company id and time; one query per table |
| T11 | B5 | T | An agent's claim inflates or hides usage | L | M | Low | Only observations are read: the proxy's usage on the audit row, `AgentRun.model` as the proxy reported, verdicts from `verifications`; a producer's claims and `approver_name` are never read (POSITIONING rules 1, 2) |
| T12 | B1 | S | A person attributed with a sprint they did not start | L | L | Low | `started_by_user_id` comes from the session, never the body; API tokens start unattributed |

Assume breach: if `require_company_role` were bypassed, the team routes still
check `_owner` themselves and refuse tokens. If an Owner account is taken over,
teams add no power the Owner did not have (invitations already grant
Manager); the audit rows name the account.

## Tests that prove the mitigations

`backend/tests/test_teams.py` (T1–T6, the rule and its places),
`backend/tests/test_usage_report.py` (T7–T12, the numbers, unknown cost,
periods, determinism, CSV escaping, the endpoints' roles and the audit row),
`frontend/src/pages/CompanyTeams.test.tsx`, `frontend/src/pages/Usage.test.tsx`
and the menu cases in `frontend/src/AppNavigation.test.tsx`.

## Residual risks

- T5 is closed for tokens with a recorded creator. A token minted while
  authentication was off has none and is not capped; the access review and
  the service-account inventory show it with no owner, to be removed there.
- A refused token is not revoked: promoted back, its creator carries it again.
  An Owner who wants it gone revokes it (the access review lists it).

**Why refuse rather than lower the token's role (T5).** The token's role is
what its holder, usually a script, was promised. Lowered silently, a Manager
token of a demoted creator would keep reading and fail only on writes, a
half-working integration whose cause is invisible to the holder; refused, it
fails at once and the Owner sees why in the audit chain. Refusing is also
the narrower rule: the request never acts with a role nobody holds, and no
code path has to compute a role different from the one stored on the token.
The holder gets the same 401 as for a revoked or expired token, so a leaked
token does not learn that its creator was demoted or left. The refusal is
audited at most once per token, reason and hour, so a script retrying does
not flood the chain.
- A person's team in the report is today's, not the one at the time of the
  work; the report says so.

## Next

- **SSO group → team mapping** (not built): per SSO connection a claim name
  (`groups` by default; Entra `groups` with object ids, Google through the
  Directory API, so the claim is not universal) and a list of `(group value,
  team id)` pairs the Owner writes. On each SSO sign-in, the observed claim
  sets the person's membership in the mapped teams only (never in a team the
  Owner manages by hand), audited with the claim values as observed. Threats
  to cover first: an IdP asserting a group to grant Manager (bounded by the
  same "within its company" trust as SSO linking, `docs/design/SSO.md`), a
  missing claim removing access (treat absent as "no change", present-and-empty
  as "remove"), and group overage tokens (Entra omits the claim past 200
  groups). Threat model, data model and rules (design only, issue #703):
  [`SSO_GROUP_MAPPING.md`](SSO_GROUP_MAPPING.md).
- **Personal notifications** from task events (read, snooze, preferences; the
  Plane study in the #512 comments): design only, not started.
- A usage history that keeps the team a person was in at the time of the work.
