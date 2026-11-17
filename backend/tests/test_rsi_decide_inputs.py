"""What ``scripts/rsi/decide.py`` refuses at the file boundary (review of PR #621).

A test count carries its own producer and sha like any observation; one card
named twice, or by its folder and by itself, counts once, and two different
cards with one stamp count for nothing; a fake-model card is no measurement
of B or C, and only a fake-model card is the circuit check (rsi-loop §2.5);
an approval in the documented format is read as the platform's record unless
it names another producer. Every case fails closed. The files are built by
``decide_helpers.cycle_files``; the other tests are ``test_rsi_decide.py``.
"""

import json

import pytest
from decide_helpers import card, codes, cycle_files, decide, run, write
from fitness_helpers import B_SHA, C_SHA


@pytest.fixture
def files(tmp_path):
    return cycle_files(tmp_path)


def test_the_fixture_is_kept(files, capsys):
    code, out = run(files, capsys)
    assert code == 0, out["reasons"]


# --- 2. The test count's own provenance --------------------------------------


@pytest.mark.parametrize(
    "tests",
    [
        {"produced_by": "candidate", "sha": C_SHA, "suites": {"backend": 4000}},
        {"sha": B_SHA, "suites": {"backend": 4000}},
        {"suites": {"backend": 4000}},
        {"backend": 4000},
        {"sha": C_SHA, "suites": {"backend": "4000"}},
    ],
    ids=["self-reported", "wrong-sha", "no-sha", "bare-counts", "count-not-integer"],
)
def test_a_test_count_without_its_own_provenance_is_refused(files, capsys, tests):
    write(files["candidate_tests"].parent, files["candidate_tests"].name, tests)
    code, out = run(files, capsys)
    assert code == 2, out["reasons"]
    assert "not_fewer" not in codes(out)


# --- 5. Cards named twice ------------------------------------------------------


def test_the_same_card_named_twice_counts_once(files, capsys):
    folder = files["candidate_cards"]
    (folder / "card-2.json").unlink()
    files["candidate_cards"] = [folder / "card-0.json", folder / "card-1.json"] * 2
    code, out = run(files, capsys)
    assert code == 2
    assert "too_few_runs" in codes(out)


def test_a_folder_and_a_card_inside_it_count_once(files, capsys):
    folder = files["candidate_cards"]
    (folder / "card-2.json").unlink()
    files["candidate_cards"] = [
        folder,
        folder / "card-1.json",
        folder / ".." / folder.name / "card-0.json",
    ]
    code, out = run(files, capsys)
    assert code == 2
    assert "too_few_runs" in codes(out)


def test_the_same_card_under_two_names_counts_once(files, capsys):
    folder = files["candidate_cards"]
    (folder / "card-2.json").write_text((folder / "card-1.json").read_text())
    code, out = run(files, capsys)
    assert code == 2
    assert "too_few_runs" in codes(out)


def test_two_different_cards_with_one_stamp_count_for_nothing(files, capsys):
    folder = files["candidate_cards"]
    write(folder, "card-2.json", card(C_SHA, 1, criteria_rate=0.5))
    code, out = run(files, capsys)
    assert code == 2
    entry = next(
        i for i in out["inputs"] if i["path"].endswith("candidate/card-2.json")
    )
    assert "stamp" in entry["error"]


# --- 6. Fake and real models -----------------------------------------------------


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_a_fake_model_card_is_no_measurement_of_b_or_c(files, capsys, side):
    sha = B_SHA if side == "baseline" else C_SHA
    path = write(files[f"{side}_cards"], "card-2.json", card(sha, 2, llm="fake"))
    code, out = run(files, capsys)
    assert code == 2
    entry = next(i for i in out["inputs"] if i["path"] == str(path))
    assert "fake" in entry["error"]


def test_the_circuit_check_must_be_a_fake_model_card(files, capsys):
    write(files["fake_card"].parent, files["fake_card"].name, card(C_SHA, 9))
    code, out = run(files, capsys)
    assert code == 2
    entry = next(i for i in out["inputs"] if i["path"] == str(files["fake_card"]))
    assert "fake" in entry["error"]


# --- 7. An approval in the documented format -----------------------------------------

_TOUCHED = [{"path": "scripts/bench/compare.py", "pattern": "scripts/bench/"}]


def _protected(files, approval):
    obj = {"sha": C_SHA, "touched": _TOUCHED, "approval": approval}
    write(files["protected"].parent, files["protected"].name, obj)


def test_an_approval_in_the_documented_format_is_read(files, capsys):
    _protected(
        files, {"sha": C_SHA, "by": "person-2", "paths": ["scripts/bench/compare.py"]}
    )
    code, out = run(files, capsys)
    assert code == 0, out["reasons"]
    assert "approved" in codes(out)


@pytest.mark.parametrize("producer", ["candidate", None, ""])
def test_an_approval_that_names_another_producer_is_still_refused(
    files, capsys, producer
):
    approval = {
        "sha": C_SHA,
        "by": "person-2",
        "paths": ["scripts/bench/compare.py"],
        "produced_by": producer,
    }
    _protected(files, approval)
    code, out = run(files, capsys)
    assert code == 1, out["reasons"]
    assert "protected_path" in codes(out)


def test_an_approval_that_is_not_an_object_is_refused(files, capsys):
    _protected(files, json.loads('["person-2"]'))
    code, out = run(files, capsys)
    assert code == 1
    assert "protected_path" in codes(out)


# --- 3. a grade the sandbox could not give (review of PR #634) ----------------------


def test_a_grade_the_sandbox_could_not_give_is_not_a_criteria_rate_of_zero(
    files, capsys
):
    # Every criterion missing because no image was there: not measured, so
    # never a regression read from 0, and never a keep.
    for path in sorted(files["candidate_cards"].glob("*.json")):
        found = json.loads(path.read_text("utf-8"))
        for spec in found["specs"]:
            spec["criteria"] = {
                **spec["criteria"],
                "passed": 0,
                "results": dict.fromkeys(spec["criteria"]["results"], "missing"),
                "unmeasured": "no grading image named",
            }
        found["totals"]["criteria_passed"], found["totals"]["criteria_rate"] = 0, 0.0
        path.write_text(json.dumps(found), "utf-8")
    code, out = run(files, capsys)
    assert code == 2
    assert out["decision"] == "unmeasured", out["reasons"]
    assert not any(
        r["outcome"] == "revert" and "criteria_rate" in r.get("detail", "")
        for r in out["reasons"]
    )
    run_c = decide.card_run(json.loads(path.read_text("utf-8")), 0)
    assert run_c["totals"]["criteria_rate"] is None
    assert all(s["criteria_rate"] is None for s in run_c["specs"].values())
