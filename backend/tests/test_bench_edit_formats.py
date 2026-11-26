"""The bench's edit format per model (issue #689; schema v7).

After Aider's edit-format benchmark (method only): the reference
specifications run once per edit format per model (``edit_formats.sh``),
each run's deliveries summed from the backend's own observation
(``edit_format`` on the run's evidence), and one row per model and format on
the card (``edit_format_card.py``): a format no delivery used well is
``unusable``, never a pass; a run that could not be graded, or ran under
another format, is ``unmeasured`` with its reason. ``compare.py`` shows the
rows without judging them.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import pytest
from bench_helpers import REPO, _card, _entry

sys.path.insert(0, str(REPO / "scripts" / "bench"))
import compare  # scripts/bench, by path
import edit_format_card
import scorecard

BENCH = REPO / "scripts" / "bench"
EDIT_FORMATS_SH = BENCH / "edit_formats.sh"
RUN_SH = (BENCH / "run.sh").read_text(encoding="utf-8")


def _seen(fmt="whole", well_formed=True, **counts):
    return {
        "edit_format": {
            "requested": fmt,
            "whole_files": counts.get("whole_files", 1),
            "edit_blocks": counts.get("edit_blocks", 0),
            "malformed_edit_blocks": counts.get("malformed", 0),
            "off_format": counts.get("off_format", 0),
            "tool_edits": 0,
            "tool_edit_errors": 0,
            "well_formed": well_formed,
        },
        "rejected_edits": counts.get("rejected", 0),
    }


def _arm_card(fmt, deliveries, results=None, stamp="20260101T000000Z"):
    entry = _entry(results=results)
    entry["edit_format"] = edit_format_card.spec_summary(deliveries)
    card = _card(entry, stamp=stamp)
    card["edit_format"] = fmt
    return card


# ---------------------------------------------------------------------------
# The names and the per-spec sums
# ---------------------------------------------------------------------------


def test_the_formats_are_the_backends():
    assert edit_format_card.known_formats() == ("auto", "whole", "search_replace")
    assert edit_format_card.parse_formats("each") == ["auto", "whole", "search_replace"]
    assert edit_format_card.parse_formats(" Whole ,whole") == ["whole"]
    for bad in ("udiff", "", "whole,diff"):
        with pytest.raises(ValueError, match="known: auto, whole, search_replace"):
            edit_format_card.parse_formats(bad)


def test_model_lists_are_checked():
    assert edit_format_card.parse_models("a-model,conn:3:b/c") == [
        "a-model",
        "conn:3:b/c",
    ]
    for bad in ("", "a,,b", "a b", "$(x)"):
        with pytest.raises(ValueError):
            edit_format_card.parse_models(bad)


def test_a_spec_sums_its_deliveries_and_counts_the_unobserved_apart():
    summary = edit_format_card.spec_summary(
        [
            _seen("search_replace", edit_blocks=2),
            _seen("search_replace", well_formed=False, malformed=1),
            _seen("search_replace", rejected=1),  # the daemon refused an edit
            {"edit_format": None, "rejected_edits": 0},  # another producer
        ]
    )
    assert summary["deliveries"] == 4 and summary["missing"] == 1
    assert summary["observed"] == 3 and summary["requested"] == {"search_replace": 3}
    assert summary["well_formed"] == 1
    assert (summary["malformed_edit_blocks"], summary["rejected_edits"]) == (1, 1)


# ---------------------------------------------------------------------------
# One row per model and format
# ---------------------------------------------------------------------------


def test_a_format_used_well_passes_or_fails_by_the_person_written_tests():
    good = edit_format_card.row("m", "whole", _arm_card("whole", [_seen()]))
    assert good["outcome"] == "pass" and good["reason"] is None
    assert (good["criteria_passed"], good["criteria_total"]) == (2, 2)
    assert good["well_formed_rate"] == 1.0 and good["served_models"] == ["fake-model"]
    failing = _arm_card("whole", [_seen()], {"REQ-1.1": "pass", "REQ-1.2": "fail"})
    assert edit_format_card.row("m", "whole", failing)["outcome"] == "fail"


def test_a_format_no_delivery_used_well_is_unusable_never_a_pass():
    deliveries = [
        _seen("search_replace", well_formed=False, malformed=1),
        _seen("search_replace", rejected=2),
    ]
    # The tests passed (the fake's files, say): still not a pass.
    r = edit_format_card.row(
        "m", "search_replace", _arm_card("search_replace", deliveries)
    )
    assert r["outcome"] == "unusable"
    assert "1 malformed" in r["reason"] and "2 edit(s) refused" in r["reason"]
    assert r["well_formed_rate"] == 0.0


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda c: c["specs"][0]["criteria"].update(unmeasured="no sandbox"),
            "not graded",
        ),
        (lambda c: c["specs"][0].update(status="error"), "not graded"),
        (
            lambda c: c["specs"][0].update(
                edit_format=edit_format_card.empty_summary()
            ),
            "no delivery",
        ),
        (lambda c: c.update(run={"status": "budget_exceeded"}), "budget"),
    ],
)
def test_a_run_that_was_not_measured_is_unmeasured_with_its_reason(change, reason):
    card = _arm_card("whole", [_seen()])
    change(card)
    r = edit_format_card.row("m", "whole", card)
    assert r["outcome"] == "unmeasured" and reason in r["reason"]


def test_deliveries_run_under_another_format_are_unmeasured():
    card = _arm_card("whole", [_seen("auto")])
    r = edit_format_card.row("m", "whole", card)
    assert r["outcome"] == "unmeasured" and "another format" in r["reason"]


def test_runs_that_are_not_one_comparison_are_refused():
    a = _arm_card("whole", [_seen()])
    b = _arm_card("search_replace", [_seen("search_replace")])
    assert (
        edit_format_card.arm_problems([("m", "whole", a), ("m", "search_replace", b)])
        == []
    )
    other = {**b, "commit": "fff"}
    assert (
        "another commit"
        in edit_format_card.arm_problems(
            [("m", "whole", a), ("m", "search_replace", other)]
        )[0]
    )
    assert (
        "run under 'whole'"
        in edit_format_card.arm_problems([("m", "search_replace", a)])[0]
    )
    assert (
        "given twice"
        in edit_format_card.arm_problems([("m", "whole", a), ("m", "whole", a)])[-1]
    )


# ---------------------------------------------------------------------------
# The card, the schema, the Markdown and compare.py
# ---------------------------------------------------------------------------


def _write_arms(tmp_path):
    cards = {
        "whole": _arm_card("whole", [_seen()], stamp="20260101T000000Z"),
        "search_replace": _arm_card(
            "search_replace",
            [_seen("search_replace", well_formed=False, malformed=1)],
            stamp="20260101T000100Z",
        ),
    }
    flags = []
    for fmt, card in cards.items():
        path = tmp_path / f"{fmt}.json"
        path.write_text(json.dumps(card))
        flags += ["--arm", "fake", fmt, str(path)]
    return flags


def test_write_puts_one_row_per_model_and_format_on_the_first_card(tmp_path):
    out = tmp_path / "out"
    assert (
        edit_format_card.main(["write", *_write_arms(tmp_path), "--out-dir", str(out)])
        == 0
    )
    card = json.loads((out / "20260101T000000Z.json").read_text())
    assert card["schema_version"] == 7 and scorecard.validate(card) == []
    rows = {r["format"]: r for r in card["edit_formats"]["rows"]}
    assert rows["whole"]["outcome"] == "pass"
    assert rows["search_replace"]["outcome"] == "unusable"
    text = (out / "20260101T000000Z.md").read_text()
    assert (
        "## Edit format per model" in text
        and "| fake | search_replace | unusable" in text
    )
    assert "Edit format asked for: `whole`" in text


def test_write_refuses_arms_that_do_not_match(tmp_path):
    card = _arm_card("whole", [_seen()])
    path = tmp_path / "c.json"
    path.write_text(json.dumps(card))
    out = tmp_path / "x"
    assert (
        edit_format_card.main(
            ["write", "--arm", "m", "search_replace", str(path), "--out-dir", str(out)]
        )
        == 2
    )
    assert (
        edit_format_card.main(
            ["write", "--arm", "m", "udiff", str(path), "--out-dir", str(out)]
        )
        == 2
    )
    assert not out.exists()


def test_the_schema_checks_the_fields_and_still_reads_a_v6_card():
    card = _arm_card("whole", [_seen()])
    card["edit_formats"] = edit_format_card.block([("m", "whole", card)])
    assert scorecard.validate(card) == []
    bad = json.loads(json.dumps(card))
    bad["edit_formats"]["rows"][0].update(outcome="pass", well_formed=0)
    assert any("cannot pass" in p for p in scorecard.validate(bad))
    bad = json.loads(json.dumps(card))
    bad["edit_formats"]["rows"][0].update(outcome="unusable", reason=None)
    assert any("needs its reason" in p for p in scorecard.validate(bad))
    bad = json.loads(json.dumps(card))
    bad["edit_format"] = "udiff"
    bad["specs"][0]["edit_format"]["observed"] = -1
    found = scorecard.validate(bad)
    assert any("edit_format must be one of" in p for p in found)
    assert any("specs[0].edit_format.observed must be a count" in p for p in found)
    old = json.loads(json.dumps(card))
    old["schema_version"] = 6
    assert any("needs schema v7" in p for p in scorecard.validate(old))
    del old["edit_format"], old["edit_formats"], old["specs"][0]["edit_format"]
    assert scorecard.validate(old) == []


def test_compare_shows_each_row_before_and_after_without_judging(tmp_path, capsys):
    out = tmp_path / "out"
    edit_format_card.main(["write", *_write_arms(tmp_path), "--out-dir", str(out)])
    capsys.readouterr()
    current = json.loads((out / "20260101T000000Z.json").read_text())
    previous = json.loads(json.dumps(current))
    previous["stamp"] = "20251231T000000Z"
    previous["edit_formats"]["rows"][1].update(outcome="fail", reason=None)
    result = compare.compare(previous, current, compare.DEFAULTS)
    text = compare.render(previous, current, result)
    assert "### Edit format per model" in text
    assert "| fake | search_replace | fail 2/2 | unusable 2/2 |" in text
    assert "Edit format: whole → whole." in text
    assert result["regressions"] == []
    # With no previous card, the table alone.
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy(out / "20260101T000000Z.json", alone / "20260101T000000Z.json")
    assert (
        compare.main([str(alone / "20260101T000000Z.json"), "--dir", str(alone)]) == 0
    )
    assert "### Edit format per model" in capsys.readouterr().out


def test_scorecard_write_records_the_runs_format(tmp_path):
    entry = tmp_path / "e.json"
    entry.write_text(json.dumps(_entry()))
    out = tmp_path / "out"
    args = ["write", "--out-dir", str(out), "--stamp", "20260101T000000Z"]
    assert scorecard.main([*args, "--edit-format", "search_replace", str(entry)]) == 0
    assert (
        json.loads((out / "20260101T000000Z.json").read_text())["edit_format"]
        == "search_replace"
    )
    assert scorecard.main([*args, "--edit-format", "each", str(entry)]) == 2
    assert scorecard.main([*args, "--edit-format", "udiff", str(entry)]) == 2


def test_collect_sums_each_deliverys_observation_beside_the_board(
    tmp_path, monkeypatch
):
    board = {"columns": {"done": [{"id": 11, "status": "done", "comments": []}]}}

    def fake_get(api, path):
        if path.endswith("/board"):
            return board
        if path.startswith("/v1/agent_runs"):
            return {"agent_runs": [{"model": "fake-model"}]}
        return {"coverage": {"verified": 1}}

    monkeypatch.setattr(scorecard, "_get", fake_get)
    monkeypatch.setattr(
        scorecard,
        "reported_tokens",
        lambda dsn, company: (
            {"reported": 10, "turns_reported": 1, "turns_unreported": 0},
            {},
            {},
        ),
    )
    monkeypatch.setattr(scorecard, "difficulty_estimates", lambda dsn, company: {})
    monkeypatch.setattr(scorecard, "prompt_sections", lambda dsn, company: {})
    monkeypatch.setattr(
        scorecard,
        "edit_formats",
        lambda dsn, company: [
            _seen("search_replace", edit_blocks=1),
            {"edit_format": None, "rejected_edits": 0},
        ],
    )
    grade = tmp_path / "grade.json"
    grade.write_text(
        json.dumps({"passed": 1, "total": 1, "results": {"REQ-1.1": "pass"}})
    )
    args = argparse.Namespace(
        api="http://bench",
        name="03-x",
        status="completed",
        company=1,
        sprint=2,
        spec_id=3,
        grade=str(grade),
        wall=4.0,
        pg=["h", "5432", "u", "p", "db"],
        error="",
    )
    entry = scorecard.collect(args)
    assert entry["status"] == "completed", entry["error"]
    seen = entry["edit_format"]
    assert (seen["deliveries"], seen["observed"], seen["missing"]) == (2, 1, 1)
    assert seen["edit_blocks"] == 1 and seen["requested"] == {"search_replace": 1}


# ---------------------------------------------------------------------------
# run.sh and edit_formats.sh
# ---------------------------------------------------------------------------


def test_run_sh_passes_the_format_to_the_backend_and_the_card():
    # The backend gets exactly the bench's setting (empty by default, so an
    # ambient EDIT_FORMAT_BY_MODEL cannot change a nightly run unnoticed).
    assert "EDIT_FORMAT=${BENCH_EDIT_FORMAT:-}" in RUN_SH
    assert 'EDIT_FORMAT_BY_MODEL="$EDIT_FORMAT_MAP"' in RUN_SH
    assert 'edit_format_card.py" parse --formats' in RUN_SH
    assert '--edit-format "$EDIT_FORMAT"' in RUN_SH


def test_edit_formats_sh_refuses_a_bad_request_before_starting_anything(tmp_path):
    env = {**os.environ, "BENCH_PYTHON": sys.executable}
    for request, models in (("udiff", ""), ("", ""), ("whole", "a b")):
        done = subprocess.run(
            ["bash", str(EDIT_FORMATS_SH), "--out", str(tmp_path / "out"), request],
            check=False,
            capture_output=True,
            text=True,
            env={**env, "BENCH_MODELS": models},
            timeout=60,
        )
        assert done.returncode == 2, done.stdout + done.stderr
    assert not (tmp_path / "out").exists()


STUB_RUN = """#!/usr/bin/env bash
# A stand-in for run.sh: one card per call, as the fake would score it.
OUT=""
while [ $# -gt 0 ]; do case "$1" in --out) OUT=$2; shift 2 ;; *) shift ;; esac; done
echo "$BENCH_EDIT_FORMAT ${AGENT_MODEL:-default}" >> "$STUB_LOG"
"$BENCH_PYTHON" "$(dirname "$0")/stub_card.py" "$OUT"
"""

STUB_CARD = """import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import edit_format_card
import scorecard
fmt = os.environ["BENCH_EDIT_FORMAT"]
# A model that cannot write SEARCH/REPLACE: every edit block malformed.
seen = {"requested": fmt, "whole_files": 1, "edit_blocks": 0,
        "malformed_edit_blocks": int(fmt == "search_replace"), "off_format": 0,
        "tool_edits": 0, "tool_edit_errors": 0, "well_formed": fmt != "search_replace"}
entry = scorecard.summarize_spec(
    name="01-a", status="completed", items=[{"id": 1, "status": "done", "comments": []}],
    runs_per_task={1: 1}, graded={"passed": 1, "total": 1, "results": {"REQ-1.1": "pass"}},
    factory_verified=1, wall_clock_seconds=1.0,
    tokens={"reported": 10, "turns_reported": 1, "turns_unreported": 0},
    models={"Developer": [os.environ.get("AGENT_MODEL", "fake")]},
    edit_format=edit_format_card.spec_summary([{"edit_format": seen, "rejected_edits": 0}]),
)
stamp = time.strftime("%Y%m%dT%H%M%SZ") + str(time.perf_counter_ns())
card = scorecard.build_scorecard([entry], stamp=stamp, commit="abc", llm="fake", edit_format=fmt)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
(out / (stamp + ".json")).write_text(json.dumps(card))
"""


def test_edit_formats_sh_runs_each_format_per_model_and_writes_the_rows(tmp_path):
    # The scripts in a copy of the repository's layout, with run.sh replaced
    # by a stand-in that scores like the fake: no stack is started.
    root = tmp_path / "repo"
    bench = root / "scripts" / "bench"
    shutil.copytree(BENCH, bench)
    models = root / "backend" / "app" / "services" / "models"
    models.mkdir(parents=True)
    shutil.copy(REPO / "backend/app/services/models/edit_format.py", models)
    (bench / "run.sh").write_text(STUB_RUN)
    (bench / "stub_card.py").write_text(STUB_CARD)
    log = tmp_path / "runs.log"
    done = subprocess.run(
        [
            "bash",
            str(bench / "edit_formats.sh"),
            "--out",
            str(tmp_path / "out"),
            "whole,search_replace",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            # The scorecard imports the backend's own readers (services.tasks):
            # pytest's pythonpath setting does not reach this subprocess.
            "PYTHONPATH": os.pathsep.join(
                p
                for p in (str(REPO / "backend" / "app"), os.environ.get("PYTHONPATH"))
                if p
            ),
            "BENCH_PYTHON": sys.executable,
            "STUB_LOG": str(log),
            "BENCH_MODELS": "model-a,model-b",
        },
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert log.read_text().split("\n")[:4] == [
        "whole model-a",
        "search_replace model-a",
        "whole model-b",
        "search_replace model-b",
    ]
    (card_path,) = (tmp_path / "out").glob("*.json")
    card = json.loads(card_path.read_text())
    assert scorecard.validate(card) == []
    outcome = {
        (r["model"], r["format"]): r["outcome"] for r in card["edit_formats"]["rows"]
    }
    assert outcome == {
        ("model-a", "whole"): "pass",
        ("model-a", "search_replace"): "unusable",
        ("model-b", "whole"): "pass",
        ("model-b", "search_replace"): "unusable",
    }
