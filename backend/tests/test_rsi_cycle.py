"""The cycle runner refuses what it must (``scripts/rsi/cycle.py``; rsi-loop §8 PR 8).

Negative tests first: a target measure that already passes on B, a
specification whose cycle was reverted (REQ-20), a check-run listing read at
another sha (REQ-8), a kept file changed after it was recorded (REQ-26),
scorecards not graded by B's tests (REQ-4), a protected path without a code
owner's approval (REQ-5), a pull request whose head moved (REQ-17), and, by
inspection of the code, no merge, push or approval anywhere. The happy path is
``test_rsi_cycle_run.py``. No test reaches the network: the one function that
does (``cycle_github.request``) is replaced, and ``urlopen`` refuses.
"""

from __future__ import annotations

import ast
import json
import urllib.request

import pytest
from cycle_helpers import (
    RSI,
    FakeGitHub,
    FakeRunner,
    cycle,
    cycle_github,
    findings_file,
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


def _bench_calls(runner):
    return [c for c in runner.calls if c[0].endswith("run.sh")]


def test_a_target_measure_that_already_passes_on_b_refuses_the_cycle(
    setup, monkeypatch
):
    tmp, repo, b_sha, c_sha = setup
    runner = FakeRunner(b_sha, c_sha)
    runner.measure["B"] = 0
    status, record = run_cycle(monkeypatch, tmp, repo, c_sha, runner, FakeGitHub(c_sha))
    assert status == cycle.EXIT["refused"] == 3
    assert record["status"] == "refused"
    assert record["refusal"]["reason"] == "target_passes_on_baseline"
    assert not _bench_calls(runner), "no bench is run once B already passes"
    assert [s["step"] for s in record["steps"] if "exit_status" in s] == ["measure_B"]


def test_a_target_measure_that_cannot_run_on_b_refuses_too(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    runner = FakeRunner(b_sha, c_sha)
    runner.measure["B"] = 1
    status, record = run_cycle(monkeypatch, tmp, repo, c_sha, runner, FakeGitHub(c_sha))
    assert status == 3
    assert record["refusal"]["reason"] == "target_error_on_baseline"


def test_a_reverted_specification_refuses_unless_a_person_is_named(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    reverted = findings_file(tmp, reverted=True)
    runner = FakeRunner(b_sha, c_sha)
    status, record = run_cycle(
        monkeypatch, tmp, repo, c_sha, runner, FakeGitHub(c_sha), findings=reverted
    )
    assert status == 3
    assert record["refusal"]["reason"] == "specification_reverted"
    assert record["findings_gate"]["reverted_findings"] == [7]
    assert runner.calls == [], "nothing runs for a reverted specification"

    asked = FakeRunner(b_sha, c_sha)
    status, record = run_cycle(
        monkeypatch,
        tmp,
        repo,
        c_sha,
        asked,
        FakeGitHub(c_sha),
        "--retry-by",
        "A person",
        findings=reverted,
    )
    assert record["findings_gate"]["reason"] == "person_asked"
    assert record["findings_gate"]["retry_by"] == "A person"
    assert status == 0 and record["decision"] == "keep"


def test_a_cycle_without_the_findings_export_does_not_start(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    runner = FakeRunner(b_sha, c_sha)
    status, record = run_cycle(
        monkeypatch, tmp, repo, c_sha, runner, FakeGitHub(c_sha), findings=None
    )
    assert status == 3
    assert "REQ-20" in record["refusal"]["detail"]
    assert runner.calls == []


def test_a_check_run_listing_for_another_sha_is_refused(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    gh = FakeGitHub(c_sha)
    gh.listings[c_sha] = listing(b_sha)  # C's listing, its runs on B
    status, record = run_cycle(
        monkeypatch, tmp, repo, c_sha, FakeRunner(b_sha, c_sha), gh
    )
    assert status == 2 and record["decision"] == "unmeasured"
    step = next(s for s in record["steps"] if s["step"] == "check_runs_C")
    assert step["refused"] == ["backend-tests", "frontend-build"]
    assert any(r["code"] == "wrong_sha" for r in record["reasons"])
    assert all(f"/commits/{c_sha}/check-runs" in u for u in step["command"][1:])


def test_a_kept_file_changed_after_it_was_recorded_is_found(setup, monkeypatch, capsys):
    tmp, repo, b_sha, c_sha = setup
    status, record = run_cycle(
        monkeypatch, tmp, repo, c_sha, FakeRunner(b_sha, c_sha), FakeGitHub(c_sha)
    )
    assert status == 0
    assert cycle.main(["verify", "--record", str(tmp / "cycle.json")]) == 0
    card = next(
        k["path"] for k in record["kept_files"] if k["path"].startswith("bench/C-real")
    )
    path = tmp / "work" / card
    tampered = json.loads(path.read_text("utf-8"))
    tampered["totals"]["criteria_rate"] = 1.0
    tampered["totals"]["tokens_reported"] = 1
    path.write_text(json.dumps(tampered), "utf-8")
    capsys.readouterr()
    assert cycle.main(["verify", "--record", str(tmp / "cycle.json")]) == 1
    assert card in capsys.readouterr().out
    status = cycle.main(
        [
            "record",
            "--record", str(tmp / "cycle.json"),
            "--merged-sha", c_sha,
            "--repo", str(repo),
            "--out", str(tmp / "final.json"),
        ]
    )  # fmt: skip
    assert status == 2
    assert not (tmp / "final.json").exists()


def test_cards_not_graded_by_b_refuse_the_cycle(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    runner = FakeRunner(b_sha, c_sha)
    runner.graders_sha = c_sha  # graded by C's own copy
    status, record = run_cycle(monkeypatch, tmp, repo, c_sha, runner, FakeGitHub(c_sha))
    assert status == 3
    assert record["refusal"]["reason"] == "graders_not_pinned"
    assert "not by B" in record["refusal"]["detail"]


def test_every_bench_run_is_pinned_to_b_and_alternates(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    runner = FakeRunner(b_sha, c_sha)
    run_cycle(monkeypatch, tmp, repo, c_sha, runner, FakeGitHub(c_sha))
    calls = _bench_calls(runner)
    assert len(calls) == 7  # K=3 per side, then the fake on C
    assert all(c[c.index("--graders") + 1] == b_sha for c in calls)
    sides = [c[0].split("/trees/")[1].split("/")[0] for c in calls]
    assert sides == ["B", "C", "B", "C", "B", "C", "C"]


def test_a_real_bench_needs_a_real_model(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    runner = FakeRunner(b_sha, c_sha)
    monkeypatch.setattr(cycle_github, "request", FakeGitHub(c_sha))
    monkeypatch.delenv("BENCH_LLM_URL", raising=False)
    from cycle_helpers import argv

    status = cycle.main(argv(repo, tmp, c_sha), runner=runner)
    assert status == 3
    assert not _bench_calls(runner)


def test_a_protected_path_without_an_approval_reverts_without_a_bench(
    tmp_path, monkeypatch
):
    repo, b_sha, c_sha = make_repo(tmp_path, change="scripts/bench/thresholds.py")
    runner = FakeRunner(b_sha, c_sha)
    status, record = run_cycle(
        monkeypatch, tmp_path, repo, c_sha, runner, FakeGitHub(c_sha), "--pr", "12"
    )
    assert status == 1 and record["decision"] == "revert"
    assert any(r["code"] == "protected_path" for r in record["reasons"])
    assert any(r["code"] == "graders_differ" for r in record["reasons"])
    assert not _bench_calls(runner)
    match = next(s for s in record["steps"] if s["step"] == "protected_match")
    assert match["touched"] == ["scripts/bench/thresholds.py"]


def test_a_head_that_moved_voids_a_keep(setup, monkeypatch):
    tmp, repo, b_sha, c_sha = setup
    gh = FakeGitHub(c_sha, heads=[c_sha, "d" * 40])
    status, record = run_cycle(
        monkeypatch, tmp, repo, None, FakeRunner(b_sha, c_sha), gh, "--pr", "12"
    )
    assert status == 2 and record["decision"] == "unmeasured"
    assert any(r["code"] == "head_moved" for r in record["reasons"])


def test_the_same_sha_on_both_sides_is_refused(setup, monkeypatch):
    tmp, repo, b_sha, _ = setup
    status, record = run_cycle(
        monkeypatch, tmp, repo, b_sha, FakeRunner(b_sha, b_sha), FakeGitHub(b_sha)
    )
    assert status == 3 and record["refusal"]["reason"] == "same_sha"


def test_the_token_reaches_no_command_and_no_record(setup, monkeypatch, capsys):
    tmp, repo, b_sha, c_sha = setup
    fake_token = "test-" + "token-" + "value"
    monkeypatch.setenv("GITHUB_TOKEN", fake_token)
    monkeypatch.setenv("GH_TOKEN", fake_token)
    seen = []

    class Watching(FakeRunner):
        def __call__(self, argv, cwd, env, timeout):
            seen.append(dict(env or {}))
            return super().__call__(argv, cwd, env, timeout)

    status, _ = run_cycle(
        monkeypatch, tmp, repo, c_sha, Watching(b_sha, c_sha), FakeGitHub(c_sha)
    )
    assert status == 0 and seen
    assert all(fake_token not in env.values() for env in seen)
    assert fake_token not in (tmp / "cycle.json").read_text("utf-8")
    assert fake_token not in capsys.readouterr().out


# ── by inspection: no merge, no push, no approval ────────────────────────────

SOURCES = sorted(RSI.glob("cycle*.py"))
GIT_READS = {"rev-parse", "show", "cat-file", "merge-base", "clone", "checkout"}


def _calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if called == name:
                yield node


def test_the_cycle_has_no_merge_push_or_approval_call():
    assert {p.name for p in SOURCES} >= {
        "cycle.py",
        "cycle_after.py",
        "cycle_gate.py",
        "cycle_github.py",
        "cycle_steps.py",
    }
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for call in _calls(tree, "git"):
            first = call.args[1] if len(call.args) > 1 else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                assert first.value in GIT_READS, f"{path.name}: git {first.value}"
        for call in _calls(tree, "request"):
            method = call.args[0] if call.args else None
            if isinstance(method, ast.Constant):
                assert method.value in ("GET", "PATCH"), f"{path.name}: {method.value}"
        strings = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        for literal in strings:
            lowered = literal.lower()
            assert "/merge" not in lowered and "/merges" not in lowered, path.name
            assert lowered not in ("push", "merge", "approve"), path.name
            assert '"event": "approve"' not in lowered, path.name
        assert "subprocess" not in text or path.name == "cycle_steps.py", path.name


def test_the_only_write_to_github_closes_the_pull_request():
    tree = ast.parse((RSI / "cycle_github.py").read_text(encoding="utf-8"))
    patches = [
        c
        for c in _calls(tree, "request")
        if isinstance(c.args[0], ast.Constant) and c.args[0].value == "PATCH"
    ]
    assert len(patches) == 1
    body = ast.literal_eval(patches[0].args[2])
    assert body == {"state": "closed"}
