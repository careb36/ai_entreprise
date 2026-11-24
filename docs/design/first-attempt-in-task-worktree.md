# A task's first attempt in its own worktree: a design

> **Status: built, 2026-11-24.** Written for the owner's review. Under the
> company's Git workflow the task's worktree now exists before the first
> agent turn, and every tool of that attempt runs in it. Every rule of
> [`docs/POSITIONING.md`](../POSITIONING.md) applies unchanged.

## Why

With the Git workflow on, each task delivers on `fabrique/task-<id>`, in a
worktree the daemon keeps under the repository's git directory
(`<git dir>/fabrique-worktrees/fabrique__task-<id>`,
`vscode-daemon/git_workspace.py`). The daemon made that worktree only at the
task's **first delivery** (`git_workspace.deliver` → `worktree_for`).

So a task's first attempt ran its tools in the person's own checkout
(`developer_tools.tool_root` falls back to the project when no verification
names a worktree). Nothing may be written there, so
`workspace_edits.allowed` returned false and `workspace__edit`,
`workspace__move` and `workspace__delete` were never offered on a first
attempt. Only reworks, whose tool root is the worktree recorded on the latest
verification, had them. Across all verifications up to 2026-11-24, edit,
move and delete were never called. A task that splits a file needs move on
its first attempt.

## What it does

1. **Daemon.** `workspace.console.prepare` (the console producers' subject,
   `console_run.prepare`) takes a new optional field `private_git`. With
   `false`, the worktree is made exactly as for a console producer, from the
   same spec as a delivery (`branch`, `base`, `merge`, `forge_policy`): the
   branch from the base when it is new (fetched only as the forge policy
   allows, `git_base`), the done prerequisites merged, put back at its `HEAD`
   with untracked files removed, a rewritten `.git` pointer repaired. What
   changes is that git's own pointer is **kept**: no private repository. The
   reply says `private_git: false`. The daemon announces the feature:
   `"workspace.console.prepare": ["forge_policy", "private_git"]`
   (`abilities.py`, PROTOCOL.md §5.1.5). No new subject, so no NATS
   permission changes and no new daemon module.
2. **Backend.** Before the first turn, `worker/execute` calls
   `developer_tools.enter_task_worktree` → `task_worktree.enter`. It asks
   only when the company has the Git workflow (`git_workflow.task_spec`) and
   **every** daemon announces `private_git` (`daemon_liveness.announces` is
   `true`). A reply is used only when it is `ok`, says `private_git: false`
   and names `…/fabrique-worktrees/<branch with / as __>`, never the
   project folder. The worktree then becomes the attempt's tool root:
   `tool_root` returns it (`prepared_for`) to the repository map, the
   related code, the reads, `run_tests` and the edit session.
   `workspace_edits.allowed` is true there (it is not the person's
   checkout), so edit, and move and delete when announced, are offered from
   the first attempt. `leave` clears it when the tools are done.
3. **Delivery, unchanged.** `workspace.generate` still sends the project
   folder with the same `git.branch`. `git_workspace.deliver` finds the
   branch, reuses the same worktree, merges nothing new (already in) and
   commits on it. Its `parent` is the `head` the prepare reported.
4. **Evidence.** The daemon's reply (`worktree`, `branch`, `head`,
   `merged`, the base observations, or `ok: false` with `error`) goes on the
   run's evidence as `tools_offered.task_worktree`: where the attempt's tools
   ran, the backend's own record, apart from anything the model says.

## Older daemons and failures

- A daemon that does not announce `private_git` (or no heartbeat heard yet):
  nothing is sent, and the attempt runs as before (reads in the person's
  checkout, no write tools). An older daemon would ignore the field and give
  the worktree a console producer's private repository, which the tools
  must not use.
- A prepare that fails (a folder that is not a repository, a conflicting
  prerequisite, a timeout): `ok: false` with why on the evidence, and the
  attempt runs as before. The delivery then reports the same problem as
  today.

## What moves earlier

The prerequisites' merges and the base fetch now happen at the prepare, not
at the delivery. The delivery's `git.merged` is then empty and its base
observations (`base_commit`, `base_behind_remote`) are absent, because the
branch already exists. Both are on `tools_offered.task_worktree`. No backend
reader uses the delivery's `merged`. Merging first is also what keeps edits
honest: an edit session's final text is written whole, so a file edited
before a prerequisite's merge would overwrite that merge's change to it.

## Undo and snapshots

Not affected. A Git delivery is undone by discarding the task's branch
(`delivery_undo`), and the prepare adds no commit of its own, only the
prerequisites' merges the delivery would have made. The snapshot model is
only for deliveries without the Git workflow, where nothing is prepared.

## Threat model (STRIDE, the agent as the adversary)

| Threat | Example | Mitigation |
|---|---|---|
| Tampering: write outside the worktree | `workspace__edit` on `../../config` (the worktree sits inside the person's git dir) | The daemon's resolver, as for every edit and read: no `..`, no symlink out, never VCS metadata (the `.git` pointer included). The edit session puts every original back at finish; only the delivery writes, and it commits only in the worktree. |
| Tampering: the person's checkout | Get the tools pointed at the checkout so edits land there | The tool root comes from the daemon's reply, never from the model. The backend uses it only if it is `ok`, `private_git: false` and names `fabrique-worktrees/fabrique__task-<id>`, not the project folder; otherwise the attempt runs as before, without write tools. `allowed` still refuses the checkout of a branch-delivering company. The daemon's prepare runs `reset` and `clean` only with the worktree's own `--git-dir` and `--work-tree`. |
| Tampering: branch name injection | A branch like `main` or `--upload-pack=…` so the prepare resets the person's branch | The spec is built by the backend from the task's integer id (`fabrique/task-<id>`) and the company's validated base (`git_workflow.valid_base`). The agent has no input into it. The daemon accepts only `valid_branch` names under `fabrique/`, and git runs without a shell. |
| Tampering: stale worktree reuse | Files a crashed run or an earlier sprint left in the worktree, or a `.git` pointer the agent's test run rewrote | Every prepare puts the worktree back at the branch's `HEAD` (`reset --hard`, `clean -ffd`) and repairs the pointer before any git runs there. A console run still active there refuses the prepare. The branch's commits are the task's own earlier deliveries, which is intended. Residual: ignored files (caches, `node_modules`) stay, as on a rework today. |
| Spoofing | The model claims its edits were in the worktree | Where the tools ran is the daemon's reply on the evidence (`tools_offered.task_worktree`). Every edit's hashes come from the daemon (move-and-delete design). |
| Repudiation | Which tree an attempt read and edited | `tools_offered.task_worktree` records the worktree and its `head`. The delivery's `git.parent` equals that `head`, and the commit holds the change. |
| Information disclosure | Read a secret through the new root | The same read rules (`is_sensitive`, redaction). The worktree holds the repository's tracked files, which the person's checkout already exposed to the read tools. |
| Denial of service | A prepare that hangs and blocks the attempt | It is bounded by the delivery's timeout plus the fetch allowance (`git_workflow.delivery_timeout`). On failure the attempt runs as before. |
| Elevation of privilege | An older daemon given `private_git: false` adds a private repository that a tool then runs git in | Sent only when every daemon announces `private_git`, and a reply without `private_git: false` is not used. |

## Not built

- Preparing the worktree for the Architect's consultation. It reads the
  project as before. Only the attempt that delivers gets the worktree.
- A dedicated subject (for example `workspace.git.prepare`). Reusing
  `workspace.console.prepare` avoided a NATS permission change. A rename can
  come later behind a new announced feature.
