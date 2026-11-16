"""``scripts/rsi/decide.py``: files in, the fitness decision out (rsi-loop §8, PR 7).

The script reads scorecards, the check-runs saved per sha, the target report
and the other observations, calls ``fitness.decide``, prints the decision as
JSON with the sha256 of every file it read (computed by the script, REQ-27)
and exits 0 keep, 1 revert, 2 unmeasured. It never reaches the network.
The scorecards are the committed fake reference with the side's commit.
"""

import ast
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fitness_helpers import B_SHA, C_SHA

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "rsi" / "decide.py"
sys.path.insert(0, str(SCRIPT.parent))
import decide  # scripts/rsi/decide.py, by path

REFERENCE = json.loads(
    (REPO / "bench" / "scorecards" / "20261114T201804Z.json").read_text("utf-8")
)


def _card(sha, index, **totals):
    card = copy.deepcopy(REFERENCE)
    card["commit"] = sha
    card["stamp"] = f"20261116T0{index}0000Z"
    card["totals"].update(totals)
    return card


def _listing(sha, conclusion="success", total=None):
    runs = [
        {
            "id": i,
            "name": name,
            "head_sha": sha,
            "status": "completed",
            "conclusion": conclusion if name == "backend-tests" else "success",
            "app": {"slug": "github-actions"},
        }
        for i, name in enumerate(("backend-tests", "frontend-build"), start=1)
    ]
    return {"total_count": len(runs) if total is None else total, "check_runs": runs}


def _write(folder: Path, name: str, obj) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


@pytest.fixture
def files(tmp_path):
    """Every file of a cycle decide.py keeps, by option name."""
    paths = {}
    for side, sha in (("baseline", B_SHA), ("candidate", C_SHA)):
        for i in range(3):
            _write(tmp_path / side, f"card-{i}.json", _card(sha, i))
        paths[f"{side}_cards"] = tmp_path / side
        paths[f"{side}_checks"] = _write(tmp_path, f"{side}-ci.json", _listing(sha))
        paths[f"{side}_tests"] = _write(
            tmp_path, f"{side}-tests.json", {"backend": 4000}
        )
        paths[f"{side}_verifier"] = _write(
            tmp_path, f"{side}-verifier.json", {"sha": sha, "cases": {"cors": "pass"}}
        )
    paths["fake_card"] = _write(tmp_path, "fake.json", _card(C_SHA, 9))
    paths["graders"] = _write(
        tmp_path, "graders.json", {"baseline_tree": "t1", "candidate_tree": "t1"}
    )
    paths["protected"] = _write(
        tmp_path, "protected.json", {"sha": C_SHA, "touched": [], "approval": None}
    )
    paths["target"] = _write(
        tmp_path,
        "target.json",
        {
            "measure": "f4-adr-files",
            "baseline": {"sha": B_SHA, "result": "fail"},
            "candidate": {"sha": C_SHA, "result": "pass"},
        },
    )
    return paths


def _argv(paths):
    argv = ["--baseline-sha", B_SHA, "--candidate-sha", C_SHA]
    for key, value in paths.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return argv


def _run(paths, capsys):
    code = decide.main(_argv(paths))
    return code, json.loads(capsys.readouterr().out)


def test_a_cycle_that_passes_every_guard_is_kept_with_exit_0(files, capsys):
    code, out = _run(files, capsys)
    assert (code, out["decision"]) == (0, "keep"), out["reasons"]
    assert out["baseline"] == B_SHA and out["candidate"] == C_SHA


def test_every_file_read_is_hashed_by_the_script(files, capsys):
    _, out = _run(files, capsys)
    target = next(i for i in out["inputs"] if i["path"] == str(files["target"]))
    assert target["sha256"] == hashlib.sha256(files["target"].read_bytes()).hexdigest()
    # six cards, checks, tests and Verifier bench per side, and four more
    assert len(out["inputs"]) == 6 + 2 * 3 + 4
    assert all(len(i["sha256"]) == 64 for i in out["inputs"])


def test_a_failing_check_run_reverts_with_exit_1(files, capsys):
    files["candidate_checks"].write_text(json.dumps(_listing(C_SHA, "failure")))
    code, out = _run(files, capsys)
    assert (code, out["decision"]) == (1, "revert")


def test_a_regression_in_the_cards_reverts(files, capsys):
    for i in range(3):
        _write(
            files["candidate_cards"],
            f"card-{i}.json",
            _card(C_SHA, i, criteria_rate=0.5),
        )
    code, _ = _run(files, capsys)
    assert code == 1


def test_a_missing_card_is_unmeasured_with_exit_2(files, capsys):
    (files["candidate_cards"] / "card-2.json").unlink()
    code, out = _run(files, capsys)
    assert (code, out["decision"]) == (2, "unmeasured")


def test_a_card_off_the_schema_is_left_out_and_named(files, capsys):
    bad = files["candidate_cards"] / "card-2.json"
    bad.write_text(json.dumps({"schema_version": 3}))
    code, out = _run(files, capsys)
    assert code == 2
    entry = next(i for i in out["inputs"] if i["path"] == str(bad))
    assert entry["error"].startswith("off the schema")


def test_a_card_of_another_commit_is_refused(files, capsys):
    _write(files["candidate_cards"], "card-1.json", _card(B_SHA, 1))
    code, out = _run(files, capsys)
    assert code == 2
    assert any(r["code"] == "wrong_sha" for r in out["reasons"])


def test_a_check_run_listing_saved_for_the_wrong_sha_is_refused(files, capsys):
    files["candidate_checks"].write_text(json.dumps(_listing(B_SHA)))
    code, out = _run(files, capsys)
    assert code == 2
    assert any(r["code"] == "wrong_sha" for r in out["reasons"])


def test_a_partial_check_run_listing_is_unmeasured(files, capsys):
    files["candidate_checks"].write_text(json.dumps(_listing(C_SHA, total=40)))
    assert _run(files, capsys)[0] == 2


def test_a_file_that_names_another_producer_is_refused(files, capsys):
    target = json.loads(files["target"].read_text())
    files["target"].write_text(json.dumps({**target, "produced_by": "candidate"}))
    code, out = _run(files, capsys)
    assert code == 2
    assert any(r["code"] == "not_produced_by_platform" for r in out["reasons"])


def test_an_unapproved_protected_path_reverts(files, capsys):
    files["protected"].write_text(
        json.dumps(
            {
                "sha": C_SHA,
                "touched": [
                    {"path": "scripts/bench/compare.py", "pattern": "scripts/bench/"}
                ],
                "approval": None,
            }
        )
    )
    code, out = _run(files, capsys)
    assert code == 1
    assert any(r["code"] == "protected_path" for r in out["reasons"])


@pytest.mark.parametrize(
    "option", ["target", "candidate_tests", "fake_card", "protected"]
)
def test_a_missing_observation_is_unmeasured(files, capsys, option):
    del files[option]
    assert _run(files, capsys)[0] == 2


def test_an_unreadable_file_is_named_and_unmeasured(files, capsys):
    files["graders"].unlink()
    code, out = _run(files, capsys)
    assert code == 2
    entry = next(i for i in out["inputs"] if i["path"] == str(files["graders"]))
    assert entry["error"].startswith("unreadable")


def test_a_card_with_unreported_turns_has_no_token_count():
    card = _card(B_SHA, 0)
    card["specs"][0]["tokens"]["turns_unreported"] = 1
    run = decide.card_run(card, 0)
    assert run["totals"]["tokens_reported"] is None
    assert run["specs"][card["specs"][1]["name"]]["tokens_reported"] is not None


def test_a_card_the_budget_cut_is_not_complete():
    card = _card(B_SHA, 0)
    card["run"] = {"status": "budget_exceeded"}
    assert decide.card_run(card, 0)["status"] == "budget_exceeded"
    card = _card(B_SHA, 0)
    card["specs"][0]["status"] = "timeout"
    assert decide.card_run(card, 0)["status"] == "timeout"


def test_the_script_imports_nothing_that_reaches_the_network():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    names |= {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    network = {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "subprocess"}
    assert not {name.split(".")[0] for name in names} & network


def test_the_command_line_exits_by_the_decision(files):
    done = subprocess.run(
        [sys.executable, str(SCRIPT), *_argv(files)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["decision"] == "keep"
