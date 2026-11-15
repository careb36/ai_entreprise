# Saved views of a sprint's tasks (issue #535)

The board is a kanban with no filters, so it cannot answer the questions a
Manager asks about the evidence: every refuted delivery this month, the tasks
waiting for a person by producer, the tasks covering REQ-3. A **filter** is a
JSON tree of conditions over an allowlist of fields, evaluated on the server
from the task projections; a **saved view** keeps a filter with how its tasks
are shown, for its author or for the company.

The design follows Plane (AGPL-3.0-only, commit
`290383d7f92fa8995ea2adbd12442220b3b9b525`), design only, no code taken:
`db/models/view.py` (`IssueView`: filters, display filters and properties,
access, `is_locked`, the owner), `db/models/cycle.py` (per-user display
preferences kept apart from the shared object),
`packages/types/src/rich-filters/expression.ts` (a tree of conditions under
AND/OR groups), `utils/filters/filter_backend.py` (a server-side allowlist of
filterable fields) and `apps/web/components/issues/issue-layouts/` (a list
layout beside the kanban).

## The filter

```json
{"and": [
  {"field": "verdict", "op": "eq", "value": "refuted"},
  {"or": [
    {"field": "risk_tier", "op": "gte", "value": "medium"},
    {"field": "covers", "op": "eq", "value": "REQ-3"}
  ]}
]}
```

`services/contracts/task_filters.py` holds the contract. A group has one key,
`and` or `or`, and a non-empty list of conditions or groups; a bare condition
at the top stands for a group of one. The fields and what each takes:

| Field | Read from | Operators | Value |
|---|---|---|---|
| `status` | the task's row, a status outside the five read as `todo` as the board places it | `eq` `ne` `in` | one of the five statuses |
| `verdict` | the Verifier's latest verification of the task's latest run | `eq` `ne` `in` | `verified`, `refuted`, `unverifiable`, or `null` (never verified) |
| `risk_tier` | the `risk_tier` check of that verification | `eq` `ne` `in` `gte` `lte` | `low` < `medium` < `high`, or `null` |
| `producer` | the task's latest run | `eq` `ne` `in` | text, 1 to 200 characters |
| `assignee_role` | the task's assignee, read through `contracts/roles.canonical` | `eq` `ne` `in` | text |
| `rework_count` | `TaskRecord.rework_request_count` | `eq` `ne` `in` `gte` `lte` | an integer ≥ 0 |
| `waits_for_person` | `TaskRecord.waits_for_a_person` | `eq` `ne` | `true` or `false` |
| `covers` | the task's `covers`; `REQ-3` matches `REQ-3` and its criteria `REQ-3.x` | `eq` `ne` `in` | `REQ-n` or `REQ-n.m` |
| `sprint_id` | the task's sprint | `eq` `ne` `in` | a sprint id |

A field outside the allowlist, an operator the field does not take or a value
of the wrong kind is refused with `FilterRefused`, which the API answers with
422 and `detail.field` naming the field. Nothing is ignored and no task is
evaluated. The tree is bounded: 4 levels of groups, 20 members per group, 50
conditions, 50 values in an `in`, 8 KB of JSON.

The verdict and the risk tier are what the Verifier observed, never what an
agent said of its own work: a run's `claims` are not read
(`docs/POSITIONING.md`, rules on claims and observations).

## Evaluation

`services/tasks/task_filter.query` reads the company's tasks (joined to their
sprints by `company_id`), narrowed to the sprints a top-level `and` names with
`sprint_id` `eq`/`in`. That narrowing is a SQLAlchemy `IN` with bound
parameters; no text of the request reaches SQL. Every condition is evaluated
in Python against the task's facts. Only the facts the filter, the order and
the grouping read are loaded for every task (the events in one query through
`task_projection.records_for`, the runs and verifications in chunks of 900
ids); the others are loaded for the page returned.

Because the facts are the ones the board and every decision read, **the board
and a view give the same counts for the same filter**:
`test_REQ_2_the_board_and_a_view_give_the_same_counts` compares each column
of `GET /v1/sprints/{id}/board` with `GET /v1/tasks` filtered on the column's
status and the sprint. A filter over 5,000 tasks answers within the page
budget of [`docs/ux/performance_usability_benchmark.md`](../ux/performance_usability_benchmark.md)
(400 ms, the Doherty threshold; the specification of #535 asks 300 ms), which
`test_a_filter_over_five_thousand_tasks_answers_within_the_page_budget`
measures on SQLite.

## Saved views

`models/saved_view.py`, migration `0033_saved_views.py`. A `SavedView` is
company-scoped: `name`, `filters` (the parsed tree, `{}` for every task),
`group_by`, `order_by` (`-` in front for descending), `layout` (`board` or
`list`), `visible_columns`, `access` (`private` or `company`), `locked`, its
author and who changed it last. The settings come from allowlists of their own
(`GROUP_FIELDS`, `ORDER_FIELDS`, `COLUMNS`); any other value is a 422 naming
the setting. A person's display preferences (collapsed groups, column widths,
density) are a `SavedViewPreference` of their own, one per view and person,
so they never change a view the company shares.

| Who | Lists and reads | Creates | Changes or deletes |
|---|---|---|---|
| Its author | their private views and every company view | a private view | their private view; their company view unless it is locked |
| Viewer | company views | private views only | not a locked view, not another's |
| Manager, Owner | company views (not another's private view) | private or company views, locked or not | any company view, locked or not; locking and unlocking |

Another's private view reads as missing (404), also through
`GET /v1/tasks?view_id=`. A stored filter is parsed again whenever the view is
read: a view naming a field taken off the allowlist is listed with
`valid: false` and its reason, and filtering through it is a 422, never a
partial answer. Writes are audited under `company:<id>:views`.

## API

| Method and path | Role | What |
|---|---|---|
| `GET /v1/tasks?company_id=&filter=<json>&view_id=&order_by=&group_by=&limit=&offset=` | Viewer | `{"tasks": [...], "count": n, "groups"?: {...}}`; with `view_id` the view's filter and the request's both apply |
| `GET /v1/companies/{id}/views` | Viewer | the views this person lists, the allowlists and `can_share` |
| `POST /v1/companies/{id}/views` | Viewer (company or locked: Manager) | a new view |
| `GET`, `PUT`, `DELETE /v1/views/{id}` | as the table above | read, change (merged over the stored settings), remove |
| `GET`, `PUT /v1/views/{id}/preferences` | Viewer who reads the view | this person's display preferences |

## The page

The sprint's Board tab (`components/SprintTaskViews.tsx`) offers the Board
and List layouts, the filter builder (`components/TaskFilterBuilder.tsx`, one
labelled row per condition, joined by all or any) and the saved views. With
no filter and the Board layout it is the kanban as before, moves included;
otherwise the filter, limited to the sprint, is evaluated by the server and
the tasks are shown read-only (`components/TaskListView.tsx`: a table, grouped
when the view says so, or columns by status).

## Custom columns (#437) as views

[`docs/WORKFLOWS.md`](../WORKFLOWS.md) §3.5 designs custom columns as views
over the five statuses. Under this design a custom column is a saved view
whose filter holds `status` `eq` its status (plus any refinement, such as a
verdict or a risk tier) and whose layout is `board`: the column never stores a
status of its own, so task events, the history checker, dependencies and
sprint closing keep reading the five statuses. Columns are not built here.
