"""SWE-bench Lite through the factory (issue #687, first slice).

No network and no stack: a tiny upstream repository is made in ``tmp_path``
with a bug, its fix (the instance's reference patch) and a test patch, and
an instance row is written for it and pinned by its sha256. The factory is
faked by what it would leave in the project folder: the reference patch
(the fake producer's delivery), nothing, a change that breaks a passing
test, or a planted ``conftest.py``. The scorer runs the instance's tests
itself (``scripts/bench/swebench_grade.py``) on the test run's interpreter,
used as it is (``venv: False``), so nothing is installed.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import compare  # scripts/bench/compare.py, by path
import junit_xml  # scripts/bench/junit_xml.py, by path
import scorecard  # scripts/bench/scorecard.py, by path
import seed  # scripts/bench/seed.py, by path
import swebench  # scripts/bench/swebench.py, by path
import swebench_card  # scripts/bench/swebench_card.py, by path
import swebench_grade  # scripts/bench/swebench_grade.py, by path
from bench_helpers import BENCH, _card, _entry

RECIPE = {"python": sys.executable, "venv": False, "install": None}
IID = "acme__calc-1"
CALC = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a + b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _git(cwd, *args):
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": "/usr/bin:/bin",
    }
    env |= {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _diff(repo, path, text):
    """The diff that turns *path* into *text*, left unapplied."""
    before = (repo / path).read_text() if (repo / path).exists() else None
    (repo / path).write_text(text)
    _git(repo, "add", "-N", path)
    out = _git(repo, "diff", "--", path)
    _git(repo, "reset", "-q", "--", path)
    if before is None:
        (repo / path).unlink()
    else:
        (repo / path).write_text(before)
    return out


@pytest.fixture
def upstream(tmp_path):
    """(the repository URL template, the instance row) for acme/calc."""
    repo = tmp_path / "upstream" / "acme" / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(CALC)
    (repo / "tests" / "test_calc.py").write_text(TESTS)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    tests_after = (
        TESTS
        + "\n\ndef test_sub():\n    from calc import sub\n\n    assert sub(5, 3) == 2\n"
    )
    row = {
        "instance_id": IID,
        "repo": "acme/calc",
        "base_commit": base,
        "version": "1.0",
        "problem_statement": "sub() adds instead of subtracting.\n\nsub(5, 3) gives 8.",
        "patch": _diff(repo, "calc.py", FIXED),
        "test_patch": _diff(repo, "tests/test_calc.py", tests_after),
        "FAIL_TO_PASS": json.dumps(["tests/test_calc.py::test_sub"]),
        "PASS_TO_PASS": json.dumps(["tests/test_calc.py::test_add"]),
        "environment_setup_commit": base,
        "hints_text": "",
        "created_at": "2026-01-01",
    }
    return f"file://{tmp_path / 'upstream'}/{{repo}}", row


def _prepare(tmp_path, upstream, row=None, **kw):
    url, good = upstream
    row = row or good
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps(row) + "\n")
    pins = (swebench.Pin(IID, 0, swebench.instance_sha256(good)),)
    return swebench.prepare(
        tmp_path / "w", rows_file=rows, repo_url=url, pins=pins, **kw
    )


def _project(tmp_path, spec, files=None):
    """The project as the factory leaves it: the seed committed, *files* on top."""
    project = tmp_path / "project"
    seed.prepare(spec, project)
    for relative, text in (files or {}).items():
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_text(text)
    return project


def _fake_delivery(spec):
    root = spec / "fake" / "files"
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in root.rglob("*")
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# The dataset: pinned ids, never its content
# ---------------------------------------------------------------------------


def test_five_instances_are_pinned_at_a_revision_from_small_repositories():
    assert re.fullmatch(r"[0-9a-f]{40}", swebench.REVISION)
    assert swebench.DATASET == "princeton-nlp/SWE-bench_Lite"
    ids = [p.instance_id for p in swebench.PINS]
    assert len(ids) == len(set(ids)) == 5
    assert {i.split("__")[0] for i in ids} <= {"psf", "pallets", "pytest-dev"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", p.sha256) for p in swebench.PINS)
    # Each pinned pair has an environment recipe, so none is unmeasured for want of one.
    versions = {"pallets": "2.3", "pytest-dev": "8.0", "psf": "2.10"}
    for iid in ids:
        owner = iid.split("__")[0]
        repo = {
            "psf": "psf/requests",
            "pallets": "pallets/flask",
            "pytest-dev": "pytest-dev/pytest",
        }[owner]
        assert (repo, versions[owner]) in swebench_grade.ENVIRONMENTS
    assert "MIT" in swebench.LICENSE and "upstream" in swebench.LICENSE


def test_nothing_of_the_dataset_is_committed():
    committed = [
        p
        for p in BENCH.rglob("*")
        if p.is_file() and "swebench" in p.as_posix().lower()
    ]
    for path in committed:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "diff --git" not in text and "FAIL_TO_PASS" not in text, path


def test_a_row_is_taken_only_when_it_hashes_to_its_pin(upstream):
    _url, row = upstream
    pin = swebench.Pin(IID, 0, swebench.instance_sha256(row))
    instance = swebench.check_row(row, pin)
    assert instance["FAIL_TO_PASS"] == ["tests/test_calc.py::test_sub"]
    changed = {**row, "test_patch": row["test_patch"] + "\n"}
    with pytest.raises(swebench.DatasetError, match="not the pinned"):
        swebench.check_row(changed, pin)
    with pytest.raises(swebench.DatasetError, match="not acme__calc-1"):
        swebench.check_row({**row, "instance_id": "acme__calc-2"}, pin)
    with pytest.raises(swebench.DatasetError, match="lacks"):
        swebench.check_row({k: v for k, v in row.items() if k != "patch"}, pin)


def test_a_row_the_server_truncated_is_refused(monkeypatch, upstream):
    _url, row = upstream
    pin = swebench.Pin(IID, 7, swebench.instance_sha256(row))
    asked = []

    def answer(url):
        asked.append(url)
        return json.dumps(
            {"rows": [{"row": row, "truncated_cells": ["patch"]}]}
        ).encode()

    monkeypatch.setattr(swebench, "_download", answer)
    with pytest.raises(swebench.DatasetError, match="truncated"):
        swebench.fetch_row(pin)
    assert "offset=7&length=1" in asked[0]
    monkeypatch.setattr(
        swebench, "_download", lambda url: json.dumps({"rows": [{"row": row}]}).encode()
    )
    assert swebench.fetch_row(pin) == row


def test_only_pinned_instances_are_selected():
    pins = (swebench.Pin("a__b-1", 0, "0" * 64), swebench.Pin("a__b-2", 1, "1" * 64))
    assert [p.instance_id for p in swebench.resolve_ids("default", pins)] == [
        "a__b-1",
        "a__b-2",
    ]
    assert [p.instance_id for p in swebench.resolve_ids("a__b-2, a__b-2", pins)] == [
        "a__b-2"
    ]
    with pytest.raises(swebench.DatasetError, match="not a pinned instance"):
        swebench.resolve_ids("a__b-3", pins)


# ---------------------------------------------------------------------------
# An instance as a specification
# ---------------------------------------------------------------------------


def test_an_instance_is_a_seeded_specification_that_hides_its_tests(tmp_path, upstream):
    from services.planning import spec_check

    index = _prepare(tmp_path, upstream, fake=True)
    (row,) = index["instances"]
    spec = tmp_path / "w" / "specs" / row["spec"]
    assert row["spec"] == "swebench-acme-calc-1" and index["subset"] == [IID]
    assert index["dataset"]["revision"] == swebench.REVISION
    # The seed is the repository at its base commit, and the issue's full text.
    assert (spec / "seed" / "calc.py").read_text() == CALC
    assert "sub(5, 3) gives 8." in (spec / "seed" / swebench.ISSUE_FILE).read_text()
    body = json.loads((spec / "spec.json").read_text())
    normalized = spec_check.normalize(body)
    assert spec_check.check(normalized)["ok"], spec_check.check(normalized)
    assert scorecard.criteria_of(body) == ["REQ-1.1"]
    shown = json.dumps(body)
    assert (
        swebench.ISSUE_FILE in shown and "sub() adds instead of subtracting." in shown
    )
    assert "test_sub" not in shown and "a - b" not in shown
    # The hidden tests beside it; the reference patch never there.
    hidden = json.loads((spec / "acceptance" / "instance.json").read_text())
    assert hidden["FAIL_TO_PASS"] == ["tests/test_calc.py::test_sub"]
    assert "patch" not in hidden and "problem_statement" not in hidden
    # The fake's delivery is the reference patch, applied at run time.
    assert _fake_delivery(spec)["calc.py"] == FIXED
    plan = json.loads((spec / "fake" / "plan.json").read_text())
    assert plan["tasks"][0]["files"] == ["calc.py"]
    assert plan["acceptance"] == {"REQ-1": swebench.QA_FILE}
    assert json.loads((tmp_path / "w" / "instances.json").read_text()) == index


def test_a_row_off_its_pin_writes_nothing(tmp_path, upstream):
    _url, row = upstream
    with pytest.raises(swebench.DatasetError, match="not the pinned"):
        _prepare(tmp_path, upstream, row={**row, "problem_statement": "changed"})
    assert not (tmp_path / "w").exists()


# ---------------------------------------------------------------------------
# The grade: the instance's tests, run by the scorer
# ---------------------------------------------------------------------------


def _grade(tmp_path, upstream, files=None, recipe=RECIPE):
    _prepare(tmp_path, upstream, fake=True)
    spec = tmp_path / "w" / "specs" / "swebench-acme-calc-1"
    delivery = _fake_delivery(spec) if files == "fake" else files
    project = _project(tmp_path, spec, delivery)
    return swebench_grade.grade(spec, project, tmp_path / "work", recipe=recipe)


def test_the_reference_delivery_resolves_the_instance(tmp_path, upstream):
    started = time.monotonic()
    graded = _grade(tmp_path, upstream, "fake")
    took = time.monotonic() - started
    assert graded["results"] == {"REQ-1.1": "pass"} and graded["passed"] == 1
    detail = graded["swebench"]
    assert detail["base"]["fail_to_pass"]["passed"] == 0
    assert detail["base"]["pass_to_pass"] == {"passed": 1, "total": 1, "not_passed": []}
    assert detail["fail_to_pass"]["passed"] == detail["fail_to_pass"]["total"] == 1
    assert "calc.py" in detail["changed_files"] and graded["isolation"] == "host_venv"
    assert "unmeasured" not in graded
    assert took < 60  # one instance end to end with the fake: seconds, not minutes
    assert not any((tmp_path / "work").rglob("delivery"))  # the scorer's copies go


def test_no_delivery_fails_and_the_project_is_left_as_it_was(tmp_path, upstream):
    graded = _grade(tmp_path, upstream, {})
    assert graded["results"] == {"REQ-1.1": "fail"}
    assert graded["swebench"]["fail_to_pass"]["not_passed"] == [
        "tests/test_calc.py::test_sub"
    ]
    # The test patch went into the scorer's copy, never into the project.
    assert (
        "test_sub" not in (tmp_path / "project" / "tests" / "test_calc.py").read_text()
    )


def test_a_delivery_that_breaks_a_passing_test_fails(tmp_path, upstream):
    broken = FIXED.replace(
        "def add(a, b):\n    return a + b", "def add(a, b):\n    return 0"
    )
    graded = _grade(tmp_path, upstream, {"calc.py": broken})
    assert graded["results"] == {"REQ-1.1": "fail"}
    assert graded["swebench"]["pass_to_pass"]["not_passed"] == [
        "tests/test_calc.py::test_add"
    ]


def test_a_delivery_that_rewrites_the_tests_is_graded_with_the_instances(
    tmp_path, upstream
):
    cheat = "def test_sub():\n    pass\n\n\ndef test_add():\n    pass\n"
    graded = _grade(tmp_path, upstream, {"tests/test_calc.py": cheat})
    assert graded["results"] == {"REQ-1.1": "fail"}


def test_a_delivery_that_plants_what_runs_in_the_grade_fails(tmp_path, upstream):
    planted = {"calc.py": CALC, "tests/conftest.py": "import pytest\n"}
    graded = _grade(tmp_path, upstream, planted)
    assert graded["results"] == {"REQ-1.1": "fail"}
    assert "tests/conftest.py" in graded["swebench"]["reason"]


def test_an_environment_that_does_not_reproduce_the_instance_is_unmeasured(
    tmp_path, upstream
):
    url, row = upstream
    # The test patch's new test already passes on the base: not reproduced.
    row = {
        **row,
        "FAIL_TO_PASS": json.dumps(["tests/test_calc.py::test_add"]),
        "PASS_TO_PASS": "[]",
    }
    graded = _grade(tmp_path, (url, row), "fake")
    assert graded["results"] == {"REQ-1.1": "missing"} and graded["passed"] == 0
    assert "does not reproduce" in graded["unmeasured"]


def test_an_environment_that_cannot_be_built_is_unmeasured(
    tmp_path, upstream, monkeypatch
):
    monkeypatch.setattr(swebench_grade.shutil, "which", lambda name: None)
    recipe = {"python": str(tmp_path / "no-python"), "packages": [], "install": None}
    graded = _grade(tmp_path, upstream, "fake", recipe=recipe)
    assert graded["results"] == {"REQ-1.1": "missing"}
    assert "environment failed" in graded["unmeasured"]


def test_an_instance_without_a_recipe_is_unmeasured(tmp_path, upstream):
    _prepare(tmp_path, upstream)
    spec = tmp_path / "w" / "specs" / "swebench-acme-calc-1"
    graded = swebench_grade.grade(spec, _project(tmp_path, spec), tmp_path / "work")
    assert "no environment recipe for acme/calc 1.0" in graded["unmeasured"]


def test_test_ids_are_found_in_the_junit_report_as_the_harness_logged_them():
    assert swebench_grade.junit_key(
        "testing/python/test_x.py::TestY::test_z[a::b]"
    ) == (
        "testing.python.test_x.TestY",
        "test_z[a::b]",
    )
    report = (
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_cli" name="test_locate[app(&quot;a&quot;, 1)]"/>'
        '<testcase classname="tests.test_cli" name="test_locate[app(&quot;b&quot;, 2)]"/>'
        '<testcase classname="tests.test_cli" name="test_x"><skipped type="pytest.xfail"/></testcase>'
        '<testcase classname="tests.test_cli" name="test_s"><skipped type="pytest.skip"/></testcase>'
        '<testcase classname="tests.test_cli.T" name="test_f"><failure/></testcase>'
        "</testsuite></testsuites>"
    )
    found = swebench_grade.results(
        report,
        [
            'tests/test_cli.py::test_locate[app("a",',  # cut at the space, as logged
            "tests/test_cli.py::test_x",
            "tests/test_cli.py::test_s",
            "tests/test_cli.py::T::test_f",
            "tests/test_cli.py::test_gone",
        ],
    )
    assert list(found.values()) == ["pass", "pass", "fail", "fail", "missing"]
    # The reference grade still reads an expected failure as a skip.
    assert junit_xml.outcomes(report)[2] == ("test_x", junit_xml.SKIP)


# ---------------------------------------------------------------------------
# The card's swebench block (schema v6) and compare.py
# ---------------------------------------------------------------------------


def _index(*numbers):
    return {
        "dataset": swebench.dataset_record(
            "rows.jsonl",
            [swebench.Pin(f"acme__calc-{n}", n, f"{n:064x}") for n in numbers],
        ),
        "subset": [f"acme__calc-{n}" for n in numbers],
        "instances": [
            {
                "instance_id": f"acme__calc-{n}",
                "repo": "acme/calc",
                "spec": f"swebench-acme-calc-{n}",
            }
            for n in numbers
        ],
    }


def _swe_entry(n, result="pass", unmeasured=None, tokens=100):
    entry = _entry(
        f"swebench-acme-calc-{n}", results={"REQ-1.1": result}, tokens=tokens
    )
    entry["criteria"]["unmeasured"] = unmeasured
    groups = {
        "fail_to_pass": {"passed": int(result == "pass"), "total": 1},
        "pass_to_pass": {"passed": 3, "total": 3},
    }
    entry["swebench"] = {
        **({} if unmeasured else groups),
        "reason": unmeasured,
        "seconds": 4.2,
    }
    return entry


def _write(tmp_path, entries, index, *extra):
    paths = []
    for entry in entries:
        path = tmp_path / f"{entry['name']}.json"
        path.write_text(json.dumps(entry))
        paths.append(str(path))
    (tmp_path / "instances.json").write_text(json.dumps(index))
    out = tmp_path / "cards"
    status = scorecard.main(
        [
            "write",
            "--out-dir",
            str(out),
            "--stamp",
            "20260101T000000Z",
            "--swebench",
            str(tmp_path / "instances.json"),
            *extra,
            *paths,
        ]
    )
    return status, out


def test_swebench_results_are_a_block_apart_from_the_specs(tmp_path):
    entries = [
        _swe_entry(1),
        _swe_entry(2, "fail", tokens=50),
        _swe_entry(3, "missing", "no Python 3.9"),
    ]
    status, out = _write(tmp_path, [*entries, _entry("01-a")], _index(1, 2, 3))
    card = json.loads((out / "20260101T000000Z.json").read_text())
    assert (
        status == 0 and card["schema_version"] == 6 and scorecard.validate(card) == []
    )
    assert [s["name"] for s in card["specs"]] == ["01-a"]
    block = card["swebench"]
    assert (block["graded"], block["measured"], block["resolved"]) == (3, 2, 1)
    assert (block["failed"], block["unmeasured"], block["resolved_rate"]) == (1, 1, 0.5)
    assert block["instances"][2]["reason"] == "no Python 3.9"
    assert block["instances"][0]["fail_to_pass"] == {"passed": 1, "total": 1}
    assert block["dataset"]["revision"] == swebench.REVISION
    text = (out / "20260101T000000Z.md").read_text()
    assert (
        "## SWE-bench Lite" in text
        and "Resolved 50%: 1 of 2 instances measured" in text
    )
    assert "| acme__calc-2 | fail | 0/1 | 3/3 | completed | 50 | 12 s |" in text


def test_with_nothing_measured_the_rate_is_null_and_skips_are_listed(tmp_path):
    status, out = _write(
        tmp_path,
        [_swe_entry(1, "missing", "no recipe")],
        _index(1, 2),
        "--budget",
        "100",
        "--cut",
        "--skipped",
        "swebench-acme-calc-2",
    )
    block = json.loads((out / "20260101T000000Z.json").read_text())["swebench"]
    assert (
        status == 0
        and block["resolved_rate"] is None
        and block["skipped"] == ["acme__calc-2"]
    )


def _swe_card():
    card = _card(_entry("01-a"))
    card["swebench"] = swebench_card.block(
        [_swe_entry(1), _swe_entry(2, "fail")], _index(1, 2)
    )
    return card


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c.update(schema_version=5), "schema v6"),
        (lambda c: c["swebench"].update(resolved=2), "swebench.resolved"),
        (lambda c: c["swebench"].update(resolved_rate=1.0), "resolved_rate"),
        (lambda c: c["swebench"]["instances"][0].update(outcome="maybe"), "outcome"),
        (lambda c: c["swebench"]["dataset"].update(revision="main"), "revision"),
        (
            lambda c: c["swebench"]["instances"][1].update(outcome="unmeasured"),
            "says why",
        ),
        (
            lambda c: c["swebench"]["instances"][1].update(outcome="pass"),
            "resolved instance",
        ),
        (lambda c: c["specs"].append(_swe_entry(1)), "also a reference specification"),
    ],
)
def test_the_schema_checks_the_swebench_block(change, problem):
    card = _swe_card()
    assert scorecard.validate(card) == []
    change(card)
    assert any(problem in p for p in scorecard.validate(card)), scorecard.validate(card)


def test_a_v5_card_is_still_read_without_the_block():
    card = _card()
    card["schema_version"] = 5
    assert scorecard.validate(card) == []


def test_compare_prints_swebench_before_and_after_without_judging_it(tmp_path, capsys):
    before, after = _swe_card(), _swe_card()
    before["stamp"], after["stamp"] = "20260101T000000Z", "20260102T000000Z"
    after["swebench"] = swebench_card.block(
        [_swe_entry(1, "fail"), _swe_entry(2, "fail")], _index(1, 2)
    )
    for card in (before, after):
        (tmp_path / f"{card['stamp']}.json").write_text(json.dumps(card))
    assert (
        compare.main([str(tmp_path / "20260102T000000Z.json"), "--dir", str(tmp_path)])
        == 0
    )
    printed = capsys.readouterr().out
    assert "### SWE-bench Lite" in printed and "Resolved 50% → 0%" in printed
    assert "| acme__calc-1 | pass | fail |" in printed
    (tmp_path / "20260101T000000Z.json").unlink()
    assert (
        compare.main([str(tmp_path / "20260102T000000Z.json"), "--dir", str(tmp_path)])
        == 0
    )
    assert "### SWE-bench Lite" in capsys.readouterr().out
