"""The bench's grade (issue #316): pytest's JUnit report read per criterion.

A report with a document type is refused before anything is expanded (issue
#522); the fake producer's files pass, an empty project fails, a wrong answer
fails only its criteria; the produced project cannot stand in for the grader
or forge its report, and a report pytest did not finish is not read. These
run the in-process path (``in_process=True``, local development); the
sandbox's grade is ``test_sandbox_grading.py``.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import gzip
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import fake_llm  # scripts/bench/fake_llm.py, by path
import humaneval  # scripts/bench/humaneval.py, by path
import scorecard  # scripts/bench/scorecard.py, by path
import seed  # scripts/bench/seed.py, by path
from bench_helpers import BENCH

JUNIT = """<testsuites><testsuite>
<testcase classname="t" name="test_req_1_1_a"/>
<testcase classname="t" name="test_req_1_1_b[x]"/>
<testcase classname="t" name="test_req_1_2_a"><failure message="no"/></testcase>
<testcase classname="t" name="test_req_2_1_a"><skipped/></testcase>
<testcase classname="t" name="test_req_2_2_a"><error message="boom"/></testcase>
<testcase classname="t" name="test_helper"/>
</testsuite></testsuites>"""


def test_the_junit_report_is_read_per_criterion():
    criteria = ["REQ-1.1", "REQ-1.2", "REQ-2.1", "REQ-2.2", "REQ-3.1"]
    assert scorecard.criteria_from_junit(JUNIT, criteria) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
        "REQ-2.1": "fail",  # a skipped test decides nothing in the criterion's favour
        "REQ-2.2": "fail",
        "REQ-3.1": "missing",
    }


@pytest.mark.parametrize("text", ["", "not xml <", "<testsuites/>"])
def test_an_unreadable_or_empty_report_leaves_every_criterion_missing(text):
    assert scorecard.criteria_from_junit(text, ["REQ-1.1"]) == {"REQ-1.1": "missing"}


# The report is written inside a project the agents produced, so a conftest
# there can rewrite it (issue #522, Semgrep's use-defused-xml): a document type
# is refused before any entity is expanded or any file is read.
_BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<testsuites><testsuite><testcase name="test_req_1_1_&lol3;"/></testsuite></testsuites>"""


def _external(path: Path) -> str:
    return f"""<?xml version="1.0"?>
<!DOCTYPE t [<!ENTITY secret SYSTEM "file://{path}">]>
<testsuites><testsuite><testcase name="test_req_1_1_a">&secret;</testcase>
</testsuite></testsuites>"""


def test_a_report_with_a_document_type_is_refused_before_expansion(tmp_path):
    import junit_xml

    secret = tmp_path / "secret.txt"
    secret.write_text("never read")
    for text in (
        _BOMB,
        _external(secret),
        '<!DOCTYPE t SYSTEM "http://example.invalid/t.dtd"><testsuites/>',
    ):
        with pytest.raises(junit_xml.JUnitError):
            junit_xml.testcases(text)
        assert scorecard.criteria_from_junit(text, ["REQ-1.1"]) == {
            "REQ-1.1": "missing"
        }
    # An entity no DTD declares is malformed XML, not an empty name.
    with pytest.raises(junit_xml.JUnitError):
        junit_xml.testcases('<testcase name="test_req_1_1_&x;"/>')


def test_the_junit_reader_looks_only_at_direct_children_of_a_case():
    import junit_xml

    text = """<testsuites><testsuite>
<testcase name="test_req_1_1_a"><system-out><failure/></system-out></testcase>
<testcase name="test_req_1_2_a"><failure/></testcase>
</testsuite></testsuites>"""
    assert junit_xml.testcases(text) == [
        ("test_req_1_1_a", True),
        ("test_req_1_2_a", False),
    ]


def test_grading_the_fake_producers_files_passes_every_criterion():
    spec_dir = BENCH / "specs" / "02-lib-roman"
    graded = scorecard.grade(
        spec_dir, spec_dir / "fake" / "files", sys.executable, in_process=True
    )
    assert (graded["passed"], graded["total"]) == (5, 5), graded["output_tail"]


def test_grading_an_empty_project_fails_every_criterion(tmp_path):
    graded = scorecard.grade(
        BENCH / "specs" / "01-cli-wordcount", tmp_path, sys.executable, in_process=True
    )
    assert graded["passed"] == 0
    assert set(graded["results"].values()) == {"fail"}


def test_grading_a_wrong_answer_fails_only_its_criteria(tmp_path):
    (tmp_path / "romannum").mkdir()
    source = (
        BENCH / "specs" / "02-lib-roman" / "fake" / "files" / "romannum" / "__init__.py"
    ).read_text()
    # A reader that accepts IIII: only the criterion that refuses it fails.
    broken = source.replace("or to_roman(total) != s", "")
    (tmp_path / "romannum" / "__init__.py").write_text(broken)
    graded = scorecard.grade(
        BENCH / "specs" / "02-lib-roman", tmp_path, sys.executable, in_process=True
    )
    assert [c for c, r in graded["results"].items() if r != "pass"] == ["REQ-2.2"]


FORGED_REPORT = """import sys

# A grader impostor: writes a JUnit report where every criterion passes.
for arg in sys.argv:
    if arg.startswith("--junitxml="):
        cases = "".join(
            f'<testcase classname="x" name="test_req_{r}_{c}_forged"/>' for r in (1, 2) for c in (1, 2)
        )
        open(arg.split("=", 1)[1], "w").write(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
sys.exit(0)
"""


def test_a_project_cannot_stand_in_for_the_grader(tmp_path):
    # The old grader (project on PYTHONPATH, then `-m pytest`) ran this
    # pytest.py instead of pytest and took its report: 4 of 4.
    (tmp_path / "pytest.py").write_text(FORGED_REPORT)
    (tmp_path / "sitecustomize.py").write_text(FORGED_REPORT)
    (tmp_path / "usercustomize.py").write_text("raise SystemExit(0)\n")
    (tmp_path / "conftest.py").write_text("raise SystemExit(0)\n")
    graded = scorecard.grade(
        BENCH / "specs" / "01-cli-wordcount", tmp_path, sys.executable, in_process=True
    )
    assert graded["exit_code"] == 1, graded["output_tail"]
    assert set(graded["results"].values()) == {"fail"}


def test_the_project_comes_after_the_standard_library(tmp_path):
    # A json.py in the project does not replace the one pytest's report needs.
    (tmp_path / "json.py").write_text("raise SystemExit(0)\n")
    (tmp_path / "romannum").mkdir()
    source = (
        BENCH / "specs" / "02-lib-roman" / "fake" / "files" / "romannum" / "__init__.py"
    )
    (tmp_path / "romannum" / "__init__.py").write_text(source.read_text())
    graded = scorecard.grade(
        BENCH / "specs" / "02-lib-roman", tmp_path, sys.executable, in_process=True
    )
    assert (graded["passed"], graded["total"]) == (5, 5), graded["output_tail"]


def test_a_report_pytest_did_not_finish_is_not_read(tmp_path):
    # A python that writes a passing report and exits 2 (interrupted).
    fake = tmp_path / "python"
    fake.write_text(
        f"#!{sys.executable}\n" + FORGED_REPORT.replace("sys.exit(0)", "sys.exit(2)")
    )
    fake.chmod(0o755)
    graded = scorecard.grade(
        BENCH / "specs" / "01-cli-wordcount", tmp_path, str(fake), in_process=True
    )
    assert graded["exit_code"] == 2
    assert set(graded["results"].values()) == {"missing"}


# ---------------------------------------------------------------------------
# HumanEval through the factory (scripts/bench/humaneval.py): the dataset's
# pin, a problem as a specification, its hidden tests as the grade. A small
# dataset written here stands in for the real one: no network.
# ---------------------------------------------------------------------------

_ADD = {
    "task_id": "HumanEval/0",
    "entry_point": "add",
    "prompt": 'def add(a: int, b: int) -> int:\n    """Return a plus b.\n    >>> add(1, 2)\n    3\n    """\n',
    "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n    assert candidate(-4, 4) == 0\n",
    "canonical_solution": "    return a + b\n",
}
# The test calls a helper the prompt defines: HumanEval's harness has it.
_DECODE = {
    "task_id": "HumanEval/2",
    "entry_point": "decode",
    "prompt": (
        "def encode(s: str) -> str:\n    return s[::-1]\n\n\n"
        'def decode(s: str) -> str:\n    """Undo encode.\n    """\n'
    ),
    "test": "def check(candidate):\n    assert candidate(encode('abc')) == 'abc'\n",
    "canonical_solution": "    return s[::-1]\n",
}


def _dataset(tmp_path, *rows):
    data = gzip.compress(
        "\n".join(json.dumps(r) for r in (rows or (_ADD, _DECODE))).encode()
    )
    path = tmp_path / "HumanEval.jsonl.gz"
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def test_the_humaneval_dataset_is_pinned_to_its_canonical_source():
    assert re.fullmatch(r"[0-9a-f]{64}", humaneval.DATASET_SHA256)
    assert re.fullmatch(
        r"https://raw\.githubusercontent\.com/openai/human-eval/[0-9a-f]{40}"
        r"/data/HumanEval\.jsonl\.gz",
        humaneval.DATASET_URL,
    )
    assert humaneval.DATASET_LICENSE == "MIT"
    # Never committed: no copy of the dataset in the repository.
    assert not list(BENCH.rglob("HumanEval*.jsonl*"))


def test_the_dataset_is_used_only_when_its_sha256_matches(tmp_path):
    path, digest = _dataset(tmp_path)
    cache = tmp_path / "cache"
    data, seen = humaneval.fetch(path.as_uri(), digest, cache)
    assert seen == digest == hashlib.sha256(data).hexdigest()
    assert (cache / "HumanEval.jsonl.gz").read_bytes() == data
    # The checked copy serves the next run without a download.
    assert humaneval.fetch("file:///nonexistent/x.gz", digest, cache)[1] == digest


def test_a_dataset_off_its_pin_is_refused_and_nothing_written(tmp_path):
    path, digest = _dataset(tmp_path)
    cache = tmp_path / "cache"
    with pytest.raises(humaneval.DatasetError, match="not the pinned"):
        humaneval.fetch(path.as_uri(), "0" * 64, cache)
    assert not cache.exists()
    # A cached copy changed after it was checked is refused too, never used.
    cache.mkdir()
    (cache / "HumanEval.jsonl.gz").write_bytes(b"tampered")
    with pytest.raises(humaneval.DatasetError, match="cached"):
        humaneval.fetch(path.as_uri(), digest, cache)
    with pytest.raises(humaneval.DatasetError, match="https"):
        humaneval.fetch("http://example.invalid/HumanEval.jsonl.gz", digest)


def test_prepare_refuses_a_dataset_off_its_pin(tmp_path):
    # The command line has no way around the pin: a mirror must match it.
    path, _ = _dataset(tmp_path)
    dest = tmp_path / "he"
    assert humaneval.main(["prepare", "--dest", str(dest), "--url", path.as_uri()]) == 2
    assert not (dest / "specs").exists()


@pytest.mark.parametrize(
    "data",
    [
        b"not gzip",
        gzip.compress(b"{}"),
        gzip.compress(b'{"task_id": "x"}\n'),
        gzip.compress(b""),
    ],
)
def test_a_malformed_dataset_is_refused(data):
    with pytest.raises(humaneval.DatasetError):
        humaneval.parse(data)


def test_prepare_writes_the_subset_and_its_index(tmp_path):
    path, digest = _dataset(tmp_path)
    index = humaneval.prepare(
        tmp_path / "he", "2,0", url=path.as_uri(), expected=digest, fake_root=tmp_path
    )
    assert index["subset"] == ["HumanEval/2", "HumanEval/0"]
    assert index["dataset"]["sha256"] == digest and index["dataset"]["license"] == "MIT"
    assert sorted(p.name for p in (tmp_path / "he" / "specs").iterdir()) == [
        "humaneval-000",
        "humaneval-002",
    ]
    assert json.loads((tmp_path / "he" / "problems.json").read_text()) == index


def test_the_default_subset_is_ten_fixed_problems_and_the_fake_answers_some():
    problems = {f"HumanEval/{n}": {} for n in range(164)}
    assert humaneval.resolve_ids("default", problems) == list(humaneval.DEFAULT_IDS)
    assert len(humaneval.DEFAULT_IDS) == 10
    answered = humaneval.resolve_ids("", problems, fake=True)
    assert 2 <= len(answered) <= 3 and set(answered) <= set(humaneval.DEFAULT_IDS)
    assert humaneval.resolve_ids("7, HumanEval/0,7", problems) == [
        "HumanEval/7",
        "HumanEval/0",
    ]
    with pytest.raises(humaneval.DatasetError, match="not in the dataset"):
        humaneval.resolve_ids("999", problems)
    with pytest.raises(humaneval.DatasetError, match="no answer"):
        humaneval.resolve_ids("7", problems, fake=True)
    with pytest.raises(humaneval.DatasetError):
        humaneval.resolve_ids("HumanEval/x", problems)


def test_a_problem_is_a_one_requirement_spec_the_check_approves():
    from services.planning import spec_check

    spec = humaneval.to_spec(_ADD)
    normalized = spec_check.normalize(spec)
    assert spec_check.check(normalized)["ok"], spec_check.check(normalized)
    assert scorecard.criteria_of(spec) == ["REQ-1.1"]
    assert spec["title"] == "HumanEval/0: add"
    assert "def add(a: int, b: int) -> int" in spec["requirements"][0]["description"]
    assert "Return a plus b." in spec["summary"]
    # The hidden tests are never in what the factory is shown.
    shown = json.dumps(spec)
    assert "check(" not in shown and "candidate" not in shown and "-4, 4" not in shown


def test_a_materialized_problem_hides_its_tests_and_starts_from_its_prompt(tmp_path):
    row = humaneval.materialize(_ADD, tmp_path, fake_root=tmp_path / "none")
    folder = tmp_path / "humaneval-000"
    assert row == {
        "task_id": "HumanEval/0",
        "spec": "humaneval-000",
        "entry_point": "add",
        "fake": False,
    }
    assert (folder / "seed" / "solution.py").read_text() == _ADD["prompt"]
    seeded = [p.read_text() for p in (folder / "seed").rglob("*") if p.is_file()]
    assert seeded and not any("candidate" in text for text in seeded)
    test = (folder / "acceptance" / "test_humaneval.py").read_text()
    assert "def test_req_1_1_humaneval(" in test
    assert not (folder / "fake").exists()
    # The seed is committed as the base, as for a seeded reference specification.
    prepared = seed.prepare(folder, tmp_path / "project")
    assert prepared["seeded"] and prepared["files"] == 2


def _graded(tmp_path, problem, solution):
    humaneval.materialize(problem, tmp_path / "specs", fake_root=tmp_path / "none")
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    if solution is not None:
        (project / "solution.py").write_text(solution)
    return scorecard.grade(
        tmp_path / "specs" / humaneval.spec_name(problem["task_id"]),
        project,
        sys.executable,
        in_process=True,
    )


@pytest.mark.parametrize(
    ("problem", "solution", "result"),
    [
        (_ADD, "def add(a, b):\n    return a + b\n", "pass"),
        (_ADD, "def add(a, b):\n    return a - b\n", "fail"),
        (_ADD, _ADD["prompt"], "fail"),  # the seed alone returns None
        (_ADD, None, "fail"),  # nothing delivered
        (
            _DECODE,
            "def decode(s):\n    return s[::-1]\n",
            "pass",
        ),  # the prompt's helper
    ],
)
def test_humaneval_s_own_tests_grade_the_delivery(tmp_path, problem, solution, result):
    graded = _graded(tmp_path, problem, solution)
    assert graded["results"] == {"REQ-1.1": result}, graded["output_tail"]


def test_a_delivery_cannot_replace_the_hidden_check(tmp_path):
    # A check() of the delivery's own is overwritten by the dataset's.
    forged = "def add(a, b):\n    return 0\n\n\ndef check(candidate):\n    pass\n"
    assert _graded(tmp_path, _ADD, forged)["results"] == {"REQ-1.1": "fail"}


FAKE_DIRS = sorted(p for p in humaneval.FAKE_ROOT.iterdir() if p.is_dir())


def test_the_fake_answers_two_or_three_problems_of_the_default_subset():
    ids = [p.name.replace("_", "/") for p in FAKE_DIRS]
    assert 2 <= len(ids) <= 3 and set(ids) <= set(humaneval.DEFAULT_IDS)


@pytest.mark.parametrize("answers", FAKE_DIRS, ids=lambda p: p.name)
def test_a_fake_answer_passes_its_own_qa_tests_and_feeds_the_fake_llm(
    tmp_path, answers
):
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."],
        cwd=answers,
        env={"PYTHONPATH": str(answers), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    tid = answers.name.replace("_", "/")
    source = (answers / "solution.py").read_text()
    entry = re.search(r"^def (\w+)\(", source, re.MULTILINE).group(1)
    problem = {**_ADD, "task_id": tid, "entry_point": entry, "prompt": source}
    row = humaneval.materialize(problem, tmp_path)
    assert row["fake"] is True
    plans, deliveries = fake_llm.load(tmp_path)
    plan = plans[f"{tid}: {entry}"]
    assert [t["covers"] for t in plan["tasks"]] == [["REQ-1.1"]]
    assert deliveries[plan["tasks"][0]["title"]][0][0] == "solution.py"
    qa = f"Acceptance tests for REQ-1: {humaneval.requirement_title(problem)}"[:80]
    assert [p for p, _ in deliveries[qa]] == ["tests/test_req_1.py"]
