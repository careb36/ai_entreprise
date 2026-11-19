# Local landing: the proposed CI and branch-protection change

Issue #728. Status: **proposed, not applied.** Everything below changes
`.github/workflows/**` or the repository's settings, which are the owner's hand
(`backend/app/services/contracts/protected_paths.py`: "CI is the evidence of a
run"). The pull request that adds `scripts/land.sh` changes neither; this
document is the exact diff for the owner to read, apply or reject.

## What changes and why

`scripts/land.sh` (described in `docs/OPERATIONS.md`, Landing locally) moves
the gate from GitHub to the machine that runs `scripts/check.sh full`: it
checks the exact merge commit and only then fast-forwards `main`. For that to
be the gate, three things change on GitHub:

1. **CI on `main` becomes a second opinion.** The workflows keep running on
   every push to `main`. A red result opens an issue instead of blocking
   anything, and the issue quotes the landing's record, so a disagreement
   between the machine's claim and CI's observation is visible.
2. **Runs on `main` are no longer cancelled by the next push.** Today a newer
   push to `main` cancels the run in progress (`cancel-in-progress: true`),
   which was acceptable while every commit had already been checked on its pull
   request. Post-hoc, each landed commit needs its own complete run, or a red
   commit can hide behind a cancelled one.
3. **Who may update `main` on GitHub.** The required status checks and the
   required pull request go; what replaces them is a rule that only the owner
   (the bypass list) may update `main`, with force pushes and deletion blocked.
   This is the security boundary: `LAND_OWNER=1` is a guard against mistakes,
   and anyone who can run the script can set it.

What does not change: the `protected-paths` check keeps running on pull
requests, which remain possible for review; the security scans keep their
schedule; release tags stay on `main` (`docs/OPERATIONS.md`, Releasing).

## Standards followed

- **ISO/IEC 27001:2022 A.8.32 (change management)**: every change to `main`
  is tested before it lands, the test is recorded with who ran it and where,
  and changes to the change process itself (the workflows, the protected list)
  need the owner. **A.8.25 (secure development life cycle)**: the same checks
  CI runs gate the landing.
- **NIST SP 800-218 (SSDF) PS.3** (archive and protect each release's
  provenance) and **PW.7** (review and test the code, keep the results): the
  record is kept in the repository as a git note, pushed with `main`, and its
  log's hash pins the full log kept on the landing machine.
- **SLSA v1.0 provenance**: the record has the shape of a provenance statement
  (subject commit and tree, the command as build definition, the builder's
  host and tools, start and finish), but it is **unsigned and produced by the
  party being recorded**, so it is a claim at no SLSA build level.
  `docs/POSITIONING.md` forbids accepting hashes and signatures from the party
  being recorded; post-hoc CI and `land.sh --verify` on another machine are
  the independent observation. Signing the record (for example with a
  Sigstore keyless signature from CI over the commit) is a follow-up.
- Where the standards leave a choice, the stricter one is taken: only the
  owner may update `main` on GitHub (separation of duties, A.5.3, rather than
  any writer), and a protected list that cannot be read refuses the landing
  (fail closed, as `scripts/protected_paths_check.py` does).

## 1. A new workflow: `.github/workflows/post-landing.yml`

Two jobs. `landing-record` runs on each push to `main`: for every commit the
push adds on the first-parent line, it reads the note under
`refs/notes/verification`, and opens an issue when a landed commit has no
record, a record that did not pass, or a change to a protected path (judged
by the list as it was before the push, as `protected-paths.yml` judges by the
base). `red-on-main` runs when one of the listed workflows completes on `main`
with a failure, and opens an issue that quotes the record's claim next to
CI's result. Neither job blocks anything.

```yaml
# Post-landing: CI as a second opinion after scripts/land.sh (issue #728,
# docs/design/LOCAL_LANDING.md). Nothing here gates: main was moved by the
# machine that ran the checks, and its record (refs/notes/verification) is a
# claim by that machine. This workflow compares the claim with what CI sees
# and opens an issue when they disagree, a landed commit has no passing
# record, a protected path landed, or a workflow is red on main.

name: Post-landing

on:
  push:
    branches: [main]
  workflow_run:
    workflows:
      - Backend Tests
      - Frontend Build
      - End to end
      - Docker Build
      - File Sizes
      - Security Scan
      - Workflows
    types: [completed]
    branches: [main]

permissions:
  contents: read

# One run per push and per completed workflow run; none cancels another.
concurrency:
  group: post-landing-${{ github.event_name }}-${{ github.event.workflow_run.id || github.sha }}
  cancel-in-progress: false

jobs:
  record:
    name: landing-record
    if: github.event_name == 'push'
    runs-on: [self-hosted, linux, x64, lafabrique]
    timeout-minutes: 5
    permissions:
      contents: read
      issues: write
    steps:
      - name: Check out main with its history
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0

      - name: Fetch the verification notes
        run: git fetch --no-tags origin "refs/notes/verification:refs/notes/verification" || true

      - name: Read the records of the landed commits
        id: read
        env:
          BEFORE: ${{ github.event.before }}
          AFTER: ${{ github.sha }}
        run: |
          out="$RUNNER_TEMP/landing.md"
          : > "$out"
          if [ -z "$BEFORE" ] || [ "$BEFORE" = "0000000000000000000000000000000000000000" ]; then
            echo "No previous main to compare with." | tee -a "$GITHUB_STEP_SUMMARY"
            exit 0
          fi
          problems=0
          echo "| commit | record | status | host |" >> "$out"
          echo "|---|---|---|---|" >> "$out"
          for sha in $(git rev-list --first-parent --reverse "$BEFORE..$AFTER"); do
            if note="$(git notes --ref=verification show "$sha" 2>/dev/null)"; then
              status="$(printf '%s' "$note" | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("status"))')"
              host="$(printf '%s' "$note" | python3 -I -c 'import json,sys; print((json.load(sys.stdin).get("host") or {}).get("name"))')"
              echo "| ${sha:0:12} | yes | $status | $host |" >> "$out"
              [ "$status" = "passed" ] || problems=1
            else
              echo "| ${sha:0:12} | **none** | | |" >> "$out"
              problems=1
            fi
          done
          # The protected list as it was before the push, never the pushed copy.
          git show "$BEFORE:backend/app/services/contracts/protected_paths.py" > "$RUNNER_TEMP/protected_paths.py"
          git show "$BEFORE:docs/architecture/contexts.json" > "$RUNNER_TEMP/contexts.json"
          git diff --no-renames --name-only -z "$BEFORE" "$AFTER" > "$RUNNER_TEMP/changed"
          python3 -I - "$RUNNER_TEMP/protected_paths.py" "$RUNNER_TEMP/contexts.json" "$RUNNER_TEMP/changed" > "$RUNNER_TEMP/protected" <<'PY'
          import importlib.util, json, sys
          spec = importlib.util.spec_from_file_location("protected_paths_before", sys.argv[1])
          module = importlib.util.module_from_spec(spec)
          spec.loader.exec_module(module)
          tests = json.load(open(sys.argv[2], encoding="utf-8"))["tests"]["files"]
          paths = [p for p in open(sys.argv[3], encoding="utf-8", newline="").read().split("\0") if p]
          for match in module.matches(paths, module.protected_patterns(tests)):
              print(f"- `{match.path}` ({match.pattern})")
          PY
          if [ -s "$RUNNER_TEMP/protected" ]; then
            { echo; echo "Protected paths changed (the owner reviews them after the fact):"; cat "$RUNNER_TEMP/protected"; } >> "$out"
            problems=1
          fi
          cat "$out" >> "$GITHUB_STEP_SUMMARY"
          echo "problems=$problems" >> "$GITHUB_OUTPUT"

      - name: Open an issue for the owner
        if: steps.read.outputs.problems == '1'
        env:
          GH_TOKEN: ${{ github.token }}
          AFTER: ${{ github.sha }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          {
            echo "A push to main needs the owner's look (docs/design/LOCAL_LANDING.md)."
            echo
            cat "$RUNNER_TEMP/landing.md"
            echo
            echo "Run: $RUN_URL"
            echo
            echo "A record is a claim by the machine that landed the commit; confirm it with"
            echo "\`scripts/land.sh --verify <sha>\` on another machine."
          } > "$RUNNER_TEMP/issue.md"
          gh issue create --title "Landing to review: main at ${AFTER:0:12}" --body-file "$RUNNER_TEMP/issue.md"

  red:
    name: red-on-main
    if: >-
      github.event_name == 'workflow_run' &&
      github.event.workflow_run.event == 'push' &&
      (github.event.workflow_run.conclusion == 'failure' || github.event.workflow_run.conclusion == 'timed_out')
    runs-on: [self-hosted, linux, x64, lafabrique]
    timeout-minutes: 5
    permissions:
      contents: read
      issues: write
    steps:
      - name: Check out main
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.workflow_run.head_sha }}
          fetch-depth: 1

      - name: Open an issue that quotes the record
        env:
          GH_TOKEN: ${{ github.token }}
          WORKFLOW: ${{ github.event.workflow_run.name }}
          HEAD_SHA: ${{ github.event.workflow_run.head_sha }}
          CONCLUSION: ${{ github.event.workflow_run.conclusion }}
          RUN_URL: ${{ github.event.workflow_run.html_url }}
        run: |
          git fetch --no-tags --depth=1 origin "refs/notes/verification:refs/notes/verification" || true
          title="Red on main: $WORKFLOW at ${HEAD_SHA:0:12}"
          if gh issue list --state open --search "\"$title\" in:title" --json number --jq '.[0].number' | grep -q .; then
            echo "An open issue already reports this." | tee -a "$GITHUB_STEP_SUMMARY"
            exit 0
          fi
          {
            echo "$WORKFLOW concluded **$CONCLUSION** on main at $HEAD_SHA: $RUN_URL"
            echo
            echo "What the landing machine claimed (refs/notes/verification):"
            echo
            echo '```json'
            git notes --ref=verification show "$HEAD_SHA" 2>/dev/null || echo '{"record": null}'
            echo '```'
            echo
            echo "CI's result is the observation; the record is a claim. If they disagree,"
            echo "run \`scripts/land.sh --verify $HEAD_SHA\` and fix forward (docs/OPERATIONS.md, Landing locally)."
          } > "$RUNNER_TEMP/issue.md"
          gh issue create --title "$title" --body-file "$RUNNER_TEMP/issue.md"
```

The notes are fetched only shallowly in `red-on-main` (the notes ref has its
own history; depth 1 is its tip, which holds every note).

## 2. Runs on `main` finish instead of being cancelled

The same one-line change in `backend-tests.yml`, `frontend-build.yml`,
`e2e.yml`, `docker-build.yml` and `file-sizes.yml` (each with its own group
name; `e2e.yml` also updates its comment):

```diff
 concurrency:
   group: backend-tests-${{ github.ref }}
-  cancel-in-progress: true
+  # A pull request's superseded run is cancelled; on main every landed
+  # commit's run finishes, since CI there is the post-hoc second opinion.
+  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

For `e2e.yml`, the comment above the group changes as well:

```diff
-# The self-hosted runners are shared by every pull request and by main: a new
-# push cancels the runs of the commit it replaces, on main too (the newest
-# commit's run covers the ones merged before it), instead of queueing behind them.
+# The self-hosted runners are shared by every pull request and by main: a new
+# push to a pull request cancels the run of the commit it replaces. On main
+# each landed commit's run finishes (docs/design/LOCAL_LANDING.md): CI there is
+# the second opinion on a landing, and a cancelled run would hide a red commit.
```

The cost is runner time when several landings are pushed close together; the
runners' capacity is in `docs/OPERATIONS.md`, Runner capacity. Pushing several
landings at once (`scripts/land.sh --push` after several landings) starts one
run per push, not per commit, and `landing-record` still reads every commit's
record.

## 3. Branch protection of `main`

Today `main` requires pull requests and status checks. Proposed, as a ruleset
(Settings → Rules → Rulesets, or the API below), replacing the classic branch
protection rule of `main` once the ruleset is active:

- **Restrict updates**: only bypass actors may update `main`. The bypass list
  is the repository admin role (the owner), mode "always".
- **Block force pushes** and **restrict deletions**.
- **No** required status checks and **no** required pull request: the gate is
  `scripts/land.sh`, and CI runs after the push.

```bash
gh api --method POST "repos/{owner}/{repo}/rulesets" --input - <<'JSON'
{
  "name": "main: owner lands, history is kept",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "bypass_actors": [
    { "actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "update" },
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
JSON
```

`actor_id` 5 is the built-in admin repository role; check it in the ruleset
editor's bypass list before applying. Then remove the classic rule for `main`
(Settings → Branches), whose "Require a pull request" and required checks
would otherwise still apply.

**What this does not cover, honestly.**

- `refs/notes/verification` is neither a branch nor a tag, so no branch or tag
  ruleset protects it: anyone with write access can push or rewrite notes. That
  is acceptable only because a note is a claim; nothing may treat it as an
  observation, and `landing-record` reads it to compare, never to approve.
- Rule 5 of `CONTRIBUTING.md` (agents merge pull requests) no longer holds in
  its current form under "Restrict updates": an agent cannot update `main` on
  GitHub, by push or by the merge button. Agents land on the owner's machine
  (`scripts/land.sh` run there) or hand the owner a branch. If the owner wants
  agents to keep landing on their own, the alternative is to add their account
  to the bypass list, which gives up the separation of duties above and leaves
  `LAND_OWNER=1` as the only guard on protected paths; that is the owner's
  decision, and this document recommends against it.
- A protected path that lands is no longer stopped by the `protected-paths`
  status check (it runs on pull requests only); `landing-record` reports it
  after the fact, and only the owner can push `main` in the first place.

## 4. Order of application

1. Merge the pull request with `scripts/land.sh` (it changes no workflow).
2. Add `post-landing.yml` and the `cancel-in-progress` change (one owner pull
   request; `protected-paths` asks for the owner's approval of it).
3. Land one change with `scripts/land.sh <branch> --push` while the classic
   rule still requires pull requests: the push is refused, which shows the
   gate is still GitHub's. Open the pull request as usual.
4. Activate the ruleset, remove the classic rule, and land the next change
   with `scripts/land.sh <branch> --push`; check that `landing-record` reads
   its record and that a deliberately failing commit on a scratch branch never
   reaches `main`.

## Reference implementations

- **OpenAI Codex cloud**: its pull requests cite the commands that were run and
  their logs. Taken: the record names the exact command and keeps the log's
  hash, with the log on the landing machine.
- **GitHub Copilot coding agent**: CI as the evidence of a run. Taken and moved:
  the same checks run before the landing, locally, and CI on `main` stays as
  the independent second run, with the record quoted next to its result.
- **git's own `pre-push` model and trunk-based development with a local gate**:
  the check runs on the machine that publishes, before it publishes.
