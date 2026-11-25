# The release context

What leaves the factory once accepted: publishing a branch, merging locally, a
sprint's integration and its review, undoing a delivery. Read this before
changing `backend/app/services/release/`; each module's docstring has the
detail.

- **Folder:** `backend/app/services/release/`
- **May import:** [contracts](contracts.md), [platform](platform.md), [tasks](tasks.md), [models](models.md), [evidence](evidence.md), [workspace](workspace.md), [planning](planning.md), [loop](loop.md).
- **Interface:** `delivery_undo`, `git_publish`, `integration`, `integration_review`, `local_merge`, `protected_merge`, `self_change`.
- **Modules:** `auto_publish`, `base_sync`, `delivery_undo`, `folder_check`, `git_publish`, `git_setup`, `integration`, `integration_events`, `integration_jobs`, `integration_publish`, `integration_review`, `integration_state`, `local_merge`, `protected_merge`, `review_sync`, `self_change`.

## How it works

Release starts when the loop has closed work, through an event, never from
inside the loop. When a sprint closes, the task worker emits `sprint.closed`;
`integration_jobs.py` records a `running` integration and asks the daemon to
merge the done task branches into `fabrique/sprint-<id>` and run the suite.
When that integration passes its suite and the company's `forge_policy` is
`publish`, `auto_publish.py` opens it as a draft pull or merge request on its
own; otherwise a person publishes it. Either way a person reviews it, then
merges it locally or on the forge. Every git operation runs on the daemon; the backend records what
the daemon observed.

## Rules

- Nothing leaves the computer for a forge unless the company's
  `forge_policy` allows it (`none` by default, then `fetch`, then `publish`),
  capped by `FORGE_POLICY_MAX`. The backend refuses with `forge_disabled`, and
  the daemon checks again on its own. The local merge, the integration and its
  diff never contact a remote.
- A local merge into the base branch is a release: `sod.enforce` runs before
  the daemon is asked, so the person who started the sprint does not merge or
  approve its work (403, audited `sod_refused`). Design:
  [`SEGREGATION_OF_DUTIES.md`](../archive/design/SEGREGATION_OF_DUTIES.md).
- A diff is never the client's: the backend asks the daemon and computes
  `diff_sha256` from the per-file hashes. Review words are claims; commit,
  merge base and hashes are observations.
- In a published request, files are listed as the daemon read them back; a
  producer's diff or summary is marked as a claim.
- Merging a self-improvement cycle's work, or a protected path, needs a
  signed-in Owner: never the Manager agent, an API token or a Manager.

## Components

### Publishing and merging

- `git_publish.py`: publishes a task's branch as a pull or merge request
  through the daemon, with the person's own credentials; the description is the
  run's evidence. `require_forge` holds the forge policy.
- `auto_publish.py`: after a closed sprint's integration ends `tests_passed`,
  publishes it as a draft with `integration_publish.publish_spec`, as
  `La Fabrique, after the sprint closed` (audited `system:auto_publish`). A
  DevOps agent on the team adds its account (what changes, how to test it,
  risks) under a heading that names it a claim; it reads only the platform's
  record. Why a request did not open is kept in `details["auto_publish"]`.
- `local_merge.py`: merges a task's or the integration's branch into a local
  base branch, with no remote. A conflict changes nothing.
- `review_sync.py`: brings a published request's comments back as
  `ReviewComment`s, never duplicated.
- `git_setup.py`: sets the Git workflow from what the daemon observed in the
  folder (`git.is_repo`), never from what the request says.

### The forge policy and the base

- `folder_check.py`: what the New company form may learn about a folder's git
  before the company exists. Restricted callers, rate-limited, audited, and the
  answer limited to branch names, flags and counts.
- `base_sync.py`: fast-forwards the company's local base from its remote,
  under the forge policy, at creation or later from the settings. What the
  daemon observed is recorded as `base_sync_observed`. A refusal never stops a
  company's creation.

### A sprint's integration

- `integration.py`: orders the task branches by dependency and integrates them
  with real git on the daemon, stopping at the first conflict. Running it again
  only moves forward. One integration of a sprint runs at a time across
  replicas, and the suite runs in a worktree pinned to the integrated commit.
- `integration_jobs.py`: the job that survives a restart; `retry_loop` retries
  `pending` integrations.
- `integration_publish.py`: publishes the integration as one request, pinned to
  its commit and refused when the remote base has commits the branch lacks.
- `integration_state.py`: `integrating`, `integrated`, `failed` or `none`
  beside a sprint's `status`, read from its latest integration.
- `integration_events.py`: each task's part in the integration as a task
  event; the task's status does not change.

`conflict`, `tests_failed`, `incomplete`, `pending` and `error` wait for a
person in the inbox.

### A person's review of the integration

`integration_review.py`: the integration is reviewed as a run is
([loop](loop.md#reviews-of-a-run)), published or not. A Manager comments on
lines of the daemon's diff and submits `comment`, `approve` or
`request_changes`. Only the latest integration may be reviewed. An approval is
held to segregation of duties, and an integration stopped at a conflict or
whose suite failed cannot be approved.

With `integration_review_required` on (an Owner's choice), the local merge is
refused unless the latest decision is an `approve` of the integration's
commit. A published integration is decided by the forge's own review; nothing
is posted there.

### Protected paths at the merge

`protected_merge.py`: for a company with `protected_paths_enabled`. Before
the integration merge, the daemon's diff is matched against
`evidence.protected_check.patterns_in_force`; what tasks declared is never
read. A match merges only when a signed-in Owner's approval names every
matched path and was made on the current `diff_sha256`. Otherwise the merge is
a 409, audited `integration_merge_refused_protected_paths`. A truncated diff,
a missing path list or a disagreement between the list and the parsed patch
also refuses. Design: [`rsi-loop.md`](../design/rsi-loop.md) §3.3.

`self_change.py`: `require_owner` refuses (403, audited
`self_change_merge_refused`) the merge of a self-improvement cycle's
integration or task branches unless the merger is a signed-in Owner.

### Undoing a delivery

`delivery_undo.py`: undoes a task's delivery for a company without the Git
workflow, from a snapshot the daemon keeps in a separate store, never the
project's `.git` (after opencode's snapshots). All or nothing: a file changed
since the delivery is a 409 and nothing is touched. A second undo is refused.
Files only; the task's status stays. A delivery on a Git branch is refused
(discard the branch instead).

## See also

- The Git workflow and the workspace routes: [workspace](workspace.md).
- The forge policy setting and protected paths flag: [platform](platform.md).
- Settings (`FORGE_POLICY_MAX`, `INTEGRATION_MAX_CONCURRENT`, `INTEGRATION_RETRY_SECONDS`): [configuration](../CONFIGURATION.md).
- Self-improvement cycles and the Manager inbox: [management](management.md).
- How the context was moved into its folder: [the migration record](../architecture/MIGRATION.md#step-12-release).
