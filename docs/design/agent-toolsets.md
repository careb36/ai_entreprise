# The tools each role should have: a design

> **Status: proposed, 2026-11-24; the step/role rule approved by the owner the same day.** Written for the owner's review after an
> audit of 108 real runs (1460 tool calls). The owner's concern: agents must
> have the useful, necessary tools to work. Every rule of
> [`docs/POSITIONING.md`](../POSITIONING.md) applies unchanged.

## Why

Tools today follow a role list, not the work. Only `roles.FILE_ROLES`
(the developer roles, QA and the Designer) get workspace tools in a task's
execution turn, and every other step of the loop is a plain chat. The audit
found the cost of that:

- A task assigned to the Architect ran five times with no tool and was
  blocked ("research-only: there is no shell/exec"); one under SecOps ran
  twice with none.
- 8 of 75 Architect briefs were only a tool call typed as text, 12 guessed
  file paths, 15 told the Developer to run commands it cannot run.
- The QA reviewer judged excerpts; 9 verdicts said the excerpt was cut
  "exactly where it matters", 7 said it could not verify.
- The TechWriter, who writes documentation, gets no file tools at all; nor
  does SecOps, who reviews code.
- The PO plans from a root listing that the backend cannot read (the project
  is not mounted in its container).

## Principles

1. **Reading is never withheld.** Every agent that decides something about
   the project can read it: list, read, search, find a file, and read-only
   Git. Reading changes nothing and is observed.
2. **Writing follows the task, not the role.** A turn that delivers files can
   edit, move and delete in the task's worktree, whatever the role. The
   person's checkout is never written.
3. **Running follows verification.** A turn that delivers or verifies can run
   the folder's tests (narrowed) and the project's committed check scripts.
   Never a shell.
4. **Research where a choice is made.** A turn that picks a dependency, an
   action, an image or a standard can query the registries and advisories
   and fetch allowed hosts.
5. **The record of a run is readable.** Agents that judge or plan (reviewer,
   ScrumMaster, Architect on a rework, Manager) can read the task's history
   and the Verifier's observations through a tool, instead of a summary cut
   to fit a prompt.
6. **Offer and demand are both observed.** Each run records the tools it was
   offered and why any was withheld (`tools_offered`). Each tool it asked for
   and did not have (an unknown name, a tool call typed as text, a refused
   command) is recorded as demand and counted per sprint, so a missing tool
   shows up in the retrospective instead of in a failed task.

## The step sets the ceiling, the role narrows

Approved by the owner on 2026-11-24. Tools are decided in two layers:

1. **The step sets the most a turn may do.** An execution turn that delivers
   files may read, write in the task's worktree and run tests and committed
   scripts. A brief, a review or a planning turn may only read. A
   management turn may only read the board. No role goes above its step's
   ceiling: a QA reviewer cannot edit the delivery it judges, although QA
   can edit when it executes a task.
2. **The role may only narrow, never widen.** A role's profile can drop
   tools it does not need within the step (for example, no `run_tests` for a
   TechWriter task that changes only documents). It cannot add a tool the
   step does not allow.

What stays with the role, and is not a matter of tools:

- **The prompt and judgement.** Assigning the right role still matters for
  quality; the planner's reassignment (`fix/plans-see-repo-and-tools`) stays,
  as a quality rule, not a permission rule.
- **Separation of duties.** The run that delivered never reviews itself;
  that follows who did what, not which tools were offered.
- **Limits independent of tools.** Protected paths, the risk tier and a
  person's review of `.github/` apply whatever tools a turn had.

## The tool families

| Family | Tools | Notes |
|---|---|---|
| R: read | `workspace__list`, `read`, `search`, `find_file`, `git` (status, changed, diff vs base, log, file at base) | Read-only, on the task's root. |
| W: write | `workspace__edit`, `move`, `delete`, whole-file delivery | Only in the task's worktree (or a folder without the Git workflow). |
| X: run | `workspace__run_tests` (narrowed), committed `scripts/*.py` | Sandbox and command policy; no shell. |
| S: research | `research__package`, `advisories`, `fetch` | Allowed hosts only. |
| E: record | `board__task` (history, comments, attempts), `board__evidence` (a run's checks and observations) | Read-only, the company's own tasks only. **New.** |

## Target, per step

| Step | Roles | R | W | X | S | E |
|---|---|---|---|---|---|---|
| Execute a task that delivers files | any role (developer roles, QA, Designer, TechWriter, SecOps, Architect, DataEngineer) | yes | yes | yes | yes | own task |
| Architect brief before a task | Architect | yes | — | — | yes | own task |
| QA review of a delivery | QA | yes | — | narrowed tests | — | own task |
| PO plans a sprint | PO | yes | — | — | — | the company's board |
| PO writes a specification, Architect an architecture | PO, Architect | yes | — | — | yes | — |
| ScrumMaster decides on a blocked task, retrospective | ScrumMaster | yes | — | — | — | the sprint |
| Manager, Reviewer, Support | management | — | — | — | — | the company's board |
| Console producer | external CLI | its own, in the sandbox | | | | |

## Today, and what closes each gap

| Gap | Today | Closed by |
|---|---|---|
| Architect brief has no tools | plain chat | `fix/architect-reads-the-repo` (R), `fix/brief-not-a-tool-call` |
| QA review sees excerpts | plain chat | `fix/qa-review-reads` (R) |
| No edit/move/delete on a first attempt | runs in the person's checkout | `fix/first-attempt-in-task-worktree` |
| No read-only Git | none | `feat/workspace-git-tool` |
| `run_tests` never narrows | whole suite every time | `fix/run-tests-narrows` |
| Reads lost to history compression, 100-line window | 16% re-reads | `fix/reads-stay-and-widen` |
| Tool errors and spent rounds not recorded | sha256 only | `fix/tool-observability` |
| Tasks given to roles without tools; PO blind to the repo | role list | `fix/plans-see-repo-and-tools` |
| **TechWriter, SecOps, Architect, PO executing a task get no R/W/X** | `FILE_ROLES` gate | **next: tools follow the step** |
| **No tool to read a task's record or a run's evidence** | prompt summaries | **next: `board__task`, `board__evidence`** |
| **Developer roles cannot query registries or advisories** | research only for deciding roles | **next: S for execution turns** |
| **PO plans without R** | one daemon listing | **next: R for planning** |
| **ScrumMaster decides without R or E** | plain chat | **next** |
| **Missing tools are not counted** | nowhere | **next: tool demand per sprint** |

## Next changes

1. **Tools follow the step** (the ceiling above). Replace the `FILE_ROLES` gate in
   `developer_tools.toolbox_for` with the step: an execution turn gets R and
   X for every role, and W where edits may go. Roles keep their prompts. With
   it, reassigning a task away from a role without tools
   (`fix/plans-see-repo-and-tools`) becomes a fallback, not the rule.
2. **`board__task` and `board__evidence`.** Read-only, scoped to the
   company and, for task turns, to the task and its dependencies. Bounded,
   redacted like reads. Offered to the reviewer, the ScrumMaster, the
   Architect brief and the PO's planning.
3. **Research in execution turns.** `research__package` and `advisories`
   for the developer roles and DevOps (dependency versions, pinned actions
   and images). `fetch` stays limited to allowed hosts.
4. **R for the PO's planning and the ScrumMaster.** Through the same
   read-only toolbox as the Architect brief, with a small round limit.
5. **Tool demand.** Count, per sprint, unknown tool names, typed tool calls
   and refused commands from the observations, and show them beside the
   retrospective's other numbers.

6. **Code navigation as tools.** The daemon already keeps the reference
   graph (`workspace.related`) and the signatures (`workspace.signatures`),
   but only the prompt and the Verifier use them. Offer read-only
   `workspace__definition` (where a name is defined, with its signature) and
   `workspace__references` (who uses it) to every turn with R, so an agent
   checks a call before writing it instead of guessing.
7. **Lint on demand.** The lint after an edit (`edit_lint`) runs only on the
   edited file. Offer `workspace__lint` (the stack profile's lint and type
   check, narrowed to given paths, in the sandbox) to every turn with X.
8. **Run the project's own programs.** Since the scope directive
   (2026-11-19) projects include data science and ML: a turn with X can run a
   committed entry point declared in the project's plan (a training or
   evaluation script, a notebook run headless) in the sandbox, bounded in
   time and output, with its output recorded as an observation and any
   metric it prints kept as an observation of the platform, not a claim.
9. **See the result.** Frontend and Designer tasks cannot look at what they
   built. Offer a sandboxed headless-browser screenshot of a local page of
   the project (no external hosts), recorded with its sha256.

## Threat model (STRIDE, the agent as the adversary)

| Threat | Example | Mitigation |
|---|---|---|
| Tampering | A reviewer edits the delivery it judges | Review and brief toolboxes are read-only; edit tools are not offered and a call to one is refused. |
| Tampering | A role that should only read writes files | W only in an execution turn and only where `workspace_edits.allowed`; the worktree, never the person's checkout. |
| Information disclosure | `board__evidence` of another company | Scoped by the turn's company and task; the backend resolves the scope, never the arguments. |
| Information disclosure | Reading `.env` | The daemon's `is_sensitive` refuses it for every read tool, as today. |
| Elevation of privilege | `run_tests` used as a shell | Narrowing keeps the runner and wrapper of the folder's command; shell operators refused; committed scripts only. |
| Denial of service | Endless reading | Round limits per step, result size limits, history budget. |
| Repudiation | A decision taken on unseen data | Every tool call is an observation on the turn that made it. |
