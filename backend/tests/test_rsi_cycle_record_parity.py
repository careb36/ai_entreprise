"""The backend refuses exactly the cycle records the script refuses
(``services/contracts/rsi_cycle_record.py`` beside ``scripts/rsi/cycle_record.py``;
``docs/design/rsi-loop.md``, PR 10 of section 8).

The backend imports the records ``scripts/rsi/cycle.py`` writes
(``services/management/rsi_cycle_import.py``) and checks them with the
contracts copy of the rules, given fitness's guarded metrics. This test runs
both validators over the same records, sound and broken, and fails on the
first record they answer differently: when the script's schema moves, the
contracts module must follow. Then a record ``cycle.py`` itself writes after a
merge is read by the backend's import: it validates, and its kept files'
hashes are checked by the backend.
"""

from __future__ import annotations

import copy
import json
import urllib.request

import pytest
from cycle_helpers import FakeGitHub, FakeRunner, commit, cycle, make_repo, run_cycle
from services.contracts import rsi_cycle_record
from services.evidence import fitness
from services.management import rsi_cycle_import
from watch_helpers import baseline_totals, cycle_record, summary

SHA = "a" * 40


def _sound() -> dict:
    return {
        "schema_version": 1,
        "cycle_id": "cycle-1-folder-lint",
        "merged_sha": SHA,
        "merged_at": "2026-11-15T12:00:00Z",
        "graders_sha": SHA,
        "models": {"Developer": ["fake-model"]},
        "baseline_sha": SHA,
        "finding_id": "F5",
        "specification": "folder-lint",
        "pull_request": 630,
        "prompt_targeted": False,
        "k": 3,
        "baseline": {
            "totals": {**baseline_totals(), "prompt_tokens_mean": summary(2000.0, 1.0)},
            "specs": {"01-cli-wordcount": baseline_totals()},
        },
    }


def _broken() -> list:
    cases: list = [None, [], "record", {}]
    changes = [
        ("schema_version", 2),
        ("schema_version", None),
        ("cycle_id", ""),
        ("cycle_id", "a/b"),
        ("cycle_id", "cycle..1"),
        ("cycle_id", "cycle."),
        ("cycle_id", "cycle.lock"),
        ("cycle_id", "c\n"),
        ("cycle_id", 7),
        ("merged_sha", "B" * 40),
        ("merged_sha", SHA + "\n"),
        ("graders_sha", None),
        ("merged_at", "yesterday"),
        ("merged_at", "2026-11-15T12:00:00"),
        ("models", {}),
        ("models", {"Developer": []}),
        ("models", {"D": [""]}),
        ("baseline_sha", "short"),
        ("finding_id", 5),
        ("specification", None),
        ("pull_request", 0),
        ("pull_request", True),
        ("k", "3"),
        ("prompt_targeted", "yes"),
        ("baseline", None),
        ("baseline", {}),
        ("baseline", {"totals": {}, "extra": 1}),
        ("baseline", {"totals": baseline_totals(), "specs": []}),
    ]
    for key, value in changes:
        rec = _sound()
        rec[key] = value
        cases.append(rec)
    for metric in fitness.GUARDED:
        rec = _sound()
        del rec["baseline"]["totals"][metric]
        cases.append(rec)
    for bad in (
        {"median": float("nan"), "spread": 0},
        {"median": 1, "spread": -1},
        {"median": True, "spread": 0},
        {"median": 1, "spread": 0, "runs": 0},
        {"median": 1, "spread": 0, "extra": 1},
        [1, 2],
    ):
        rec = _sound()
        rec["baseline"]["totals"]["criteria_rate"] = bad
        cases.append(rec)
    rec = _sound()
    rec["baseline"]["specs"]["01-cli-wordcount"]["unknown_metric"] = summary(1, 0)
    cases.append(rec)
    return cases


@pytest.mark.parametrize("record", [_sound(), *_broken()])
def test_the_backend_and_the_script_agree(record):
    script = cycle_record.validate(copy.deepcopy(record))
    backend = rsi_cycle_record.validate(copy.deepcopy(record), fitness.GUARDED)
    assert backend == script


def test_the_corpus_holds_one_sound_record_and_only_broken_others():
    assert cycle_record.validate(_sound()) == []
    assert all(cycle_record.validate(record) for record in _broken())


def test_both_name_the_same_metrics_and_version():
    assert tuple(cycle_record.REQUIRED_METRICS) == tuple(fitness.GUARDED)
    assert cycle_record.OPTIONAL_METRICS == rsi_cycle_record.OPTIONAL_METRICS
    assert cycle_record.SCHEMA_VERSION == rsi_cycle_record.SCHEMA_VERSION


def test_a_record_cycle_py_writes_is_imported_with_its_hashes_checked(
    tmp_path, monkeypatch
):
    """The record ``scripts/rsi/cycle.py record`` writes after a merge (PR 8,
    every command and GitHub faked) validates in the backend, and every kept
    file it lists hashes, read by the backend, to what it claims."""

    def no_network(*args, **kwargs):
        raise AssertionError("a test reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    repo, b_sha, c_sha = make_repo(tmp_path)
    runner, github = FakeRunner(b_sha, c_sha), FakeGitHub(c_sha)
    status, _ = run_cycle(monkeypatch, tmp_path, repo, c_sha, runner, github)
    assert status == 0
    (repo / "MERGED.md").write_text("merged by a person\n", "utf-8")
    merged = commit(repo, "Merge C")
    final = tmp_path / "final.json"
    argv = ["record", "--record", str(tmp_path / "cycle.json")]
    argv += ["--merged-sha", merged, "--repo", str(repo), "--out", str(final)]
    assert cycle.main(argv) == 0
    written = json.loads(final.read_text("utf-8"))
    assert written["kept_files"] and written["candidate_sha"] == c_sha
    assert rsi_cycle_import.validate(written) == []
    work = tmp_path / "work"
    kept, problems = rsi_cycle_import.observe_kept_files(written["kept_files"], work)
    assert problems == []
    assert all(k["sha256_observed"] == k["sha256_claimed"] for k in kept)
    # A kept file changed after the run: the backend's own hash refuses it.
    first = written["kept_files"][0]["path"]
    (work / first).write_bytes(b"changed")
    _, problems = rsi_cycle_import.observe_kept_files(written["kept_files"], work)
    assert len(problems) == 1 and first in problems[0]
