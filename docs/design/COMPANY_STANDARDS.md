# Company standards: per-company checks imported from the repository

Status: decided (§12, 2026-11-19, PR #746); in implementation under issue
#700. The first pull request delivers the file format and its validator
(slice 1), the import from the default branch with the backend's hashes and
the dry run, the four-eyes Owner activation, `change_standards` in
`ALWAYS_ESCALATE` (slice 2), the evaluation of the in-backend kinds used by
the dry run, and the time-boxed exceptions (decision 1) with the access
review. The Verifier's check, `command` standards on deliveries, the
measurement and revert proposals, the every-delivery migration, the
ScrumMaster's drafts and the page come next (slices 3 to 7). · Part of issue #555 (finding F8, "there are no per-company
standards to import") · SDL practice 3: the threat model is §10, written with
the `threat-model` skill.

## Summary

- Today every company is held to one catalogue, `STD-ARCH-1` … `STD-PO-1`
  ([`docs/STANDARDS.md`](../STANDARDS.md), `services/contracts/standards_catalogue.py`,
  checked by `services/evidence/standards.py`). No model, request or procedure
  edits it. That is right for the catalogue, but a company has rules of its
  own that the catalogue cannot express. The self-hosting run
  ([`retrospectives/2026-11-self-hosting.md`](../retrospectives/2026-11-self-hosting.md))
  found that "importing the standards" had nothing to import, and that La
  Fabrique's own rules (the dependency rule, HTTP adapters, the file-size
  ratchet, i18n in both languages) could not be written down as its
  standards.
- A **company standard** is a check the Verifier runs **without a model**,
  with an id, a source (a file in the company's repository), a version and a
  declared owner. Its kind comes from a **closed list in code** (§3). A file
  places and configures checks; it cannot invent a new kind, and it carries
  no executable code of its own.
- It is **imported** from one file in the repository, `.fabrique/standards.toml`,
  read by the daemon at a named commit of the default branch. The **backend**
  computes the sha256 over the bytes it received; no hash in the file, in a
  request or in the daemon's reply is ever read (rule 6 of
  [the positioning](../POSITIONING.md)).
- Anything that needs judgement is **not a standard**. It stays a procedure
  (prompt text, measured by the board's numbers), and whatever an agent says
  about it is a claim.
- Company standards **only add** to the catalogue. A company cannot relax,
  waive or replace a catalogue standard (§6).
- A Manager imports and validates a **draft**. Only a signed-in **Owner**
  activates or deactivates a version, and `change_standards` joins
  `ALWAYS_ESCALATE`, as `change_protected_paths` did. Every change is audited
  with both hashes (§7).
- Versions are measured with the board's numbers, like procedures and
  workflow versions. A version that makes the company **unable to finish
  work**, or that degrades evidence into `unverifiable`, reverts to the
  version it replaced. Rising rework alone **does not** revert a version,
  because a stricter check is expected to send more work back (§8).
- The worked example (§9) writes La Fabrique's own rules as its standards,
  and also shows which rules must **not** be company standards: the
  protected paths stay the running backend's list, never the repository's.

## 1. What exists today

| Piece | Where | What it gives this design |
|---|---|---|
| The catalogue and its check | `services/contracts/standards_catalogue.py`, `services/evidence/standards.py` (protected) | The shape of a standard (id, area, roles, rule, `checked`), the `standards` Verifier check with `refuted` on a blocking finding and `pass` with findings otherwise, `prompt_section(role)` |
| Every-delivery commands | `services/workspace/stack_roots.py` (`every_delivery`), `services/loop/delivery_suites.py`, `services/evidence/command_runs.py` (F5) | A company can already name commands run on every delivery. Their exit status and the sha256 of their output, computed by the backend, are observations, judged by the `required_commands` check. They are configuration without an id, a version, an owner or a hash of their source. |
| Protected paths | `services/contracts/protected_paths.py`, `services/evidence/protected_check.py`, `services/platform/protected_paths_setting.py` | Path matching (`matches`, both readings of a path, declared paths may add a match and never remove one); a company setting only a signed-in Owner changes, audited, in `ALWAYS_ESCALATE` |
| Procedures | `services/learning/procedures.py` (protected) | Versioned records proposed at retrospectives, adopted by autonomy or a Manager, kept or reverted after `MEASURE_AFTER_SPRINTS = 2` sprints by rework per task (tolerance 0.25), blocked share (0.10) and the claimed-not-verified share (0.10) |
| Workflow versions | [`docs/WORKFLOWS.md`](../WORKFLOWS.md) §3.6, §4, §9 (designed) | The pattern this design follows: a closed list of types in code, a validator plus a run-time guard, Owner-only activation, versions pinned per task, measured and reverted by the procedures' rule, a draft with the sha256 of a reverted one dropped |
| Segregation of duties | `services/platform/sod.py`, [`SEGREGATION_OF_DUTIES.md`](SEGREGATION_OF_DUTIES.md) | Who requests a change does not approve it, when the company's SoD mode holds it |

What is missing: a company cannot give its every-delivery commands an id,
a severity, a source it can review in a pull request, or a version; it
cannot write a rule that is not a command (a pattern, a pair of files that
change together, a size limit); and nothing measures whether such a rule
helps.

## 2. What the reference agents do, and what is taken

Read from their public documentation on 2026-11-18; design only, no code.

| Agent | How it handles repository rules | Taken | Not taken |
|---|---|---|---|
| **Kiro** ([steering](https://kiro.dev/docs/steering/)) | Markdown files in `.kiro/steering/` with front matter: `inclusion: always`, `fileMatch` with `fileMatchPattern` globs, `manual`, `auto`. Workspace steering wins over global. "Treat steering changes like code changes - require reviews." | The file lives in the repository, under a tool folder, reviewed like code; rules scoped by **globs** over the paths a change touches | Steering is prompt context; nothing checks it. Here a standard must be a check. |
| **GitHub Copilot coding agent** ([repository instructions](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions), [setup steps](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-environment), [risks and mitigations](https://docs.github.com/en/copilot/concepts/agents/coding-agent/risks-and-mitigations)) | `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` with `applyTo` globs: "guidance", not enforced. `copilot-setup-steps.yml` "only runs when it's on the default branch". "GitHub Actions workflows don't run on a pull request until a user with write access approves them"; branch protections and required checks still apply. | **Read only from the default branch**, never from the branch the agent writes; **CI and required checks are the enforcement**, not the instructions; a person with write access gates what the agent's changes run | Copilot's code review reads instructions "from the head branch, not the base branch". Here that would let a delivery rewrite the rule it is judged by, so it is refused (§4, T2). |
| **OpenHands** ([skills](https://docs.openhands.dev/overview/skills)) | `AGENTS.md` at the root, skills in `.agents/skills/` (legacy `.openhands/microagents/`) triggered by keywords or `paths`. "Skills guide the agent's behavior; they do not grant permissions"; it warns to review a skill before installing it, since it can direct the agent to run scripts. | Path-triggered scoping; the warning that a rules file can make the agent **run scripts**, which is the core threat of §10 | Rules as prompt text |
| **Factory** ([AGENTS.md](https://docs.factory.ai/cli/configuration/agents-md), [hooks](https://docs.factory.com/cli/configuration/hooks-guide)) | `AGENTS.md` loaded "as coding guidelines"; it advises rules "easy to verify" and not ones that "cannot be checked". Hooks give "deterministic behavior that should happen every time"; exit code 2 blocks; "invalid JSON fails open" unless `onFailure: "block"`; org-managed hooks with `allowManagedHooksOnly` so lower levels "cannot remove required checks". | **Only checkable rules are standards**; **fail closed** when a check cannot give an answer; a higher level (here the catalogue) that a lower level (the company) **cannot remove** | Hooks run "with your local environment and credentials"; here every command runs in the daemon's sandbox |
| **OpenAI Codex cloud** ([AGENTS.md format](https://agents.md/)) | `AGENTS.md` may list programmatic checks that the agent runs and reports in its pull request | Each command a standard runs is cited with its exit status and the hash of its output, as Codex cites its commands | The agent's own report that the checks passed: rule 2 makes it a claim. The daemon runs them and the backend records the result. |

The common thread: every agent above keeps repository rules as **guidance
for the model** and relies on **CI and required checks** for enforcement.
La Fabrique's evidence layer is that enforcement, so the part of a
company's rules that can be checked becomes a check, and the rest stays
guidance (procedures).

## 3. What a company standard is

A company standard has:

| Field | Meaning | Claim or observation |
|---|---|---|
| `id` | `[A-Z][A-Z0-9]{1,7}-[A-Z0-9]+-[0-9]+` (`LF-ARCH-1`), unique in the file. The `STD-` prefix is refused: it belongs to the catalogue. | Part of the source |
| `title`, `rule` | One line and one paragraph, shown in prompts and on the Procedures page | Part of the source |
| `binds` | Roles from `services/contracts/roles.py`, or `*`. A specialised developer is bound by what binds `Developer` (`DISCIPLINES`), as with the catalogue. | Part of the source |
| `kind` and its fields | One of the closed list below | Part of the source |
| `severity` | `blocking` (a failure refutes the delivery) or `finding` (a reviewer sees it) | Part of the source |
| `paths` | Globs (the `contracts.protected_paths` syntax) a delivery must touch for the standard to apply; absent means every delivery | Part of the source |
| `owner` | Who to ask about the rule: a team name (#512) or a member's role. It is a **declaration** in the file. | **Claim**. The accountable party is the Owner who activated the version, which the platform records (§7). |
| source | Path, commit sha and sha256 of the file it came from | **Observation**: the daemon read the bytes, the backend hashed them |
| version | The company's standards set version it belongs to | Platform record |

### 3.1 The closed list of kinds

Each kind is implemented once, in code, in the evidence context, with the
inputs it reads named. A file cannot add a kind.

| Kind | Fields | Evaluated from | Outcome |
|---|---|---|---|
| `command` | `command`, `folder` (optional), `guards` (the files the command's verdict depends on, at least the script it runs) | The daemon runs it in the delivery's sandbox, like an every-delivery command; exit status and the sha256 of the output computed by the backend (`command_runs`) | Exit 0 passes; non-zero fails; 126/127, a policy refusal, a timeout or no exit status is `unverifiable`, never a pass |
| `forbid_pattern` | `pattern` (a regular expression, §10 T5), `files` (globs the pattern is applied to), `except` (globs) | The bytes of the observed written files that match `files`, read back from the daemon at delivery time and hashed by the backend | A match fails, citing file and line |
| `require_together` | `when` (globs), `then` (globs), `on` (`changed` or `added`) | The paths the daemon observed written, plus declared paths, which may add to `when` and never satisfy `then` | A `when` path without a `then` path fails |
| `max_lines` | `limit`, `files`, `baseline` (optional path of a JSON map `path -> lines` in the repository, read at the activated commit only) | The observed bytes, as for `forbid_pattern` | Over the limit fails, unless the baseline allows the file and it did not grow |
| `forbid_path` | `files`, `except` (globs) | Observed and declared paths | A delivery that writes a matching path fails |

`command` is the general escape hatch: it lets a company reuse the checks
it already has (a linter, an architecture script, a test file). The other
four kinds exist so that a company without such scripts can still write a
rule, and so that the most common rules run inside the backend with no
execution at all.

Deliberately absent: a kind that holds a task for a person on a path (that
is the protected paths, §9.2), a kind that calls a model, a kind that
fetches from the network, and a kind with free-form code.

### 3.2 How the Verifier records it

A new Verifier check, `company_standards`, beside the catalogue's
`standards` check, which stays exactly as it is:

- `refuted` when any applicable `blocking` standard failed; the gate sends
  the task back with `company standard violated` and the failing ids, a
  code problem.
- `unverifiable` when no blocking standard failed and a blocking standard
  could not be evaluated (a command that could not run, a file that could
  not be read back, a pattern that hit its budget). It never collapses into
  `pass` (rule 3).
- `pass` otherwise, with `finding` failures and the skipped standards
  (their `paths` not touched) listed.
- The check carries `{set_version_id, sha256, source_commit}` of the set it
  applied and, per standard, `{id, kind, outcome, observation}`; a command's
  entry carries the `runs` of `command_runs` (command, folder, exit status,
  output sha256).

A task pins the set version in force when it starts, as a workflow version
is pinned (WORKFLOWS §9, decision 6); a re-attempt keeps it.

### 3.3 Where it goes in a prompt

Company standards that bind a role go into its prompts after the
catalogue's section and before the company's procedures, under "Company
standards", each with its id, title, rule and `[checked]`. The prompt says
they add to the catalogue and never relax it, and that what the agent says
about them is not how they are judged.

## 4. How a standard set is imported

1. **The file.** `.fabrique/standards.toml` at the repository root (for a
   repository with several folders, F5, one file covers them all; a
   standard scopes itself with `folder` and `paths`). TOML is read with the
   standard library's `tomllib`, which builds data and never runs code.
   Size cap 64 KiB, at most 50 standards.
2. **The ref.** The import names no branch: the backend asks the daemon for
   the **default branch's** head commit and the file's bytes at that commit
   (`git show <sha>:.fabrique/standards.toml` in the project, over the
   existing `workspace.*` request path). A task branch, a sprint's
   integration branch or a delivery's worktree is never a source. A
   standards file that a delivery wrote reaches the company only after a
   person merged it into the default branch **and** an Owner activated it
   (§7).
3. **The hash.** The backend computes the sha256 over the bytes it received
   and stores the bytes and the hash with the commit sha. For every
   `command` standard it also reads each `guards` file at the same commit
   and stores its sha256, computed the same way. A hash in the file, in the
   import request or in the daemon's reply is ignored (rule 6).
4. **Validation.** The parser refuses, each with a named reason: an
   unknown kind or field, a duplicate or `STD-` id, a role not in the
   catalogue, a glob that does not compile, a regular expression outside
   the allowed subset (§10 T5), a `command` without `guards`, a command the
   daemon's command policy would refuse, a `baseline` that is not a JSON
   map of path to integer.
5. **Dry run on the default branch.** Before it can be activated, the
   draft is evaluated once against the default branch's own tree as if
   every file had been delivered. A standard that fails on the tree it was
   imported from is reported (`fails_on_source`): the Owner sees it before
   activating, so a rule that would block every task from the first one is
   caught at import, not after two sprints.
6. **The draft.** The result is a draft version with its refusals and dry
   run. Importing never changes what is in force.

At verification time the backend never re-reads the file. For a `command`
standard, the daemon reports the bytes of each `guards` file in the
delivery's tree, the backend hashes them, and a hash that differs from the
activated one makes the standard `unverifiable` with `guard_changed` (the
script that decides was edited by the delivery itself) and raises the risk
tier to high, so a person decides (§10 T3).

## 5. How a standard is checked: deterministic only

- Each kind's evaluation is a pure function of observations the loop
  recorded at delivery time: the daemon's write report, the bytes it read
  back (hashed by the backend), and the command runs. No model is called,
  no network is reached, no clock is read (rule 5).
- What the producer says about a standard (its summary, "ran the linter,
  all green", a test report it pasted) is a claim and is never read by the
  check (rules 1 and 2).
- A rule that needs judgement ("use the design system's primitives rather
  than one-off classes", "functions that fit on a screen") cannot be
  written as a standard: none of the five kinds can express it. It belongs
  in a procedure, which is prompt text measured by the board's numbers,
  and a reviewer's opinion on it is a claim.
- A rule partly checkable is split: the checkable part becomes a standard,
  the rest a procedure (§9.3 shows this for the design system).

## 6. How it interacts with the catalogue

**Proposal: a company can only add.** No field names a catalogue
standard; a file that mentions an `STD-` id anywhere is refused. The
catalogue's `standards` check runs first and unchanged; the company's check
runs beside it and can only add `refuted`, `unverifiable` or findings.

Why not allow relaxing, even with an Owner's signature:

- The catalogue is what makes two companies' evidence comparable, and what
  the bench and the gate measure. A relaxed catalogue makes "verified" mean
  different things per company.
- The threats in §10 get worse: a malicious or mistaken file that can turn
  off `STD-SEC-1` is far more dangerous than one that can only add checks.
- A real exception already has a path: a person accepts a specific
  delivery after reading why it was refuted (`sprint_control`, the run
  review), and that acceptance is recorded per run. An exception stays per
  run and visible, not a standing switch.

A company standard may tighten what the catalogue checks as a finding (for
example make "code without a test" blocking for `backend/**` with a
`require_together` rule). That adds a check; it does not change the
catalogue's.

## 7. Who may change them

| Action | Who | Recorded |
|---|---|---|
| Import a draft, validate, dry run | A Manager or an Owner (an API token at most a Manager) | Audited `company_standards_imported` with the commit, the sha256 and the account |
| Propose a draft at a retrospective | The ScrumMaster, in a fixed format a parser checks (`STANDARD … END`, naming the kind and fields); it becomes a draft marked as proposed by an agent, in the inbox | A message, so a claim; a draft with the sha256 of a version reverted or rejected before is dropped, as `propose(skip_failed=True)` does for procedures |
| Activate a version, deactivate the set, revert by hand | A **signed-in human Owner** only; the router refuses an API token; `change_standards` joins `ALWAYS_ESCALATE`, so no Manager policy can name it and the Manager agent never does it, at any autonomy level | Audited `company_standards_activated` / `_deactivated` with the account, both versions and both sha256 values, and the dry run's result |
| Revert on the numbers | The platform (§8) | Audited `company_standards_reverted` with the baseline, the measurement and both versions |

With the company's SoD mode on (`sod.py`), the account that imported a
draft cannot activate it; a company with one Owner follows the
solo-eligible path SoD already defines.

## 8. Kept or reverted by the numbers

The amendment to the positioning says procedure changes are kept or
reverted by deterministic metrics. A standard is a check, not a procedure,
and that changes which metric may revert it:

- **Baseline and measurement** follow procedures and workflow versions:
  on activation, the numbers of the company's last two completed sprints;
  after two completed sprints of tasks pinned to the version, the same
  numbers over those tasks; fewer than two is `unmeasured`, never kept.
- **What reverts a version** (to the version it replaced, for tasks
  started from then on):
  - the **blocked share** worse than the baseline by more than
    `BLOCKED_TOLERANCE` (0.10): the company can no longer finish work,
    whatever the cause;
  - the **share of deliveries whose `company_standards` check was
    `unverifiable`** above a new `UNVERIFIABLE_TOLERANCE` (proposed 0.20):
    the set is degrading evidence instead of producing it (a command the
    sandbox cannot run, a pattern that keeps hitting its budget).
- **What does not revert it on its own: rework per task.** A stricter
  standard is expected to send more deliveries back; reverting it for that
  would reward the weakest check (Goodhart). Rework, the claimed-not-verified
  share and each standard's refutation count are recorded on the version
  and shown to the Owner and to the ScrumMaster's next retrospective, beside
  the two triggers.
- **A version that only removes or loosens** standards is never kept or
  reverted by the numbers: it is an Owner's decision, recorded, and the
  measurement is shown for information.
- The revert never touches the catalogue, and never activates anything the
  Owner had not activated before (as WORKFLOWS §3.6).
- The tolerances live beside the procedures' in a protected module, so a
  self-change cannot loosen them.

## 9. Worked example: La Fabrique's own standards

La Fabrique's rules beyond the catalogue are in `CLAUDE.md`,
`CONTRIBUTING.md` and the CI workflows. Written as its standards, they would
be `.fabrique/standards.toml` in this repository (illustrative: the import's
dry run, §4 step 5, checks each against `main` before the Owner activates
it).

### 9.1 The file

Kept verbatim as `backend/tests/fixtures/company_standards/la_fabrique.toml`,
which `test_company_standards_file.py` requires to parse with no refusal:
change both together.

```toml
# .fabrique/standards.toml: La Fabrique's company standards.
# Each one adds to the catalogue (docs/STANDARDS.md); none relaxes it.
format = 1

[[standard]]
id = "LF-ARCH-1"
title = "The backend's dependency rule and HTTP adapters"
rule = """A context imports only the contexts its row in docs/architecture/contexts.json \
names, only through their public modules; a module under services/ that imports an HTTP \
library is listed in http_adapters; no cycle between contexts."""
binds = ["Developer", "Architect"]
kind = "command"
command = "python3 scripts/check_architecture.py"
guards = ["scripts/check_architecture.py", "docs/architecture/contexts.json"]
paths = ["backend/app/**", "docs/architecture/contexts.json"]
severity = "blocking"
owner = "Architect"

[[standard]]
id = "LF-ARCH-2"
title = "The frontend's layers and network modules"
rule = "A frontend module imports only from its layer or below; only the modules in network.modules use fetch, EventSource, WebSocket, XMLHttpRequest or axios."
binds = ["FrontendDeveloper"]
kind = "command"
command = "python3 scripts/check_frontend_architecture.py"
guards = ["scripts/check_frontend_architecture.py", "docs/architecture/frontend.json"]
paths = ["frontend/src/**"]
severity = "blocking"
owner = "Architect"

[[standard]]
id = "LF-SIZE-1"
title = "The file-size ratchet"
rule = "A source file stays at or under 600 lines; a file already over it may shrink, never grow."
binds = ["Developer"]
kind = "command"
command = "python3 scripts/check_file_sizes.py"
guards = ["scripts/check_file_sizes.py", "scripts/file_size_baseline.json",
          "scripts/test_file_size_baseline.json"]
severity = "blocking"
owner = "Architect"

[[standard]]
id = "LF-I18N-1"
title = "Every string in both languages"
rule = "A change to the English strings changes the Spanish ones in the same delivery, and the other way round."
binds = ["FrontendDeveloper", "Designer"]
kind = "require_together"
when = ["frontend/src/i18n/en.json"]
then = ["frontend/src/i18n/es.json"]
on = "changed"
severity = "blocking"
owner = "Designer"

[[standard]]
id = "LF-I18N-2"
title = "Same keys in every locale, no key used and missing"
rule = "en.json and es.json hold the same keys, and every key the code calls exists."
binds = ["FrontendDeveloper"]
kind = "command"
folder = "frontend"
command = "npx vitest run src/i18n/locales.test.ts src/i18n/coverage.test.ts"
guards = ["frontend/src/i18n/locales.test.ts", "frontend/src/i18n/coverage.test.ts"]
paths = ["frontend/src/**"]
severity = "blocking"
owner = "Designer"

[[standard]]
id = "LF-UI-1"
title = "Status badges come from the design system"
rule = "A page shows a status with StatusBadge from components/ui; the shell's StatusPill is the services' health only."
binds = ["FrontendDeveloper"]
kind = "forbid_pattern"
pattern = "components/shell/StatusPill"
files = ["frontend/src/pages/**/*.tsx"]
except = ["frontend/src/**/*.test.tsx"]
severity = "finding"
owner = "Designer"

[[standard]]
id = "LF-LINT-1"
title = "The backend passes the pinned ruff"
rule = "ruff check and ruff format --check pass over app and tests, with the version CI pins."
binds = ["BackendDeveloper"]
kind = "command"
folder = "backend"
command = "ruff check app tests && ruff format --check app tests"
guards = ["backend/pyproject.toml"]
paths = ["backend/**"]
severity = "blocking"
owner = "BackendDeveloper"

[[standard]]
id = "LF-DOC-1"
title = "A new backend test has its row in TESTING.md"
rule = "A test file added under backend/tests is listed in docs/TESTING.md in the same delivery."
binds = ["Developer", "QA"]
kind = "require_together"
when = ["backend/tests/test_*.py"]
then = ["docs/TESTING.md"]
on = "added"
severity = "finding"
owner = "QA"

[[standard]]
id = "LF-DOC-2"
title = "A change to the product names itself in the changelog"
rule = "A delivery that changes the backend's or the frontend's code adds a line to CHANGELOG.md."
binds = ["Developer"]
kind = "require_together"
when = ["backend/app/**", "frontend/src/**"]
then = ["CHANGELOG.md"]
on = "changed"
severity = "finding"
owner = "TechWriter"

[[standard]]
id = "LF-SEC-1"
title = "No environment file in the repository"
rule = "A .env file is never delivered; configuration is documented in docs/CONFIGURATION.md and read from the environment."
binds = ["*"]
kind = "forbid_path"
files = [".env", "**/.env", "**/.env.*"]
except = ["**/.env.example"]
severity = "blocking"
owner = "SecOps"
```

### 9.2 What is deliberately not a company standard

- **The protected paths.** They decide which changes a person must see
  because they could weaken the grader. If they came from the repository,
  a delivery that edits `.fabrique/standards.toml` could remove a path
  from the list that protects it. They stay the running backend's list
  (`contracts/protected_paths.py`, `protected_list.json`), switched on by an
  Owner, exactly as `docs/design/rsi-loop.md` decided. The standards file
  and every `guards` file are instead treated like protected paths for
  every company that has a set in force (§10 T2, T3).
- **The catalogue's own rules** (no secret, a test per code file, pinned
  dependencies): already enforced, and a company cannot restate them under
  another id to change their severity downward.
- **Rules that need judgement**, which stay procedures or prompt text:
  "read the context's document before changing code", English
  identifiers, "name what was taken from the reference agents in the pull
  request", EARS criteria in a pull request, and the design system's
  "use the primitives rather than new one-off classes".
- **Rules about the pull request** (draft, "Part of #N", attribution):
  the factory's integration writes those itself; they are not properties
  of a delivery's files.

### 9.3 Splitting a partly checkable rule

The design system rule (`docs/contexts/frontend.md`, Design system) has a
checkable part and a judgement part:

| Part | Becomes |
|---|---|
| "Not the shell's `StatusPill` for a status" | `LF-UI-1`, `forbid_pattern`, a finding |
| "A page's one `<h1>` is `PageHeader`" | A candidate `forbid_pattern` over `<h1` in `pages/`, imported only if the dry run passes on `main` (or with a `baseline` if the existing pages still have one) |
| "A page is restyled by using the primitives, not by new one-off classes" | A procedure for `FrontendDeveloper`; QA's opinion about it is a claim |

## 10. Threat model

Written with the `threat-model` skill; boundaries by id from its
`reference.md`. Status: draft, for review with the design.

### 10.1 What is built

A Manager imports `.fabrique/standards.toml` from the default branch through
the daemon; an Owner activates it from the browser. On every delivery the
loop runs the set's `command` standards in the daemon's sandbox and reads
back the bytes the other kinds need; the Verifier judges them without a
model.

### 10.2 Assets

The evidence (the Verifier's verdicts, which a standard can now refute or
leave unverifiable); the user's machine (a `command` runs there); the
company's ability to finish work; secrets the sandbox can see; the
catalogue's guarantee for every company.

### 10.3 Flow and boundaries

```
Owner/Manager ──B1── Backend ──B2── NATS ──B2── Daemon ──B3── repo at default-branch sha
                       │                          │
                       │                          └──B3── sandbox: command standards
                       └── company_standard_sets (bytes, sha256 computed here)
Repository content written by agents and by people ──B14── standards file and guards
```

**B14 (new): the company's standards file and the files its commands run.**
They live in the repository the agents write to, so their content is
untrusted input of the B5 kind even when a person wrote them, and they
decide verdicts, so they are also a grader of the B13 kind.

### 10.4 Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| T1 | B14, B3 | E | **A malicious standards file runs code on the user's machine** through a `command` standard (`curl … \| sh`, reading `~/.ssh`) | M | H | Critical | Existing: every command runs through the daemon's command policy (`vscode-daemon/command_policy.py`, a deny list the request cannot relax) and its sandbox (`FABRIQUE_SANDBOX_REQUIRED`). New: a set with a `command` standard can be activated only by an Owner who sees each command verbatim; the import refuses a command the policy refuses; with `host` sandbox mode, activation shows a warning and the open question Q4 decides whether it is refused. Test: a command with `curl` and a pipe to a shell is refused at import. |
| T2 | B14 | T | **A delivery weakens its own judge** by editing `.fabrique/standards.toml` | H | H | High | New: the file is never read from a task branch (§4 step 2), so the edit has no effect on the delivery's own verdict; a delivery that writes the file is held for a person (risk tier high, area `company_standard`); activation needs the file merged on the default branch and an Owner's click. Test: a delivery editing the file is judged by the activated version and held. |
| T3 | B14, B3 | T | **A delivery edits a script a `command` standard runs** (`check_architecture.py` made to exit 0) | H | H | High | New: `guards` hashes at the activated commit; at verification the backend hashes the delivered `guards` bytes and a difference is `unverifiable` (`guard_changed`) plus risk tier high. A `command` without `guards` is refused at import. Residual: a guard list that misses an imported module; the Owner reviews `guards`, and Q5 asks whether to hash the whole folder. |
| T4 | B2, B14 | S/T | The sha256 or the commit is supplied by the requester or the daemon's reply rather than computed | M | H | High | New: the backend hashes the bytes it received (rule 6); a hash field in the file or the request is ignored. Test: a request carrying a `sha256` is ignored and the stored hash is the computed one. |
| T5 | B14 | D | **A regular expression with catastrophic backtracking** stalls the worker; an enormous file or glob set exhausts memory | M | M | Medium | New: patterns limited to a subset without back-references or nested quantifiers (validated at import), or run on a linear-time engine (Q3); a per-standard and per-delivery budget of bytes and time; on hitting it the standard is `unverifiable`, never a pass. File cap 64 KiB, 50 standards, glob count and pattern length capped. |
| T6 | B14 | D | A set that **blocks every task** (a command that always fails), stalling the company | M | M | Medium | New: the dry run on the default branch (§4 step 5); the revert on the blocked share (§8); deactivation by the Owner at any time. |
| T7 | B14 | E | A file that **relaxes the catalogue** (`STD-SEC-1` off, an `STD-` id redefined) | M | H | High | New: no field can name a catalogue standard; an `STD-` id anywhere is refused; the catalogue check runs first and unchanged. Test: each attempt refused with a named reason. |
| T8 | B1 | E | A Manager, an API token or the Manager agent activates a set | M | H | High | New: Owner-only signed-in route; `change_standards` in `ALWAYS_ESCALATE`; SoD between importer and activator when the mode holds it. Tests: 403 for a Manager and a token; a policy naming it refused with 422. |
| T9 | B5 | T | An agent's message ("the linter passed", "this standard does not apply") is read as the standard's outcome | M | M | High | Existing: claims and observations in separate fields (rules 1 and 2). New: the check reads only the daemon's reply and the backend's hashes; a test asserts no claim field is read. |
| T10 | B3 | I | A command's output carries secrets into the stored evidence or a prompt | M | M | Medium | Existing: the sandbox's environment without the runner's variables; the read tools' redaction. New: output stored as its hash and a bounded tail, redacted like the delivery tests' output. |
| T11 | B14 | T | A ScrumMaster proposal loops back a reverted set | L | L | Low | New: drafts with the sha256 of a reverted or rejected version are dropped; only an Owner activates. |
| T12 | — | R | No record of who changed the company's rules | L | M | Low | New: every import, activation, deactivation and revert audited with the account and both hashes, through `AgentService`. |

Assume breach: if the command policy and the sandbox both fail (T1 with
`host` mode), a malicious `command` runs with the user's privileges; that
is the same exposure as an every-delivery command today, and the Owner's
activation of the exact command is the extra layer. If the Owner is the
attacker, the catalogue still holds (T7) and every change is audited
(T12).

### 10.5 Tests that prove the mitigations

Negative first, one per threat: a `curl | sh` command refused (T1); a
delivery that edits the file judged by the activated version and held
(T2); an edited guard giving `unverifiable` and high risk (T3); a supplied
hash ignored (T4); a backtracking pattern refused or stopped at its budget
as `unverifiable` (T5); a set failing the dry run flagged before activation
(T6); every `STD-` attempt refused (T7); a Manager, a token and a policy
refused (T8); a claim of success with a failing command still `refuted`
(T9); output with a secret-shaped line stored redacted (T10).

### 10.6 Residual risk

A `command` standard is code the company runs on every delivery; its
safety is the sandbox's and the Owner's review of it. A `guards` list can
be incomplete. A company can add a standard that is pointless or
expensive; the numbers show its cost, they do not prevent it.

## 11. Storage, API and migration path

### 11.1 Placement in the contexts

| Piece | Context | Why |
|---|---|---|
| The file's schema, the parser and the validator (pure) | `contracts` (`company_standards.py`) | Data and pure functions several contexts read |
| The five kinds' evaluation and the `company_standards` check | `evidence` (protected, a code owner reviews it) | Judging a run from observations |
| The import: reading the file and the guards at the default branch's sha through the daemon, the dry run | `planning` (it already writes the architecture through the daemon) | May import workspace and evidence |
| Running `command` standards and reading back bytes at delivery | `loop` (`delivery_suites`, beside every-delivery commands) | Drives the delivery |
| Storage, Owner-only activation, audit | `platform` (as `protected_paths_setting`) | A company setting |
| Measurement and revert | `learning` (beside procedures), tolerances in a protected module | How the company learns |

No new HTTP adapter; the import goes over NATS.

### 11.2 Table

`company_standard_sets`: `id`, `company_id`, `version`, `status`
(`draft`, `active`, `superseded`, `reverted`, `rejected`), `source_path`,
`source_commit`, `sha256`, `raw` (the bytes), `parsed` (JSON),
`guard_hashes` (path → sha256), `dry_run` (JSON), `proposed_by`
(account or `agent:ScrumMaster`), `imported_by`, `activated_by`,
`activated_at`, `supersedes_id`, `baseline`, `measurement`, `result`. One
migration, with its downgrade; no existing row changes. The Verification's
evidence carries the pinned version per task.

### 11.3 API sketch

| Route | Role | Does |
|---|---|---|
| `GET /v1/companies/{id}/standards` | Viewer | The catalogue and the set in force: version, sha256, commit, the standards, the measurement |
| `GET /v1/companies/{id}/standards/versions` | Viewer | History with status, who imported and activated, results |
| `POST /v1/companies/{id}/standards/import` | Manager | Reads the file at the default branch's head; returns the draft with its refusals and dry run (`202`; `409` when the daemon is absent, the draft not created) |
| `POST /v1/companies/{id}/standards/versions/{v}/activate` | Owner, signed in, no API token | Activates; `409` while a refusal blocks |
| `POST /v1/companies/{id}/standards/deactivate` | Owner, signed in | No company set in force; the catalogue alone |
| `POST /v1/companies/{id}/standards/versions/{v}/reject` | Owner | Rejects a draft |

`GET /v1/standards` stays the catalogue. Each route is declared in
`docs/architecture/authorization.json`.

### 11.4 Slices

1. The schema, parser and validator in `contracts`, with the refusals'
   tests; this document's La Fabrique file as a fixture that must parse.
2. Storage, import through the daemon with the backend's hashes, the dry
   run, the Owner-only activation and audit, `change_standards` in
   `ALWAYS_ESCALATE`. Nothing is evaluated on deliveries yet.
3. The four in-backend kinds in the Verifier's new check, the gate's
   reason, the pinned version, the prompt section. A protected change: a
   code owner reviews it.
4. `command` standards through the delivery suites, `guards` hashing and
   the high-risk hold; the file and guards held like protected paths.
5. Measurement and revert in `learning`; the ScrumMaster's proposals.
6. The Procedures page shows the company's set beside the catalogue, with
   its history (new component files; strings in both languages).
7. La Fabrique imports its own set (§9) in the self-hosting harness,
   replacing the every-delivery commands it names today.

Existing `every_delivery` commands keep working; slice 7 shows how a
company moves them into standards, and Q6 asks whether to retire them.

## 12. Decisions (2026-11-19)

The owner delegated these decisions on 2026-11-19 ("the app is corporate and must scale: decide with the highest standards"). Each answer names the standard it follows. They replace the open questions this section held; the code pull request (#700) implements them.

1. **Relaxing the catalogue: no.** A company only adds standards; it never relaxes or waives a catalogue standard. A real need goes through a time-boxed exception instead. An Owner requests it and a second Owner approves it. It carries a reason, a compensating control and an expiry of at most 90 days. It is audited, listed in the access review, and lapses on its own. (ISO/IEC 27001:2022 clause 6.1.3 and A.5.36; risk acceptance by a named owner.) A company with a single Owner can confirm alone, and the exception is then flagged in the access review, as single-Owner approvals are elsewhere (#703).
2. **Reverting on the numbers: flag and propose, never revert alone.** A standards version whose blocked share or `unverifiable` share passes its tolerance is flagged to the Owners, and a revert to the previous version is prepared as a draft. Only an Owner activates the revert, and the activation is audited. A control changes only through a recorded human decision (ISO/IEC 27001:2022 A.8.32). The `unverifiable` tolerance starts at 0.20 and can be configured per company. It is judged only over at least 20 deliveries since activation, so that a handful of runs cannot trigger it. Rework alone never triggers it.
3. **Regular expressions: a linear-time engine.** Patterns run on RE2 through the pinned `google-re2` binding (BSD-3-Clause), installed from a hash-pinned lock entry. Python's `re` is never used on a pattern a company wrote. RE2 has no backtracking, so a pattern cannot be crafted to take exponential time. (OWASP: Regular expression Denial of Service; CWE-1333.) A pattern RE2 refuses (backreferences, lookaround) is refused at import with that reason.
4. **`command` standards on the `host` sandbox: refused.** A set that holds a `command` standard cannot be activated unless the daemon reports `container` or `bwrap`. The refusal names the sandbox it saw. (Fail closed: OWASP ASVS 4.0.3 V1.4, NIST SP 800-53 SC-39 process isolation.) Every-delivery commands keep today's warning until they move into standards (point 6).
5. **Guards: every file under the guarded folders.** A guard hashes every file under each folder it names, recursively, sorted and with symbolic links refused. A guard may list exclude globs for generated files. Integrity favours the false positive over the silent change (ISO/IEC 27001:2022 A.8.32, A.8.4 access to source code).
6. **Every-delivery commands: migrated, then retired.** `stack_profile.every_delivery` becomes `command` standards. A migration writes the existing commands as a draft set per company for an Owner to activate. `every_delivery` stays readable for one release, with a deprecation warning, and is then removed. One mechanism, one audit trail.
7. **Format and place: TOML, one file.** The file is `.fabrique/standards.toml` at the repository root, parsed with `tomllib` (standard library, no code execution, typed values). Per-folder rules are tables inside it (`[folders."backend/app"]`), not separate files. YAML is not accepted: its implicit typing (`no` → false, `1.10` → 1.1) silently changes a standard's meaning.
8. **Who may import: four-eyes always.** A Manager imports drafts and a different person, an Owner, activates them, whatever the company's SoD mode (ISO/IEC 27001:2022 A.5.3 segregation of duties). A company with a single Owner can activate its own import, and the activation is flagged in the access review.
9. **Agent proposals: yes, as drafts.** The ScrumMaster may propose a standard at a retrospective. The proposal is a draft that cites the retrospective's evidence, and it is a claim until an Owner activates it under point 8. An agent never activates a standard.
10. **Findings on the board: counted, apart.** A company standard's finding counts in the board's numbers as its own metric ("standard findings per delivery"), next to rework but never folded into it, and the reviewer sees each finding. What is not counted cannot be managed. Keeping it apart lets the numbers tell a new standard's effect from the producers' quality.
