"""A whole cycle with every external command and GitHub's API faked
(``scripts/rsi/cycle.py``; rsi-loop §7, §8 PR 8; REQ-1, REQ-8, REQ-17,
REQ-18, REQ-26).

The happy path keeps C: the target fails on B and passes on C, the bench runs
alternate with graders pinned to B, CI is green on both shas, and B's copy of
``decide.py`` (run for real) answers ``keep``. The record lists every command
with its exit status and the sha256 of its output and every kept file with its
hash, all computed by the runner, and — once a person merged C — becomes the
record ``cycle_record.validate`` accepts and ``watch.py`` reads. ``--dry-run``
runs nothing and calls nothing; ``--close-pr`` is the only write, off by
default. The refusals are ``test_rsi_cycle.py``.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request

import pytest
from cycle_helpers import (
    FakeGitHub,
    FakeRunner,
    commit,
    cycle,
    cycle_record,
    make_repo,
    run_cycle,
)
from decide_helpers import listing


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def setup(tmp_path):
    repo, b_sha, c_sha = make_repo(tmp_path)
    return tmp_path, repo, b_sha, c_sha


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_happy_path_keeps_c_and_records_every_step(setup, monkeypatch, capsys):
    tmp, repo, b_sha, c_sha = setup
    runner, gh = FakeRunner(b_sha, c_sha), FakeGitHub(c_sha)
    status, record = run_cycle(monkeypatch, tmp, repo, None, runner, gh, "--pr", "12")
    out = capsys.readouterr().out
    assert status == 0 and record["decision"] == "keep", record.get("reasons")
    assert record["reasons"] == []  # only the guards that did not pass are kept
    # REQ-1: B is the sha the baseline ref resolved to; C is the PR's head.
    assert record["baseline_sha"] == b_sha and record["baseline_ref"] == "main~1"
    assert record["candidate_sha"] == c_sha and record["pull_request"] == 12
    assert record["graders_sha"] == b_sha
    assert record["finding_id"] == "F5"
    # REQ-17: the keep is a recommendation; nothing merged, nothing written.
    assert "eligible for a person's merge" in out and c_sha in out
    assert "does not merge" in out
    assert all(method == "GET" for method, _, _ in gh.calls)
    # REQ-8: the check-runs were read by sha, both sides.
    urls = [u for _, u, _ in gh.calls if "/check-runs" in u]
    assert any(f"/commits/{b_sha}/" in u for u in urls)
    assert any(f"/commits/{c_sha}/" in u for u in urls)
    assert not any("/issues/" in u or "/comments" in u for _, u, _ in gh.calls)
    # The steps of §7, in order, each command with its status and output hash.
    commands = [s for s in record["steps"] if "exit_status" in s]
    names = [s["step"] for s in commands]
    assert names[0] == "measure_B" and names[-1] == "decide"
    assert names.index("measure_C") > names.index("bench_C_fake")
    for step in commands:
        log = tmp / "work" / step["output"]
        assert step["output_sha256"] == _sha256(log)
        assert isinstance(step["exit_status"], int)
    assert [s["step"] for s in record["steps"]][-1] == "pull_request_head_after"


def test_every_kept_file_is_listed_with_the_hash_the_runner_computed(
    setup, monkeypatch
):
    tmp, repo, b_sha, c_sha = setup
    _, record = run_cycle(
        monkeypatch, tmp, repo, c_sha, FakeRunner(b_sha, c_sha), FakeGitHub(c_sha)
    )
    kept = {k["path"]: k["sha256"] for k in record["kept_files"]}
    for name in (
        "B-ci.json",
        "C-ci.json",
        "B-tests.json",
        "C-tests.json",
        "B-verifier.json",
        "C-verifier.json",
        "graders.json",
        "protected.json",
        "target.json",
        "decision.json",
        "measure/B-measure.json",
        "measure/C-measure.json",
        "inputs/findings.json",
    ):
        assert kept[name] == _sha256(tmp / "work" / name), name
    cards = [p for p in kept if p.startswith("bench/") and p.endswith(".json")]
    assert len(cards) == 7
    decision = json.loads((tmp / "work" / "decision.json").read_text("utf-8"))
    read = {i["path"]: i["sha256"] for i in decision["inputs"] if "sha256" in i}
    ci = str(tmp / "work" / "C-ci.json")
    assert read[ci] == kept["C-ci.json"], "decide.py hashed the same bytes"
    target = json.loads((tmp / "work" / "target.json").read_text("utf-8"))
    assert target["baseline"] == {"sha": b_sha, "result": "fail"}
    assert target["candidate"] == {"sha": c_sha, "result": "pass"}


def test_b_medians_and_spread_make_a_record_the_watch_accepts(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    _, record = run_cycle(
        monkeypatch, tmp, repo, c_sha, FakeRunner(b_sha, c_sha), FakeGitHub(c_sha)
    )
    totals = record["baseline"]["totals"]
    assert totals["criteria_rate"] == {"median": 1.0, "spread": 0.0, "runs": 3}
    assert record["models"] == {"Developer": ["fake-model"], "QA": ["fake-model"]}
    problems = cycle_record.validate(record)
    assert problems and all("merged_" in p for p in problems), problems
    (repo / "MERGED.md").write_text("merged by a person\n", "utf-8")
    merged = commit(repo, "Merge C")
    final = tmp / "rsi" / "cycles" / "cycle-1-demo.json"
    status = cycle.main(
        [
            "record",
            "--record", str(tmp / "cycle.json"),
            "--merged-sha", merged,
            "--repo", str(repo),
            "--out", str(final),
        ]
    )  # fmt: skip
    assert status == 0
    written = json.loads(final.read_text("utf-8"))
    assert cycle_record.validate(written) == []
    assert written["merged_sha"] == merged
    assert cycle_record.merged_at_dt(written) is not None


def test_a_merge_that_does_not_hold_c_makes_no_record(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    run_cycle(
        monkeypatch, tmp, repo, c_sha, FakeRunner(b_sha, c_sha), FakeGitHub(c_sha)
    )
    status = cycle.main(
        [
            "record",
            "--record", str(tmp / "cycle.json"),
            "--merged-sha", b_sha,
            "--repo", str(repo),
            "--out", str(tmp / "final.json"),
        ]
    )  # fmt: skip
    assert status == 2 and not (tmp / "final.json").exists()


def test_a_revert_says_so_and_closes_the_pull_request_only_when_asked(
    setup, monkeypatch, capsys
):
    tmp, repo, b_sha, c_sha = setup
    gh = FakeGitHub(c_sha)
    failing = listing(c_sha, conclusion="failure")
    gh.listings[c_sha] = failing
    status, record = run_cycle(
        monkeypatch, tmp, repo, None, FakeRunner(b_sha, c_sha), gh, "--pr", "12"
    )
    assert status == 1 and record["decision"] == "revert"
    assert any(r["code"] == "ci_failed" for r in record["reasons"])
    assert "close it unmerged (REQ-18)" in capsys.readouterr().out
    assert not any(method == "PATCH" for method, _, _ in gh.calls)

    gh = FakeGitHub(c_sha)
    gh.listings[c_sha] = failing
    status, record = run_cycle(
        monkeypatch,
        tmp,
        repo,
        None,
        FakeRunner(b_sha, c_sha),
        gh,
        "--pr",
        "12",
        "--close-pr",
    )
    patches = [(u, b) for m, u, b in gh.calls if m == "PATCH"]
    assert patches == [
        (
            "https://api.github.com/repos/example/la-fabrique/pulls/12",
            {"state": "closed"},
        )
    ]
    assert any(s["step"] == "close_pull_request" for s in record["steps"])


def test_a_keep_never_closes_the_pull_request(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    gh = FakeGitHub(c_sha)
    status, _ = run_cycle(
        monkeypatch,
        tmp,
        repo,
        None,
        FakeRunner(b_sha, c_sha),
        gh,
        "--pr",
        "12",
        "--close-pr",
    )
    assert status == 0
    assert not any(method == "PATCH" for method, _, _ in gh.calls)


def test_the_dry_run_runs_no_bench_and_calls_no_api(setup, monkeypatch, capsys):
    tmp, repo, b_sha, c_sha = setup
    runner, gh = FakeRunner(b_sha, c_sha), FakeGitHub(c_sha)
    status, record = run_cycle(
        monkeypatch, tmp, repo, None, runner, gh, "--pr", "12", "--dry-run"
    )
    assert status == 0 and record["status"] == "dry_run"
    assert runner.calls == [] and gh.calls == []
    plan = capsys.readouterr().out
    assert "no bench, no network" in plan
    assert f"--graders {b_sha}" in plan
    assert "must fail (exit 3)" in plan


def test_the_dry_run_still_checks_the_inputs(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    status, record = run_cycle(
        monkeypatch,
        tmp,
        repo,
        c_sha,
        FakeRunner(b_sha, c_sha),
        FakeGitHub(c_sha),
        "--dry-run",
        findings=None,
    )
    assert status == 3 and record["status"] == "refused"
