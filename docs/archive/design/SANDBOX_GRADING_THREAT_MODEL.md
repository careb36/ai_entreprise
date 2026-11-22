# Threat model: grading candidate code in the sandbox

Status: draft, 2026-11-17, for a person's review · Issues: #323, rsi-loop PR 12
(G3, G4) · SDL practice 3 · Procedure: `.claude/skills/threat-model/SKILL.md`

## What is built

The self-improvement runner grades a candidate build C with checks taken from
a pinned commit B: the finding's target measure
(`scripts/self_host/spec_source.run_measure`, O8) and the bench's
person-written acceptance tests (`scripts/bench/bench_grade.grade`, O1).
Both run pytest on code the factory produced. Until now that pytest ran on
the runner's own Python (`python -I`, no conftest, no ini file), so the
candidate's code ran with the runner's files, network and environment. This
change runs it in the daemon's container sandbox
(`scripts/bench/sandbox_grading.py` over `vscode-daemon/sandbox.py`) and
reads the verdict from outside the container. It is triggered by a person or
the runner's script; it changes the measure's report, a scorecard's
criteria and, through them, the fitness decision (`services/evidence/fitness.py`).

## Assets

- The verdict's integrity: `result`, `passed`, the criteria, and the output's
  sha256. A forged pass keeps a change that should be reverted (rule 7:
  unknown is never a pass).
- The graders and the measure at B: they must not change while grading.
- The runner's machine: the person's files, the repository with B, the
  credentials in the runner's environment (`FABRIQUE_API_TOKEN`, a GitHub
  token, provider keys), the network.
- Availability: the runner's CPU, memory and time, and the cycle's budget.

## Data flow and trust boundaries

```
 Runner (B's scripts) ──B13── container (sandbox)        B13 (new): runner ↔ grading container
   │  docker run --network none --read-only --pull=never --cap-drop ALL
   │      --memory/--cpus/--pids-limit, uid of the person
   │      -v C:/candidate:ro   -v graders(B):/graders:ro
   │      -v scratch:/workspace:rw   --tmpfs /tmp
   │  ◄── exit status (from the runtime), stdout/stderr pipes, the frame
   │
   └── report (result, reason, sandbox, sha256 computed by the runner) → fitness.decide
```

B12 (rsi-loop §6, the runner ↔ the build under test) is where the candidate
is the party being recorded. B13 is new: the container in which the
candidate's code runs during grading, a narrower case of B3 (daemon ↔
sandbox). Everything that comes out of it is B5 input (agent output).

What the candidate controls: every file of C (its code, tests it adds,
`conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`,
`sitecustomize.py`, a `pytest.py` or `json.py` shadow, requirements), and
everything its code does once imported: output, exit status, child
processes, files in writable places, network attempts, CPU and memory.
What it does not control: the runner's scripts and the measure (B's), the
image (the operator's, already on the machine), the container's options, the
environment the wrapper sets.

## Threats

| ID | Boundary | STRIDE | Threat | L | I | Sev | Mitigation |
|---|---|---|---|---|---|---|---|
| S1 | B13 | T | A candidate `conftest.py`, `pytest.ini`/`pyproject.toml`/`setup.cfg` or plugin entry point changes what pytest runs or reports | H | H | Critical | Existing: `--noconftest` (measure), `-c /dev/null`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `-p no:cacheprovider`. New: `--confcutdir` the graders' folder (bench); rootdir `/graders`, outside C. Tests: `test_a_candidate_file_cannot_turn_a_failing_measure_into_a_pass`, `test_a_project_cannot_forge_the_bench_grade_in_the_sandbox` |
| S2 | B13 | T | `sitecustomize.py`, `usercustomize.py` or a `pytest.py` shadow in C runs before pytest | H | H | Critical | Existing: `python -I` (no cwd, no `PYTHONPATH`, no user site), C appended to `sys.path` after pytest is imported. New: the image's site-packages are read-only (`--read-only`, non-root uid), so no `.pth` can be planted. Same tests |
| S3 | B13 | T | The runner's or candidate's environment (`PYTHONPATH`, `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `PYTHONSTARTUP`) reaches pytest | M | H | High | New: the container gets the daemon's base environment only (`sandbox.BASE_ENV`, no listed names); the wrapper unsets those names. Test: `test_the_runners_environment_does_not_reach_the_sandbox` |
| S4 | B13 | S | The verdict is read from a file the candidate wrote (a fake JUnit, a `result.json`), or a test the candidate added writes it | H | H | Critical | New: the runner reads no file from the container. The exit status comes from the runtime; the report is printed by the runner's wrapper after pytest exited and, as PID 1, after every other process was killed (`kill -9 -1`); only the last frame, carrying this run's nonce and the runtime's exit status, is read. A candidate's tests are not collected (only the measure's file, or B's acceptance tests). Tests: `test_a_test_the_candidate_adds_is_not_collected_and_writes_no_verdict`, `test_a_forged_frame_on_the_output_is_not_read`, `test_the_frame_must_be_this_runs_and_the_last_output` |
| S5 | B13 | T | Imported code forges the result from inside the pytest process (rewrites the report at exit, `os._exit(0)`) | M | H | High | New, partial: a pass needs exit 0 *and* a report where every test passed *and* at least one test; exit 0 without a report, a report that disagrees with the exit status, exit codes pytest does not give for a finished run are `unmeasured`. Tests: `test_code_that_rewrites_the_report_on_exit_is_not_a_pass`, `test_code_that_exits_zero_before_the_report_is_not_a_pass`. **Residual** (see below) |
| S6 | B13 | T | The candidate rewrites the measure, B's acceptance tests or the grader conftest while they run | M | H | High | New: `/graders` and `/candidate` mounted read-only, root file system read-only; only the empty scratch and `/tmp` are writable. Test (real runtime): `test_a_real_sandbox_holds[read_only]` |
| S7 | B13 | I | Candidate code reads the runner's credentials or the person's files | M | H | High | New: nothing of the host is mounted but C, the graders and an empty scratch; no variable of the runner's environment enters. Existing: the uid is the person's, capabilities dropped, `no-new-privileges` |
| S8 | B13 | I/E | Candidate code reaches the network (exfiltration, fetching a payload, calling the backend's API) | M | H | High | New: `--network none` always (the request's `network` is fixed false). Test (real runtime): `test_a_real_sandbox_holds[network]`; argv checked with the fake |
| S9 | B13 | D | A fork bomb, a memory hog or an endless sleep exhausts the runner | H | M | High | New: `--pids-limit 256`, `--memory`/`--memory-swap 2048m`, `--cpus 2`, the wall-clock timeout (`docker kill`); a timeout or a kill (137) is `unmeasured`, never `fail` read as information and never `pass`. Tests: `test_a_long_sleep_is_stopped_and_unmeasured`, `test_a_run_killed_by_a_limit_is_unmeasured`, `test_a_real_sandbox_holds[fork_bomb|memory|sleep]` |
| S10 | B13 | T | A huge output fills the runner's memory or pushes the frame out | M | L | Low | Existing: `run_bounded` keeps the last 1 MiB of each stream; the frame is the last output, so it is in the tail |
| S16 | B13 | D | The candidate fills the host's disk through a writable mount | M | M | High | New (review of PR #634): no folder of the host is writable; `/workspace` is a 256 MB tmpfs (`container_command(scratch_mb=...)`) and `/tmp` a 512 MB tmpfs, both charged to the memory limit; the wrapper reports either filled up and the run is `unmeasured`. A hung runtime after a timeout is bounded too (`docker kill` within 30 s, its failure recorded). Tests: `test_sandbox_grading_trust.py` |
| S11 | — | E | The sandbox is missing and grading falls back to the host | M | H | High | New: fail closed. No runtime, a runtime that cannot start the container (125), no image named, an image not on the machine, `bwrap` (no CPU or memory limit), the daemon's module absent: each is `unmeasured` with the reason; `self_host.py` exits 4. Tests: the "failing closed" section of `test_sandbox_grading.py` |
| S12 | — | T | An image pulled during grading is not the one the operator chose (a squatted name on a registry) | L | H | Medium | New: `--pull=never`; the image must be on the machine and is run from its content ID observed before the run (`sandbox_image.pinned`), recorded in the report. Test: `test_an_image_not_on_the_machine_is_never_pulled` |
| S13 | — | R | A cycle kept on a result graded in-process, or nobody can tell how a result was graded | M | M | Medium | New: every report and grade carries `isolation` (`sandbox` or `in_process`) and the applied sandbox; the in-process path needs an explicit flag and prints a warning; `fitness.decide` refuses a target side graded `in_process` (`not_sandboxed`). Test: `test_a_target_graded_outside_the_sandbox_is_unmeasured` |
| S14 | B5 | T/E | The candidate changes `scripts/bench/sandbox_grading.py` or the daemon's `sandbox.py`, `sandbox_image.py`, `sandbox_cache.py` or `run_logs.py` (imported by the grader on the host before any container starts) to weaken grading or run code on the host | M | H | High | New (review of PR #634): with `run.sh --graders <B>` the daemon's modules are taken from B's git objects (`graders.py daemon`), their sha256 checked before import and imported from that folder alone (`sandbox_grading.daemon_sandbox`), never from C's checkout; all four are protected paths with CODEOWNERS lines. Tests: `test_sandbox_grading_trust.py` |
| S15 | B13 | E | Container escape through a kernel or runtime bug | L | H | Medium | Existing: capabilities dropped, `no-new-privileges`, non-root uid, read-only root. Residual: a shared kernel; gVisor or a VM would narrow it |

Questions STRIDE misses. *Assume breach:* if the container is escaped (S15),
the attacker has the person's uid on the runner, as with the in-process path
today; nothing else in this design depends on the container for that.
*False assumption:* that the image holds a working pytest; if not, pytest
cannot start and the run has no report, which reads as `unmeasured`, never as
`fail` or `pass`. *A model deciding:* no model is involved; the verdict is a
pure function of the exit status and the report (`measure_verdict`).

## Tests that prove the mitigations

`backend/tests/test_sandbox_grading.py` (the sandbox faked at its boundary,
plus real-runtime tests that skip without one), `test_rsi_fitness.py`
(`not_sandboxed`), and the in-process path's existing tests in
`test_bench_grading.py` and `test_self_hosting.py`.

## Residual risks

- **In-process forgery by imported code (S5).** The measure imports C's
  code into the grading pytest process. Code written to forge pytest's
  result from within (rewrite the report and exit 0 with a report consistent
  with it) is not stopped by the sandbox. The consistency checks catch the
  cheap forms; the person reading a protected change and the diff review,
  and, later, the held-out set (G3, decision 5) keep this from being a
  reliable attack. Running each test in a separate process from the reporter
  would narrow it; not done here.
- **Same-uid tampering inside the container before the kill.** A background
  process can race to rewrite pytest's report between pytest's exit and the
  wrapper's kill; the result then disagrees with the exit status or needs
  S5's forgery as well.
- **A shared kernel (S15).**
- **The nightly bench** grades in-process until the runner holds a grading
  image (`vars.BENCH_GRADING_IMAGE` in `bench.yml`); its cards say
  `isolation: in_process`. The owner accepts this until the image is built.

## Open questions

- Which image the runner keeps for the backend's measure (the backend's
  dependencies with pytest), who builds it and how it is pinned
  (docs/OPERATIONS.md proposes one recipe).
- Whether a measure should run each test in a process of its own to close S5.
- The held-out set (rsi-loop §10, decision 5): its interface is documented in
  rsi-loop.md §2.3; fetching it is not built.
