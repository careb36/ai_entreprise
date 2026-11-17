# The self-improvement loop's scripts

The pieces of [`docs/design/rsi-loop.md`](../../docs/design/rsi-loop.md) that
run outside the backend. Everything here is a protected path (§3.1): a change
needs a code owner's approval.

| Script | What it does | Design |
|---|---|---|
| `cycle.py` (+ `cycle_steps.py`, `cycle_gate.py`, `cycle_github.py`, `cycle_after.py`) | Drives one cycle: the steps of §7 scripted, with no merge | PR 8; REQ-1, 8, 17, 18, 20, 26 |
| `decide.py` | Keep, revert or unmeasured from the files a cycle saved (`services/evidence/fitness.py`) | PR 7; REQ-5, 15, 27 |
| `cycle_record.py` | The cycle record's schema and validator | PR 11's contract with PR 8 |
| `watch.py`, `revert.py` | The nightly watch after a merge, and the revert pull request | PR 11; REQ-19 |

## A cycle: `cycle.py`

```bash
python3 scripts/rsi/cycle.py run --spec scripts/self_host/specs/<name>-specification.json \
  --findings findings.json --cycle-id cycle-1-<name> \
  --baseline origin/main --pr <n> --repository <owner>/<repo> \
  --python <backend python> --work /tmp/rsi/cycle-1 --out /tmp/rsi/cycle-1.json \
  [--k 3] [--retry-by "<person>"] [--close-pr] [--dry-run]
python3 scripts/rsi/cycle.py verify --record /tmp/rsi/cycle-1.json
python3 scripts/rsi/cycle.py record --record /tmp/rsi/cycle-1.json \
  --merged-sha <merge commit> --out rsi/cycles/cycle-1-<name>.json
```

`run` takes B, the sha `--baseline` resolves to (REQ-1), and C, `--candidate`
or the head of `--pr` read from GitHub. Both must be in the local repository
(`git fetch origin pull/<n>/head`). Then, in order:

1. **The specification**, read from B's objects with its `target_measure`
   (REQ-2).
2. **REQ-20.** The script has no database, so the findings' state is the
   platform's own answer to `GET /v1/findings?company_id=<id>`, saved to a
   file (`--findings`) and kept with its sha256. The specification's sha256 is
   computed here from its canonical text with `contracts/specification_text`,
   the key `learning/findings` stores a finding under; a finding with that key
   and a `revert` outcome stops the cycle unless `--retry-by` names the person
   who asks. Without the file the cycle does not start: an unknown state is not
   "no revert". This file was chosen over calling the API because it is the
   smaller input to test (no token, no stack) and is kept with its hash; the
   person who saves it is the one running the cycle.
3. **From git alone**: the tree ids of the grader paths (`bench`,
   `scripts/bench`, `scripts/self_host`, `scripts/e2e`, `scripts/rsi`,
   `services/evidence`) on B and C, and the files C changes matched with B's
   copy of the protected list and of `scripts/protected_paths_check.py`, with
   an approval only from a code owner's review of C's sha read from the API.
   If C changed the graders or touches a protected path without that approval,
   the bench is not run: the decision cannot be `keep`.
4. **Trees**: B and C cloned into `--work/trees` (`git clone --shared`, refs of
   their own) at their shas.
5. **The target measure on B**, B's copy of `self_host.py --steps measure`; it
   must fail (exit 3), or the cycle is refused.
6. **The bench**: K real-model runs per side, alternating B, C, each
   `run.sh --out ... --graders <B>` from that side's tree, then the fake bench
   on C. A card whose `graders.sha` is not B's refuses the cycle (REQ-4).
   `BENCH_LLM_URL` must be set; the fake measures no factory.
7. **The Verifier bench** (B's cases, each side's Verifier) and **the tests
   collected** (`pytest --collect-only` on each side's backend).
8. **The target measure on C**, B's copy.
9. **CI** (REQ-8): `GET /repos/{owner}/{repo}/commits/{sha}/check-runs` for B
   and for C, every page, saved as the listing `decide.py` reads; a run whose
   `head_sha` is not the side's sha is named as refused, and `decide.py`
   answers `unmeasured` for it. The pull request's body and comments are never
   read.
10. **B's copy of `decide.py`** decides from the saved files.
11. With `--pr`, the head is read again; if it moved, the decision is void
    (`unmeasured`, REQ-17).

Every command goes through `cycle_steps.run_command`, and every GitHub call
through `cycle_github.request`; each step records its command, its exit status
and the sha256 of its output (saved under `--work/logs`), and every kept file
(cards, listings, measure reports, `graders.json`, `protected.json`,
`target.json`, `decision.json`, the findings) is listed with the sha256 the
script computed (REQ-26, REQ-27). The record goes to `--out`. The GitHub
token is read from the environment for the API calls only and withheld from
every command, since the bench and measures run the build under test.

On `keep` the script says the pull request is eligible for a person's merge
pinned to C's sha (REQ-17). On `revert` or `unmeasured` it says so with the
reasons; `--close-pr` closes the pull request unmerged (`PATCH` with
`{"state": "closed"}`, the only write the script can make, REQ-18). There is
no merge, push or approval anywhere; `test_rsi_cycle.py` checks the code for
one.

`--dry-run` resolves B and C, checks the specification, the findings and the
graders' trees, and prints the plan: no bench, no network.

`verify` hashes the kept files again (exit 1 when one changed). `record`, once
a person merged the pull request, adds `merged_sha` (C must be its ancestor)
and git's committer date as `merged_at` to a `keep` record, checks the kept
files, validates it with `cycle_record.validate` and writes the record
`watch.py` reads; a person commits it under `rsi/cycles/`.

Exit status of `run`: 0 keep, 1 revert, 2 unmeasured (or a usage error), 3
refused.

Tests: `backend/tests/test_rsi_cycle.py` (the refusals, and the code inspected
for a merge, push or approval) and `test_rsi_cycle_run.py` (the happy path,
every command and the API faked; `decide.py` runs for real).
